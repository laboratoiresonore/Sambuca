#!/usr/bin/env bash
#
# sambuca :: engine/autoinstall/luks-tpm-enroll.sh
#
# Optional: seal the LUKS key to the TPM so the appliance boots unattended after
# a power cut without anyone typing a passphrase at a headless machine.
#
# ══════════════════════════════════════════════════════════════════════════
# THE TRADE, STATED HONESTLY
#
# TPM auto-unlock means the disk decrypts itself whenever it boots in this
# machine. It protects against a stolen DRIVE. It does NOT protect against a
# stolen MACHINE — the thief powers it on and the disk unlocks.
#
# For an appliance holding client documents, that may be the wrong trade. It is
# therefore OPT-IN, and the passphrase slot is NEVER removed: TPM unlock is an
# addition, never a replacement. A firmware update that changes PCR values is
# routine, and a machine whose only key was sealed to the old values is a brick.
# ══════════════════════════════════════════════════════════════════════════
#
set -uo pipefail

SB_TAG="tpm-enroll"
_SB_SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=engine/lib/common.sh
source "${_SB_SELF_DIR}/../lib/common.sh"
sb_trap_err
sb_require_root

# PCR 7 = secure-boot state. PCR 7 alone survives kernel updates, which PCR 4/8
# do not — binding to those means every apt upgrade bricks unattended boot.
PCRS="${SAMBUCA_TPM_PCRS:-7}"

[[ -c /dev/tpmrm0 || -c /dev/tpm0 ]] || die "no TPM 2.0 device present"
sb_require systemd-cryptenroll cryptsetup

luks_dev="$(lsblk -rno NAME,FSTYPE | awk '$2=="crypto_LUKS"{print "/dev/"$1; exit}')"
[[ -n $luks_dev ]] || die "no LUKS volume found"
log "LUKS volume: ${luks_dev}"

# Refuse to proceed unless a passphrase slot will survive. Enrolling the TPM as
# the only key is the mistake this check exists to make impossible.
slots="$(cryptsetup luksDump "$luks_dev" | grep -cE '^\s+[0-9]+: luks2' || echo 0)"
((slots >= 1)) || die "cannot read LUKS key slots — refusing to modify ${luks_dev}"

if cryptsetup luksDump "$luks_dev" | grep -q "systemd-tpm2"; then
    log "TPM slot already enrolled — nothing to do"
    exit 0
fi

warn "───────────────────────────────────────────────────────────────"
warn " Enrolling the TPM means this machine unlocks its own disk."
warn " Physical theft of the MACHINE then yields the data."
warn " Your recovery passphrase slot is kept and remains valid."
warn "───────────────────────────────────────────────────────────────"

log "enrolling TPM against PCR ${PCRS}"
if PASSWORD="$(cat "${SB_ETC}/secrets/luks_passphrase" 2>/dev/null || true)" \
   systemd-cryptenroll "$luks_dev" --tpm2-device=auto --tpm2-pcrs="$PCRS"; then
    ok "TPM slot enrolled"
else
    die "enrolment failed — the existing passphrase slot is untouched"
fi

# crypttab must reference the TPM or the initramfs will still prompt.
if ! grep -q 'tpm2-device=auto' /etc/crypttab 2>/dev/null; then
    sed -i 's#\(^[^#].*luks.*\)$#\1,tpm2-device=auto#' /etc/crypttab
    sb_run update-initramfs -u -k all || die "initramfs rebuild failed — reverting is now manual"
fi

ok "TPM auto-unlock armed. VERIFY IT BY REBOOTING NOW, while you still have"
ok "the recovery document in front of you."
