#!/usr/bin/env bash
#
# sambuca :: engine/maintenance/health.sh — what needs looking at, if anything.
#
# THE PROBLEM THIS SOLVES. The maintenance jobs already know when they have
# failed: backup.sh handles restic's exit 3 explicitly and verifies its snapshot
# by reading it back, because a wrapper once reported success while capturing 17
# of 966 files. It says so with err(), which goes to the journal.
#
# NOBODY READS THE JOURNAL ON AN APPLIANCE. A backup can stop working for months
# and look exactly like one that is working — which is the worst possible
# failure for a machine somebody trusted with their files.
#
# WHAT THIS IS, PRECISELY: state you cannot miss once you look. The login banner
# shows it, and this command shows it on demand. It is NOT push notification —
# it does not email, message or ring anybody, and saying otherwise would be the
# false assurance the whole thing exists to prevent.
#
# Exit codes are for scripts: 0 healthy, 1 warnings, 2 failures.

set -uo pipefail

_SB_SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=engine/lib/common.sh
source "${_SB_SELF_DIR}/../lib/common.sh" 2>/dev/null || {
    SB_HEALTH_DIR="${SB_HEALTH_DIR:-/var/lib/sambuca/health}"
}

BRIEF=0
[[ ${1:-} == "--brief" ]] && BRIEF=1

worst=0
found=0

if [[ -d ${SB_HEALTH_DIR} ]]; then
    for f in "${SB_HEALTH_DIR}"/*; do
        [[ -f $f ]] || continue
        found=1
        state="$(sed -n 1p -- "$f" 2>/dev/null)"
        when="$(sed -n 2p -- "$f" 2>/dev/null)"
        msg="$(sed -n 3p -- "$f" 2>/dev/null)"
        name="$(basename -- "$f")"

        case "$state" in
            fail) ((worst < 2)) && worst=2 ;;
            warn) ((worst < 1)) && worst=1 ;;
        esac

        if ((BRIEF)); then
            printf '  [%s] %s: %s\n' "${state^^}" "$name" "$msg"
        else
            printf '\n%s  %s\n' "${state^^}" "$name"
            printf '  since   %s\n' "$when"
            printf '  detail  %s\n' "$msg"
        fi
    done
fi

if ((found == 0)); then
    # SILENCE IN BRIEF MODE IS DELIBERATE. This runs at every login; a banner
    # that always says something is a banner people stop reading, and then the
    # one time it matters they will not read that either.
    ((BRIEF)) || printf 'Everything that reports its health is reporting it healthy.\n'
    exit 0
fi

if ((BRIEF)); then
    printf '  run `sambuca-health` for detail\n'
else
    printf '\nWhat to do\n'
    printf '  backup    sambuca-backup verify     (proves a snapshot restores)\n'
    printf '  logs      %s/\n' "${SB_LOG_DIR:-/var/log/sambuca}"
    printf '\nA line here clears itself when the job next succeeds.\n'
fi

exit "$worst"
