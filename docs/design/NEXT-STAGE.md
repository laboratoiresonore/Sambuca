# Next stage: the three axes

This is a decision document, not a wish list. Every section states what gets
built, what gets rejected, and why. Anything already shipped is marked **DONE**;
anything not yet built says so.

The three axes, in the order they were given:

1. **User-friendliness** — the desktop app must not merely write a USB stick. It
   must take an absolute novice by the hand from setup to full use, including
   the whole de-Googling migration, phone apps and all.
2. **Security** — a state-of-the-art fortress, including unbricking a machine
   whose owner lost the master password, and update control that is rigorous
   about what it is being asked to swallow.
3. **Perfected setup** — no stock components where a hardened, privacy-first
   variant is possible; ephemeral inputs and outputs; and a machine that runs as
   lean as it can, spending spare memory on storage rather than on idling
   services.

---

## Axis 1 — user-friendliness

### The load-bearing insight

The hard part of de-Googling is not the server. It is the **fourteen small
migrations** afterwards: contacts, calendar, photos, mail, passwords, files,
the phone, the second phone, the spouse's laptop. Every self-hosting project
ships the server and abandons the user at exactly the point where the work
actually starts. That abandonment is why self-hosting has a reputation for being
for hobbyists.

So the deliverable is not a flasher with a nicer window. It is a **companion**
that persists after installation and walks the migration to completion.

### What gets built

**`apps/companion/` — a guided migration app, served BY the appliance.**

Not a desktop app. It runs on the appliance and is opened from any browser,
because the migrations happen on phones and laptops, not on the machine that
wrote the USB. The desktop flasher hands off to it and gets out of the way.

Its shape:

- **A checklist with state.** Every migration is a task with a status the
  appliance remembers. The novice closes the tab, comes back tomorrow, and the
  list knows where they were. This is the single most important feature and the
  one that is always missing.
- **Per-device, per-platform instructions with real screenshots**, chosen from
  what the user answers to "what phone do you have?" — not a generic wiki page
  that assumes Android.
- **QR codes for every phone step.** Nextcloud, Immich and Bitwarden all support
  QR or deep-link onboarding. A novice pointing a camera at a screen succeeds;
  a novice typing `https://cloud.sambuca.local` into a phone browser and hitting
  a certificate warning gives up.
- **The certificate problem, solved rather than explained.** The local CA is the
  first thing every novice trips on. The companion serves per-platform install
  profiles (`.mobileconfig` for iOS, a signed cert + instructions for Android,
  a one-click script for macOS/Windows) instead of a paragraph about trust
  stores.
- **Verified, not asserted.** Each step ends with the appliance checking the
  thing actually happened — the client connected, the first photo synced, the
  vault unlocked from the phone. A checklist that only records clicks is a
  checklist that lies. This is the same discipline as `sambuca-recovery verify`.

**Migration coverage, in dependency order:**

| Step | From | To | Mechanism |
|---|---|---|---|
| 1 | — | trust the local CA | per-platform profile |
| 2 | Google Takeout | Nextcloud | guided export, resumable import |
| 3 | Google Contacts | Nextcloud CardDAV | vCard, phone-side account setup |
| 4 | Google Calendar | Nextcloud CalDAV | iCal |
| 5 | Google Photos | Immich | Takeout, with the album/metadata caveats stated up front |
| 6 | Chrome/1Password/LastPass | Vaultwarden | native importers |
| 7 | Gmail | (kept, or IMAP-mirrored) | see below |
| 8 | Drive/Docs | Nextcloud Office | format conversion warnings |
| 9 | — | phones + laptops | client install and sync verification |

**Mail is deliberately last and deliberately conservative.** Self-hosting
inbound mail is the one migration that fails badly and silently: deliverability
depends on IP reputation the owner does not control. The companion will offer
IMAP mirroring for archival and will *not* pretend a home appliance should be
your MX. Saying so plainly is more useful than shipping a mail server that
quietly drops mail.

**Takeout is treated as a first-class import problem**, because it is: multi-part
zip archives, split albums, sidecar JSON metadata Google made deliberately
awkward. The companion gets a real importer with resume, integrity checks and a
per-item report — not a "just unzip it into the folder" instruction.

### What gets rejected

- **Rewriting the flasher in Tauri/Rust for prettiness.** The flasher's job is
  ten minutes long and already works on three platforms. A GUI wrapper around
  the existing CLI (native file picker, device list, progress, big red confirm)
  is worth building; a rewrite is not.
- **Hiding the recovery document behind a "remind me later".** It is printed
  before anything is written, and that stays.

---

## Axis 2 — security

### DONE — the disk now has two independent keys

Shipped in this stage. Previously, the root passphrase was the only thing that
opened the disk: forget it and every file was gone permanently. Now a
seed-derived recovery key is enrolled into a second LUKS keyslot during
installation, either secret opens the disk, and `sambuca-recovery verify` proves
the key works *before* it is needed. Full detail in [SECURITY.md](../SECURITY.md).

### Update control — what the appliance refuses to swallow

