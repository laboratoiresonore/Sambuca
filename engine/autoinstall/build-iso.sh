#!/usr/bin/env bash
#
# sambuca :: engine/autoinstall/build-iso.sh
#
# Rebuild a Debian 12 netinst ISO with the sambuca payload embedded, so the
# installer finds preseed.cfg without needing a second partition or a network
# fetch. The flasher can then write a single image and inject only the
# machine-specific provision.json afterwards.
#
# Requires: xorriso, isolinux (for BIOS), rsync. Linux only — this is a release
# engineering step, not something an end user runs.
#
set -uo pipefail

SB_TAG="build-iso"
_SB_SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=engine/lib/common.sh
source "${_SB_SELF_DIR}/../lib/common.sh"
sb_trap_err

SB_LOG_FILE="${SB_LOG_FILE:-/tmp/sambuca-build-iso.log}"
REPO_ROOT="$(cd -- "${_SB_SELF_DIR}/../.." && pwd)"

SOURCE_ISO=""
OUTPUT_DIR="${REPO_ROOT}/build"

usage() {
    cat <<'USAGE'
Rebuild a Debian netinst ISO with the sambuca payload embedded.

Usage: build-iso.sh --iso <debian-netinst.iso> [--output DIR]

  --iso PATH      Source Debian netinst ISO (required)
  --output DIR    Where to write the result (default: ./build)
  -h, --help      This text
USAGE
}

while (($# > 0)); do
    case "$1" in
        --iso)    SOURCE_ISO="${2:-}"; shift ;;
        --output) OUTPUT_DIR="${2:-}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; SB_EXIT_CODE=2 die "unknown argument: $1" ;;
    esac
    shift
done

[[ -n $SOURCE_ISO ]] || { usage >&2; SB_EXIT_CODE=2 die "--iso is required"; }
[[ -r $SOURCE_ISO ]] || die "cannot read ${SOURCE_ISO}"
sb_require xorriso rsync

# Verify we were handed a Debian installer and not, say, a live image — the
# preseed hooks below silently do nothing on a live ISO and the failure only
# surfaces when a machine boots to a manual installer nobody is standing at.
if ! xorriso -indev "$SOURCE_ISO" -find /install.amd -type d >/dev/null 2>&1 \
   && ! xorriso -indev "$SOURCE_ISO" -find /install -type d >/dev/null 2>&1; then
    die "${SOURCE_ISO} does not look like a Debian installer ISO (no /install directory)"
fi

WORK="$(mktemp -d)"
# shellcheck disable=SC2064
trap "rm -rf -- '${WORK}'" EXIT

log "extracting ${SOURCE_ISO}"
mkdir -p "${WORK}/iso"
xorriso -osirrox on -indev "$SOURCE_ISO" -extract / "${WORK}/iso" >/dev/null 2>&1 \
    || die "extraction failed"
chmod -R u+w "${WORK}/iso"

# --- payload ---------------------------------------------------------------
log "embedding the sambuca payload"
mkdir -p "${WORK}/iso/sambuca"
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' \
    "${REPO_ROOT}/engine"  "${WORK}/iso/sambuca/"
rsync -a --exclude '.env' \
    "${REPO_ROOT}/compose" "${WORK}/iso/sambuca/"
cp "${_SB_SELF_DIR}/preseed.cfg"        "${WORK}/iso/sambuca/"
cp "${_SB_SELF_DIR}/abort-countdown.sh" "${WORK}/iso/sambuca/"
cp "${_SB_SELF_DIR}/disk-select.sh"     "${WORK}/iso/sambuca/"
cp "${_SB_SELF_DIR}/late-command.sh"    "${WORK}/iso/sambuca/"
chmod +x "${WORK}/iso/sambuca/"*.sh

# A git bundle so the appliance's first gitops sync fast-forwards from a real
# ancestor instead of hitting unrelated-histories.
if git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "$REPO_ROOT" bundle create "${WORK}/iso/sambuca/sambuca.bundle" HEAD \
        >/dev/null 2>&1 && log "git bundle embedded" \
        || warn "could not create a git bundle — the appliance will clone on first sync"
fi

# --- boot menu -------------------------------------------------------------
# Point both the BIOS and UEFI menus at the preseed. Editing only one is the
# classic bug: the stick works on the test machine and drops to a manual
# installer on anything that boots the other way.
log "wiring the boot menus to the preseed"
KERNEL_ARGS="auto=true priority=critical file=/cdrom/sambuca/preseed.cfg quiet ---"

for cfg in "${WORK}/iso/isolinux/txt.cfg" "${WORK}/iso/isolinux/gtk.cfg"; do
    [[ -f $cfg ]] || continue
    sed -i "s|append |append ${KERNEL_ARGS} |" "$cfg"
    log "  patched $(basename "$cfg") (BIOS)"
done

if [[ -f "${WORK}/iso/boot/grub/grub.cfg" ]]; then
    sed -i "s|--- quiet|${KERNEL_ARGS}|g" "${WORK}/iso/boot/grub/grub.cfg"
    # Ten seconds so a human can still pick "rescue mode" without fighting it.
    sed -i "s|^set timeout=.*|set timeout=10|" "${WORK}/iso/boot/grub/grub.cfg"
    log "  patched grub.cfg (UEFI)"
fi

# Debian verifies md5sum.txt during installation; a stale one fails the install
# with an error that says nothing about the files we added.
if [[ -f "${WORK}/iso/md5sum.txt" ]]; then
    ( cd "${WORK}/iso" && find . -type f ! -name md5sum.txt -print0 \
        | xargs -0 md5sum >md5sum.txt.new && mv md5sum.txt.new md5sum.txt )
    log "  md5sum.txt regenerated"
fi

# --- repack ----------------------------------------------------------------
mkdir -p "$OUTPUT_DIR"
version="$(cat "${REPO_ROOT}/VERSION" 2>/dev/null || echo 0.1.0)"
OUT="${OUTPUT_DIR}/sambuca-installer-${version}-amd64.iso"

log "repacking hybrid BIOS+UEFI ISO"
xorriso -as mkisofs \
    -r -V "SAMBUCA" \
    -o "$OUT" \
    -J -joliet-long \
    -isohybrid-mbr "${WORK}/iso/isolinux/isohdpfx.bin" \
    -c isolinux/boot.cat \
    -b isolinux/isolinux.bin \
      -no-emul-boot -boot-load-size 4 -boot-info-table \
    -eltorito-alt-boot \
    -e boot/grub/efi.img \
      -no-emul-boot -isohybrid-gpt-basdat \
    "${WORK}/iso" >/dev/null 2>&1 \
    || die "xorriso repack failed — see ${SB_LOG_FILE}"

sha256sum "$OUT" >"${OUT}.sha256"

ok "built ${OUT}"
ok "sha256 $(cut -d' ' -f1 <"${OUT}.sha256")"
log ""
log "Write it with:  sudo sambuca-flasher write --iso ${OUT} --config <config.json>"
