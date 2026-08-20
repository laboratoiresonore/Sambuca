# shellcheck shell=bash
#
# sambuca :: lifecycle for the install beacon.
#
# The setup page is served by Caddy, and Caddy starts in 60-stack — the last
# phase. Everything before it (disk, base system, Docker, GPU, storage,
# network) happens with nothing an owner can watch, and that is the window
# where somebody decides it has hung and pulls the power mid-partition.
#
# So the beacon runs FIRST and dies when Caddy can take over. It is scaffolding
# with a scheduled end, not a service.
#
# STARTED WITHOUT systemd ON PURPOSE. A unit file would have to be installed,
# enabled, and then reliably removed, and this must run before 10-system has
# configured anything. A backgrounded process with a pidfile is honest about
# what it is: something temporary, torn down by the script that started it.

SB_BEACON_PIDFILE="${SB_BEACON_PIDFILE:-${SB_LIB:-/var/lib/sambuca}/beacon.pid}"
SB_BEACON_KEYFILE="${SB_BEACON_KEYFILE:-${SB_LIB:-/var/lib/sambuca}/beacon.key}"
SB_BEACON_SCRIPT="${SB_BEACON_SCRIPT:-${SB_ENGINE_DIR:-/opt/sambuca/engine}/beacon/sambuca-beacon.py}"
SB_BEACON_LOG="${SB_BEACON_LOG:-${SB_LIB:-/var/lib/sambuca}/beacon.log}"

sb_beacon_start() {
    local key="${1:-}"

    # NO KEY, NO BEACON, and this is not an error worth failing the install
    # over. An unauthenticated one would announce to every device on the
    # network — a guest phone included — that this machine is mid-install and
    # therefore in its least-defended state. Silence is the correct fallback.
    if [[ -z $key ]]; then
        log "beacon: no pairing key in provision.json — not starting one"
        return 0
    fi

    if [[ ! -r $SB_BEACON_SCRIPT ]]; then
        warn "beacon: ${SB_BEACON_SCRIPT} not found — install progress will not"
        warn "        be visible until the setup page comes up in phase 60"
        return 0
    fi

    # python3, not python3-minimal: http.server lives in the full stdlib. The
    # package list installs it for exactly this reason (see 10-system.sh).
    if ! command -v python3 >/dev/null 2>&1; then
        warn "beacon: python3 not available yet — skipping"
        return 0
    fi

    install -d -m 0755 "$(dirname -- "$SB_BEACON_KEYFILE")" 2>/dev/null || return 0

    # THE KEY NEVER GOES THROUGH argv OR THE ENVIRONMENT. ps is world-readable
    # and /proc/<pid>/environ is readable by anything that can see the process;
    # the beacon reads it from this file, which only root can open.
    ( umask 077; printf '%s' "$key" >"$SB_BEACON_KEYFILE" ) || {
        warn "beacon: could not write its key file — skipping"
        return 0
    }
    chmod 0600 -- "$SB_BEACON_KEYFILE" 2>/dev/null || true

    sb_beacon_stop   # DETECT AND KILL A PRIOR INSTANCE before binding a port.

    SAMBUCA_PROGRESS="${SB_PROGRESS_FILE}" \
    SAMBUCA_BEACON_KEY="${SB_BEACON_KEYFILE}" \
        setsid python3 "$SB_BEACON_SCRIPT" >>"$SB_BEACON_LOG" 2>&1 &
    local pid=$!
    printf '%s' "$pid" >"$SB_BEACON_PIDFILE" 2>/dev/null || true

    # A process that exits immediately (port taken, key unreadable) must not be
    # reported as running. Give it a moment, then check it is still there.
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        log "beacon: watching on the local network while this installs"
    else
        warn "beacon: failed to start — see ${SB_BEACON_LOG}"
        rm -f -- "$SB_BEACON_PIDFILE"
    fi
    return 0
}

sb_beacon_stop() {
    # IT MUST DIE, and this is the only thing that kills it. A provisioning-time
    # listener that survives provisioning is an unauthenticated-by-obscurity
    # service nobody remembers is running.
    local pid=""
    [[ -r $SB_BEACON_PIDFILE ]] && pid="$(cat -- "$SB_BEACON_PIDFILE" 2>/dev/null)"

    if [[ -n $pid ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        # `_` because this is a countdown, not an index. Naming it `i`
        # made shellcheck rightly ask what it was for.
        for _ in 1 2 3 4 5; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f -- "$SB_BEACON_PIDFILE" 2>/dev/null || true

    # The key goes with it. Leaving it on disk would let a restarted beacon
    # answer with credentials nobody is tracking any more.
    if [[ -e $SB_BEACON_KEYFILE ]]; then
        if command -v shred >/dev/null 2>&1; then
            shred -u -- "$SB_BEACON_KEYFILE" 2>/dev/null || rm -f -- "$SB_BEACON_KEYFILE"
        else
            rm -f -- "$SB_BEACON_KEYFILE"
        fi
    fi
    return 0
}
