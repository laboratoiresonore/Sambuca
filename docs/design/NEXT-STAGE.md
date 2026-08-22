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

### DECIDED — health alerting, and why automatic shutdown is NOT the default

The flasher already collects contact details, so alerting is wired in by default
rather than being a thing the owner discovers they never set up. It rides the
same SMTP submission credentials the mail stack uses — no new egress path, no
third-party notification service, nothing that requires an account anywhere.

Monitored: disk space and SMART health, memory pressure, temperature, failed
logins, backup outcome, **engine-tree integrity**, and **firewall ruleset
drift** — alongside the service healthchecks Uptime Kuma already runs.

**The response ladder, and the argument for it.**

The instinct to shut the machine down on anomaly is the right instinct pointed
at the wrong action. Two things make automatic shutdown the worst available
response:

1. **A shut-down appliance cannot be restarted remotely.** A false positive at
   2am becomes a self-inflicted outage that ends only when someone physically
   walks to the machine — while their photos, passwords and documents are
   offline. The "safety" measure causes the harm.
2. **Autonomous self-healing acting on false positives is a documented,
   repeatedly-observed failure mode**, not a hypothetical. Guards that restart
   healthy services on a bad signal, and patches that fight the fix they were
   meant to apply, are how this class of automation actually behaves in the
   field.

So responses are graduated, and **severity is matched to confidence**:

| Confidence | Example | Response |
|---|---|---|
| Informational | disk 80% full; partial backup | alert only |
| Degraded | service unhealthy; bridge down; backup failed | alert + **one** bounded restart, never a loop |
| Suspicious | new outbound host; unexpected listening port; burst of failed logins | alert + tighten — rate-limit or block that flow |
| High-confidence compromise | engine tree modified; a LUKS keyslot we did not add; a new key in authorized_keys; firewall ruleset replaced | **ISOLATE** — drop everything but the tailnet. Fully reversible, and the owner can still get in to look. |
| Irreversible threat only | repeated failed unlock attempts suggesting the machine is not where it should be | power off — **after** an alert with a hold window the owner can cancel |

**Isolate, not shutdown, is the top of the automatic ladder.** It stops an
attacker's traffic, preserves forensic state, keeps the data intact, and leaves
the owner a way in. Shutdown throws away the running state that would explain
what happened and guarantees a physical trip.

**Firewall failure is a repair, not a shutdown.** If the ruleset fails to load
or has drifted from what we wrote, the response is to re-apply the known-good
ruleset — and only if *that* fails, isolate.

**Every automatic action is announced and reversible**, and every alert says
what was done, why, and the exact command to undo it. An appliance that acts
without telling you is the thing this project exists to replace.

### Rescue mode

Boot the same USB, unlock with any of the three secrets, get a menu: repair the
bootloader, re-run a provisioning phase, copy the data off, reset the
passphrase. Driven by the desktop app, which by then is the recovery tool.

---

## Axis 3 — perfected setup

### HOW AMBITIOUS SHOULD THE SUBSTRATE BE? — the decision that shapes the rest

The tempting answer to "make it a titan" is orchestration: k3s instead of
Compose, Proxmox and VMs, an immutable OS, a service mesh with mTLS everywhere.
**That is rejected, and the reason matters more than the conclusion.**

Complexity is only free when it stays hidden. k3s does not stay hidden. It
surfaces the moment anything goes wrong, on a reclaimed office PC, in front of
someone who left Google because the alternative "required compiling kernels in a
terminal". It adds failure modes that need a specialist to diagnose, eats memory
that a tier-4 machine does not have, and makes the recovery story — the thing
principle 2 says must always work — dramatically worse. A cluster orchestrator
on a single box buys resilience the box cannot use and costs recoverability the
owner desperately needs.

**So the ambition goes into hardening, not orchestration.** The test for any
"titan" feature is one question:

> Does the owner ever have to know it is there?

Yes to everything that passes:

| Ambition that stays invisible | What it buys |
|---|---|
| Drop **all** capabilities, re-add only what each service needs | a compromised container cannot mount, trace or raw-socket |
| `read_only` rootfs + explicit tmpfs | malware cannot persist in the container it landed in |
| User-namespace remapping | container root is not host root |
| **Secrets as files, not environment variables** | `docker inspect` and `/proc/<pid>/environ` stop being credential dumps |
| Per-image CVE scanning, in CI and on-device | the single highest-signal "is this actually secure" check available |
| Per-session ephemeral containers | nothing to exfiltrate later, because nothing survives |
| Signed-tag updates with diff review and proven rollback | supply chain, which is the realistic attack |

