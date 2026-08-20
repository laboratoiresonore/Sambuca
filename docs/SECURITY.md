# Security model

This document states what sambuca protects against, what it does not, and the
tradeoffs that were made deliberately. Where a compromise exists, it is named
rather than described in a way that implies it does not.

## Threat model

**In scope**

- A cloud provider reading, scanning, training on, or losing your data — solved
  structurally: the data never leaves the machine.
- Passive network surveillance of your access — all remote access is over
  WireGuard via Tailscale.
- Theft of a **disk** — full-disk LUKS encryption.
- A hostile device on your LAN — services are behind a reverse proxy, databases
  are on a Docker network marked `internal: true` with no route off the host,
  and the host firewall is default-deny inbound.
- Supply-chain compromise via the update channel — the GitOps sync verifies a
  signed tag, refuses changes to sensitive paths without human review, and rolls
  back on a failed health check.
- Data loss from disk failure, ransomware, or fat-fingered deletion — SnapRAID
  parity plus verified encrypted backups plus a deletion threshold that aborts
  a parity sync rather than destroying the recoverable state.

**Explicitly out of scope**

- Theft of the **whole machine while TPM auto-unlock is enabled.** The thief
  powers it on and the disk unlocks. This is inherent to TPM sealing, which is
  why it is opt-in (`engine/autoinstall/luks-tpm-enroll.sh`) and why the script
  says so before enrolling.
- A compromised host kernel or firmware. There is no measured-boot attestation
  chain here.
- A malicious container image. Images come from upstream registries; sambuca
  pins them but does not audit their contents.
- Physical access to a running, unlocked machine.
- Nation-state adversaries with the resources to attack Tailscale's coordination
  plane or your hardware supply chain.

---

## The three honest compromises

### 1. The installer USB is a key

Unattended installation requires the LUKS passphrase to be present in the
preseed on the boot medium. There is no way around this: the disk must be
encrypted before any interactive session exists, and something has to supply the
passphrase.

Encrypting the payload would not help — the decryption key would have to be on
the same stick.

**What is done about it:**

- The recovery document says this in a box on page one, in plain language.
- `first-boot.sh` shreds the payload from the unencrypted boot partition on
  first boot (`shred -u -n 3`).
- `--interactive` mode exists and writes no secret to the stick at all. The
  installer stops once and prompts. The cost is one person standing at the
  machine for ten seconds.

**Use `--interactive` if the stick will be mailed, left in a drawer, or handled
by anyone you would not hand the disk passphrase to.**

### 2. Passkey enrolment is attended

A WebAuthn credential is created by an authenticator touching a browser. No
provisioning script can mint one. Any design that claims fully automated
zero-trust identity has instead placed a password-equivalent bootstrap secret on
the disk, which is strictly worse than an honest manual step.

`80-identity.sh` therefore does everything automatable and then **fails closed**:
until enrolment completes, oauth2-proxy is unhealthy, Caddy cannot reach it, and
gated routes return 502/503. Services with their own authentication (Nextcloud,
Immich, Vaultwarden, Synapse) are unaffected and fully usable.

The one attended step is printed in the completion report and in the MOTD, so it
cannot be missed.

### 3. Docker group membership is root

The admin user is added to the `docker` group so that ordinary operation does not
require `sudo`. On any machine, `docker` group membership is equivalent to root —
`docker run -v /:/host` is all it takes.

For a single-owner appliance where that user is already the person who holds the
LUKS passphrase, this grants nothing they did not already have. `20-docker.sh`
prints a warning when it does this, because the equivalence is frequently not
understood.

If you add a second, less-trusted operator account, do **not** put it in the
`docker` group.

---

## Secret handling

| Secret | Generated | Stored | On the USB? |
|---|---|---|---|
| 24-word seed phrase | flasher, offline | **paper only** | no (SHA-256 only) |
| Root / LUKS passphrase | flasher, offline | paper; LUKS keyslot 0 | yes in unattended mode, no in interactive |
| Disk recovery key | derived from the seed | paper; LUKS keyslot 1 | yes in unattended mode (enrolled, then never needed again) |
| Backup repository password | derived from the seed | `/etc/sambuca/secrets/`, 0600 | no |
| Service passwords, tokens, cookie secrets | on-device, `openssl rand` | `/etc/sambuca/secrets/`, 0600 | no |
| Tailscale auth key | your tailnet admin console | payload, then cleared after use | yes — use a **single-use, tagged, expiring** key |

`payload.py` runs `_assert_no_secrets()` on every build and refuses to write a
payload containing the seed phrase, the root passphrase, or the backup password.
It is a guard that runs unconditionally, not a code comment. `tests/test_keys.py`
proves the guard fires by handing it a deliberately leaking config.

Secrets are never regenerated on a re-run. `secret_get()` in `60-stack.sh`
returns the existing value if one is present — rotating a database password out
from under an initialised database is data loss, and it is the classic
idempotency bug in provisioning scripts.

---

## The disk has two independent keys

A single key that only exists on one sheet of paper is a single point of failure
for every file the owner has. So the disk is opened by either of two secrets,
neither derivable from the other:

| Slot | Secret | Where it comes from |
|---|---|---|
| 0 | root passphrase | random, printed on the recovery sheet |
| 1 | disk recovery key | HKDF from the 24-word seed, also printed |

Slot 1 is enrolled by `engine/autoinstall/enroll-recovery-key.sh`, which runs in
the **installer** rather than in the installed system — the only moment the disk
passphrase is legitimately available (from debconf) without persisting it
anywhere. Enrolment failure is non-fatal: the machine installs with one keyslot,
and both the completion report and the MOTD say so in plain language until it is
fixed with `sambuca-recovery enrol`.

Recompute the key from the seed on any computer, offline:

```bash
sambuca-flasher derive-recovery-key
```

**Test it before you need it.** A keyslot that exists is not a keyslot that
works — a stray newline in a key file or a transcription error off the printed
sheet produces a slot that looks perfect in `luksDump` and opens nothing. This
is exactly why `cryptsetup` reading a key file byte-for-byte is called out in
the enrolment script: a trailing `\n` enrols a passphrase no human can type.

```bash
sambuca-recovery verify
```

That tests the key against the real disk without unlocking it or changing a
byte. Do it while the sheet is still in front of you.

**What this does NOT protect against:** losing the sheet. Both secrets are on
it. That is the deliberate trade — the alternative is an escrow service, which
means someone else holds a key to your disk, which is the arrangement this
appliance exists to escape.

## Network exposure

Nothing is exposed to the public internet by default. There is no port
forwarding, no dynamic DNS, no ACME HTTP challenge reachable from outside.

**Open on the LAN:** 22 (ssh), 80/443 (Caddy), 5353/udp (mDNS), 6667/6697 (IRC).

**Loopback only:** 8443–8452 (the tailnet front door, reachable only by
`tailscaled`).

**Not published at all:** every database, Redis, Ollama, and the Matrix
federation port 8448. Federating Synapse requires publishing 8448 and opening it
in `/etc/nftables.conf` — two deliberate acts, because it makes the machine
reachable from the public Matrix network.

Ollama is on an internal Docker network with no `edge` membership. An
unauthenticated model server reachable from the LAN is a remote code execution
surface dressed up as a convenience.

---

## Reporting a vulnerability

Open a GitHub security advisory at
`https://github.com/laboratoiresonore/Sambuca/security/advisories/new`.

Please do not open a public issue for anything affecting disk encryption, the
provisioning payload, the update channel, or the auth gate.
