#!/usr/bin/env bash
#
# sambuca :: engine/first-boot.sh
#
# The provisioning orchestrator. Runs once at first boot (systemd oneshot), then
# stays available as the manual repair entrypoint.
#
# It wires the appliance together in a fixed, resumable order:
#
#   00-preflight     validate the payload, network, clock, disk
#   10-system        hostname, locale, users, ssh, unattended-upgrades
#   20-docker        Docker CE + Compose plugin + daemon hardening
#   30-gpu-runtime   NVIDIA Container Toolkit / ROCm  -> re-profile hardware
#   40-storage-pool  MergerFS union + SnapRAID parity
#   50-network       Tailscale mesh + Caddy trust + CasaOS port move
#   60-stack         render .env, docker compose up
#   70-models        ollama pull, sized by the detected tier
#   80-identity      Pocket ID + oauth2-proxy bootstrap
#   90-report        completion report + first-login instructions
#
# Every phase is IDEMPOTENT and records completion in ${SB_LIB}/state. A failed
# phase halts the run with a precise error; re-invoking resumes from that phase
# rather than redoing the work that already succeeded.
#
set -uo pipefail

SB_TAG="first-boot"
_SB_SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=engine/lib/common.sh
source "${_SB_SELF_DIR}/lib/common.sh"
sb_trap_err

PHASE_DIR="${_SB_SELF_DIR}/provision"
PROVISION_JSON="${SAMBUCA_PROVISION_JSON:-/boot/sambuca/provision.json}"

ONLY=""; SKIP=""; FORCE=0; LIST=0; FROM=""

usage() {
    cat <<'USAGE'
sambuca first-boot — provision the appliance from a clean install.

Usage: first-boot.sh [options]

  --list             Show the phase plan and each phase's state, then exit.
  --only NAME[,...]  Run only these phases (by name or number prefix).
  --skip NAME[,...]  Skip these phases.
  --from NAME        Start at this phase and run everything after it.
  --force            Re-run phases already marked complete.
  --dry-run          Print what each phase would do without changing the system.
  --quiet            Suppress INFO logging.
  -h, --help         This text.

Examples:
  first-boot.sh                      # full run, resuming where it left off
  first-boot.sh --only 70-models     # just re-pull the model set
  first-boot.sh --from 60-stack --force
USAGE
}

while (($# > 0)); do
    case "$1" in
        --list)    LIST=1 ;;
        --only)    ONLY="${2:-}"; shift ;;
        --skip)    SKIP="${2:-}"; shift ;;
        --from)    FROM="${2:-}"; shift ;;
        --force)   FORCE=1 ;;
        --dry-run) SB_DRY_RUN=1 ;;
        --quiet)   SB_QUIET=1 ;;
        -h|--help) usage; exit 0 ;;
        *)         usage >&2; SB_EXIT_CODE=2 die "unknown argument: $1" ;;
    esac
    shift
done

sb_require_root
# Provisioning mutates Docker state, fstab and the network. Two concurrent runs
# would interleave `compose up` with `tailscale up`. Kill any predecessor first.
sb_single_instance "first-boot" 15

mkdir -p -- "$SB_ETC" "$SB_LIB" "$SB_LOG_DIR" "$SB_STATE_DIR"
chmod 0750 -- "$SB_ETC"

