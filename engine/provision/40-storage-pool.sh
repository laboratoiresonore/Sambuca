# shellcheck shell=bash
# sambuca :: phase 40-storage-pool — MergerFS union + SnapRAID parity.
#
# WHY NOT RAID: hardware RAID and mdadm both demand matched disks and destroy
# every member's data on rebuild failure. MergerFS unions arbitrary mismatched
# drives while leaving each one an independently readable filesystem, and
# SnapRAID adds parity on top. Pull a disk out of a MergerFS pool and its files
# are still there, on a plain filesystem, readable in any machine.
#
# SAFETY: this phase NEVER formats a disk that already carries a filesystem or
# data. Disks are opt-in via provision.json; anything unexpected aborts the phase.

# shellcheck source=/dev/null
[[ -r "${SB_ETC}/provision.env" ]] && source "${SB_ETC}/provision.env"

: "${SAMBUCA_DATA_DISKS:=}"
: "${SAMBUCA_PARITY_DISKS:=}"
: "${SAMBUCA_DATA:=/srv/sambuca}"

export DEBIAN_FRONTEND=noninteractive

POOL_MNT="${SAMBUCA_POOL_MNT:-/mnt/pool}"
BRANCH_ROOT="${SAMBUCA_BRANCH_ROOT:-/mnt/disk}"