No to everything that fails it: cluster orchestration, VM-per-service,
custom Secure Boot keys, a service mesh. Each is defensible on a rack. On one
reclaimed desktop with one owner, each trades a large amount of recoverability
for a small amount of isolation the threat model does not need.

**The one genuine gap this exposes, found while writing it down:** service
secrets are currently passed as **environment variables**. Anyone who can run
`docker inspect`, read `/proc/<pid>/environ`, or receive a crash dump gets the
database passwords. That undercuts the fortress claim more than any missing
orchestrator does, and it is fixed by configuration rather than architecture —
exactly the kind of ambition worth having.


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

### DECIDED — stock Tor Browser, one click, ephemeral

A slow but anonymous browser, always one click away from the control panel. It
belongs here because it is the perfect example of the axis-3 rule: a thing that
is genuinely annoying to set up correctly, and trivial once someone has done it
properly on your behalf.

**STOCK AND UNMODIFIED IS THE SECURITY REQUIREMENT, not a shortcut.** Tor
Browser's protection comes from every user presenting an *identical*
fingerprint. A customised build — different fonts, a tweaked window size, an
added extension, a "helpful" default — makes its user more identifiable, not
less. The single most damaging thing this project could do here is improve it.

So:

- **The official binary**, fetched at image-build time and **verified against
  the Tor Project's signing key**. Not a repackage, not a fork, not a patch.
- **Ephemeral by construction**, exactly like the generative stack: a
  per-session container, the browser profile on tmpfs, destroyed on exit. No
  history, no cookies, no cache surviving the session — which is the behaviour
  Tor Browser wants anyway.
- **No shared state with anything else on the appliance.** Its own volume-less
  container, its own network namespace, egress only through Tor.
- **Delivered over the tailnet**, so the LAN leg is encrypted and the appliance
  is not exposing a remote-desktop surface to the local network.

**What it honestly gives you, and what it does not.** It anonymises you from the
destination site: the site sees a Tor exit, not your address, and each session
starts clean. It is *not* the full Tor Browser threat model against a global
passive adversary, because you are driving it remotely rather than running it on
the machine in front of you. For "read something without it being logged against
me", it is exactly right. For "my life depends on this", use Tor Browser on a
laptop, on Tails, and do not take routing advice from a README.

That paragraph ships with the feature. A privacy tool that overstates itself is
worse than no privacy tool, because people calibrate their behaviour to what
they were told.

#### What this needs before anyone starts — checked 2026-08-22

**Verified upstream, so the build does not begin by guessing:**

- The signing identity is **Tor Browser Developers (signing key)
  `<torbrowser@torproject.org>`**, fingerprint
  `EF6E286DDA85EA2A4BA7DE684E2C6E8793298290`. Pin the fingerprint, not the
  key file: a key fetched at build time and trusted because it was fetched is
  not verification, it is a longer download.
- Every download is accompanied by a detached `.asc` with the same name, on the
  same page. So the verify step is `gpg --verify <file>.asc <file>` against that
  fingerprint — the same shape as the CasaOS installer pin, which this project
  already got wrong once by piping an unverified script into a shell.

**The gap the design does not close, and it is the actual blocker:** Tor Browser
is a GUI application, and nothing here says what puts it in front of the owner.
"Delivered over the tailnet" describes the transport, not the mechanism. Every
other service on this appliance is web-native; this one is not, so it needs a
remote-display layer — KasmVNC, Selkies, xpra or similar — and that component,
not the browser, is what has to be chosen and maintained.

Two constraints narrow it, and they come from decisions already made above:

1. **A third-party "Tor Browser in a container" image is disqualified by this
   design's own rule.** "The official binary… not a repackage, not a fork, not a
   patch" means the browser must be fetched and verified by us. So the thing to
   wrap is the DISPLAY, and the browser rides inside it.
2. **Axis 3 rejects unmaintained dependencies** — no tagged releases and no
   commits in seven months disqualifies a component from an appliance meant to
   run untouched for years. Whichever display layer is chosen must be checked
   against that bar and registered in MAINTENANCE.md as an external coupling
   before a line of it is written, not after.

Until that choice is made and justified, this item is **not ready to build** —
which is worth stating, because everything else about it reads as settled.

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
