# shellcheck shell=bash
# sambuca :: phase 10-system — base OS, identity, and the packages everything else needs.
#
# Sourced by first-boot.sh inside a subshell; common.sh is already loaded.
# Idempotent: safe to re-run at any time.

# shellcheck source=/dev/null
[[ -r "${SB_ETC}/provision.env" ]] && source "${SB_ETC}/provision.env"

: "${SAMBUCA_HOSTNAME:=sambuca}"
: "${SAMBUCA_TIMEZONE:=UTC}"
: "${SAMBUCA_LOCALE:=en_US.UTF-8}"
: "${SAMBUCA_ADMIN_USER:=sambuca}"
: "${SAMBUCA_ADMIN_SSH_KEY:=}"

export DEBIAN_FRONTEND=noninteractive

# --- identity ---------------------------------------------------------------
if [[ "$(hostnamectl --static 2>/dev/null || cat /etc/hostname)" != "$SAMBUCA_HOSTNAME" ]]; then
    sb_run hostnamectl set-hostname "$SAMBUCA_HOSTNAME"
    # Keep /etc/hosts consistent or sudo stalls on reverse lookup.
    if ! grep -qE "^127\.0\.1\.1[[:space:]]+${SAMBUCA_HOSTNAME}\b" /etc/hosts 2>/dev/null; then
        sed -i '/^127\.0\.1\.1/d' /etc/hosts
        printf '127.0.1.1\t%s\n' "$SAMBUCA_HOSTNAME" >>/etc/hosts
    fi
fi

sb_run timedatectl set-timezone "$SAMBUCA_TIMEZONE" || warn "timezone '${SAMBUCA_TIMEZONE}' rejected — leaving as-is"
sb_run timedatectl set-ntp true || warn "could not enable NTP"

# --- packages ---------------------------------------------------------------
# Deliberately minimal. Anything a single service needs is that service's
# container's problem; the host stays a thin, auditable substrate.
PKGS=(
    ca-certificates curl wget gnupg jq git rsync
    sudo openssh-server unattended-upgrades apt-listchanges
    lsb-release pciutils usbutils smartmontools lm-sensors
    cryptsetup-bin tpm2-tools
    restic
    # NOT python3-minimal, WHICH WOULD NOT WORK. The install beacon
    # (docs/design/INSTALLER.md §2) is specified as "a tiny read-only HTTP
    # responder from the Python standard library, no dependencies" — and
    # http.server is NOT in python3-minimal. Verified against Debian's own
    # package contents for trixie: http/server.py ships in
    # libpython3.13-stdlib, and python3-minimal's file list does not contain
    # it. The beacon would have failed at the one moment it exists for.
    #
    # The base system's "standard" task probably pulls full python3 anyway,
    # but "probably" is not a dependency. Naming it here makes the beacon's
    # uphill requirement explicit instead of inherited by luck.
    python3
)

log "refreshing apt metadata"
sb_retry 3 5 apt-get update -qq || die "apt-get update failed — no network? check phase 00 preflight"

log "installing ${#PKGS[@]} base packages"
sb_retry 2 10 apt-get install -y -qq --no-install-recommends "${PKGS[@]}" \
    || die "base package installation failed"

# --- admin account ----------------------------------------------------------
if ! id -u "$SAMBUCA_ADMIN_USER" >/dev/null 2>&1; then
    log "creating admin user '${SAMBUCA_ADMIN_USER}'"
    sb_run useradd -m -s /bin/bash -G sudo "$SAMBUCA_ADMIN_USER"
    # No password is set here. Access is by SSH key or the console password the
    # installer set. A blank-password sudo account is how appliances get owned.
    sb_run passwd -l "$SAMBUCA_ADMIN_USER" || true
fi

if [[ -n $SAMBUCA_ADMIN_SSH_KEY ]]; then
    ssh_dir="/home/${SAMBUCA_ADMIN_USER}/.ssh"
    mkdir -p -- "$ssh_dir"
    if ! grep -qxF -- "$SAMBUCA_ADMIN_SSH_KEY" "${ssh_dir}/authorized_keys" 2>/dev/null; then
        printf '%s\n' "$SAMBUCA_ADMIN_SSH_KEY" >>"${ssh_dir}/authorized_keys"
        ok "admin SSH key installed"
    fi
    chmod 0700 -- "$ssh_dir"; chmod 0600 -- "${ssh_dir}/authorized_keys"
    chown -R "${SAMBUCA_ADMIN_USER}:${SAMBUCA_ADMIN_USER}" -- "$ssh_dir"
fi

# --- sshd hardening ---------------------------------------------------------
# Password auth is disabled ONLY when a key is present; otherwise we would lock
# the owner out of their own machine on first boot.
sshd_drop="/etc/ssh/sshd_config.d/10-sambuca.conf"
{
    printf '%s\n' "# managed by sambuca — edit /etc/ssh/sshd_config.d/99-local.conf instead"
    printf '%s\n' "PermitRootLogin no"
    printf '%s\n' "KbdInteractiveAuthentication no"
    printf '%s\n' "X11Forwarding no"
    if [[ -n $SAMBUCA_ADMIN_SSH_KEY ]]; then
        printf '%s\n' "PasswordAuthentication no"
    else
        printf '%s\n' "# PasswordAuthentication left enabled: no admin key was provisioned."
        printf '%s\n' "# Add a key, then set 'PasswordAuthentication no' in 99-local.conf."
        printf '%s\n' "PasswordAuthentication yes"
    fi
} | sb_atomic_write "$sshd_drop" 0644

