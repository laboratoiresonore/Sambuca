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
    # WAS SILENTLY FAILING. Every step here was unchecked: cat, chmod and mv
    # could all fail and the function still returned 0. A profile that never
    # landed reported success, and the caller carried on as though it had —
    # which is the same silent-success shape this project already documents as
    # a failure mode elsewhere (a checkin that exits 0 on a failed push masks
    # itself from every exit-code monitor watching it).
    #
    # It also left litter. A failed mv — target is a directory, permission
    # denied, disk full — abandoned the temp file next to the target, so a
    # config directory slowly filled with .tmp.XXXXXX copies of things that
    # never got written.
    local path="$1" mode="${2:-0644}"
    local dir; dir="$(dirname -- "$path")"

    # REFUSE A DIRECTORY TARGET. `mv file dir` does not fail — it moves the
    # file INTO the directory, keeping its temp name. So writing a profile to
    # a directory path returned 0 and produced a randomly-named file inside it:
    # apparent success, real content, and a path nothing will ever look at.
    # Found by testing the failure branch and discovering there was not one.
    if [[ -d $path ]]; then
        err "refusing to write $path: it is a directory, not a file"
        return 1
    fi

    mkdir -p -- "$dir" || { err "cannot create $dir"; return 1; }

    local tmp
    tmp="$(mktemp -- "${path}.tmp.XXXXXX")" || { err "cannot create a temp file beside $path"; return 1; }

    # Remove the temp on ANY exit from here, successful or not. Cleared once
    # the rename has taken ownership of it.
    local _sb_aw_tmp="$tmp"
    trap 'rm -f -- "$_sb_aw_tmp" 2>/dev/null' RETURN

    if ! cat >"$tmp"; then
        err "failed writing content for $path"
        return 1
    fi
    if ! chmod "$mode" -- "$tmp"; then
        err "failed setting mode $mode on $path"
        return 1
    fi
    if ! mv -f -- "$tmp" "$path"; then
        err "failed installing $path (is it a directory, or read-only?)"
        return 1
    fi

    # The rename succeeded, so the temp path no longer exists; stop the trap
    # from reporting on a file that is now the target.
    _sb_aw_tmp=""
    return 0
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
# Stage narration.
#
# An unattended installer that prints nothing but log lines is terrifying to a
# non-technical owner: a black screen scrolling `exec: apt-get` for forty
# minutes is indistinguishable from a machine that has hung. Every stage
# therefore announces four things, in the owner's language, before it starts:
#
#     WHAT is happening now
#     HOW LONG it usually takes
#     WHAT THE OWNER SHOULD DO (usually: nothing, and saying so matters)
#     WHAT COMES NEXT
#
# and, if it fails, exactly what to do about it. This is the difference between
# "it's working" and "I think it's broken, I'll power-cycle it" — which, during
# disk provisioning, is how installs get corrupted.
# ---------------------------------------------------------------------------
# Only the TOTAL is state here. The current index is passed in by the caller,
# which already tracks it — keeping a second copy would give two variables that
# can disagree about which step is running.
SB_STAGE_TOTAL="${SB_STAGE_TOTAL:-0}"

sb_stage() {
    # sb_stage <number> <title> <what> <how-long> <your-move> <next>
    local num="$1" title="$2" what="$3" howlong="$4" action="$5" next="$6"
    local width=72
    local bar; bar="$(printf '%*s' "$width" '' | tr ' ' '-')"

    {
        printf '\n%s\n' "$bar"
        if [[ ${SB_STAGE_TOTAL:-0} -gt 0 ]]; then
            printf '  STEP %s OF %s   %s\n' "$num" "$SB_STAGE_TOTAL" "$title"
        else
            printf '  %s\n' "$title"
        fi
        printf '%s\n' "$bar"
        printf '  What is happening : %s\n' "$what"
        printf '  How long          : %s\n' "$howlong"
        printf '  What you do       : %s\n' "$action"
        [[ -n $next ]] && printf '  Next              : %s\n' "$next"
        printf '\n'
    } >&2

    # Also to the log file, so a support conversation can reconstruct the run.
    if [[ -n ${SB_LOG_FILE:-} ]]; then
        mkdir -p -- "$(dirname -- "$SB_LOG_FILE")" 2>/dev/null || true
        printf '[stage %s/%s] %s — %s\n' "$num" "${SB_STAGE_TOTAL:-?}" "$title" "$what" \
            >>"$SB_LOG_FILE" 2>/dev/null || true
    fi

    # And to a JSON file the setup page polls, so the owner can watch progress
    # from their laptop instead of standing at a monitor. Best-effort: a failure
    # to write it must never affect provisioning, because a progress display is
    # not worth risking the thing whose progress it displays.
    sb_progress_write "$num" "$title" "$what" "$howlong" "$action" "$next" "running" || true
}

# Written by sb_stage; served read-only to the setup page at /setup/progress.json.
SB_PROGRESS_FILE="${SB_PROGRESS_FILE:-$SB_LIB/progress.json}"

sb_progress_write() {
    local num="$1" title="$2" what="$3" howlong="$4" action="$5" next="$6" state="$7"
    mkdir -p -- "$(dirname -- "$SB_PROGRESS_FILE")" 2>/dev/null || return 0
    {
        printf '{\n'
        printf '  "schema": 1,\n'
        printf '  "updated": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '  "state": "%s",\n' "$state"
        printf '  "step": %s,\n' "${num:-0}"
        printf '  "steps_total": %s,\n' "${SB_STAGE_TOTAL:-0}"
        printf '  "title": "%s",\n'    "$(sb_json_escape "$title")"
        printf '  "what": "%s",\n'     "$(sb_json_escape "$what")"
        printf '  "how_long": "%s",\n' "$(sb_json_escape "$howlong")"
        printf '  "your_move": "%s",\n' "$(sb_json_escape "$action")"
        printf '  "next": "%s"\n'      "$(sb_json_escape "$next")"
        printf '}\n'
    } >"${SB_PROGRESS_FILE}.tmp" 2>/dev/null \
        && mv -f -- "${SB_PROGRESS_FILE}.tmp" "$SB_PROGRESS_FILE" 2>/dev/null \
        && chmod 0644 -- "$SB_PROGRESS_FILE" 2>/dev/null
    return 0
}

sb_stage_ok() {
    # sb_stage_ok <title> <plain-language outcome>
    printf '\n  ✓ %s — %s\n\n' "$1" "$2" >&2
}

sb_stage_failed() {
    # sb_stage_failed <title> <what it means> <what to do...>
    local title="$1" meaning="$2"; shift 2
    {
        printf '\n%s\n' "======================================================================"
        printf '  STEP FAILED: %s\n' "$title"
        printf '%s\n\n' "======================================================================"
        printf '  What this means : %s\n\n' "$meaning"
        printf '  What to do next :\n'
        local step
        for step in "$@"; do printf '      %s\n' "$step"; done
        printf '\n  Nothing after this step has run. The machine is safe to leave\n'
        printf '  powered on while you sort it out.\n\n'
    } >&2
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
