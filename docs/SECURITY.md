# Security model

<p align="center">
  <img src="../assets/brand/fortress-shielded-server.webp" width="820"
       alt="A single server on a stone plinth inside a blue spherical shield, red attack traces breaking apart against the barrier.">
</p>

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

In unattended mode the stick therefore carries three secrets, not one:
`preseed.cfg` holds the LUKS passphrase, `luks-recovery.key` holds the
seed-derived recovery key, and `restic-password.key` holds the backup
repository password.

**And nothing used to take them back.** `first-boot.sh` shreds
`/boot/sambuca/provision.json` — the copy on the INSTALLED machine's boot
partition. No code in the engine writes to `/cdrom` at all. Until 2026-08-22 the
flasher told owners "the appliance erases it on first boot", on the console and
on the printed recovery sheet, which invited them to stop treating a key like a
key. Both now say the opposite.

**What is done about it:**

- The recovery document says this in a box on page one, in plain language: the
  USB stays a key, nothing erases it, reformat it or keep it as safely as the
  sheet.
- `sambuca-flasher handover` offers to remove the secrets once real services
  answer on the network — the first moment the stick is provably spent. It
  identifies the volume by the payload marker and the appliance's fingerprint,
  never by "is it removable", and it removes the four secret-bearing files
  rather than reformatting, so the stick stays usable.
- It states the limit rather than overclaiming: on flash storage, wear levelling
  means no overwrite can promise the old blocks are unrecoverable. It defeats
  undelete, which is the realistic threat for a stick in a drawer. For anything
  more serious, destroy the stick.
- `first-boot.sh` shreds the payload from the unencrypted boot partition on
  first boot (`shred -u -n 3`). That is about the appliance, not the stick.
- `--interactive` mode exists and writes no secret to the stick at all. The
  installer stops once and prompts. The cost is one person standing at the
  machine for ten seconds — and one consequence, stated under "Secret handling"
  below: the backup password is then generated on the appliance instead of
  derived, so the 24 words will not recover that repository.

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
| Backup repository password | derived from the seed (unattended) / `openssl rand` on the appliance (interactive) | `/etc/sambuca/secrets/`, 0600 | yes in unattended mode, as its own file — see below |
| Service passwords, tokens, cookie secrets | on-device, `openssl rand` | `/etc/sambuca/secrets/`, 0600 | no |
| Tailscale auth key | your tailnet admin console | payload, then cleared after use | yes — use a **single-use, tagged, expiring** key |

`payload.py` runs `_assert_no_secrets()` on every build and refuses to write a
payload containing the seed phrase, the root passphrase, or the backup password.
It is a guard that runs unconditionally, not a code comment. `tests/test_keys.py`
proves the guard fires by handing it a deliberately leaking config.

### The backup password, and why it is on the stick at all

It has to reach the appliance, and until 2026-08-22 it never did. `keys.py`
derived it from the seed and warned that changing the derivation "would orphan
every existing backup"; `sambuca-flasher derive-backup-key` offered to recover it
from the 24 words; the README called it a design commitment. Meanwhile
`backup.sh` found no password file on the machine and generated a random
48-character one — so every archive was encrypted with a secret that existed in
exactly one place, the disk being backed up. Losing the machine, which is the
event backups exist for, lost the backups, while the recovery tool printed a
password that opened nothing.

It travels as `restic-password.key`, its own file beside `luks-recovery.key`, and
**not** inside `provision.json` — the guard above is right to refuse it there,
because `provision.json` rests on the unencrypted boot partition until first boot
shreds it. `late-command.sh` runs in-target, so it writes the password straight
to `/etc/sambuca/secrets/restic_password` on the ENCRYPTED root; it never touches
the unencrypted partition at all.

This widens what the stick carries and not what an attacker gains: anyone holding
that stick already has the LUKS passphrase and the disk recovery key, so they
already have the disk. What it buys is that the printed sheet alone can restore
the backups on a machine that has never seen this one — which is what the
documentation had been claiming.

**In `--interactive` mode none of this applies.** No secret goes on the stick, so
`backup.sh` generates its own password and says so at generation time: the 24
words will not recover that repository, and the only copy is
`/etc/sambuca/secrets/restic_password`. Copy it somewhere else.
`derive-backup-key` states the same fork rather than printing a password that
might be the wrong one.

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

> **5353 is open and nothing is listening on it.** The rule was added for
> `sambuca.local` discovery, but no mDNS responder is installed by the preseed,
> by `10-system.sh`, or by Debian's `standard` task — so the name resolves
> nowhere, and the port accepts traffic for a service that does not exist. It is
> inbound-only on a default-deny ruleset and reaches no process, so it is not a
> hole; it is an accurate signal that the LAN-only naming story is unfinished.
> See ROADMAP.md — installing a responder would not be enough on its own,
> because the addresses handed out are per-service SUBDOMAINS and mDNS publishes
> a host, not a zone.

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

## Container hardening, as measured rather than as intended

Counted from `compose/*.yml` on 2026-08-22 — **20 services**:

