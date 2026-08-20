# shellcheck shell=bash
# sambuca :: phase 50-network — Tailscale mesh, CasaOS port relocation, firewall.
#
# THE TLS DESIGN, stated plainly because it is the part most likely to be
# misunderstood and "fixed" into something worse:
#
#   * REMOTE access uses Tailscale Serve. tailscaled terminates real, publicly
#     trusted Let's Encrypt certificates for <host>.<tailnet>.ts.net and proxies
#     to Caddy. No port forwarding, no DNS ownership, no ACME challenge exposure.
#
#   * LOCAL access uses Caddy's internal CA over the LAN. Those certificates are
#     NOT publicly trusted — by design. The root certificate is exported here and
#     shipped in the recovery bundle so the owner installs it once per device.
#
# We do not pretend a self-signed LAN certificate is "secure by default", and we
# do not open port 80/443 to the internet to obtain a real one. Those are the two
# usual shortcuts and both trade away the appliance's threat model.

# shellcheck source=/dev/null
[[ -r "${SB_ETC}/provision.env" ]] && source "${SB_ETC}/provision.env"

: "${SAMBUCA_TS_AUTHKEY:=}"
: "${SAMBUCA_TS_TAGS:=tag:sambuca}"
: "${SAMBUCA_HOSTNAME:=sambuca}"

export DEBIAN_FRONTEND=noninteractive

# --- tailscale --------------------------------------------------------------
if ! sb_have tailscale; then
    log "installing tailscale"
    install -d -m 0755 /etc/apt/keyrings
    codename="$(. /etc/os-release && echo "${VERSION_CODENAME:-bookworm}")"
    sb_retry 3 5 curl -fsSL --proto '=https' \
        "https://pkgs.tailscale.com/stable/debian/${codename}.noarmor.gpg" \
        -o /etc/apt/keyrings/tailscale-archive-keyring.gpg \
        || die "could not fetch the tailscale signing key"
    chmod 0644 /etc/apt/keyrings/tailscale-archive-keyring.gpg
    printf 'deb [signed-by=/etc/apt/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/debian %s main\n' \
        "$codename" | sb_atomic_write /etc/apt/sources.list.d/tailscale.list 0644
    sb_retry 3 5 apt-get update -qq || die "apt update failed after adding the tailscale repo"
    sb_retry 2 10 apt-get install -y -qq tailscale || die "tailscale installation failed"
fi

sb_run systemctl enable --now tailscaled || die "tailscaled failed to start"

ts_state="$(tailscale status --json 2>/dev/null | jq -r '.BackendState // "Unknown"' || echo Unknown)"
if [[ $ts_state == "Running" ]]; then
    log "tailscale already connected as $(tailscale status --json | jq -r '.Self.DNSName // "?"')"
elif [[ -n $SAMBUCA_TS_AUTHKEY ]]; then
    log "joining the tailnet with the provisioned auth key"
    # --ssh gives the owner a recovery path that survives a broken sshd config.
    if sb_run tailscale up \
        --authkey="$SAMBUCA_TS_AUTHKEY" \
        --hostname="$SAMBUCA_HOSTNAME" \
        --advertise-tags="$SAMBUCA_TS_TAGS" \
        --ssh \
        --accept-dns=false; then
        ok "joined the tailnet"
        # The key is single-use and now spent; do not leave it on disk.
        sed -i 's/^SAMBUCA_TS_AUTHKEY=.*/SAMBUCA_TS_AUTHKEY='"''"'/' "${SB_ETC}/provision.env" 2>/dev/null || true
    else
        warn "tailscale up FAILED — the key may be expired, already used, or tag-restricted."
        warn "  Recover with:  tailscale up --hostname=${SAMBUCA_HOSTNAME} --ssh"
    fi
else
    warn "no tailscale auth key provisioned — remote access is not configured."
    warn "  Run manually:  tailscale up --hostname=${SAMBUCA_HOSTNAME} --ssh"
fi

