#!/usr/bin/env bash
#
# sambuca :: engine/maintenance/snapraid-sync.sh
#
# Weekly parity sync + rolling scrub.
#
# THE DELETION THRESHOLD is the important part. SnapRAID parity reflects the
# last sync. If a disk fails and you sync AFTER the failure, you overwrite the
# parity that could have recovered it. Equally, a ransomware event or a bad
# rsync that deletes 40,000 files is faithfully synced into parity and the
# recovery window closes.
#
# So: an abnormal number of deletions ABORTS the sync and alerts, rather than
# dutifully destroying the only thing that could undo the damage.
#
set -uo pipefail

SB_TAG="snapraid"
_SB_SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=engine/lib/common.sh
source "${_SB_SELF_DIR}/../lib/common.sh"
sb_trap_err

sb_require_root
sb_single_instance "snapraid" 60

if [[ ! -r /etc/snapraid.conf ]]; then
    log "no /etc/snapraid.conf — this appliance has no parity configured. Nothing to do."
    exit 0
fi
sb_require snapraid

: "${SNAPRAID_DELETE_THRESHOLD:=500}"
: "${SNAPRAID_UPDATE_THRESHOLD:=2000}"
: "${SNAPRAID_SCRUB_PERCENT:=8}"
: "${SNAPRAID_SCRUB_OLDER_THAN:=10}"

# ---------------------------------------------------------------------------
# 1. What changed since the last sync?
# ---------------------------------------------------------------------------
log "computing the difference against current parity"
set +e
diff_out="$(snapraid diff 2>&1)"
diff_rc=$?
set -e

# snapraid diff: 0 = no differences, 2 = differences found. Anything else is an
# error, and treating 2 as failure would abort every normal run.
if ((diff_rc != 0 && diff_rc != 2)); then
    err "snapraid diff failed (exit ${diff_rc})"
    printf '%s\n' "$diff_out" | tail -n 20 | while read -r l; do err "  ${l}"; done
    die "cannot determine what changed — refusing to sync blind"
fi

get_count() { printf '%s\n' "$diff_out" | awk -v k="$1" '$2==k{print $1; found=1} END{if(!found) print 0}'; }
removed="$(get_count removed)"
updated="$(get_count updated)"
added="$(get_count added)"
moved="$(get_count moved)"

log "added=${added} removed=${removed} updated=${updated} moved=${moved}"

if ((diff_rc == 0)); then
    ok "no changes since the last sync"
    exit 0
fi

# ---------------------------------------------------------------------------
# 2. The guard.
# ---------------------------------------------------------------------------
abort=""
((removed > SNAPRAID_DELETE_THRESHOLD)) && \
    abort="${abort}  ${removed} files DELETED (threshold ${SNAPRAID_DELETE_THRESHOLD})"$'\n'
((updated > SNAPRAID_UPDATE_THRESHOLD)) && \
    abort="${abort}  ${updated} files MODIFIED (threshold ${SNAPRAID_UPDATE_THRESHOLD})"$'\n'

if [[ -n $abort ]]; then
    err "═══════════════════════════════════════════════════════════════"
    err " SYNC ABORTED — the change volume is abnormal:"
    printf '%s' "$abort" | while read -r l; do [[ -n $l ]] && err "$l"; done
    err ""
    err " Parity still reflects the state BEFORE these changes, so the old"
    err " files remain recoverable. Syncing now would discard that."
    err ""
    err " If the changes are legitimate (a large import, a deliberate purge):"
    err "   snapraid diff | less        # review them"
    err "   snapraid sync               # then sync by hand"
    err "═══════════════════════════════════════════════════════════════"
    # THE LOUDEST CASE. Parity still reflects the state BEFORE these deletions,
    # so the old files remain recoverable — but only until somebody syncs by
    # hand. A stale JSON file nobody opens is not how that decision should
    # reach them.
    sb_health_set snapraid fail \
        "parity sync ABORTED — ${removed} deletions looked abnormal; parity still reflects the state BEFORE them"
    printf '{"last_run":"%s","status":"aborted","removed":%s,"updated":%s}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$removed" "$updated" \
        | sb_atomic_write "${SB_LIB}/snapraid-state.json" 0644
    exit 1
fi

# ---------------------------------------------------------------------------
# 3. Sync
# ---------------------------------------------------------------------------
log "syncing parity"
if ! snapraid sync >"${SB_LOG_DIR}/snapraid-sync.out" 2>&1; then
    sb_health_set snapraid fail \
        "parity sync FAILED — parity is stale, and a disk failure now would not be recoverable"
    err "snapraid sync FAILED"
    tail -n 20 "${SB_LOG_DIR}/snapraid-sync.out" | while read -r l; do err "  ${l}"; done
    die "parity is stale — investigate before the next disk failure, not after"
fi
ok "parity synced"
# Cleared HERE rather than at the end of the script: parity being current is
# what this job exists to guarantee. The scrub below is a different concern —
# it looks for corruption in old blocks — and sets its own state if it finds
# any. Clearing at the end would let a scrub warning wipe itself.
sb_health_set snapraid ok

# ---------------------------------------------------------------------------
# 4. Rolling scrub — verify a slice of old blocks against parity each week, so
#    silent corruption is found while parity can still repair it.
# ---------------------------------------------------------------------------
log "scrubbing ${SNAPRAID_SCRUB_PERCENT}% of blocks older than ${SNAPRAID_SCRUB_OLDER_THAN} days"
set +e
snapraid scrub -p "$SNAPRAID_SCRUB_PERCENT" -o "$SNAPRAID_SCRUB_OLDER_THAN" \
    >"${SB_LOG_DIR}/snapraid-scrub.out" 2>&1
scrub_rc=$?
set -e

errors="$(grep -cE '^[0-9]+ errors' "${SB_LOG_DIR}/snapraid-scrub.out" 2>/dev/null || echo 0)"
if ((scrub_rc != 0)); then
    # A DIFFERENT KIND OF TROUBLE from a failed sync: the parity job worked,
    # and it found bad blocks. Silent corruption is exactly the thing nobody
    # notices until a restore fails, so it must outlive this log line.
    sb_health_set snapraid warn \
        "scrub found problems (exit ${scrub_rc}) — possible silent corruption; see ${SB_LOG_DIR}/snapraid-scrub.out"
    warn "scrub reported problems (exit ${scrub_rc}) — silent corruption may be present"
    grep -iE 'error|corrupt|mismatch' "${SB_LOG_DIR}/snapraid-scrub.out" | head -n 10 \
        | while read -r l; do warn "  ${l}"; done
    warn "  repair with: snapraid -e fix"
else
    ok "scrub clean"
fi

{
    printf '{\n'
    printf '  "last_run": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '  "status": "%s",\n' "$([[ $scrub_rc == 0 ]] && echo ok || echo degraded)"
    printf '  "added": %s, "removed": %s, "updated": %s, "moved": %s,\n' "$added" "$removed" "$updated" "$moved"
    printf '  "scrub_exit": %d, "scrub_error_lines": %s\n' "$scrub_rc" "${errors:-0}"
    printf '}\n'
} | sb_atomic_write "${SB_LIB}/snapraid-state.json" 0644

exit 0