`gitops-sync.sh` already verifies a signed tag, refuses changes under
`autoinstall/`, `provision/40-`, `provision/50-` and `maintenance/backup`, and
rolls back on a failed health check. The next increment adds the checks you
named, all of which are cheap and all of which are absent from every
auto-updating self-hosted project I know of:

- **Size and shape limits.** Reject an update whose diff exceeds a threshold, or
  that adds a binary, or that grows the repository by more than a set factor.
  A legitimate config update is small. A 40 MB "update" is an exfiltration
  payload or a compromised release.
- **Secret scanning of the incoming diff.** Refuse an update that introduces
  anything shaped like a private key, an API token or a credential — whether it
  is an attack or an upstream mistake, it must not land unattended.
- **No new network egress without review.** An update that adds a URL, a new
  registry, or a new outbound host is held for a human. This is the single
  highest-signal check available: supply-chain compromise almost always needs to
  phone somewhere.
- **Image digest drift.** Once `make pin-images` has run, an update that changes
  a digest is a deliberate, reviewable act — not a tag silently repointing.
- **Rollback proven, not assumed.** The rollback path gets exercised in CI
  against a deliberately poisoned update, because a recovery path nobody has run
  is a recovery path that does not work.

### The unbricking story, completed

The recovery keyslot handles the forgotten passphrase. Two gaps remain:

- **A rescue mode on the same USB.** Boot the stick, unlock with either secret,
  and get a menu: repair the bootloader, re-run a provisioning phase, mount and
  copy data off, reset the root passphrase. Today the answer is "boot Debian
  rescue and know what you are doing", which is not an answer for this audience.
- **The desktop app as the rescue driver.** `sambuca-flasher rescue` should walk
  the user through it — derive the key from the seed, tell them exactly which
  prompt to type it at, and verify success — rather than handing them a
  `cryptsetup` command and wishing them luck.

---

## Axis 3 — perfected setup

### Generative stack: not stock ComfyUI

Stock ComfyUI is the wrong default for an appliance holding client documents:

- Inputs and outputs accumulate on disk forever, by design.
- The custom-node ecosystem executes arbitrary Python fetched at runtime.
- It happily reaches the internet for models and nodes.
- It has no notion of a session boundary or of who is asking.

The hardened variant:

- **Ephemeral I/O by construction.** Input and output directories are tmpfs.
  They do not survive a restart, and a purge timer clears them on a schedule the
  owner sets. Deletion is the default, retention the exception — the inverse of
  every stock configuration.
- **No runtime node installation.** Custom nodes are declared and installed at
  build time and pinned. A generative server that can `pip install` from a
  workflow file is a remote code execution surface with a nice UI.
- **No egress.** Models are fetched during provisioning and verified; at runtime
  the container has no route off the host. A workflow cannot phone home.
- **VRAM under the same arbitration as everything else.** It joins the existing
  ladder — inference engine first, generative second, background photo ML last —
  rather than being a fourth actor that believes it owns the card.
- **Off by default**, and a separate bundle, because most owners want files and
  photos rather than a diffusion server.

### Lean by default; spare memory becomes storage

Your framing was right: unused RAM is wasted, and the appliance should spend it
on making storage fast rather than on idling services.

- **Nothing idles that is not asked for.** Bundles are already opt-in; the next
  step is socket-activation or on-demand start for the heavy, rarely-used
  services, so a tier-4 box is not holding a diffusion server resident to serve
  nobody.
- **Spare RAM becomes cache and scratch, deliberately sized.** tmpfs for
  generative I/O and transcode scratch, sized from the measured total by
  `hardware-detect.sh` — the same profiler that already sizes everything else —
  with a hard floor reserved so a large render cannot squeeze Postgres into the
  OOM killer. `vm.swappiness=10` is already set for exactly this reason.
- **zram before disk swap.** Compressed RAM swap on a low-tier box beats
  thrashing an SSD, and costs nothing when unused.
- **Measured, not asserted.** The profiler already emits real numbers; the
  memory plan gets emitted the same way, so "lean" is a value in `profile.env`
  that can be checked rather than an adjective in a README.

---

## Sequence

Ordered by what unblocks the most, and by what is worst if left undone.

1. **DONE** — disk recovery keyslot, `sambuca-recovery {status,enrol,verify}`.
2. **Update-control hardening** — size/shape limits, secret scanning, egress
   review, rollback exercised in CI. Small, high-value, and the appliance is
   already pulling updates unattended.
3. **Companion, step 1 only** — the CA install flow and the checklist substrate.
   This is the wall every novice hits first, and everything else hangs off the
   checklist.
4. **Companion migrations** — in the dependency order tabled above.
5. **Rescue mode + `sambuca-flasher rescue`** — completes the unbricking story.
6. **Hardened generative bundle** — ephemeral I/O, pinned nodes, no egress.
7. **Memory plan** — tmpfs sizing, zram, on-demand services.
8. **Flasher GUI** — last, because the CLI already works and a window is the
   least load-bearing thing on this list.
