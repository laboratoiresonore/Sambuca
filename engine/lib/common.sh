#!/usr/bin/env bash
# shellcheck shell=bash
#
# sambuca :: engine/lib/common.sh
#
# Shared primitives for every engine script: logging, error trapping, retries,
# atomic writes, single-instance enforcement and phase-state tracking.
#
# This file is SOURCED, never executed. It is intentionally dependency-free
# (coreutils + util-linux only) so it works inside the Debian installer's
# minimal chroot, before Docker or anything else exists.

[[ -n ${_SB_COMMON_LOADED:-} ]] && return 0
_SB_COMMON_LOADED=1

# ---------------------------------------------------------------------------
# Canonical paths. Overridable for tests via the environment.
# ---------------------------------------------------------------------------
SB_ETC="${SB_ETC:-/etc/sambuca}"
SB_LIB="${SB_LIB:-/var/lib/sambuca}"
SB_LOG_DIR="${SB_LOG_DIR:-/var/log/sambuca}"
SB_RUN="${SB_RUN:-/run/sambuca}"
SB_STATE_DIR="${SB_STATE_DIR:-$SB_LIB/state}"
SB_REPO_ROOT="${SB_REPO_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)}"

SB_LOG_FILE="${SB_LOG_FILE:-$SB_LOG_DIR/sambuca.log}"
SB_QUIET="${SB_QUIET:-0}"
SB_DRY_RUN="${SB_DRY_RUN:-0}"

# ---------------------------------------------------------------------------
# Logging. Everything goes to stderr + the log file so stdout stays clean for
# machine-readable output (--json). Colour only when stderr is a TTY.
# ---------------------------------------------------------------------------
if [[ -t 2 ]] && [[ ${TERM:-dumb} != dumb ]]; then
    _SB_C_RED=$'\033[31m'; _SB_C_YEL=$'\033[33m'; _SB_C_GRN=$'\033[32m'
    _SB_C_DIM=$'\033[2m';  _SB_C_OFF=$'\033[0m'
else
    _SB_C_RED=''; _SB_C_YEL=''; _SB_C_GRN=''; _SB_C_DIM=''; _SB_C_OFF=''
fi

_sb_emit() {
    # _sb_emit <level> <colour> <message...>
    local level="$1" colour="$2"; shift 2
    local ts; ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local line="[$ts] [$level] [${SB_TAG:-sambuca}] $*"

    [[ $SB_QUIET == 1 && $level == "INFO" ]] || printf '%s%s%s\n' "$colour" "$line" "$_SB_C_OFF" >&2

    # Log-file writes must never abort the caller (read-only fs in the installer,
    # full disk, etc.) — the console line above is the guaranteed channel.
    if [[ -n ${SB_LOG_FILE:-} ]]; then
        mkdir -p -- "$(dirname -- "$SB_LOG_FILE")" 2>/dev/null || true
        printf '%s\n' "$line" >>"$SB_LOG_FILE" 2>/dev/null || true
    fi
}

log()   { _sb_emit INFO  "$_SB_C_DIM" "$@"; }
ok()    { _sb_emit  OK   "$_SB_C_GRN" "$@"; }
warn()  { _sb_emit WARN  "$_SB_C_YEL" "$@"; }
err()   { _sb_emit ERROR "$_SB_C_RED" "$@"; }
die()   { err "$@"; exit "${SB_EXIT_CODE:-1}"; }

# ---------------------------------------------------------------------------
# Error trapping. Installs a backtrace-on-failure handler. Call once, early.
# ---------------------------------------------------------------------------
sb_trap_err() {
    set -Eeuo pipefail
    trap '_sb_on_err $? $LINENO "${BASH_COMMAND}"' ERR
    trap '_sb_on_exit $?' EXIT
}