ts_dnsname="$(tailscale status --json 2>/dev/null | jq -r '.Self.DNSName // ""' | sed 's/\.$//' || true)"
ts_ip="$(tailscale ip -4 2>/dev/null | head -n1 || true)"

# --- casaos -----------------------------------------------------------------
# CasaOS binds :80 by default, which collides head-on with Caddy. Move its
# gateway BEFORE installing, otherwise the installer wins the port and Caddy
# fails to start with an error that points nowhere useful.
CASAOS_PORT="${CASAOS_PORT:-8095}"
if ! sb_have casaos; then
    log "installing CasaOS (gateway pinned to :${CASAOS_PORT})"
    install -d -m 0755 /etc/casaos
    printf '[server]\nport = %s\n' "$CASAOS_PORT" | sb_atomic_write /etc/casaos/gateway.ini 0644
    if sb_retry 2 10 bash -c 'curl -fsSL https://get.casaos.io | bash -s -- --no-color'; then
        ok "CasaOS installed"
    else
        warn "CasaOS installation failed — the appliance is fully functional without it."
        warn "  Caddy still serves every service; only the CasaOS tile view is missing."
    fi
fi
if [[ -f /etc/casaos/gateway.ini ]]; then
    printf '[server]\nport = %s\n' "$CASAOS_PORT" | sb_atomic_write /etc/casaos/gateway.ini 0644
    sb_run systemctl restart casaos-gateway 2>/dev/null || true
fi

# --- firewall ---------------------------------------------------------------
# Default-deny inbound on the physical NIC; the tailnet interface is trusted.
# LAN HTTP/HTTPS stays open because "reachable on the local network without a
# VPN" is a requirement, not an oversight.
if sb_have nft; then
    {
        cat <<'NFT'
#!/usr/sbin/nft -f
# managed by sambuca phase 50 — `nft -f /etc/nftables.conf` to reload
flush ruleset

table inet sambuca {
    chain input {
        type filter hook input priority filter; policy drop;

        ct state established,related accept
        ct state invalid drop
        iif lo accept

        # Tailscale: fully trusted transport, already authenticated + encrypted.
        iifname "tailscale0" accept
        udp dport 41641 accept          # tailscale direct/DERP negotiation

        icmp type echo-request limit rate 5/second accept
        icmpv6 type { echo-request, nd-neighbor-solicit, nd-neighbor-advert,
                      nd-router-advert, nd-router-solicit } accept

        # LAN service surface.
        tcp dport { 22, 80, 443 } accept
        udp dport { 5353 } accept       # mDNS: sambuca.local discovery

        # Docker manages its own chains; do not fight it here.
        counter comment "sambuca-dropped"
    }
    chain forward { type filter hook forward priority filter; policy accept; }
    chain output  { type filter hook output  priority filter; policy accept; }
}
NFT
    } | sb_atomic_write /etc/nftables.conf 0755

    if nft -c -f /etc/nftables.conf 2>/dev/null; then
        sb_run systemctl enable --now nftables
        sb_run nft -f /etc/nftables.conf
        ok "nftables ruleset applied (default-deny inbound)"
    else
        err "generated nftables ruleset failed validation — NOT applying it"
        err "  the host is left with its previous firewall state"
    fi
else
    warn "nft unavailable — no host firewall configured"
fi

# --- record for later phases ------------------------------------------------
{
    printf 'SAMBUCA_TS_DNSNAME=%s\n' "${ts_dnsname:-}"
    printf 'SAMBUCA_TS_IP=%s\n' "${ts_ip:-}"
    printf 'CASAOS_PORT=%s\n' "$CASAOS_PORT"
    printf 'SAMBUCA_LAN_IP=%s\n' "$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}' || echo '')"
} | sb_atomic_write "${SB_ETC}/network.env" 0644

ok "network ready — tailnet=${ts_dnsname:-not-joined} lan=$(sb_env_get "${SB_ETC}/network.env" SAMBUCA_LAN_IP unknown)"
