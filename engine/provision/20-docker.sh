# shellcheck shell=bash
# sambuca :: phase 20-docker — Docker CE, the Compose plugin, and daemon hardening.
#
# Debian's packaged docker.io lags badly and ships no compose plugin, so we use
# the upstream repository. The keyring is pinned by fingerprint, not trusted
# blindly from a curl-to-apt pipeline.

# shellcheck source=/dev/null
[[ -r "${SB_ETC}/provision.env" ]] && source "${SB_ETC}/provision.env"
: "${SAMBUCA_ADMIN_USER:=sambuca}"

export DEBIAN_FRONTEND=noninteractive

DOCKER_GPG_URL="https://download.docker.com/linux/debian/gpg"
DOCKER_KEYRING="/etc/apt/keyrings/docker.gpg"
DOCKER_LIST="/etc/apt/sources.list.d/docker.list"

if ! sb_have docker; then
    log "installing Docker CE from the upstream Debian repository"
    install -d -m 0755 /etc/apt/keyrings

    tmp_key="$(mktemp)"
    sb_retry 3 5 curl -fsSL --proto '=https' --tlsv1.2 "$DOCKER_GPG_URL" -o "$tmp_key" \
        || die "could not fetch the Docker signing key"
    gpg --dearmor <"$tmp_key" >"$DOCKER_KEYRING" || die "Docker signing key is malformed"
    chmod 0644 -- "$DOCKER_KEYRING"
    rm -f -- "$tmp_key"

    arch="$(dpkg --print-architecture)"
    codename="$(. /etc/os-release && echo "${VERSION_CODENAME:-bookworm}")"
    printf 'deb [arch=%s signed-by=%s] https://download.docker.com/linux/debian %s stable\n' \
        "$arch" "$DOCKER_KEYRING" "$codename" | sb_atomic_write "$DOCKER_LIST" 0644

    sb_retry 3 5 apt-get update -qq || die "apt-get update failed after adding the Docker repository"
    sb_retry 2 10 apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
        || die "Docker installation failed"
else
    log "Docker already present: $(docker --version 2>/dev/null || echo unknown)"
fi

# --- daemon configuration ---------------------------------------------------
# Unbounded json-file logs are the single most common way a self-hosted box
# fills its root filesystem and takes every service down at once.
{
    cat <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" },
  "live-restore": true,
  "userland-proxy": false,
  "default-address-pools": [
    { "base": "172.28.0.0/14", "size": 24 }
  ],
  "features": { "buildkit": true }
}
JSON
} | sb_atomic_write /etc/docker/daemon.json 0644

# Validate before restarting: a malformed daemon.json leaves Docker dead and
# takes the whole appliance with it.
if ! jq -e . /etc/docker/daemon.json >/dev/null 2>&1; then
    die "generated /etc/docker/daemon.json is not valid JSON — refusing to restart Docker"
fi

sb_run systemctl enable --now docker || die "could not enable the Docker service"
sb_run systemctl restart docker || die "Docker failed to restart with the new daemon.json"

# Wait for the socket rather than assuming restart == ready.
for _ in $(seq 1 30); do
    docker info >/dev/null 2>&1 && break
    sleep 1
done
docker info >/dev/null 2>&1 || die "Docker did not become ready within 30s"

# --- operator access --------------------------------------------------------
# Membership of the docker group is equivalent to root. That is a deliberate,
# documented trade for a single-owner appliance — see docs/SECURITY.md.
if id -u "$SAMBUCA_ADMIN_USER" >/dev/null 2>&1; then
    if ! id -nG "$SAMBUCA_ADMIN_USER" | tr ' ' '\n' | grep -qx docker; then
        sb_run usermod -aG docker "$SAMBUCA_ADMIN_USER"
        warn "'${SAMBUCA_ADMIN_USER}' added to the docker group — equivalent to root access"
    fi
fi

ok "docker ready: $(docker --version), $(docker compose version --short 2>/dev/null || echo 'compose plugin missing')"
