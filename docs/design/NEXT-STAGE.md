# Next stage: the three axes

This is a decision document, not a wish list. Every section states what gets
built, what gets rejected, and why. Anything already shipped is marked **DONE**;
anything not yet built says so.

The three axes:

1. **User-friendliness** — take an absolute novice from setup to full use,
   including the whole de-Googling migration, phones and all.
2. **Security** — a state-of-the-art fortress that can also be unbricked by an
   owner who lost the master password, with update control that is rigorous
   about what it is asked to swallow.
3. **Perfected setup** — hardened variants instead of stock components,
   ephemeral by construction, running as lean as the hardware allows.

---

## Axis 1 — user-friendliness

### The split: appliance owns the companion, desktop owns recovery

The hard part of de-Googling is not the server. It is the **fourteen small
migrations afterwards**, and they happen on phones and laptops — not on the
machine that wrote the USB. So the guided companion lives **on the appliance**,
served to any browser, with a checklist that survives closing the tab.

The desktop app therefore keeps exactly one job after the USB is written:
**recovery**. It is the thing you still have when the appliance will not boot.

### DECIDED — the recovery vault

The printed sheet is the primary recovery path and stays primary. But paper gets
lost, and "you lost the sheet, everything is gone" is not an acceptable answer
for this audience. So the desktop app keeps an **encrypted copy of the key
material**, unlocked by answering three questions.

**The shape:**

```
~/.sambuca/vault/recovery-<fingerprint>.json
```

- Encrypts the seed phrase, root passphrase and disk recovery key.
- Key = `scrypt(normalised answers, salt)` — memory-hard, and in the **standard
  library** (`hashlib.scrypt`), so this costs the flasher one new dependency
  (an AEAD) rather than a crypto stack.
- `n=2**20, r=8, p=1` → roughly 1 GB and several seconds per attempt. Slow is
  the point: the input is low-entropy, so the KDF has to do the work the answers
  do not.
- AEAD (AES-256-GCM). The auth tag tells you the answers were right; there is no
  separate answer-hash to verify guesses against cheaply.
- The vault is portable. It is a file. Copy it to a second machine, a USB stick,
  wherever — the companion will nag until it is in two places.

**Answer normalisation is versioned and load-bearing.** Unicode NFKD, strip
combining marks, casefold, collapse internal whitespace, strip surrounding
punctuation. Get this wrong and a correct answer fails to decrypt years later.
It is pinned by a test vector like the key derivations already are.

**Questions are free-form, not from a list.** "Mother's maiden name" is a public
record. The app asks for three facts that will not change and are not on your
social media, shows a live strength estimate, and refuses answers under a
minimum length. It never stores the answers — only the derived key is used, and
it is discarded immediately.

**THE HONEST WARNING, stated in the app and not buried:** this vault is a second
complete copy of every secret on the machine. Someone who steals the laptop
**and** guesses the three answers has the disk. That is why the KDF is
deliberately brutal and why the app pushes for answers with real entropy. It is
opt-in, it can be deleted, and the sheet alone remains sufficient without it.

**Rejected:** any escrow that leaves the user's own machines. The moment a
recovery key is recoverable by someone else's server, this appliance has
recreated the arrangement it exists to escape.

### DECIDED — email: inbound sovereign, outbound relayed

The requirement is that the user forwards their mail to the new setup and
"everything works perfectly downstream", with the option to pull everything down
and delete it from the server — and without the Google Cloud Console, which is
genuinely unusable for a normal person.

**The insight that makes this easy: if the appliance continuously drains the
mailbox, the provider is a transport, not a store.** Mail lands there and is
gone within minutes. That changes the whole calculus:

- Free tiers become viable, because storage never accumulates.
- Provider trust matters far less, because nothing sits there to be mined.
- Switching providers later is trivial — the archive is already yours.

**The architecture:**

| Direction | Path | Why |
|---|---|---|
| Inbound | provider → `mbsync` → local Maildir → Dovecot | you own the archive |
| Read | Dovecot → webmail + any phone IMAP client over Tailscale | works everywhere |
| Outbound | appliance → provider's SMTP submission | deliverability you cannot self-host |

We do **not** run an MX. Home-IP reputation is not controllable, and a mail
server that silently drops mail is worse than no mail server. Sovereignty over
the *archive* is the achievable and valuable half; sovereignty over *delivery*
is a fight with the entire anti-spam ecosystem that the owner would lose.

**The Google problem, solved without the API console.** Gmail needs an **App
Password** — enable 2FA, generate a 16-character password, paste it in. Six
clicks, no Cloud project, no OAuth consent screen, no verification review. The
companion walks it with screenshots. Bulk history comes from Takeout as mbox,
imported locally. The Cloud Console never appears.

**Provider choice is a curated shortlist with honest notes, not a hardcoded
pick** — because "free, trustworthy, and real IMAP" is a genuinely thin set and
the honest ones have caveats (Proton and Tuta do not offer plain IMAP; the
donation-funded providers are wonderful and fragile; the free tiers of large
providers are neither private nor going away). Since the appliance drains the
mailbox anyway, the shortlist can favour "reliable pipe" over "trusted vault",
which is a much easier bar to clear — and the companion says exactly that rather
than pretending one of them is a privacy haven.

**Optional and clearly labelled: drain-and-delete.** After a verified local copy
exists — verified, not assumed — the appliance can delete from the server. Off
by default, with a dry run first, because it is irreversible.

### DECIDED — Signal and WhatsApp alongside IRC

Encrypted comms that only talk to other sambuca owners are a toy. The appliance
already runs Synapse, so the bridges go there and everything lands in one
client:

