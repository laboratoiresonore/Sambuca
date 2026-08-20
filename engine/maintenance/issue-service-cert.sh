#!/usr/bin/env bash
#
# sambuca :: engine/maintenance/issue-service-cert.sh
#
# Mint a LEAF TLS certificate for a service that does not speak HTTP and so
# cannot get one from Caddy — IRC, and anything like it added later.
#
# ══════════════════════════════════════════════════════════════════════════
# THE RULE THIS EXISTS TO ENFORCE: THE CA KEY NEVER LEAVES THE HOST
#
# The obvious shortcut is to mount Caddy's CA directory into the service and
# point it at root.crt/root.key. That is wrong twice over:
#
#   1. SECURITY — it hands that container the CA PRIVATE KEY. Anything that
#      compromises a chat server could then mint certificates trusted by every
#      device on which the owner installed the appliance's CA, for every other
#      service on the box. One container escape becomes total MITM.
#
#   2. IT DOES NOT WORK — a CA root has no hostname SAN and asserts
#      basicConstraints CA:TRUE. Clients that check either one reject it.
#
# So the CA key is read here, on the host, as root, used once, and never
# mounted anywhere. Only the leaf certificate and its own key reach the service.
# ══════════════════════════════════════════════════════════════════════════
#
# Usage: issue-service-cert.sh <common-name> <output-dir> [extra-san ...]
#
set -uo pipefail

SB_TAG="issue-cert"
_SB_SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=engine/lib/common.sh
source "${_SB_SELF_DIR}/../lib/common.sh"
sb_trap_err

CN="${1:-}"
OUT_DIR="${2:-}"
shift 2 2>/dev/null || true
EXTRA_SANS=("$@")

[[ -n $CN && -n $OUT_DIR ]] || {
    printf 'usage: issue-service-cert.sh <common-name> <output-dir> [extra-san ...]\n' >&2
    exit 2
}
sb_require openssl

: "${SAMBUCA_APPDATA:=/srv/sambuca/appdata}"
CA_DIR="${SAMBUCA_CA_DIR:-${SAMBUCA_APPDATA}/caddy/data/caddy/pki/authorities/local}"
CA_CRT="${CA_DIR}/root.crt"
CA_KEY="${CA_DIR}/root.key"

DAYS="${SAMBUCA_CERT_DAYS:-825}"   # the maximum most clients still accept

install -d -m 0750 "$OUT_DIR"
CERT="${OUT_DIR}/cert.pem"
KEY="${OUT_DIR}/key.pem"

# Idempotent: do not churn a certificate the service is already serving. Renew
# only when it is close to expiry.
if [[ -s $CERT && -s $KEY ]]; then
    if openssl x509 -in "$CERT" -noout -checkend $((30 * 86400)) >/dev/null 2>&1; then
        log "certificate for ${CN} is present and valid for >30 days — keeping it"
        exit 0
    fi
    log "certificate for ${CN} expires within 30 days — reissuing"
fi

# --- SAN list ---------------------------------------------------------------
# A certificate without a SAN is rejected outright by every modern client;
# CN alone has not been sufficient for years.
san="DNS:${CN}"
for extra in "${EXTRA_SANS[@]}"; do
    [[ -n $extra ]] || continue
    if [[ $extra =~ ^[0-9.]+$ ]]; then san="${san},IP:${extra}"; else san="${san},DNS:${extra}"; fi
done
log "issuing ${CN} (SAN: ${san})"

tmp="$(mktemp -d)"
# shellcheck disable=SC2064
trap "rm -rf -- '${tmp}'" EXIT
chmod 0700 -- "$tmp"

openssl ecparam -genkey -name prime256v1 -out "${tmp}/key.pem" 2>/dev/null \
    || die "could not generate a private key"
chmod 0600 -- "${tmp}/key.pem"

openssl req -new -key "${tmp}/key.pem" -subj "/CN=${CN}" -out "${tmp}/csr.pem" 2>/dev/null \
    || die "could not generate a CSR"

cat >"${tmp}/ext.cnf" <<EXT
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = ${san}
EXT

if [[ -s $CA_CRT && -s $CA_KEY ]]; then
    # Signed by the appliance CA, so any device that already trusts the
    # appliance trusts this service too — no second trust prompt.
    log "signing with the appliance CA"
    openssl x509 -req -in "${tmp}/csr.pem" \
        -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial \
        -out "${tmp}/cert.pem" -days "$DAYS" -sha256 \
        -extfile "${tmp}/ext.cnf" 2>/dev/null \
        || die "signing failed — the CA key may be unreadable or malformed"
    signed_by="appliance CA"
else
    # Caddy materialises its CA on the first TLS handshake, so on a very fresh
    # install it may not exist yet. Self-sign rather than leave the service
    # without TLS; 60-stack reissues on the next run once the CA appears.
    warn "appliance CA not found at ${CA_DIR}"
    warn "  self-signing instead — clients will see an untrusted certificate."
    warn "  Re-run once the CA exists:  sambuca-first-boot --only 60-stack --force"
    openssl x509 -req -in "${tmp}/csr.pem" -signkey "${tmp}/key.pem" \
        -out "${tmp}/cert.pem" -days "$DAYS" -sha256 \
        -extfile "${tmp}/ext.cnf" 2>/dev/null \
        || die "self-signing failed"
    signed_by="self-signed (CA absent)"
fi

install -m 0644 "${tmp}/cert.pem" "$CERT"
install -m 0640 "${tmp}/key.pem"  "$KEY"

ok "issued ${CN} — ${signed_by}, valid ${DAYS} days"
log "  cert: ${CERT}"
log "  key : ${KEY} (0640, never mounted anywhere but this service)"