| Control | Coverage | Note |
|---|---|---|
| `no-new-privileges` | **19 / 20** | one named exception, below |
| `cap_drop` | **0 / 20** | not started; needs hardware |
| `read_only` rootfs | **1 / 20** | `bentopdf` — it serves static assets and has no reason to write |

**The exception is `nextcloud-aio`, and it is a gap rather than a decision.** It
holds the docker socket and spawns the entire Nextcloud deployment, so a wrong
guess there does not degrade a feature — it removes the file server. Upstream's
own `compose.yaml` sets no `security_opt` at all, and the only one it mentions is
`label:disable` for SELinux, which loosens rather than tightens, so there is no
upstream answer to copy either way. The reason and the test to run are written
above the service in `compose/cloud.yml`, and
`tests/test_axis3_properties.py` asserts it as a *named* exception — an
unexplained absence in a security posture gets "fixed" blind by whoever notices
it next, or left forever.

**`cap_drop` and `read_only` are deliberately not applied more widely yet.**
`cap_drop: [ALL]` removes the `CHOWN`/`SETUID`/`SETGID`/`FOWNER` that the
Postgres images use to drop privileges, the `NET_BIND_SERVICE` Caddy needs for
:443, and Watchtower's socket access. Each of those turns a running appliance
into a stopped one, and none of it is verifiable from a machine that has never
booted the stack. Shipping untested hardening into the substrate is the failure
this project's status discipline exists to prevent, so it waits for hardware.

**What holds the line meanwhile is a ratchet.** `READ_ONLY_TODAY` and
`CAP_DROP_TODAY` in `tests/test_axis3_properties.py` record what is hardened now
and fail if any of it disappears; rule 7 of the update guard does the same for
nightly updates. Hardening therefore lands one verified service at a time rather
than as one risky sweep, and cannot silently regress in between.

---

## Known weaknesses, named rather than buried

Every external coupling is registered in [MAINTENANCE.md](MAINTENANCE.md). Those
carrying security weight are stated here so they are not discovered later —
**including the ones since fixed**, which stay on the page with what they were.
A security document that quietly deletes its own history reads as though nothing
was ever wrong, and the reader has no way to judge whether the remaining entries
are the whole list or merely the ones not yet found.

Two live, three fixed:

**Fixed 2026-08-20 — the CasaOS installer was unverified remote code.** It was
`curl … | bash` as root, unpinned and unsigned: the only remote-execution point
in the project without verification, and whoever controlled that URL controlled
every appliance at install time, as root, at the moment the machine has the
fewest defences up. It is now **downloaded, SHA-256 verified against a pin, and
only then run** — never piped into a shell (`50-network.sh`,
`CASAOS_INSTALLER_SHA256`). The endpoint was checked byte-stable across repeated
fetches before pinning, because an endpoint that varies per request cannot be
pinned at all and discovering that after shipping would have broken every
install. `tools/check-upstreams.py` reports the hash daily, so drift shows up in
a routine run rather than as a failed provision.

The residual: a pin is only as good as its rotation. If upstream changes the
installer legitimately, provisioning fails closed with the new hash printed and
the file to edit named — loud, and a manual step. CasaOS remains optional; an
operator who wants no third-party installer at all can drop `casaos` from
provisioning, and Caddy serves every service regardless.

**Bridges terminate end-to-end encryption**, and WhatsApp bridging breaches
Meta's terms of service with a real if small risk of account suspension. Both
facts are shown to the owner in those words before pairing. The bridge running
on the owner's own encrypted hardware is a genuine improvement over a hosted
one — and still a change to the threat model.

**Pocket ID's one-time setup token is scraped from container logs.** A log
format change breaks identity bootstrap silently. A log line is not an API; this
is the most fragile coupling in the repository. It is still a scrape — surfacing
the token upstream already emits beats inventing a parallel bootstrap path with
weaker properties — so the fragility stands.

**Fixed 2026-08-20 — that token was being treated as status, not as a
credential.** Whoever opens the setup link becomes the FIRST ADMIN of the
identity provider gating every other service, and it was landing in four places
at once: `identity.json` (0644), the completion report (0644), `/var/log/sambuca`
(0755 directory), and — because the MOTD cats that report — on the screen of
every user at every login, for as long as enrolment stayed outstanding. It now
goes to exactly one root-only file (`/etc/sambuca/secrets/pocket_id_setup_url`,
0600) and everything else points at the command that reads it. Root-only **by
file permission, not by politeness**: a non-root caller gets a permission error
rather than a credential.

**Fixed 2026-08-20 — CA key exposure.** Ergo was mounted Caddy's CA directory
and configured to serve `root.crt`/`root.key`. That gave a chat container the CA
**private key**: one container escape would have yielded the ability to mint
certificates trusted by every device the owner had onboarded, for every service
on the appliance. It was also non-functional — a CA root has no hostname SAN.
Services that cannot get a certificate from Caddy now receive a dedicated leaf
from `issue-service-cert.sh`, and **no container is given the CA key, ever.**

## Reporting a vulnerability

Open a GitHub security advisory at
`https://github.com/laboratoiresonore/Sambuca/security/advisories/new`.

Please do not open a public issue for anything affecting disk encryption, the
provisioning payload, the update channel, or the auth gate.
