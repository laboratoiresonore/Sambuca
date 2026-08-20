# shellcheck shell=bash
# sambuca :: phase 80-identity — Pocket ID bootstrap and the zero-trust gate.
#
# HONEST LIMITATION, stated up front because pretending otherwise would ship a
# false sense of security:
#
#   Passkey enrolment CANNOT be automated. A WebAuthn credential is created by
#   the owner's authenticator (Touch ID, YubiKey, phone) touching a browser. No
#   provisioning script can mint one, and any design that claims to has instead
#   left a password-equivalent bootstrap secret lying on the disk.
#
# So this phase does everything that CAN be automated — starts Pocket ID, wires
# oauth2-proxy, and emits a one-time setup link — and then FAILS CLOSED on the
# gated routes until a human completes enrolment. Services that carry their own
# authentication (Nextcloud, Immich, Vaultwarden) are unaffected and fully usable
# in the meantime.

# shellcheck source=/dev/null
for f in provision.env profile.env network.env; do
    [[ -r "${SB_ETC}/${f}" ]] && source "${SB_ETC}/${f}"
done

: "${SAMBUCA_DOMAIN:=sambuca.local}"
COMPOSE_DIR="${SAMBUCA_COMPOSE_DIR:-/opt/sambuca/compose}"
POCKET_ID_CONTAINER="${POCKET_ID_CONTAINER:-sambuca-pocket-id}"
IDENTITY_STATE="${SB_LIB}/identity.json"

cd "$COMPOSE_DIR" || die "cannot enter ${COMPOSE_DIR}"

# --- wait for the identity provider ----------------------------------------
log "waiting for Pocket ID"
ready=0
for _ in $(seq 1 60); do
    if docker exec "$POCKET_ID_CONTAINER" wget -qO- http://127.0.0.1:1411/healthz >/dev/null 2>&1 \
       || docker inspect -f '{{.State.Health.Status}}' "$POCKET_ID_CONTAINER" 2>/dev/null | grep -qx healthy; then
        ready=1; break
    fi
    sleep 2
done

if ((ready == 0)); then
    warn "Pocket ID did not become healthy within 120s."
    warn "  The appliance still works: every service with its own login is reachable."
    warn "  Gated routes (dashboard, notes, monitoring) will return 503 until this is fixed."
    warn "  logs: docker logs ${POCKET_ID_CONTAINER} --tail 50"
    printf '{"provider":"pocket-id","state":"unavailable","enrolled":false}\n' \
        | sb_atomic_write "$IDENTITY_STATE" 0644
    return 0
fi
ok "Pocket ID is healthy"

# --- one-time setup link ----------------------------------------------------
# Pocket ID emits an initial-admin onboarding token in its logs on first start.
# We surface it rather than inventing a parallel bootstrap path with its own
# (weaker) security properties.
setup_token="$(docker logs "$POCKET_ID_CONTAINER" 2>&1 \
    | grep -oE '/(setup|login)/[A-Za-z0-9_-]{16,}' | tail -n1 || true)"

setup_url=""
if [[ -n $setup_token ]]; then
    setup_url="https://id.${SAMBUCA_DOMAIN}${setup_token}"
    ok "one-time admin setup link captured"
else
    setup_url="https://id.${SAMBUCA_DOMAIN}/setup"
    warn "no one-time setup token found in the logs (Pocket ID may already be configured)."
    warn "  If this is a fresh install, retrieve it with:"
    warn "    docker logs ${POCKET_ID_CONTAINER} 2>&1 | grep -i setup"
fi

# --- oauth2-proxy client ----------------------------------------------------
# The OIDC client must be created inside Pocket ID by the admin, which requires
# the admin to exist, which requires enrolment. We therefore write the client
# secret we WILL use, and let the owner paste the matching client ID back in.
# The alternative — provisioning an API token before any human has authenticated
# — is precisely the bootstrap backdoor this appliance exists to avoid.
client_secret_path="${SB_ETC}/secrets/oauth2_client_secret"
if [[ ! -s $client_secret_path ]]; then
    sb_secret 48 | tr -d '\n' | sb_atomic_write "$client_secret_path" 0600
    log "generated the oauth2-proxy client secret"
fi

client_id_path="${SB_ETC}/secrets/oauth2_client_id"
if [[ -s $client_id_path ]]; then
    ok "OIDC client already configured — the gate is live"
    enrolled=true
else
    printf '%s' "PENDING_ENROLMENT" | sb_atomic_write "$client_id_path" 0600
    enrolled=false
    warn "───────────────────────────────────────────────────────────────"
    warn " ACTION REQUIRED — one attended step, then the gate is armed."
    warn ""
    warn "  1. Open:  ${setup_url}"
    warn "  2. Register your passkey as the first admin."
    warn "  3. Create an OIDC client:"
    warn "       name          sambuca-gate"
    warn "       callback URL  https://auth.${SAMBUCA_DOMAIN}/oauth2/callback"
    warn "       client secret $(head -c 8 "$client_secret_path")…  (full value in ${client_secret_path})"
    warn "  4. Then run:  sambuca identity set-client <client-id>"
    warn ""
    warn " Until step 4, gated routes return 503. Everything with its own"
    warn " login (Nextcloud, Immich, Vaultwarden) works right now."
    warn "───────────────────────────────────────────────────────────────"
fi

# --- helper CLI -------------------------------------------------------------
{
    cat <<'HELPER'
#!/usr/bin/env bash
# sambuca identity — finish the one attended step of identity bootstrap.
set -euo pipefail
SB_ETC="${SB_ETC:-/etc/sambuca}"
COMPOSE_DIR="${SAMBUCA_COMPOSE_DIR:-/opt/sambuca/compose}"

case "${1:-}" in
  set-client)
    id="${2:?usage: sambuca-identity set-client <client-id>}"
    printf '%s' "$id" >"${SB_ETC}/secrets/oauth2_client_id"
    chmod 0600 "${SB_ETC}/secrets/oauth2_client_id"
    cd "$COMPOSE_DIR"
    docker compose up -d --force-recreate oauth2-proxy caddy
    printf 'OIDC client set; the gate is armed.\n'
    ;;
  status)
    id="$(cat "${SB_ETC}/secrets/oauth2_client_id" 2>/dev/null || echo missing)"
    [[ $id == PENDING_ENROLMENT || $id == missing ]] \
      && printf 'identity: NOT bootstrapped (gated routes return 503)\n' \
      || printf 'identity: bootstrapped (client %s…)\n' "${id:0:8}"
    ;;
  *) printf 'usage: sambuca-identity {set-client <id>|status}\n' >&2; exit 2 ;;
esac
HELPER
} | sb_atomic_write /usr/local/bin/sambuca-identity 0755

{
    printf '{\n'
    printf '  "provider": "pocket-id",\n'
    printf '  "state": "healthy",\n'
    printf '  "enrolled": %s,\n' "$enrolled"
    printf '  "setup_url": "%s",\n' "$setup_url"
    printf '  "gated_services": ["dashboard", "blinko", "uptime-kuma", "bentopdf"],\n'
    printf '  "self_authenticating_services": ["nextcloud", "immich", "vaultwarden", "synapse"]\n'
    printf '}\n'
} | sb_atomic_write "$IDENTITY_STATE" 0644

ok "identity phase complete (enrolled=${enrolled})"