| Network | Bridge | Status |
|---|---|---|
| Signal | `mautrix-signal` | links as a secondary device |
| WhatsApp | `mautrix-whatsapp` | multi-device web protocol |
| IRC | `heisenbridge` | Ergo and any external network |

**Two things must be said plainly rather than discovered later:**

**WhatsApp bridging violates WhatsApp's terms of service and carries a real, if
small, risk of account suspension.** Meta bans unofficial clients periodically.
The bridge is off by default, and enabling it shows that sentence — not a
euphemism about "unofficial APIs" — before the QR code appears. That is the
owner's call to make, but they get to make it informed.

**Bridges terminate end-to-end encryption.** Messages are decrypted at the
bridge to be re-encrypted into Matrix. On a hosted bridge that means somebody
else reads your messages; here the bridge is **your own hardware, inside your
own tailnet, on an encrypted disk**. That is a genuine advantage over every
hosted bridge — and it is still a change to the threat model that gets stated,
not glossed.

**Bridges are the highest-maintenance component in the entire appliance.** They
break when upstream protocols change, which is often. They are their own bundle,
excluded from unattended updates, and monitored — so a broken bridge surfaces as
an alert rather than as messages that quietly stopped arriving.

---

## Axis 2 — security

### DONE — the disk has two independent keys

Previously the root passphrase was the only thing that opened the disk: forget it
and every file was gone permanently. Now a seed-derived recovery key occupies a
second LUKS keyslot, enrolled during installation, and `sambuca-recovery verify`
proves it works *before* it is needed. Detail in [SECURITY.md](../SECURITY.md).

The recovery vault above adds a third path that does not depend on paper at all.

### Update control — what the appliance refuses to swallow

`gitops-sync.sh` already verifies a signed tag, refuses changes under
`autoinstall/`, `provision/40-`, `provision/50-` and `maintenance/backup`, and
rolls back on a failed health check. Next:

- **Size and shape limits.** Reject a diff over a threshold, one that adds a
  binary, or one that grows the repository by more than a set factor. A
  legitimate config update is small; a 40 MB "update" is a payload.
- **Secret scanning of the incoming diff.** Refuse anything shaped like a
  private key or a credential, whether it is an attack or an upstream mistake.
- **No new network egress without review.** An update adding a URL, a registry
  or an outbound host is held for a human. Highest-signal check available:
  supply-chain compromise almost always needs to phone somewhere.
- **Image digest drift.** Once pinned, a changed digest is a reviewable act.
- **Rollback proven, not assumed** — exercised in CI against a deliberately
  poisoned update, because a recovery path nobody has run does not work.

### Rescue mode

Boot the same USB, unlock with any of the three secrets, get a menu: repair the
bootloader, re-run a provisioning phase, copy the data off, reset the
passphrase. Driven by the desktop app, which by then is the recovery tool.

---

## Axis 3 — perfected setup

### DECIDED — ephemerality by container lifecycle, not by timer

Containers are the right mechanism, and they are stronger than the tmpfs-plus-
purge-timer this document originally proposed. A timer is a promise; a
destroyed container is a fact.

**The generative session model:**

- One **disposable container per session**, `--rm`, `read_only: true` rootfs.
- Inputs and outputs on **tmpfs inside that container**. The session ends, the
  container dies, and the I/O is gone by construction — no cleanup job to fail
  silently, nothing to forget, nothing left if the power is cut.
- **Models on a read-only bind mount**, shared across sessions. They are the
  only large, reusable state, and nothing running in the session can modify
  them.
- **No network namespace access at all.** Models are fetched and verified during
  provisioning. A workflow cannot phone home because there is nowhere to phone
  from.
- **No runtime node installation.** Nodes are pinned at image build time. A
  generative server that can `pip install` from a workflow file is remote code
  execution with a nice UI.

The cost is honest: **cold model load per session**, tens of seconds for a large
checkpoint. Mitigated by keeping the model store hot in page cache and, on tier‑1
hardware, an optional warm session that still dies on logout. The trade is
deliberate — a few seconds of load in exchange for "your client's document is
not sitting in an output folder six months later" is the correct side of that
bargain for this machine.

This model generalises. Any component whose defaults keep data around gets the
same treatment rather than a bespoke cleanup script.

### Lean by default; spare memory becomes storage

- **Nothing idles that was not asked for** — on-demand start for heavy, rarely
  used services, so a tier-4 box is not holding a diffusion server resident to
  serve nobody.
- **Spare RAM becomes cache and scratch, deliberately sized** by
  `hardware-detect.sh`, with a hard floor reserved so a large render cannot
  squeeze Postgres into the OOM killer.
- **zram before disk swap** on low-tier boxes.
- **Measured, not asserted** — the memory plan is emitted into `profile.env`, so
  "lean" is a value you can check.

---

## Sequence

1. **DONE** — disk recovery keyslot, `sambuca-recovery {status,enrol,verify}`.
2. **Recovery vault** — scrypt + AEAD, pinned normalisation vector. Self-
   contained, and it completes the recovery story.
3. **Update-control hardening** — the appliance is already pulling updates
   unattended, so this is the most urgent unbuilt security work.
4. **Companion substrate + CA install flow** — the wall every novice hits first;
   everything else hangs off the checklist.
5. **Mail** — mbsync/Dovecot/webmail, App Password flow, Takeout import,
   drain-and-delete behind a dry run.
6. **Companion migrations** — contacts, calendar, photos, passwords, files.
7. **Bridges** — Signal, WhatsApp, IRC, with the ToS warning surfaced.
8. **Rescue mode** — completes the unbricking story.
9. **Ephemeral generative bundle** — per-session containers.
10. **Memory plan** — tmpfs sizing, zram, on-demand services.
11. **Flasher GUI** — last; the CLI works, and a window is the least
    load-bearing thing on this list.
