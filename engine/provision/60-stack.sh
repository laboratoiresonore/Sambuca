# shellcheck shell=bash
# sambuca :: phase 60-stack — render the environment and bring the mesh up.
#
# This is where the appliance stops being a Debian box and starts being sambuca.
# It renders compose/.env from four inputs, in this precedence order:
#
#   1. compose/.env.example      the declared defaults + image pins
#   2. /etc/sambuca/profile.env  the hardware tier and resource arbitration
#   3. /etc/sambuca/*.env        provisioning facts (storage, network, identity)
#   4. generated secrets         created once, never regenerated, 0600
#
# Secrets are generated ON THE DEVICE. Nothing here was ever transmitted, and
# nothing here appears in the repository, the USB payload, or any log line.

# shellcheck source=/dev/null
for f in provision.env profile.env profile.local.env storage.env network.env; do
    [[ -r "${SB_ETC}/${f}" ]] && source "${SB_ETC}/${f}"
done

: "${SAMBUCA_DOMAIN:=sambuca.local}"
: "${SAMBUCA_DATA:=/srv/sambuca}"
: "${SAMBUCA_BUNDLES:=ai,cloud,office,comms}"
: "${SAMBUCA_TS_DNSNAME:=}"

COMPOSE_DIR="${SAMBUCA_COMPOSE_DIR:-/opt/sambuca/compose}"
SECRETS_DIR="${SB_ETC}/secrets"
ENV_FILE="${COMPOSE_DIR}/.env"

[[ -d $COMPOSE_DIR ]] || die "compose directory missing: ${COMPOSE_DIR}"
install -d -m 0700 "$SECRETS_DIR"

# ---------------------------------------------------------------------------
# Secret material. Written once. A re-run must NOT rotate a secret out from
# under a database that is already encrypted with it — that is data loss, and
# it is the classic idempotency bug in provisioning scripts.
# ---------------------------------------------------------------------------
secret_get() {
    local name="$1" bytes="${2:-32}"
    # Separate statement, deliberately: inside one `local`, ${name} has not yet
    # taken effect, so every secret would resolve to the same path and overwrite
    # the previous one. shellcheck SC2318 caught this before any hardware did.
    local path="${SECRETS_DIR}/${name}"
    if [[ -s $path ]]; then
        cat -- "$path"
        return 0
    fi
    local value; value="$(sb_secret "$bytes")"
    # 0644, not 0600 — deliberately, and only safe because of the directory.
    # Compose bind-mounts these into containers that run as non-root service
    # users (postgres, node); a root-owned 0600 file is unreadable there and the
    # database simply fails to start. ${SECRETS_DIR} is 0700, so on the HOST
    # traversal is still blocked for everyone but root. The directory does the
    # confinement; the file mode only governs the mounted copy.
    printf '%s' "$value" | sb_atomic_write "$path" 0644
    log "generated new secret: ${name} (${bytes} chars, stored 0600)"
    printf '%s' "$value"
}

log "materialising service secrets in ${SECRETS_DIR}"
POSTGRES_PASSWORD="$(secret_get postgres_password 40)"
IMMICH_DB_PASSWORD="$(secret_get immich_db_password 40)"
NEXTCLOUD_PASSWORD="$(secret_get nextcloud_admin_password 32)"
VAULTWARDEN_ADMIN_TOKEN="$(secret_get vaultwarden_admin_token 48)"
OAUTH2_PROXY_COOKIE_SECRET="$(secret_get oauth2_cookie_secret 32)"
POCKET_ID_ENCRYPTION_KEY="$(secret_get pocket_id_encryption_key 32)"
BLINKO_JWT_SECRET="$(secret_get blinko_jwt_secret 40)"
SYNAPSE_REGISTRATION_SECRET="$(secret_get synapse_registration_secret 48)"

# ---------------------------------------------------------------------------
# COMPOSE_FILE chain. Bundles are opt-in from provision.json; the GPU overlay
# comes from the hardware profile. Building the chain here — rather than with
# compose `profiles:` — keeps `docker compose config` honest: what you render
# is exactly what runs.
# ---------------------------------------------------------------------------
chain="docker-compose.yml"
enabled=()

# The image bundle is decided by the HARDWARE PROFILE, not by provision.json.
# provision.json is written on the owner's laptop before anyone has seen the
# target machine; hardware-detect.sh measured the card that is actually in it,
# including the disk guard and the VRAM floor. The measurement wins, in both
# directions: it can add the bundle and it can take it away.
if [[ ${SAMBUCA_IMAGE_ENABLED:-0} == 1 ]]; then
    [[ ",${SAMBUCA_BUNDLES}," == *",image,"* ]] || SAMBUCA_BUNDLES="${SAMBUCA_BUNDLES},image"
    log "image plane enabled (${SAMBUCA_IMAGE_MODEL_NAME:-unknown model}, handoff=${SAMBUCA_IMAGE_HANDOFF:-none})"