# --- single-disk fallback ---------------------------------------------------
# The overwhelming majority of installs have exactly one disk. That must work
# perfectly, with no pool, no parity, and no warnings that imply breakage.
if [[ -z ${SAMBUCA_DATA_DISKS// /} ]]; then
    log "no data disks declared — using a single-volume layout at ${SAMBUCA_DATA}"
    install -d -m 0755 "${SAMBUCA_DATA}"/{appdata,media,photos,files,backups}
    printf 'SAMBUCA_DATA=%s\nSAMBUCA_POOL=none\n' "$SAMBUCA_DATA" \
        | sb_atomic_write "${SB_ETC}/storage.env" 0644
    ok "single-volume storage ready at ${SAMBUCA_DATA}"
    return 0
fi

sb_retry 2 10 apt-get install -y -qq mergerfs snapraid \
    || die "mergerfs/snapraid installation failed"

# --- disk validation --------------------------------------------------------
# Refuse to touch anything that is mounted, holds an LVM/LUKS signature, or is
# the disk we booted from. An installer that eats the OS disk is unforgivable.
root_disk="$(lsblk -no PKNAME "$(findmnt -no SOURCE / 2>/dev/null || echo /dev/null)" 2>/dev/null | head -n1 || true)"

validate_disk() {
    local dev="$1"
    [[ -b $dev ]] || { err "not a block device: ${dev}"; return 1; }

    local base; base="$(basename -- "$dev")"
    if [[ -n $root_disk && $base == "$root_disk"* ]]; then
        err "refusing ${dev}: it is the root/boot disk"
        return 1
    fi
    if findmnt -S "$dev" >/dev/null 2>&1; then
        err "refusing ${dev}: currently mounted"
        return 1
    fi
    local sig; sig="$(blkid -o value -s TYPE -- "$dev" 2>/dev/null || true)"
    if [[ -n $sig && $sig != "ext4" && $sig != "xfs" ]]; then
        err "refusing ${dev}: carries a '${sig}' signature — wipe it deliberately first"
        return 1
    fi
    return 0
}

prepare_disk() {
    # Formats ONLY a blank device. An existing ext4/xfs member is adopted as-is.
    local dev="$1" label="$2" mnt="$3"
    local sig; sig="$(blkid -o value -s TYPE -- "$dev" 2>/dev/null || true)"

    if [[ -z $sig ]]; then
        log "formatting blank device ${dev} as ext4 (label=${label})"
        sb_run mkfs.ext4 -q -L "$label" -m 0 -- "$dev" || return 1
    else
        log "adopting existing ${sig} filesystem on ${dev} — not reformatting"
    fi

    install -d -m 0755 "$mnt"
    local uuid; uuid="$(blkid -o value -s UUID -- "$dev")"
    [[ -n $uuid ]] || { err "no UUID for ${dev} after preparation"; return 1; }

    # fstab is keyed by UUID: device names reorder across boots and a pool that
    # silently mounts the wrong branch corrupts parity.
    if ! grep -q "UUID=${uuid}" /etc/fstab; then
        printf 'UUID=%s  %s  auto  defaults,nofail,noatime  0  2\n' "$uuid" "$mnt" >>/etc/fstab
    fi
    mountpoint -q "$mnt" || sb_run mount "$mnt" || { err "could not mount ${dev} at ${mnt}"; return 1; }
    return 0
}

# --- build the pool ---------------------------------------------------------
IFS=',' read -ra DATA_DISKS   <<<"$SAMBUCA_DATA_DISKS"
IFS=',' read -ra PARITY_DISKS <<<"${SAMBUCA_PARITY_DISKS:-}"

branches=(); i=1
for dev in "${DATA_DISKS[@]}"; do
    dev="${dev// /}"; [[ -z $dev ]] && continue
    validate_disk "$dev" || die "data disk validation failed — nothing was modified"
    mnt="${BRANCH_ROOT}${i}"
    prepare_disk "$dev" "sambuca-d${i}" "$mnt" || die "could not prepare ${dev}"
    branches+=("$mnt")
    ((i++))
done
((${#branches[@]} > 0)) || die "no usable data disks after validation"

# mergerfs options, and why each one is here:
#   category.create=mfs  place new files on the branch with the most free space
#   moveonenospc=true    transparently relocate a write that hits a full branch
#   minfreespace=20G     stop choosing a branch before it is genuinely full
#   fsname=sambuca-pool  a readable name in df/mount output
#   cache.files=partial  correct mmap behaviour for sqlite-backed services
MERGER_OPTS="defaults,nonempty,allow_other,use_ino,cache.files=partial,moveonenospc=true,category.create=mfs,dropcacheonclose=true,minfreespace=20G,fsname=sambuca-pool,nofail,x-systemd.requires=${branches[0]}"
branch_spec="$(printf '%s:' "${branches[@]}")"; branch_spec="${branch_spec%:}"

install -d -m 0755 "$POOL_MNT"
if ! grep -q "[[:space:]]${POOL_MNT}[[:space:]]" /etc/fstab; then
    printf '%s  %s  fuse.mergerfs  %s  0  0\n' "$branch_spec" "$POOL_MNT" "$MERGER_OPTS" >>/etc/fstab
fi
sb_run systemctl daemon-reload
mountpoint -q "$POOL_MNT" || sb_run mount "$POOL_MNT" || die "mergerfs pool failed to mount"
ok "mergerfs pool mounted: ${#branches[@]} branch(es) at ${POOL_MNT}"

# --- parity -----------------------------------------------------------------
if ((${#PARITY_DISKS[@]} > 0)) && [[ -n ${PARITY_DISKS[0]// /} ]]; then
    p=1; parity_lines=""
    for dev in "${PARITY_DISKS[@]}"; do
        dev="${dev// /}"; [[ -z $dev ]] && continue
        validate_disk "$dev" || die "parity disk validation failed"
        mnt="/mnt/parity${p}"
        prepare_disk "$dev" "sambuca-p${p}" "$mnt" || die "could not prepare parity disk ${dev}"
        if ((p == 1)); then parity_lines+="parity ${mnt}/snapraid.parity"$'\n'
        else                parity_lines+="${p}-parity ${mnt}/snapraid.parity"$'\n'; fi
        ((p++))
    done

    {
        printf '%s\n' "# generated by sambuca phase 40 — regenerate, do not hand-edit"
        printf '%s' "$parity_lines"
        for b in "${branches[@]}"; do
            printf 'content %s/snapraid.content\n' "$b"
        done
        printf 'content %s/snapraid.content\n' "$SB_LIB"
        n=1
        for b in "${branches[@]}"; do
            printf 'data d%d %s\n' "$n" "$b"; ((n++))
        done
        cat <<'EXCL'

# Parity over live databases is worthless — they are captured by the backup
# daemon (engine/maintenance/backup.sh) with a proper dump instead.
exclude *.unrecoverable
exclude /tmp/
exclude /lost+found/
exclude .Trash-*/
exclude appdata/*/postgres/
exclude appdata/*/redis/
exclude *.tmp
EXCL
    } | sb_atomic_write /etc/snapraid.conf 0644
    ok "snapraid configured with $((p - 1)) parity disk(s)"
else
    log "no parity disks declared — the pool has no fault tolerance"
    log "  Add parity later: edit /etc/snapraid.conf, then 'snapraid sync'."
fi

# --- data layout ------------------------------------------------------------
SAMBUCA_DATA="$POOL_MNT/sambuca"
install -d -m 0755 "${SAMBUCA_DATA}"/{appdata,media,photos,files,backups}

{
    printf 'SAMBUCA_DATA=%s\n' "$SAMBUCA_DATA"
    printf 'SAMBUCA_POOL=%s\n' "$POOL_MNT"
    printf 'SAMBUCA_POOL_BRANCHES=%s\n' "$branch_spec"
} | sb_atomic_write "${SB_ETC}/storage.env" 0644

ok "storage pool ready — data root ${SAMBUCA_DATA}"