# ---------------------------------------------------------------------------
# Payload ingestion.
#
# The flasher wrote provision.json to the boot partition. It carries only
# non-derived configuration plus the single-use Tailscale auth key. The root
# passphrase is NOT stored here in plaintext — see docs/SECURITY.md for the
# unattended-vs-interactive tradeoff and why the payload is shredded below.
# ---------------------------------------------------------------------------
ingest_payload() {
    if [[ ! -r $PROVISION_JSON ]]; then
        warn "no provision payload at ${PROVISION_JSON}"
        warn "falling back to defaults — hostname/timezone/tailscale must be set manually"
        return 0
    fi

    if ! sb_have jq; then
        # jq is guaranteed by the preseed, but never let a missing tool wedge boot.
        warn "jq unavailable; installing it before parsing the payload"
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends jq >/dev/null 2>&1 || true
    fi
    sb_have jq || die "cannot parse ${PROVISION_JSON} without jq"

    jq -e . "$PROVISION_JSON" >/dev/null 2>&1 \
        || die "provision payload is not valid JSON: ${PROVISION_JSON}"

    local schema; schema="$(jq -r '.schema // 0' "$PROVISION_JSON")"
    [[ $schema == 1 ]] || die "unsupported provision schema '${schema}' (this engine speaks 1)"

    # Render to a root-only env file the phases source. Never echo secret values.
    {
        printf '%s\n' "# generated from ${PROVISION_JSON} at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        jq -r '
          def q: @sh;
          "SAMBUCA_HOSTNAME="       + ((.hostname      // "sambuca") | q),
          "SAMBUCA_TIMEZONE="       + ((.timezone      // "UTC") | q),
          "SAMBUCA_LOCALE="         + ((.locale        // "en_US.UTF-8") | q),
          "SAMBUCA_ADMIN_USER="     + ((.admin_user    // "sambuca") | q),
          "SAMBUCA_ADMIN_SSH_KEY="  + ((.admin_ssh_key // "") | q),
          "SAMBUCA_DOMAIN="         + ((.domain        // "sambuca.local") | q),
          "SAMBUCA_ACME_EMAIL="     + ((.acme_email    // "") | q),
          "SAMBUCA_TS_AUTHKEY="     + ((.tailscale_authkey // "") | q),
          "SAMBUCA_TS_TAGS="        + ((.tailscale_tags // "tag:sambuca") | q),
          "SAMBUCA_BACKUP_SEED_HASH=" + ((.backup_seed_hash // "") | q),
          "SAMBUCA_BUNDLES="        + (((.bundles // ["ai","cloud","office","comms"]) | join(",")) | q),
          "SAMBUCA_TIER_OVERRIDE="  + ((.tier_override // "" | tostring) | q),
          "SAMBUCA_DATA_DISKS="     + (((.data_disks // []) | join(",")) | q),
          "SAMBUCA_PARITY_DISKS="   + (((.parity_disks // []) | join(",")) | q)
        ' "$PROVISION_JSON"
    } | sb_atomic_write "${SB_ETC}/provision.env" 0600

    ok "payload ingested -> ${SB_ETC}/provision.env (0600)"

    # The USB is a key until this point. Once the config is on the encrypted
    # root, the copy on the unencrypted boot partition is a liability.
    if [[ $SB_DRY_RUN != 1 ]] && [[ "$(jq -r '.shred_after_install // true' "$PROVISION_JSON")" == "true" ]]; then
        if sb_have shred; then
            shred -u -n 3 -- "$PROVISION_JSON" 2>/dev/null \
                && ok "provision payload shredded from the boot partition" \
                || warn "could not shred ${PROVISION_JSON} — remove it manually"
        else
            rm -f -- "$PROVISION_JSON" && warn "payload removed (shred unavailable; overwrite not guaranteed)"
        fi
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Phase execution
# ---------------------------------------------------------------------------
list_phases() {
    local f n
    for f in "$PHASE_DIR"/[0-9][0-9]-*.sh; do
        [[ -e $f ]] || continue
        n="$(basename -- "$f" .sh)"
        printf '  %-18s %s\n' "$n" "$(sb_state_done "$n" && echo "done ($(cat "$SB_STATE_DIR/$n.done"))" || echo "pending")"
    done
}

in_csv() {
    local needle="$1" csv="$2" item
    [[ -z $csv ]] && return 1
    IFS=',' read -ra _items <<<"$csv"
    for item in "${_items[@]}"; do
        item="${item// /}"
        [[ -z $item ]] && continue
        [[ $needle == "$item" || $needle == "$item"* ]] && return 0
    done
    return 1
}

run_phase() {
    local script="$1"
    local name; name="$(basename -- "$script" .sh)"

    if [[ -n $ONLY ]] && ! in_csv "$name" "$ONLY"; then return 0; fi
    if [[ -n $SKIP ]] &&   in_csv "$name" "$SKIP"; then log "skip ${name} (--skip)"; return 0; fi

    if sb_state_done "$name" && ((FORCE == 0)) && [[ -z $ONLY ]]; then
        log "skip ${name} (already complete; --force to re-run)"
        return 0
    fi

    log "───── phase ${name} ─────"
    local started; started="$(date +%s)"

    # Phases run in a subshell: a phase that leaks `set -x`, changes directory,
    # or exports something odd cannot contaminate the orchestrator.
    # shellcheck source=/dev/null  (phase path is resolved at runtime by design)
    if ( set -uo pipefail; SB_TAG="$name"; source "$script" ); then
        local elapsed=$(( $(date +%s) - started ))
        [[ $SB_DRY_RUN == 1 ]] || sb_state_mark "$name"
        ok "phase ${name} complete (${elapsed}s)"
        return 0
    fi

    err "phase ${name} FAILED"
    err "  the system is left in the state that phase reached — nothing was rolled back."
    err "  inspect:  journalctl -u sambuca-first-boot -n 200"
    err "            tail -n 200 ${SB_LOG_FILE}"
    err "  resume :  ${BASH_SOURCE[0]} --from ${name}"
    return 1
}

main() {
    if ((LIST)); then
        printf 'sambuca provisioning plan (state in %s):\n' "$SB_STATE_DIR"
        list_phases
        return 0
    fi

    log "sambuca first-boot starting (dry-run=${SB_DRY_RUN})"
    ingest_payload

    # Profile the hardware before any phase that depends on the tier. Phase
    # 30 re-runs it once the GPU driver exists and VRAM becomes readable.
    log "───── phase 05-profile ─────"
    if ! "${_SB_SELF_DIR}/hardware-detect.sh" ${SB_QUIET:+--quiet}; then
        warn "hardware profiling failed — continuing with conservative defaults"
        printf 'SAMBUCA_TIER=4\nSAMBUCA_TIER_NAME=low-resource\nSAMBUCA_GPU_PROFILE=cpu\n' \
            | sb_atomic_write "${SB_ETC}/profile.env" 0644
    fi

    local started_at_phase=0
    [[ -z $FROM ]] && started_at_phase=1

    local f name rc=0
    for f in "$PHASE_DIR"/[0-9][0-9]-*.sh; do
        [[ -e $f ]] || continue
        name="$(basename -- "$f" .sh)"
        if ((started_at_phase == 0)); then
            if [[ $name == "$FROM" || $name == "$FROM"* ]]; then started_at_phase=1; else continue; fi
        fi
        run_phase "$f" || { rc=1; break; }
    done

    if ((rc != 0)); then
        err "provisioning INCOMPLETE"
        return 1
    fi

    ok "provisioning complete"
    [[ -r "${SB_LIB}/completion-report.txt" ]] && cat -- "${SB_LIB}/completion-report.txt"
    return 0
}

main "$@"
