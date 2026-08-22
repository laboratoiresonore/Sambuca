# shellcheck shell=bash
# sambuca :: phase 90-report — the completion report.
#
# This is the last thing the owner sees, and for many it is the only thing they
# will read. It states what exists, where it is, what is NOT yet done, and how to
# recover. It contains no secret VALUES — only the paths where they live.

# shellcheck source=/dev/null
for f in provision.env profile.env storage.env network.env; do
    [[ -r "${SB_ETC}/${f}" ]] && source "${SB_ETC}/${f}"
done

: "${SAMBUCA_DOMAIN:=sambuca.local}"
: "${SAMBUCA_HOSTNAME:=sambuca}"

COMPOSE_DIR="${SAMBUCA_COMPOSE_DIR:-/opt/sambuca/compose}"
REPORT="${SB_LIB}/completion-report.txt"

lan_ip="$(sb_env_get "${SB_ETC}/network.env" SAMBUCA_LAN_IP "unknown")"
ts_name="${SAMBUCA_TS_DNSNAME:-}"
enrolled="$(jq -r '.enrolled // false' "${SB_LIB}/identity.json" 2>/dev/null || echo false)"
# THE REPORT IS 0644 AND THE MOTD CATS IT AT EVERY LOGIN, so the one-time admin
# link must never appear in it. identity.json now carries a flag instead of the
# credential; the report points at the root-only command that prints it.
setup_link_available="$(jq -r '.setup_link_available // false' "${SB_LIB}/identity.json" 2>/dev/null || echo false)"

ca_fpr="unavailable"
if [[ -r "${SB_LIB}/sambuca-local-ca.crt" ]]; then
    ca_fpr="$(openssl x509 -in "${SB_LIB}/sambuca-local-ca.crt" -noout -fingerprint -sha256 2>/dev/null \
        | cut -d= -f2 || echo unavailable)"
fi

running="$(cd "$COMPOSE_DIR" && docker compose ps --format '{{.Name}}' 2>/dev/null | wc -l || echo 0)"
unhealthy="$(cd "$COMPOSE_DIR" && docker compose ps --format '{{.Name}}\t{{.Health}}' 2>/dev/null \
    | awk -F'\t' '$2=="unhealthy"{print "  - "$1}' || true)"

