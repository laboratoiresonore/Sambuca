#!/bin/sh
#
# sambuca :: engine/autoinstall/late-command.sh
#
# Runs inside the freshly installed system (in-target), immediately before the
# installer reboots. Its only job is to stage the engine and arm first-boot —
# it does NOT provision. Provisioning needs a real network, a real init system
# and hours of model downloads; the installer environment has none of those.
#
set -eu

INSTALL_ROOT=/opt/sambuca
SRC=/cdrom/sambuca

printf 'sambuca: staging the engine into %s\n' "$INSTALL_ROOT"

mkdir -p "$INSTALL_ROOT" /boot/sambuca /var/lib/sambuca/state /var/log/sambuca /etc/sambuca

# ---------------------------------------------------------------------------
# 1. Engine source.
#
# The USB carries a bundle; the appliance later tracks the git remote. Copying
# the bundle first means a machine with no internet still boots into a working
# appliance and syncs later, rather than failing to provision at all.
# ---------------------------------------------------------------------------
if [ -d "$SRC/engine" ]; then
    cp -a "$SRC/engine"  "$INSTALL_ROOT/"
    cp -a "$SRC/compose" "$INSTALL_ROOT/" 2>/dev/null || true
    printf 'sambuca: engine staged from the installation medium\n'
elif [ -f "$SRC/sambuca.bundle" ]; then
    # A git bundle preserves history, so gitops-sync has a real ancestor to
    # fast-forward from instead of an unrelated-histories merge on first sync.
    git clone -b main "$SRC/sambuca.bundle" "$INSTALL_ROOT" >/dev/null 2>&1 \
        || { printf 'sambuca: FATAL — could not unpack the engine bundle\n' >&2; exit 1; }
    git -C "$INSTALL_ROOT" remote set-url origin \
        https://github.com/laboratoiresonore/Sambuca.git
    printf 'sambuca: engine unpacked from the git bundle\n'
else
    printf 'sambuca: FATAL — no engine payload found on the installation medium\n' >&2
    exit 1
fi

chmod +x "$INSTALL_ROOT"/engine/*.sh 2>/dev/null || true
chmod +x "$INSTALL_ROOT"/engine/maintenance/*.sh 2>/dev/null || true
chmod +x "$INSTALL_ROOT"/engine/autoinstall/*.sh 2>/dev/null || true

# ---------------------------------------------------------------------------
# 2. Provisioning payload -> the boot partition.
#
# first-boot.sh reads it, writes what it needs to the encrypted root, and then
# shreds this copy. It lives unencrypted for exactly one boot.
# ---------------------------------------------------------------------------
if [ -f "$SRC/provision.json" ]; then
    cp "$SRC/provision.json" /boot/sambuca/provision.json
    chmod 0600 /boot/sambuca/provision.json
    printf 'sambuca: provisioning payload staged (shredded after first boot)\n'
else
    printf 'sambuca: WARNING — no provision.json; first boot will use defaults\n'
fi

# ---------------------------------------------------------------------------
# 3. Systemd units + convenience entrypoints.
# ---------------------------------------------------------------------------
cp "$INSTALL_ROOT"/engine/maintenance/systemd/*.service /etc/systemd/system/ 2>/dev/null || true
cp "$INSTALL_ROOT"/engine/maintenance/systemd/*.timer   /etc/systemd/system/ 2>/dev/null || true

systemctl enable sambuca-first-boot.service      >/dev/null 2>&1 || true
systemctl enable sambuca-hardware-detect.service >/dev/null 2>&1 || true
systemctl enable sambuca-gitops.timer            >/dev/null 2>&1 || true
systemctl enable sambuca-backup.timer            >/dev/null 2>&1 || true
systemctl enable sambuca-snapraid.timer          >/dev/null 2>&1 || true

ln -sf "$INSTALL_ROOT/engine/first-boot.sh"                /usr/local/bin/sambuca-first-boot
ln -sf "$INSTALL_ROOT/engine/hardware-detect.sh"           /usr/local/bin/sambuca-hardware
ln -sf "$INSTALL_ROOT/engine/maintenance/backup.sh"        /usr/local/bin/sambuca-backup
ln -sf "$INSTALL_ROOT/engine/maintenance/health.sh"        /usr/local/bin/sambuca-health
ln -sf "$INSTALL_ROOT/engine/maintenance/gitops-sync.sh"   /usr/local/bin/sambuca-gitops
ln -sf "$INSTALL_ROOT/engine/maintenance/snapraid-sync.sh" /usr/local/bin/sambuca-snapraid
ln -sf "$INSTALL_ROOT/engine/maintenance/recovery-key.sh"  /usr/local/bin/sambuca-recovery

# ---------------------------------------------------------------------------
# 4. TPM enrolment is deferred, on purpose.
#
# Sealing the LUKS key to the TPM before the final kernel and initramfs exist
# binds it to PCR values that change on the next boot, and the machine then
# refuses to unlock. Phase 10 enrols it once the system is stable.
# ---------------------------------------------------------------------------
if [ -c /dev/tpmrm0 ] || [ -c /dev/tpm0 ]; then
    touch /var/lib/sambuca/tpm-available
    printf 'sambuca: TPM 2.0 present — enrolment deferred to first boot\n'
fi

printf 'sambuca: staging complete; first boot will provision the appliance\n'
exit 0
