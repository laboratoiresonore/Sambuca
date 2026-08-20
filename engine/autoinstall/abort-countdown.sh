#!/bin/sh
#
# sambuca :: engine/autoinstall/abort-countdown.sh
#
# The fail-safe. Runs from preseed/early_command, BEFORE the partitioner has
# touched anything, on the installer's own console.
#
# POSIX sh, not bash: the Debian installer environment is busybox ash. No
# arrays, no [[ ]], no `read -t` guarantees — hence the manual tick loop.
#
# It prints the target disk AND WHAT IS ON IT, then counts down. Any keypress
# aborts to a shell. Printing the disk's existing contents is the whole point:
# "/dev/sda, 500GB" tells you nothing, "/dev/sda containing an NTFS volume
# labelled Backups-2019" tells you to press a key.

set -u

COUNTDOWN="${SAMBUCA_ABORT_SECONDS:-30}"
CONSOLE=/dev/console
[ -w "$CONSOLE" ] || CONSOLE=/dev/tty

TARGET="$(debconf-get partman-auto/disk 2>/dev/null || true)"
[ -z "$TARGET" ] && TARGET="$(cat /tmp/sambuca-target-disk 2>/dev/null || echo 'UNRESOLVED')"

{
    printf '\n'
    printf '===============================================================\n'
    printf '   SAMBUCA — UNATTENDED INSTALL\n'
    printf '===============================================================\n\n'
    printf '  TARGET DISK:  %s\n\n' "$TARGET"

    if [ "$TARGET" = "UNRESOLVED" ]; then
        printf '  !! NO TARGET DISK WAS RESOLVED.\n'
        printf '  !! The installer cannot safely continue and will drop to a shell.\n\n'
    else
        printf '  Model / size:\n'
        lsblk -dno MODEL,SIZE "$TARGET" 2>/dev/null | sed 's/^/    /' || printf '    (unavailable)\n'
        printf '\n  EXISTING CONTENTS OF THIS DISK:\n'
        if lsblk -no NAME,FSTYPE,LABEL,SIZE "$TARGET" 2>/dev/null | grep -q '[a-z]'; then
            lsblk -no NAME,FSTYPE,LABEL,SIZE "$TARGET" 2>/dev/null | sed 's/^/    /'
        else
            printf '    (no partitions detected — disk appears blank)\n'
        fi
    fi

    printf '\n---------------------------------------------------------------\n'
    printf '  EVERYTHING ON THIS DISK WILL BE ERASED AND ENCRYPTED.\n'
    printf '  Other disks in this machine are NOT touched by the installer.\n'
    printf '---------------------------------------------------------------\n\n'
    printf '  Press ANY KEY within %s seconds to ABORT.\n\n' "$COUNTDOWN"
} >"$CONSOLE" 2>&1

i="$COUNTDOWN"
while [ "$i" -gt 0 ]; do
    printf '\r  Continuing in %2s seconds...  ' "$i" >"$CONSOLE" 2>&1

    # busybox `read -t 1` is unreliable across builds; poll stdin non-blockingly
    # via a 1-second timed read on the console instead.
    if read -t 1 -r _key <"$CONSOLE" 2>/dev/null; then
        {
            printf '\n\n'
            printf '===============================================================\n'
            printf '  ABORTED BY OPERATOR — nothing has been written to any disk.\n'
            printf '===============================================================\n\n'
            printf '  Dropping to an installer shell.\n'
            printf '    - inspect disks:   lsblk -f\n'
            printf '    - resume install:  exit\n'
            printf '    - power off:       poweroff\n\n'
        } >"$CONSOLE" 2>&1
        # A real shell on the console; leaving the installer to proceed silently
        # after an abort would defeat the entire purpose of this script.
        setsid sh -c 'exec sh <'"$CONSOLE"' >'"$CONSOLE"' 2>&1' || sh
        exit 0
    fi
    i=$((i - 1))
done

printf '\r  Proceeding with installation.                    \n\n' >"$CONSOLE" 2>&1

if [ "$TARGET" = "UNRESOLVED" ]; then
    printf '  Cannot proceed without a target disk. Dropping to a shell.\n' >"$CONSOLE" 2>&1
    setsid sh -c 'exec sh <'"$CONSOLE"' >'"$CONSOLE"' 2>&1' || sh
    exit 1
fi

exit 0