{
cat <<EOF
═══════════════════════════════════════════════════════════════════════════
  SAMBUCA — provisioning complete
  $(date -u +%Y-%m-%dT%H:%M:%SZ)   host: ${SAMBUCA_HOSTNAME}
═══════════════════════════════════════════════════════════════════════════

HARDWARE PROFILE
  tier            ${SAMBUCA_TIER:-?} (${SAMBUCA_TIER_NAME:-unknown})
  reason          ${SAMBUCA_TIER_REASON:-n/a}
  cpu             ${SAMBUCA_CPU_MODEL:-unknown} — ${SAMBUCA_CPU_CORES:-?} cores
  memory          ${SAMBUCA_RAM_TOTAL_MB:-?} MiB
  gpu             ${SAMBUCA_GPU_NAMES:-none} (${SAMBUCA_VRAM_TOTAL_MB:-0} MiB VRAM)
  runtime         ${SAMBUCA_GPU_PROFILE:-cpu}
  chat model      ${SAMBUCA_MODEL_CHAT:-none}
  photo indexing  ${IMMICH_ML_DEVICE:-cpu}   ← GPU is reserved for inference below 20 GiB VRAM

STORAGE
  data root       ${SAMBUCA_DATA:-/srv/sambuca}
  pool            ${SAMBUCA_POOL:-none}
  free            ${SAMBUCA_DISK_FREE_MB:-?} MiB

ACCESS
  local           https://${SAMBUCA_DOMAIN}/            (ip: ${lan_ip})
EOF

if [[ -n $ts_name ]]; then
    printf '  remote          https://%s/            (tailnet, publicly trusted cert)\n' "$ts_name"
else
    printf '  remote          NOT CONFIGURED — run: tailscale up --hostname=%s --ssh\n' "$SAMBUCA_HOSTNAME"
fi

cat <<EOF

  Local HTTPS uses a private certificate authority. Install this root cert on
  every device that will use the LAN address, or your browser will warn on
  every visit:
      ${SB_LIB}/sambuca-local-ca.crt
      SHA-256  ${ca_fpr}
  Remote access over Tailscale needs no such step.

SERVICES        ${running} container(s) running
  dashboard     https://${SAMBUCA_DOMAIN}/
  chat / agents https://chat.${SAMBUCA_DOMAIN}/
  files         https://cloud.${SAMBUCA_DOMAIN}/
  photos        https://photos.${SAMBUCA_DOMAIN}/
  passwords     https://vault.${SAMBUCA_DOMAIN}/
  notes         https://notes.${SAMBUCA_DOMAIN}/
  pdf           https://pdf.${SAMBUCA_DOMAIN}/
  chat (irc)    ircs://irc.${SAMBUCA_DOMAIN}:6697
  matrix        https://matrix.${SAMBUCA_DOMAIN}/
  monitoring    https://status.${SAMBUCA_DOMAIN}/
  identity      https://id.${SAMBUCA_DOMAIN}/
EOF

if [[ -n $unhealthy ]]; then
    printf '\n  UNHEALTHY:\n%s\n' "$unhealthy"
    printf '  inspect: docker compose -f %s/docker-compose.yml logs <name>\n' "$COMPOSE_DIR"
fi

cat <<EOF

CREDENTIALS
  Generated on this device, never transmitted, never in the repository:
      ${SB_ETC}/secrets/            (0600, root only)
  The rendered service environment:
      ${COMPOSE_DIR}/.env           (0600, root only)
  Your 24-word seed and root passphrase exist ONLY on the recovery document
  the flasher produced. They were never written to this machine in plaintext.

EOF

if [[ $enrolled != "true" ]]; then
# Two genuinely different situations, and telling them apart matters: pointing
# somebody at a command that will say "nothing stored" is the kind of dead end
# this project keeps removing.
if [[ $setup_link_available == "true" ]]; then
    step_one="run as root:  sambuca-identity setup-link
          then open the one-time link it prints"
else
    step_one="no one-time link was captured — Pocket ID may already be set up.
          On a fresh install:  docker logs sambuca-pocket-id 2>&1 | grep -i setup"
fi

cat <<EOF
OUTSTANDING — ONE ATTENDED STEP
  Passkey enrolment cannot be automated. Until it is done, the zero-trust gate
  fails closed and gated routes return 503. Services with their own login work
  right now.

      1.  ${step_one}
      2.  register your passkey
      3.  create an OIDC client (callback https://auth.${SAMBUCA_DOMAIN}/oauth2/callback)
      4.  sambuca-identity set-client <client-id>

EOF
fi

keyslots="$(lsblk -rno NAME,FSTYPE 2>/dev/null | awk '$2=="crypto_LUKS"{print "/dev/"$1; exit}')"
if [[ -n $keyslots ]]; then
    n="$(cryptsetup luksDump "$keyslots" 2>/dev/null \
        | grep -cE '^[[:space:]]+[0-9]+: luks2|^Key Slot [0-9]+: ENABLED' || echo 0)"
    if [[ $n -lt 2 ]]; then
cat <<EOF
⚠  THIS DISK HAS ONLY ONE KEY

  The recovery keyslot was not enrolled, so the root passphrase is the only
  thing that opens this disk. If it is lost, every file here is gone
  permanently — there is no reset and no support line.

  Fix it now, it takes ten seconds:
      sambuca-recovery enrol        (the key is on your recovery sheet)
      sambuca-recovery verify       (prove it actually works)

EOF
    else
cat <<EOF
RECOVERY
  This disk has ${n} keyslots: the root passphrase AND the seed-derived
  recovery key on your sheet. Either one opens it; losing one is survivable.

  Test it now, while the sheet is in front of you:
      sambuca-recovery verify

EOF
    fi
fi

if [[ -f "${SB_LIB}/reboot-required" ]]; then
cat <<EOF
REBOOT REQUIRED
  The GPU driver was installed but its kernel module is not loaded. The stack is
  currently running on the CPU path. After rebooting:
      sambuca-first-boot --from 30-gpu-runtime --force

EOF
fi

# THE PARTS THAT DID NOT WORK, SAID PLAINLY.
#
# Both of these used to be `die` — the machine simply stopped, and the owner got
# no report at all. Now provisioning finishes and these say what is missing. A
# marker nobody reads would be no better than the die it replaced, so this is
# the other half of that change rather than a nicety.
if [[ -f "${SB_LIB}/gpu-degraded" ]]; then
cat <<EOF
GRAPHICS CARD NOT IN USE
  $(head -n1 "${SB_LIB}/gpu-degraded" 2>/dev/null)

  Your machine has a graphics card, and everything below is running on the
  processor instead. Nothing is broken — it is slower for the AI half only, and
  the rest of the appliance is unaffected.

  Most often this is a driver that needs a reboot, a machine with Secure Boot
  on, or no route to the driver repository. To retry:
      sambuca-first-boot --only 30-gpu-runtime --force

EOF
fi

if [[ -f "${SB_LIB}/chat-model-missing" ]]; then
cat <<EOF
THE AI ASSISTANT IS NOT INSTALLED
  $(head -n1 "${SB_LIB}/chat-model-missing" 2>/dev/null)

  Everything else on this page is up and working. The chat model did not
  download — usually a temporary network or registry problem, occasionally a
  full disk. To try again:
      sambuca-first-boot --only 70-models --force

EOF
fi

cat <<EOF
MAINTENANCE (already scheduled)
  nightly 02:00   configuration sync from github.com/laboratoiresonore/Sambuca
  nightly 03:00   encrypted backup (restic)
  weekly  04:00   snapraid sync + scrub
  continuous      container updates, patch-level only, opt-in per service

  status      systemctl list-timers 'sambuca-*'
  logs        ${SB_LOG_DIR}/sambuca.log
  re-run      sambuca-first-boot --list

═══════════════════════════════════════════════════════════════════════════
EOF
} | sb_atomic_write "$REPORT" 0644

# Greet the console on every login, not just this one.
{
    printf '#!/bin/sh\n'
    printf 'cat %s 2>/dev/null || true\n' "$REPORT"
    # OUTSTANDING PROBLEMS GO LAST, so they are what is left on screen rather
    # than scrolled past. A failing backup writes a file for this to find; the
    # journal it currently shouts into is not read on an appliance.
    printf '[ -x /usr/local/bin/sambuca-health ] && /usr/local/bin/sambuca-health --brief 2>/dev/null\n'
    printf 'true\n'
} | sb_atomic_write /etc/update-motd.d/99-sambuca 0755
rm -f /etc/motd 2>/dev/null || true

ok "completion report written: ${REPORT}"