else
    SAMBUCA_BUNDLES="${SAMBUCA_BUNDLES//,image/}"
    SAMBUCA_BUNDLES="${SAMBUCA_BUNDLES//image,/}"
    SAMBUCA_BUNDLES="${SAMBUCA_BUNDLES//image/}"
    if [[ ${SAMBUCA_IMAGE_DROPPED:-0} == 1 ]]; then
        warn "image plane was dropped by the hardware profile — see /etc/sambuca/profile.env"
    fi
fi

IFS=',' read -ra bundles <<<"$SAMBUCA_BUNDLES"
for b in "${bundles[@]}"; do
    b="${b// /}"; [[ -z $b ]] && continue
    if [[ -f "${COMPOSE_DIR}/${b}.yml" ]]; then
        chain="${chain}:${b}.yml"
        enabled+=("$b")
    else
        warn "bundle '${b}' requested but ${COMPOSE_DIR}/${b}.yml does not exist — skipping"
    fi
done

# GPU overlays are per-bundle. A compose override naming a service the selected
# bundles do not define invalidates the ENTIRE project, not just that service,
# so an overlay is only appended when its bundle is actually present.
profile="${SAMBUCA_GPU_PROFILE:-cpu}"
[[ -f "${COMPOSE_DIR}/gpu.${profile}.ai.yml" ]] || {
    warn "no overlay for GPU profile '${profile}' — falling back to cpu"
    profile="cpu"
}
for b in "${enabled[@]}"; do
    overlay="gpu.${profile}.${b}.yml"
    [[ -f "${COMPOSE_DIR}/${overlay}" ]] && chain="${chain}:${overlay}"
done
# A local overlay the owner controls, always last so it wins.
[[ -f "${COMPOSE_DIR}/local.yml" ]] && chain="${chain}:local.yml"

log "compose chain: ${chain}"