if sshd -t 2>/dev/null; then
    sb_run systemctl reload ssh 2>/dev/null || sb_run systemctl reload sshd 2>/dev/null || true
else
    err "sshd config validation failed — reverting the sambuca drop-in"
    rm -f -- "$sshd_drop"
fi

# --- unattended security upgrades ------------------------------------------
# Security patches only. Feature upgrades on an appliance are a GitOps decision
# (engine/maintenance/gitops-sync.sh), not something apt does at 06:00 unasked.
{
    printf '%s\n' 'Unattended-Upgrade::Origins-Pattern {'
    printf '%s\n' '    "origin=Debian,codename=${distro_codename},label=Debian-Security";'
    printf '%s\n' '    "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";'
    printf '%s\n' '};'
    printf '%s\n' 'Unattended-Upgrade::Automatic-Reboot "false";'
    printf '%s\n' 'Unattended-Upgrade::Remove-Unused-Dependencies "true";'
} | sb_atomic_write /etc/apt/apt.conf.d/51-sambuca-unattended 0644

{
    printf '%s\n' 'APT::Periodic::Update-Package-Lists "1";'
    printf '%s\n' 'APT::Periodic::Unattended-Upgrade "1";'
    printf '%s\n' 'APT::Periodic::AutocleanInterval "7";'
} | sb_atomic_write /etc/apt/apt.conf.d/20auto-upgrades 0644

# --- kernel tuning ----------------------------------------------------------
{
    printf '%s\n' '# sambuca: appliance tuning'
    printf '%s\n' '# Container networking needs bridged traffic to traverse iptables.'
    printf '%s\n' 'net.ipv4.ip_forward = 1'
    printf '%s\n' '# Inotify: Nextcloud + Immich + Syncthing-class watchers exhaust the default.'
    printf '%s\n' 'fs.inotify.max_user_watches = 524288'
    printf '%s\n' 'fs.inotify.max_user_instances = 1024'
    printf '%s\n' '# Prefer reclaiming page cache over swapping a resident model out.'
    printf '%s\n' 'vm.swappiness = 10'
    printf '%s\n' '# Postgres (Immich/Nextcloud) wants overcommit off the "guess" heuristic.'
    printf '%s\n' 'vm.overcommit_memory = 1'
} | sb_atomic_write /etc/sysctl.d/90-sambuca.conf 0644
sb_run sysctl --system >/dev/null || warn "sysctl reload reported errors"

# --- filesystem layout ------------------------------------------------------
# 0750, NOT 0755. Provisioning logs record what the machine did while it had
# the fewest defences up, and a warn that accidentally carries a secret
# prefix or a URL should not be world-readable. root:adm is the standard
# posture for system logs and costs nothing.
install -d -m 0755 "${SB_LIB}"
install -d -m 0750 "${SB_LOG_DIR}"
install -d -m 0700 "${SB_ETC}/secrets"

# --- zram: compressed swap in RAM -------------------------------------------
#
# THE README PROMISED THIS AND NOTHING PROVIDED IT, in the same paragraph that
# says "'Lean' is a number in profile.env you can check, not an adjective in a
# README". The size is now exactly that: hardware-detect.sh measures RAM and
# writes ZRAM_SIZE_MB, and 0 means it decided this machine does not need it.
#
# It matters most where this project aims — a 4-8 GiB office box running a file
# server, a photo library and a database. Compressed pages buy roughly 2-3x on
# cold memory for a few percent of CPU, which is the difference between swapping
# to a spinning disk and not swapping at all.
zram_mb="$(sb_env_get "${SB_ETC}/profile.env" ZRAM_SIZE_MB 0)"
if [[ $zram_mb =~ ^[0-9]+$ ]] && ((zram_mb > 0)); then
    if sb_retry 2 10 apt-get install -y -qq --no-install-recommends             systemd-zram-generator >/dev/null 2>&1; then
        {
            printf '# Written by sambuca 10-system.sh. Size measured by
'
            printf '# hardware-detect.sh; edit ZRAM_SIZE_MB in profile.env, not here.
'
            printf '[zram0]
'
            printf 'zram-size = %s
' "$zram_mb"
            # zstd over lzo-rle: better ratio at a cost this machine can afford,
            # and the ratio is the entire point of the exercise.
            printf 'compression-algorithm = zstd
'
        } | sb_atomic_write /etc/systemd/zram-generator.conf 0644
        sb_run systemctl daemon-reload || warn "daemon-reload reported errors"
        # FAILING HERE IS NOT FATAL. A machine without compressed swap is slower
        # under memory pressure; a machine that refuses to finish provisioning
        # because of it is broken.
        if sb_run systemctl start systemd-zram-setup@zram0.service; then
            ok "zram: ${zram_mb} MiB of compressed swap"
        else
            warn "zram configured but the device did not start; the machine is"
            warn "  fine, it will simply swap to disk under pressure"
        fi
    else
        warn "systemd-zram-generator is unavailable; skipping compressed swap"
    fi
else
    log "zram: not configured — the profiler measured enough RAM that there is"
    log "  nothing for compressed swap to rescue"
fi

ok "base system configured (host=${SAMBUCA_HOSTNAME}, tz=${SAMBUCA_TIMEZONE})"