_sb_on_err() {
    local code="$1" line="$2" cmd="$3"
    err "FAILED (exit $code) at ${BASH_SOURCE[1]:-?}:${line}"
    err "  command: ${cmd}"
    local i
    for ((i = 1; i < ${#FUNCNAME[@]} - 1; i++)); do
        err "  at ${FUNCNAME[i]}() ${BASH_SOURCE[i + 1]:-?}:${BASH_LINENO[i]}"
    done
}

_sb_on_exit() {
    local code="$1"
    [[ -n ${_SB_LOCK_FILE:-} ]] && rm -f -- "${_SB_LOCK_FILE}.pid" 2>/dev/null || true
    if declare -F sb_cleanup >/dev/null; then sb_cleanup "$code" || true; fi
}

# ---------------------------------------------------------------------------
# Guards and small utilities
# ---------------------------------------------------------------------------
sb_require_root() {
    [[ ${EUID:-$(id -u)} -eq 0 ]] || die "must run as root (got uid ${EUID:-?})"
}

sb_have() { command -v -- "$1" >/dev/null 2>&1; }

sb_require() {
    local missing=()
    for c in "$@"; do sb_have "$c" || missing+=("$c"); done
    ((${#missing[@]} == 0)) || die "missing required commands: ${missing[*]}"
}

# sb_run <cmd...> — honours SB_DRY_RUN, logs the command.
sb_run() {
    if [[ $SB_DRY_RUN == 1 ]]; then log "DRY-RUN: $*"; return 0; fi
    log "exec: $*"
    "$@"
}

# sb_retry <attempts> <delay-seconds> <cmd...>
sb_retry() {
    local attempts="$1" delay="$2"; shift 2
    local n=1
    until "$@"; do
        if ((n >= attempts)); then
            err "command failed after ${attempts} attempts: $*"
            return 1
        fi
        warn "attempt ${n}/${attempts} failed, retrying in ${delay}s: $*"
        sleep "$delay"
        ((n++))
        delay=$((delay * 2))
    done
    return 0
}

# sb_atomic_write <path> [mode] — content on stdin. Never leaves a torn file.
sb_atomic_write() {
    local path="$1" mode="${2:-0644}"
    local dir; dir="$(dirname -- "$path")"
    mkdir -p -- "$dir"
    local tmp; tmp="$(mktemp -- "${path}.tmp.XXXXXX")"
    cat >"$tmp"
    chmod "$mode" -- "$tmp"
    mv -f -- "$tmp" "$path"
}

# sb_secret <bytes> — URL-safe random secret, no shell-hostile characters.
sb_secret() {
    local bytes="${1:-32}"
    openssl rand -base64 "$((bytes * 2))" 2>/dev/null | tr -d '/+=\n' | head -c "$bytes"
    printf '\n'
}

sb_json_escape() {
    # Minimal, correct JSON string escaping for the fields we emit.
    local s="$1"
    s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/\\n}"; s="${s//$'\t'/\\t}"
    s="${s//$'\r'/}"
    printf '%s' "$s"
}

# ---------------------------------------------------------------------------
# Single-instance enforcement (DETECT + KILL).
#
# Every long-running or boot-critical entrypoint calls this FIRST, before it
# binds a port, takes a lock or writes anything. An orphaned prior instance is
# actively killed rather than assumed absent — a wedged predecessor holding a
# half-written profile is the exact failure this prevents.
# ---------------------------------------------------------------------------
sb_single_instance() {
    local name="$1" grace="${2:-5}"
    mkdir -p -- "$SB_RUN"
    local lock="$SB_RUN/${name}.lock"
    _SB_LOCK_FILE="$lock"

    exec 9>"$lock" || die "cannot open lock $lock"

    if ! flock -n 9; then
        local prior=""
        [[ -r "${lock}.pid" ]] && prior="$(cat -- "${lock}.pid" 2>/dev/null || true)"

        if [[ -n $prior ]] && [[ $prior =~ ^[0-9]+$ ]] && kill -0 "$prior" 2>/dev/null; then
            warn "prior instance of '${name}' still running (pid ${prior}) — terminating it"
            kill -TERM "$prior" 2>/dev/null || true
            local waited=0
            while kill -0 "$prior" 2>/dev/null && ((waited < grace)); do
                sleep 1; ((waited++))
            done
            if kill -0 "$prior" 2>/dev/null; then
                warn "pid ${prior} ignored SIGTERM — sending SIGKILL"
                kill -KILL "$prior" 2>/dev/null || true
                sleep 1
            fi
        else
            warn "stale lock for '${name}' (no live pid) — reclaiming"
        fi

        flock -w 10 9 || die "could not acquire lock for '${name}' after killing prior instance"
    fi

    printf '%s\n' "$$" >"${lock}.pid"
    log "single-instance lock held for '${name}' (pid $$)"
}

# ---------------------------------------------------------------------------
# Phase state — makes provisioning resumable and idempotent.
# ---------------------------------------------------------------------------
sb_state_done()  { [[ -f "$SB_STATE_DIR/$1.done" ]]; }
sb_state_mark()  { mkdir -p -- "$SB_STATE_DIR"; date -u +%Y-%m-%dT%H:%M:%SZ >"$SB_STATE_DIR/$1.done"; }
sb_state_clear() { rm -f -- "$SB_STATE_DIR/$1.done"; }

# sb_env_get <file> <key> [default] — read a KEY=VALUE file without sourcing it.
sb_env_get() {
    local file="$1" key="$2" default="${3:-}"
    [[ -r $file ]] || { printf '%s' "$default"; return 0; }
    local v
    v="$(grep -E "^${key}=" -- "$file" 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
    v="${v%\"}"; v="${v#\"}"
    printf '%s' "${v:-$default}"
}