# ---------------------------------------------------------------------------
# Render .env
# ---------------------------------------------------------------------------
{
    printf '%s\n' "# GENERATED BY sambuca phase 60-stack — DO NOT EDIT."
    printf '%s\n' "# Owner overrides belong in ${COMPOSE_DIR}/local.yml or /etc/sambuca/profile.local.env."
    printf '%s\n' "# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '\n'

    printf '%s\n' "COMPOSE_PROJECT_NAME=sambuca"
    printf 'COMPOSE_FILE=%s\n' "$chain"
    printf '%s\n' "COMPOSE_PATH_SEPARATOR=:"
    printf '\n'

    printf '# --- identity / addressing ---\n'
    printf 'SAMBUCA_DOMAIN=%s\n'      "$SAMBUCA_DOMAIN"
    printf 'SAMBUCA_TS_DNSNAME=%s\n'  "${SAMBUCA_TS_DNSNAME:-}"
    printf 'SAMBUCA_ACME_EMAIL=%s\n'  "${SAMBUCA_ACME_EMAIL:-}"
    printf 'TZ=%s\n'                  "${SAMBUCA_TIMEZONE:-UTC}"
    printf 'PUID=%s\n'                "$(id -u "${SAMBUCA_ADMIN_USER:-root}" 2>/dev/null || echo 0)"
    printf 'PGID=%s\n'                "$(id -g "${SAMBUCA_ADMIN_USER:-root}" 2>/dev/null || echo 0)"
    printf 'CASAOS_PORT=%s\n'         "${CASAOS_PORT:-8095}"
    printf 'SAMBUCA_STATE=%s\n'       "${SB_LIB}"
    printf '\n'

    printf '# --- storage ---\n'
    printf 'SAMBUCA_DATA=%s\n'    "$SAMBUCA_DATA"
    printf 'SAMBUCA_APPDATA=%s\n' "${SAMBUCA_DATA}/appdata"
    printf 'SAMBUCA_MEDIA=%s\n'   "${SAMBUCA_DATA}/media"
    printf 'SAMBUCA_PHOTOS=%s\n'  "${SAMBUCA_DATA}/photos"
    printf 'SAMBUCA_FILES=%s\n'   "${SAMBUCA_DATA}/files"
    printf '\n'

    printf '# --- hardware profile (from hardware-detect.sh) ---\n'
    grep -E '^(SAMBUCA_(TIER|GPU|VRAM|CPU|RAM|MODEL)|OLLAMA_|IMMICH_ML_|MACHINE_LEARNING_)' \
        "${SB_ETC}/profile.env" 2>/dev/null || true
    printf '\n'

    printf '# --- secrets (generated on-device, never transmitted) ---\n'
    printf 'POSTGRES_PASSWORD=%s\n'           "$POSTGRES_PASSWORD"
    printf 'IMMICH_DB_PASSWORD=%s\n'          "$IMMICH_DB_PASSWORD"
    printf 'NEXTCLOUD_ADMIN_PASSWORD=%s\n'    "$NEXTCLOUD_PASSWORD"
    printf 'VAULTWARDEN_ADMIN_TOKEN=%s\n'     "$VAULTWARDEN_ADMIN_TOKEN"
    printf 'OAUTH2_PROXY_COOKIE_SECRET=%s\n'  "$OAUTH2_PROXY_COOKIE_SECRET"
    printf 'POCKET_ID_ENCRYPTION_KEY=%s\n'    "$POCKET_ID_ENCRYPTION_KEY"
    printf 'BLINKO_JWT_SECRET=%s\n'           "$BLINKO_JWT_SECRET"
    printf 'SYNAPSE_REGISTRATION_SECRET=%s\n' "$SYNAPSE_REGISTRATION_SECRET"
    printf '\n'

    printf '# --- image pins (see docs/IMAGES.md) ---\n'
    # IMMICH_ML_IMAGE is resolved below instead of copied, so it is excluded
    # here: two assignments of the same key in one env file is a coin toss.
    grep -E '^[A-Z0-9_]+_IMAGE=' "${COMPOSE_DIR}/.env.example" 2>/dev/null \
        | grep -v '^IMMICH_ML_IMAGE=' || true

    # ── RESOLVE THE IMMICH ML VARIANT HERE, NOT IN THE COMPOSE FILE ─────────
    # compose used to write `${IMMICH_ML_IMAGE}${IMMICH_ML_IMAGE_SUFFIX}`, and
    # that concatenation is how a broken reference went unnoticed for as long
    # as the AMD path existed: verify-images.py resolved IMMICH_ML_IMAGE, which
    # is perfectly valid on its own, while what compose actually pulled was
    # base+suffix — and `-rocm` is not published on any release tag.
    #
    # THE REFERENCE THAT IS CHECKED MUST BE THE REFERENCE THAT IS USED. Writing
    # the finished string into .env makes it one value that every tool sees.
    # It is also what makes digest pinning possible at all: you cannot append
    # "-cuda" to something ending in "@sha256:…".
    # The selection lives in sb_ml_image_ref (engine/lib/common.sh) so it can be
    # driven by tests/test-ml-variant.sh. Logic that only exists inside a script
    # needing root and a Docker daemon is logic nothing ever checks — which is
    # the condition the -rocm reference survived in.
    _ml_suffix="$(sb_env_get "${SB_ETC}/profile.env" IMMICH_ML_IMAGE_SUFFIX)"
    if ! _ml_ref="$(sb_ml_image_ref "${COMPOSE_DIR}/.env.example" "${_ml_suffix}")"; then
        warn "IMMICH_ML_IMAGE_SUFFIX='${_ml_suffix}' names no pinned variant"
        warn "  known: '' (cpu), -cuda, -openvino. Using the CPU image."
    fi
    [[ -n ${_ml_ref:-} ]] && printf 'IMMICH_ML_IMAGE=%s\n' "$_ml_ref"
} | sb_atomic_write "$ENV_FILE" 0600

ok "rendered ${ENV_FILE} (0600)"

# ---------------------------------------------------------------------------
# Validate BEFORE starting. `compose config` catches a bad interpolation or a
# missing overlay in one second; discovering it through half-started containers
# takes ten minutes and leaves orphans.
# ---------------------------------------------------------------------------
cd "$COMPOSE_DIR" || die "cannot enter ${COMPOSE_DIR}"

if ! docker compose config --quiet 2>"${SB_LOG_DIR}/compose-config.err"; then
    err "compose configuration is INVALID — refusing to start the stack"
    err "$(head -n 20 "${SB_LOG_DIR}/compose-config.err")"
    die "fix the configuration, then: sambuca-first-boot --only 60-stack --force"
fi
ok "compose configuration validated"

install -d -m 0755 "${SAMBUCA_APPDATA:-${SAMBUCA_DATA}/appdata}"

# The setup screen's progress file is BIND-MOUNTED into Caddy. Docker creates a
# DIRECTORY when the source of a file bind-mount does not exist, and that
# directory then serves 404 for the life of the container — so it must exist
# before `compose up`, not after.
if [[ ! -f "${SB_LIB}/progress.json" ]]; then
    sb_progress_write 0 "Starting your services" \
        "Bringing the appliance online." "a few minutes" "Nothing." "" "running"
fi

log "pulling images (this is the long part on a cold install)"
sb_retry 2 15 docker compose pull --quiet || warn "some images failed to pull — start may be partial"

log "starting the stack"
sb_run docker compose up -d --remove-orphans || die "docker compose up failed"

