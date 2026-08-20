#!/bin/sh
#
# sambuca :: engine/autoinstall/disk-select.sh
#
# Resolve WHICH disk to install onto, and refuse rather than guess.
#
# Runs from preseed/early_command in the busybox environment, before the
# partitioner. Writes the answer into debconf so preseed.cfg does not have to
# carry a hardcoded /dev/sda — the single most destructive line in the entire
# unattended-install genre.
#
# Selection rules, in order:
#   1. provision.json `target_disk` — an explicit by-id path from the flasher.
#   2. provision.json `target_disk_hint` — "smallest-ssd" / "largest" / "nvme".
#   3. Exactly one eligible disk present  -> use it.
#   4. Anything else                      -> REFUSE and drop to a shell.
#
# Rule 4 is the important one. "Pick the first disk" is how an installer eats
# somebody's photo archive.

set -u

PAYLOAD=/cdrom/sambuca/provision.json
OUT=/tmp/sambuca-target-disk
CONSOLE=/dev/console
[ -w "$CONSOLE" ] || CONSOLE=/dev/tty

say() { printf '%s\n' "$*" >"$CONSOLE" 2>&1; }

json_get() {
    # Dependency-free extraction of a flat string field. jq is not present here.
    [ -r "$PAYLOAD" ] || return 1
    sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$PAYLOAD" | head -n1
}

# The USB we booted from must never be a candidate.
boot_dev="$(readlink -f /dev/disk/by-label/SAMBUCA 2>/dev/null || true)"
boot_disk=""
[ -n "$boot_dev" ] && boot_disk="/dev/$(lsblk -no PKNAME "$boot_dev" 2>/dev/null | head -n1)"

eligible=""
count=0
for d in /sys/block/*; do
    name="$(basename "$d")"
    case "$name" in
        loop*|ram*|sr*|fd*|dm-*|md*|zram*) continue ;;
    esac
    # Removable media (the installer USB, an attached backup drive) is excluded.
    [ "$(cat "$d/removable" 2>/dev/null || echo 1)" = "1" ] && continue
    dev="/dev/$name"
    [ "$dev" = "$boot_disk" ] && continue
    # Anything smaller than 16 GiB is not a system disk.
    sectors="$(cat "$d/size" 2>/dev/null || echo 0)"
    [ "$sectors" -lt 33554432 ] && continue
    eligible="$eligible $dev"
    count=$((count + 1))
done

target=""

# --- rule 1: explicit ---
explicit="$(json_get target_disk || true)"
if [ -n "${explicit:-}" ]; then
    real="$(readlink -f "$explicit" 2>/dev/null || echo "$explicit")"
    if [ -b "$real" ]; then
        target="$real"
        say "  disk-select: using explicit target ${explicit} -> ${real}"
    else
        say "  disk-select: explicit target ${explicit} DOES NOT EXIST on this machine."
        say "               Refusing to fall back to a guess."
        printf 'UNRESOLVED\n' >"$OUT"
        exit 1
    fi
fi

# --- rule 2: hint ---
if [ -z "$target" ]; then
    hint="$(json_get target_disk_hint || true)"
    case "${hint:-}" in
        nvme)
            for d in $eligible; do case "$d" in */nvme*) target="$d"; break ;; esac; done
            ;;
        largest)
            best=0
            for d in $eligible; do
                s="$(cat "/sys/block/$(basename "$d")/size" 2>/dev/null || echo 0)"
                [ "$s" -gt "$best" ] && { best="$s"; target="$d"; }
            done
            ;;
        smallest-ssd)
            best=0
            for d in $eligible; do
                n="$(basename "$d")"
                [ "$(cat "/sys/block/$n/queue/rotational" 2>/dev/null || echo 1)" = "0" ] || continue
                s="$(cat "/sys/block/$n/size" 2>/dev/null || echo 0)"
                if [ "$best" = "0" ] || [ "$s" -lt "$best" ]; then best="$s"; target="$d"; fi
            done
            ;;
    esac
    [ -n "$target" ] && say "  disk-select: hint '${hint}' resolved to ${target}"
fi

# --- rule 3: exactly one ---
if [ -z "$target" ] && [ "$count" = "1" ]; then
    target="$(printf '%s' "$eligible" | tr -d ' ')"
    say "  disk-select: exactly one eligible disk — ${target}"
fi

# --- rule 4: refuse ---
if [ -z "$target" ]; then
    say ''
    say '==============================================================='
    say '  DISK SELECTION FAILED — installation will NOT proceed.'
    say '==============================================================='
    say ''
    if [ "$count" = "0" ]; then
        say '  No eligible internal disk was found (>= 16 GiB, non-removable).'
    else
        say "  ${count} eligible disks were found and no rule chose between them:"
        for d in $eligible; do
            say "    $d  $(lsblk -dno SIZE,MODEL "$d" 2>/dev/null | tr -s ' ')"
        done
        say ''
        say '  Set "target_disk" in the flasher to a /dev/disk/by-id/... path,'
        say '  or re-flash with a target_disk_hint. This installer will not'
        say '  choose which of your disks to erase.'
    fi
    say ''
    printf 'UNRESOLVED\n' >"$OUT"
    exit 1
fi

printf '%s\n' "$target" >"$OUT"

# Hand the answer to the partitioner and the bootloader.
debconf-set partman-auto/disk "$target"        2>/dev/null || true
debconf-set grub-installer/bootdev "$target"   2>/dev/null || true
debconf-set partman-auto/select_disk "$target" 2>/dev/null || true

say "  disk-select: TARGET = ${target}"
exit 0
