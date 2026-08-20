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
# shellcheck source=engine/lib/beacon.sh
SB_BEACON_SCRIPT="${_SB_SELF_DIR}/beacon/sambuca-beacon.py"
source "${_SB_SELF_DIR}/lib/beacon.sh"
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
          "SAMBUCA_BEACON_PAIRING_KEY=" + ((.beacon_key // "") | q),
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

# Plain-language narration for each phase, in the owner's words rather than
# ours. Fields: title | what is happening | how long | what you do | what next.
# A phase with no entry still runs; it just gets a terse banner.
declare -A STAGE_INFO=(
    [10-system]="Preparing the system|Setting up accounts, the clock and security updates.|about 2 minutes|Nothing — sit tight.|Installing the container engine."
    [20-docker]="Installing the container engine|Downloading and configuring Docker, which runs every service.|2 to 5 minutes, depending on your connection|Nothing.|Setting up your graphics card, if you have one."
    [30-gpu-runtime]="Setting up the graphics card|Installing the driver so the AI can use your GPU. Skipped if you have none.|2 to 10 minutes|Nothing. If it asks for a reboot later, that is normal.|Preparing your disks."
    [40-storage-pool]="Preparing your disks|Combining your drives into one pool and setting up parity.|under a minute, or a few if disks are formatted|Nothing. Your existing files are not touched.|Connecting to the network."
    [50-network]="Connecting to the network|Joining your private mesh and setting up the firewall.|about a minute|Nothing.|Starting your services."
    [60-stack]="Starting your services|Downloading and starting files, photos, passwords, chat and the AI.|10 to 40 minutes on a first install — this is the long one|Nothing. Downloads are large; leave it running.|Downloading the AI model."
    [70-models]="Downloading the AI model|Fetching the largest model your hardware can actually run.|10 minutes to several hours, depending on your connection|Nothing. You can close this screen; it keeps going.|Setting up sign-in."
    [80-identity]="Setting up sign-in|Preparing passkey login for the services that need it.|under a minute|Nothing yet — you will register your passkey at the end.|Finishing up."
    [90-report]="Finishing up|Writing your instructions and checking everything is healthy.|a few seconds|Read the summary that appears next. It has your addresses.|Done."
)

stage_banner() {
    local name="$1" num="$2"
    local info="${STAGE_INFO[$name]:-}"
    if [[ -z $info ]]; then
        log "───── phase ${name} ─────"
        return 0
    fi
    IFS='|' read -r title what howlong action next <<<"$info"
    sb_stage "$num" "$title" "$what" "$howlong" "$action" "$next"
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

    STAGE_NUM=$((STAGE_NUM + 1))
    stage_banner "$name" "$STAGE_NUM"
    local started; started="$(date +%s)"

    # Phases run in a subshell: a phase that leaks `set -x`, changes directory,
    # or exports something odd cannot contaminate the orchestrator.
    # The phase path is resolved at runtime by design, so it cannot be followed.
    # shellcheck source=/dev/null
    if ( set -uo pipefail; SB_TAG="$name"; source "$script" ); then
        local elapsed=$(( $(date +%s) - started ))
        [[ $SB_DRY_RUN == 1 ]] || sb_state_mark "$name"
        local title="${STAGE_INFO[$name]%%|*}"
        sb_stage_ok "${title:-$name}" "finished in $(fmt_duration "$elapsed")"
        return 0
    fi

    local title="${STAGE_INFO[$name]%%|*}"
    sb_stage_failed "${title:-$name}" \
        "This step could not finish. Everything before it succeeded and is safe." \
        "1. Look at what went wrong:" \
        "     tail -n 40 ${SB_LOG_FILE}" \
        "" \
        "2. Most failures at this stage are the network. Check the cable or" \
        "   Wi-Fi, then try again — it picks up where it stopped:" \
        "     sambuca-first-boot --from ${name}" \
        "" \
        "3. If it fails the same way twice, report it with that log:" \
        "     https://github.com/laboratoiresonore/Sambuca/issues"
    return 1
}

fmt_duration() {
    local s="$1"
    if ((s < 60)); then printf '%s seconds' "$s"
    elif ((s < 3600)); then printf '%s minutes' "$((s / 60))"
    else printf '%s hours %s minutes' "$((s / 3600))" "$(((s % 3600) / 60))"; fi
}

main() {
    if ((LIST)); then
        printf 'sambuca provisioning plan (state in %s):\n' "$SB_STATE_DIR"
        list_phases
        return 0
    fi

    # Count the stages up front so every banner can say "step 3 of 10" — an
    # owner watching an unattended install needs to know how much is left, or
    # a long step is indistinguishable from a hang.
    SB_STAGE_TOTAL=1   # the profiling stage below
    local f
    for f in "$PHASE_DIR"/[0-9][0-9]-*.sh; do [[ -e $f ]] && ((SB_STAGE_TOTAL++)); done
    export SB_STAGE_TOTAL
    STAGE_NUM=0

    # The console is a TTY: no images, so the mark is drawn in plain ASCII.
    # Same silhouette as assets/brand/sambuca-mark.svg - the closed corporate
    # loop, broken open by the ramp. ASCII rather than box-drawing characters
    # because the Debian installer console is not guaranteed to be in a UTF-8
    # locale, and a mojibake logo is a worse first impression than a plain one.
    {
        printf '
'
        printf '                              /
'
        printf '        .d888888b.          /
'
        printf '      d88P"    "Y88b      /
'
        printf '     d88P         Y8/b  /
'
        printf '     888          /888 /
'
        printf '     888         / 888/
'
        printf '     888        /  /88
'
        printf '     Y88b      /  d88P
'
        printf '      "Y88b.  /.d88P"
'
        printf '        "Y8/8888P"
'
        printf '        /
'
        printf '      /
'
        printf '    /
'
        printf '   S A M B U C A
'
        printf '   the open-weights siege engine
'
        printf '  --------------------------------------------------------
'
        printf '
'
        printf '  There are %s steps. Most need nothing from you.\n' "$SB_STAGE_TOTAL"
        printf '  The whole thing usually takes 30 to 90 minutes, and longer\n'
        printf '  on a slow connection because it downloads an AI model.\n\n'
        printf '  You can walk away. It keeps going if you close this screen.\n'
        printf '  You can also watch from any device on your network once the\n'
        printf '  services start, at:   https://%s/setup\n\n' "${SAMBUCA_DOMAIN:-sambuca.local}"
        printf '  Do NOT power the machine off while this runs.\n\n'
    } >&2

    ingest_payload

    # Profile the hardware before any phase that depends on the tier. Phase
    # 30 re-runs it once the GPU driver exists and VRAM becomes readable.
    STAGE_NUM=1
    sb_stage 1 "Checking your hardware" \
        "Measuring your processor, memory and graphics card." \
        "a few seconds" \
        "Nothing." \
        "Preparing the system."
    if ! "${_SB_SELF_DIR}/hardware-detect.sh" ${SB_QUIET:+--quiet}; then
        warn "hardware profiling failed — continuing with conservative defaults"
        printf 'SAMBUCA_TIER=4\nSAMBUCA_TIER_NAME=low-resource\nSAMBUCA_GPU_PROFILE=cpu\n' \
            | sb_atomic_write "${SB_ETC}/profile.env" 0644
    fi

    # STOP HERE IF THE MACHINE IS BELOW THE FLOOR.
    #
    # hardware-detect.sh sets SAMBUCA_TIER_UNSUPPORTED and prints exactly which
    # things will not fit — and, until now, NOTHING READ IT. The warning
    # scrolled past and the installer carried on to fetch Docker, the whole
    # stack and a language model onto a machine that cannot run them. A guard
    # that never guards is worse than no guard: it produces a wall of confident
    # output and then a box that thrashes, with the explanation thousands of
    # lines back.
    #
    # Refusing is the honest outcome, and it must be REFUSAL, not a slower
    # install. The override exists because someone testing the engine on
    # deliberately small hardware is a real case — but they have to say so.
    if [[ -r "${SB_ETC}/profile.env" ]]; then
        # shellcheck source=/dev/null
        local _unsupported
        _unsupported="$(grep -E '^SAMBUCA_TIER_UNSUPPORTED=' "${SB_ETC}/profile.env" 2>/dev/null | cut -d= -f2)"
        if [[ ${_unsupported:-0} == 1 && ${SAMBUCA_IGNORE_FLOOR:-0} != 1 ]]; then
            err "This machine is below the minimum sambuca needs."
            err ""
            err "The reasons are in the lines above: the file server, the photo"
            err "library and the smallest chat model each need more memory than"
            err "this machine has in total."
            err ""
            err "Nothing has been installed and nothing has been changed."
            err ""
            err "A second-hand office desktop with 8 GB is enough, and costs"
            err "very little. To install anyway — for engine testing on small"
            err "hardware, knowing the services will not come up:"
            err "    SAMBUCA_IGNORE_FLOOR=1 sambuca-first-boot"
            return 1
        fi
    fi

    local started_at_phase=0
    [[ -z $FROM ]] && started_at_phase=1

    local f name rc=0
    # THE BEACON GOES UP BEFORE THE PHASES AND COMES DOWN AFTER THEM, WHATEVER
    # HAPPENS. Its entire purpose is the stretch where nothing else can be
    # watched, so it must outlive a phase FAILING — an owner staring at a
    # stalled install is exactly who needs to see "phase 20 failed" rather than
    # a machine that has gone quiet. Hence the stop in both exits below rather
    # than only on success.
    sb_beacon_start "${SAMBUCA_BEACON_PAIRING_KEY:-}"

    for f in "$PHASE_DIR"/[0-9][0-9]-*.sh; do
        [[ -e $f ]] || continue
        name="$(basename -- "$f" .sh)"
        if ((started_at_phase == 0)); then
            if [[ $name == "$FROM" || $name == "$FROM"* ]]; then started_at_phase=1; else continue; fi
        fi
        run_phase "$f" || { rc=1; break; }
    done

    if ((rc != 0)); then
        # Leave it up for a short grace period so the failure is READ, not just
        # written. Then kill it: a listener that outlives provisioning is a
        # service nobody remembers is running.
        err "provisioning INCOMPLETE"
        sleep 30
        sb_beacon_stop
        return 1
    fi

    # Caddy is up and serving /setup by now, so the scaffolding comes down.
    sb_beacon_stop

    ok "provisioning complete"
    [[ -r "${SB_LIB}/completion-report.txt" ]] && cat -- "${SB_LIB}/completion-report.txt"
    return 0
}

main "$@"