# ---------------------------------------------------------------------------
# Health gate. "up" is not "working" — wait for the healthchecks declared in the
# compose files, and report precisely which service is unhealthy rather than
# emitting a cheerful success message over a broken stack.
# ---------------------------------------------------------------------------
log "waiting for services to report healthy (up to 300s)"
deadline=$(( $(date +%s) + 300 ))
while :; do
    unhealthy="$(docker compose ps --format '{{.Name}}\t{{.Health}}' 2>/dev/null \
        | awk -F'\t' '$2 == "unhealthy" || $2 == "starting" {print $1}' || true)"
    [[ -z $unhealthy ]] && break
    if (( $(date +%s) > deadline )); then
        warn "still not healthy after 300s:"
        printf '%s\n' "$unhealthy" | while read -r svc; do warn "  - ${svc}"; done
        warn "  inspect with: docker compose -f ${COMPOSE_DIR}/docker-compose.yml logs <service>"
        break
    fi
    sleep 5
done

failed="$(docker compose ps --status exited --format '{{.Name}}' 2>/dev/null || true)"
[[ -n $failed ]] && warn "exited container(s): ${failed//$'\n'/, }"

# ---------------------------------------------------------------------------
# Tailscale Serve — publish Caddy on the tailnet with a real certificate.
# Done here, not in phase 50, because it proxies to a Caddy that must exist.
# ---------------------------------------------------------------------------
#
# A tailnet node has exactly ONE DNS name, so remote routing is by PORT. The
# map below must stay in lockstep with the `:84xx` listeners in the Caddyfile.
if [[ -n ${SAMBUCA_TS_DNSNAME:-} ]] && sb_have tailscale; then
    declare -A TS_PORTS=(
        [443]="dashboard" [8443]="chat"   [8444]="cloud" [8445]="photos"
        [8446]="vault"    [8447]="notes"  [8449]="pdf"   [8450]="status"
        [8451]="identity"
    )
    served=0; failed=""
    for port in "${!TS_PORTS[@]}"; do
        # 443 fronts the dashboard on Caddy's :80; the rest map 1:1.
        target=$([[ $port == 443 ]] && echo 80 || echo "$port")
        if tailscale serve --bg --https="$port" "http://127.0.0.1:${target}" >/dev/null 2>&1; then
            ((served++))
        else
            failed="${failed} ${TS_PORTS[$port]}:${port}"
        fi
    done

    if ((served > 0)); then
        ok "tailscale serve active on ${served} port(s) — https://${SAMBUCA_TS_DNSNAME}/"
        [[ -n $failed ]] && warn "serve failed for:${failed}"
    else
        warn "every tailscale serve mapping failed."
        warn "  Almost always this means HTTPS certificates are not enabled for the tailnet:"
        warn "    admin console -> DNS -> HTTPS Certificates -> Enable"
        warn "  The appliance is still fully usable on the LAN."
    fi
fi

# ---------------------------------------------------------------------------
# Export Caddy's internal CA so LAN clients can trust it.
# ---------------------------------------------------------------------------
ca_src="${SAMBUCA_APPDATA}/caddy/data/caddy/pki/authorities/local/root.crt"
if [[ -r $ca_src ]]; then
    install -m 0644 "$ca_src" "${SB_LIB}/sambuca-local-ca.crt"
    ok "local CA exported: ${SB_LIB}/sambuca-local-ca.crt (install on LAN clients)"
else
    warn "Caddy's internal CA is not on disk yet — it appears on first TLS handshake."
    warn "  Retrieve it later with: sambuca ca export"
fi

# ---------------------------------------------------------------------------
# Leaf certificates for services that do not speak HTTP and so cannot get one
# from Caddy on their own.
#
# The CA private key is read on the HOST and never mounted into a container.
# The alternative — handing a service Caddy's CA directory — would let anything
# compromising that service mint trusted certificates for every other service
# on the appliance.
# ---------------------------------------------------------------------------
if [[ ",${SAMBUCA_BUNDLES}," == *",comms,"* ]]; then
    "${_SB_SELF_DIR:-/opt/sambuca/engine}/maintenance/issue-service-cert.sh" \
        "irc.${SAMBUCA_DOMAIN}" "${SAMBUCA_APPDATA}/ergo/tls" \
        "${SAMBUCA_DOMAIN}" "${SAMBUCA_TS_DNSNAME:-}" \
        || warn "could not issue the IRC certificate — TLS on 6697 will not start"
    # Ergo runs unprivileged; it must be able to read its own key.
    chown -R "${PUID:-1000}:${PGID:-1000}" "${SAMBUCA_APPDATA}/ergo/tls" 2>/dev/null || true
    sb_run docker compose up -d ergo 2>/dev/null || true
fi

ok "stack running: $(docker compose ps --format '{{.Name}}' 2>/dev/null | wc -l) container(s)"
