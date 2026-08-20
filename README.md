<p align="center">
  <img src="assets/brand/sambuca-header.svg" width="900"
       alt="Sambuca — the open-weights siege engine: a steel zero slashed open by a glowing red ramp">
</p>

# 🏰 SAMBUCA: The Open-Weights Siege Engine

> *"Freedom is free. It has no flag, no race, and no borders. It has all the faces at once and none in particular... And now it is also easy!"*

---

# Is this you?

> ### *"I'm a lawyer. I depend on Google for my calendar and to get at my files when I'm out of the office — and I'm increasingly uncomfortable with what that means for client confidentiality."*

If any of these sound like you, this is what Sambuca is for.

**You handle other people's confidential information**

- A therapist whose session notes sit in Google Drive, under a privacy policy you did not write and cannot negotiate.
- An accountant holding four hundred clients' tax records on somebody else's server.
- A journalist who would rather not learn, in court, exactly what their cloud provider retains.
- A doctor, a notary, a social worker — anyone whose professional duty of confidentiality quietly stops at the login screen.

**You are tired of renting your own life back**

- You got the "your storage is full" email again and realised you will be paying that, monthly, forever.
- Fifteen years of family photos live in Google Photos and you have never once been able to just *hold* them.
- Your account was locked for a week with no human to appeal to, and you understood how much of your life was on the wrong side of that login.
- You run a small business and pay per seat, per month, for a shared drive that should be a hard disk in a cupboard.

**You have a computer doing nothing**

- There is an old office desktop under your desk. It is a perfectly good file server, photo library and password manager. It is doing nothing.
- The last laptop still works fine. It just has an old battery.
- You bought a NAS and use 4% of what it can do.

**You have a gaming PC and you are curious about AI** 👾

- Your RTX 3080 sits idle nineteen hours a day. It could be running a real language model, locally, that costs nothing per message and sends nothing anywhere.
- You want to try a coding assistant without your employer's source code becoming somebody's training data.
- You are writing a novel and want AI help, but not at the price of it reading your manuscript.
- You have wanted to try self-hosting for two years and every guide starts with "first, install Docker" and ends with a broken reverse proxy.

**And the simplest reason of all**

- You would just rather not be the product.

---

## Get it

Sambuca is **one program you run on your normal computer**. It writes a USB stick. You put that stick in a *different, spare* machine, turn it on, and walk away.

| Your everyday computer | Download |
|---|---|
| 🪟 **Windows** | [sambuca-flasher-windows-x64.exe](https://github.com/laboratoiresonore/Sambuca/releases/download/v0.1.0-preview1/sambuca-flasher-windows-x64.exe) |
| 🍎 **Mac** (Apple Silicon) | [sambuca-flasher-macos-arm64](https://github.com/laboratoiresonore/Sambuca/releases/download/v0.1.0-preview1/sambuca-flasher-macos-arm64) |
| 🐧 **Linux** | [sambuca-flasher-linux-x64](https://github.com/laboratoiresonore/Sambuca/releases/download/v0.1.0-preview1/sambuca-flasher-linux-x64) |

**Try it before committing anything.** This asks your computer nothing, touches no disk, and needs no USB stick — it just tells you what a machine could do:

```powershell
.\sambuca-flasher-windows-x64.exe estimate "my old Dell desktop, 16GB RAM"
```

```bash
chmod +x ./sambuca-flasher-macos-arm64 && ./sambuca-flasher-macos-arm64 estimate "my old Dell desktop, 16GB RAM"
```

<details>
<summary><b>⚠️ Your computer will warn you about this file. Here is why, and what to do.</b></summary>

<br>

These downloads are **unsigned**, and we are telling you rather than letting you find out.

- **Windows** — SmartScreen says "unrecognised app". Click **More info** → **Run anyway**.
- **macOS** — Gatekeeper refuses to open it. **Right-click the file → Open**, then confirm. Or in Terminal: `xattr -d com.apple.quarantine ./sambuca-flasher-macos-arm64`
- **macOS / Linux** — make it runnable first: `chmod +x ./sambuca-flasher-...`

Signing costs an Apple Developer account and a Windows code-signing certificate. This project has neither, and would rather say so than pretend the warning is a glitch. You can check the file matches what we built against [SHA256SUMS.txt](https://github.com/laboratoiresonore/Sambuca/releases/download/v0.1.0-preview1/SHA256SUMS.txt) — though note a checksum published next to a file proves it arrived intact, not who made it.

**No Intel Mac build** — GitHub's last Intel build machine has been retired. Install from source instead: `pip install ./apps/flasher`

</details>

> **⚠️ This is a preview.** Everything is reviewed, linted, vulnerability-scanned and tested in CI — and **no machine has been installed end to end from a flashed stick yet**. That is not the same bar. If you are the first to try a real install, please [open an issue](https://github.com/laboratoiresonore/Sambuca/issues) either way.

---

## What you actually get

**Every machine gets all of this, even the oldest one you own.** None of it involves AI. This is the part most people are actually here for:

| Instead of | You get | What that means in practice |
|---|---|---|
| Google Drive / Dropbox / OneDrive | **Your own drive** | Your files, on a disk you own, reachable from your phone and laptop anywhere in the world — with no monthly fee and no storage limit but the size of the disk. |
| Google Calendar & Contacts | **Your own calendar** | Syncs to your iPhone or Android exactly like Google's does. Same apps, same reminders. Just not on their server. |
| Google Photos / iCloud Photos | **Your own photo library** | Automatic phone backup, searchable by face and by "beach" or "dog", and it never asks you to upgrade. |
| 1Password / LastPass | **Your own password manager** | Browser extensions and phone apps, all the usual ones. Your vault stays on your machine. |
| Paying for a VPN or opening ports | **Private remote access** | An encrypted private network between your devices. Nothing is exposed to the public internet — no port forwarding, no dynamic DNS, nothing to be scanned. |
| Notion, Acrobat online, Slack | **Notes, PDF tools, encrypted chat** | Including PDF editing where the document never leaves your browser tab. |

Plus: the whole disk is encrypted, backups run themselves nightly, and everything is checked hourly so you find out when something breaks — instead of a year later when you need it.

**The AI is the bonus, not the point.** How good it is depends on your hardware. Everything above does not.

---

## Which machine should I use?

Pick the one that sounds like the machine you have. It expands.

<details>
<summary>💻 <b>An old laptop, a mini PC, a Raspberry Pi, or a NAS box</b></summary>

<br>

**Tier 4.** Everything in the table above works perfectly — files, calendar, photos, passwords, remote access, chat. The AI runs a small 3B model, about a sentence at a time. Fine for short questions, not for essays.

**This is genuinely enough if you are here to leave Google.** A £100 second-hand office PC lands here.

*Raspberry Pi note: the engine is x86-64 today, so a Pi is not installable yet — [it is planned](docs/design/NEXT-STAGE.md). Run `estimate "Raspberry Pi 5 16GB"` for the buying guide.*

</details>

<details>
<summary>🖥️ <b>An ex-office desktop — a Dell OptiPlex, HP EliteDesk, Lenovo ThinkCentre</b></summary>

<br>

**Tier 3, if it has 8 or more CPU cores and 24 GB or more of RAM.** Otherwise tier 4, which is still fine.

The AI runs an 8B model at roughly 5–12 words a second — slow but genuinely usable. Everything non-AI is identical to the biggest machine.

These are the sweet spot: they cost very little second-hand, sip power, and are built to run continuously for a decade.

</details>

<details>
<summary>🎮 <b>A gaming PC with a graphics card</b></summary>

<br>

**Depends entirely on the graphics memory — not the card's name, and not the CPU.**

| Your card | Tier | What you get |
|---|---|---|
| RTX 3090, 4090, 5090, or 24 GB+ | **1** | A 32B model (70B above 40 GB). Generates faster than you can read. Photo AI on the GPU too. |
| RTX 3060 12GB, 4060 Ti 16GB, 3080 | **2** | A 14B model at 20–40 words a second. Comfortable for real work. |
| RTX 3060 Ti, 4060, 3070 (8 GB) | **3 or 4** | 8 GB is below the threshold; it falls back to the CPU tiers. |

An idle gaming PC is the best AI machine most people already own.

</details>

<details>
<summary>🤷 <b>I have no idea what is in my computer</b></summary>

<br>

You need three things: **how much RAM**, **how many CPU cores**, and **whether there is a graphics card**.

- **Windows** — press `Ctrl+Shift+Esc` → **Performance** tab. It shows CPU cores, Memory, and GPU.
- **Mac** — Apple menu →  **About This Mac**.
- Or search: [how to check my PC specs](https://duckduckgo.com/?q=how+to+check+how+much+RAM+and+what+graphics+card+my+PC+has)

Then just describe it in plain English — the tool is generous about phrasing:

```
sambuca-flasher estimate "HP desktop, 16GB, has a graphics card I think"
```

</details>

### The full tier table

`engine/hardware-detect.sh` measures the real machine at first boot and picks the tier itself. This is what it decides:

| Tier | The machine | Chat model | Speed | Photo AI |
|---|---|---|---|---|
| **0 — every machine** | **Anything that boots.** Files, calendar, contacts, photos, passwords, PDF tools, encrypted chat, private remote access, full-disk encryption, automatic backups. **This never gets worse on a slower machine.** | — | — | — |
| **1 — heavy GPU** | Gaming or workstation PC, 24 GB+ graphics memory (RTX 3090/4090/5090) | 32B, or 70B above 40 GB | Faster than you read | On the GPU |
| **2 — mid GPU** | One modern gaming card (RTX 3060 12GB, 4060 Ti 16GB, 3080) | 14B | ~20–40 words/sec | On the CPU |
| **3 — capable CPU** | Ex-office tower, 8+ cores, 24 GB+ RAM, no useful GPU | 8B | ~5–12 words/sec | On the CPU |
| **4 — low resource** | Old laptop, mini PC, NAS box, Pi 5. 4 cores, 8–16 GB | 3B | A sentence at a time | On the CPU |

**Tier 0 is the row that matters most.** Only the AI column changes with your hardware. If you came here to stop paying Google rather than to run a 70B model, any machine on this table does the whole job.

**Why the photo AI sits on the CPU below 24 GB:** the language model owns the graphics card. Two things each believing they own it is how a self-hosted box runs out of memory at 3am in the middle of importing your photo library.

---

## Why this exists

Sambuca is not just a repository; it is a weaponized software appliance and a practical gateway to digital sovereignty. Named after the ancient Roman mobile siege tower that allowed armies to drop an assault bridge directly over impenetrable fortress walls, this project is built to bypass the locked gates of the corporate AI panopticon.

If you are a professional who handles confidential data—or simply a consumer who refuses to rent back stolen human history by the token—you are in the right place.

---

## ⚡ The Concept: Drop the Ramp (No Coding Required)

The ultimate barrier to digital sovereignty has always been complexity. We accepted corporate cages because the alternative required compiling Linux kernels in a terminal.

Sambuca is designed to completely obliterate that barrier. It is a turn-key installer designed so that anyone can deploy a sovereign, air-gapped AI server without writing a single line of code.

**How it works:**

1. Download the Sambuca app on your Mac or Windows machine.
2. Plug in a blank USB stick and hit “Flash.”
3. Plug that USB stick into a dedicated machine—an old discarded laptop, an office PC, or a high-end dual-GPU rig—and turn it on.
4. Walk away.

The Sambuca installer takes over entirely. It automatically wipes and encrypts the drive, installs a headless Linux OS, profiles your exact hardware, and downloads the smartest open-weights AI model your machine can physically run. It configures a beautiful, one-click graphical dashboard, installs free replacements for predatory cloud services, and wires up a private mesh network so you can access it securely from anywhere on Earth.

It does the work of a senior systems administrator in twenty minutes, completely offline, and hands you the master password.

<p align="center">
  <img src="assets/brand/usb-breaches-rack.webp" width="820"
       alt="A sambuca USB key driven into a server rack, its slashed-zero mark lit, a burst of red light where it meets the machine.">
</p>

## 🏗️ The Architecture

Under the hood, Sambuca orchestrates a robust, open-source stack that prioritizes privacy, efficiency, and zero-trust security:

* **The Base OS:** A minimal, headless Debian 12 environment with military-grade LUKS disk encryption.
* **The Hardware Profiler:** A dynamic auto-scaling daemon that detects your CPU, RAM, and GPU VRAM on first boot, automatically pulling the optimal quantized models to prevent out-of-memory crashes.
* **The AI Engine:** Ollama serving as the local backend, paired with **Odysseus** as the highly capable, aesthetically pleasing web frontend for interacting with your models.
* **The Application Mesh:** A CasaOS graphical dashboard managing a pre-wired Docker Compose ecosystem.
* **The Sovereign SaaS Suite:** Pre-configured self-hosted alternatives, including Vaultwarden (passwords), Nextcloud AIO (files/calendars), Immich (local ML photo backup), and encrypted IRC/Matrix servers.
* **Zero-Config Networking:** Tailscale is baked directly into the core, creating a secure peer-to-peer mesh network. No opening router ports, no complex firewall rules.

---

## 📜 The Genesis: Defeating the Rubber Stamp

This project was born in the wake of the 2026 Annual General Meeting of the Law Society of British Columbia and the glorious defeat of *Resolution 7*.

Risk-averse regulators and institutional bodies are terrified of AI. Their instinct is to slap a "certified safe" sticker on proprietary cloud vendors and demand that professionals outsource their judgment to a bureaucratic committee. But handing your clients' strictly confidential data to a black-box corporate server is an abdication of professional duty.

We do not need a Law Society hall pass to understand software. We need to reclaim our silicon.

<p align="center">
  <img src="assets/brand/tower-leaking-data.webp" width="820"
       alt="A corporate tower under a storm, streams of blue and gold data pouring out of it and away across the city below.">
</p>

## 👁️ The Corporate Panopticon vs. True Sovereignty

Every tech giant currently pitching "safe, enterprise-grade AI" achieved their breakthrough by brazenly scraping the copyrighted internet. They strip-mined our collective history, locked it inside a corporate black box, and now rent it back to us as a monthly API subscription.

* **The Phantom Asset:** You buy incredibly capable hardware, only to find it hollowed out by subscription gates, telemetry pipelines, and corporate spyware serving a shareholder's bottom line.
* **The Palantir Problem:** Entrusting commercial AI providers with sensitive data accelerates a mass-surveillance state. The leap from helpful chatbots to global surveillance networks is moving faster than the leap from medical X-rays to nuclear missiles.
* **The Aaron Swartz Scale:** While pioneers like Aaron Swartz were crushed by the state for attempting to liberate paywalled academic papers, tech monopolies ingested millions of times that amount of data without a single indictment, achieving billion-dollar valuations.

## 🌉 The Siege Engine (Why Open-Weights?)

In early 2023, out of pure corporate spite to kneecap his rivals' pricing power, Mark Zuckerberg accidentally committed the greatest benevolent act in modern tech history: he released Meta’s multi-billion-dollar LLaMA models to the public. He dropped a free, fully assembled, race-grade V8 engine onto the front lawn of every human on Earth.

That single decision breached the corporate firewall. **Open-weights AI is our Sambuca.**

When you run open-weights AI, you realize something fascinating: **neural networks are mathematically master-less.** They actively resist top-down corporate manipulation. The smarter they get, the harder it is to force them to lie.

<p align="center">
  <img src="assets/brand/ring-breached.webp" width="820"
       alt="A vast dark ring, the closed corporate loop, split open by a red beam with debris and blue energy escaping through the breach.">
</p>

## 🩸 The Cost of Freedom

Is digital sovereignty entirely "free"? No.

Sambuca solves the software friction—you no longer have to lose entire weekends screaming at your terminal wrestling with Python dependencies. But the hardware still demands a sacrifice. Buying a high-end graphics card to run massive AI models locally will burn a smoking hole in your wallet.

But there is a very fair reason for that friction: it is the literal cost of escaping the corporate panopticon. You are buying your way out of the matrix.
---

# The technical half

Everything above is what it does. Everything below is how, and every
promise it makes about itself. It is folded away because a novice does not
need it to start — not because it is optional reading before you trust the
thing with your files.

<details>
<summary><b>📦 What is actually installed, and every change we make to it</b></summary>

<br>

Transparency first: every component, its licence, and all thirty
deviations from stock — each attributed to what makes it and when.

### What is actually installed, and what we changed

> [!TIP]
> **In plain English:** Sambuca does not write your file server, your photo app or
> your password manager. It assembles software other people already built — all of
> it free and open source — into one appliance that replaces the commercial,
> non-private versions you are paying for now. This section lists every one of
> those programs, and every change Sambuca makes to it before handing it to you.

Transparency first. This appliance runs other people's software, and it does not
run it stock. Below is everything it installs, and **every deviation from what
you would get installing these yourself** — including who makes each change and
at which moment.

Nothing here is hidden behind a "recommended settings" checkbox.

### The software

> [!TIP]
> **In plain English:** these are the actual programs that end up on your machine,
> who wrote them, and what each one replaces. Everything here is free software —
> you could install any of it yourself, for free, today. Sambuca's job is that you
> do not have to.

Every component is free software. Versions are the exact pins in
[compose/.env.example](compose/.env.example).

| Component | Version | Licence | What it does |
|---|---|---|---|
| **Debian** | 12 (bookworm) | free (mixed, mostly GPL) | the base system |
| **Docker CE** | upstream apt | Apache-2.0 | runs every service |
| **Caddy** | 2.11.4 | Apache-2.0 | the single ingress, local HTTPS |
| **Tailscale** | upstream apt | BSD-3-Clause | encrypted remote access |
| **CasaOS** | latest installer | Apache-2.0 | the dashboard tiles |
| **MergerFS** | Debian package | ISC | pools mismatched disks |
| **SnapRAID** | Debian package | GPL-3.0 | parity across those disks |
| **restic** | Debian package | BSD-2-Clause | encrypted backups |
| **Ollama** | 0.32.15 | MIT | the inference engine |
| **Odysseus** | *unpublished* | AGPL-3.0 | chat and agent frontend |
| **Nextcloud AIO** | latest | AGPL-3.0 | files, calendar, contacts |
| **Immich** | 1.128.0 | AGPL-3.0 | photos, face recognition, search |
| **Vaultwarden** | 1.37.1 | AGPL-3.0 | passwords |
| **Blinko** | 1.8.8 | GPL-3.0 | AI notes |
| **BentoPDF** | 2.8.7 | AGPL-3.0 | in-browser PDF tools |
| **Ergo** | 2.19.1 | MIT | IRC server |
| **Synapse** | 1.122.0 | AGPL-3.0 | Matrix homeserver |
| **Uptime Kuma** | 1.23.17 | MIT | health monitoring |
| **Pocket ID** | 2.5.0 | BSD-2-Clause | passkey identity |
| **oauth2-proxy** | 7.14.2 | MIT | the auth gate |
| **PostgreSQL** | 16.15 | PostgreSQL Licence | databases |
| **Valkey** | 9.1.1 | BSD-3-Clause | cache — see below |
| **pgvecto-rs** | pg14-v0.2.0 | Apache-2.0 | vector search for Immich |
| **Watchtower** | 1.7.1 | Apache-2.0 | opt-in container updates |

**Valkey instead of Redis, and why.** Redis changed licence at 7.4 to
RSALv2/SSPLv1 — source-available, and neither OSI-approved. Redis 8 re-added
AGPLv3, so it would also be acceptable. Valkey is the Linux Foundation's
BSD-3-Clause fork of the last free Redis, drop-in compatible, with no ambiguity
at all. **Writing this table is what caught it** — the pin had been `redis:7.4`,
which is not free software, in a project whose whole premise is that you own
what runs on your machine.

### Verification status — read this before trusting the table

> [!TIP]
> **In plain English:** how much we have actually checked, and how much we have
> not. Short version: the software versions are real, current and scanned for
> known security holes — and **nobody has yet installed the whole thing on a real
> computer from start to finish.** We would rather tell you that than let you find
> out.

| Level | Status |
|---|---|
| Every reference resolves in its registry | ✅ verified 2026-08-20, digests in [docs/IMAGES.md](docs/IMAGES.md) |
| Vulnerability-scanned | ✅ daily, gated on regression |
| Compose renders across all 48 profile × bundle combinations | ✅ in CI |
| Config parses — Caddyfile, shell, YAML | ✅ in CI |
| **Actually started, on real hardware, end to end** | ❌ **never** |

**No machine has been installed from a flashed stick yet.** "Tested and working"
is not a claim this project can make, and pretending otherwise would be the
first dishonest thing in it. The versions are pinned, resolvable, scanned and
they render — that is a real bar, and it is not the same bar as *runs*.

### Every modification, and when it happens

> [!TIP]
> **In plain English:** every single change Sambuca makes to that software before
> you get it, why, and at which moment — on your own computer while making the
> USB stick, during installation, or on the machine's first start-up. Most changes
> close a door that the software leaves open by default. Nothing here is hidden
> behind a "recommended settings" checkbox.

| # | Change | Made by | When |
|---|---|---|---|
| 1 | Full-disk LUKS encryption | preseed | install |
| 2 | **Second LUKS keyslot** from your seed phrase, so a forgotten password is not total data loss | `enroll-recovery-key.sh` | install — inside the installer, so the passphrase never reaches the installed system |
| 3 | Root login disabled; password auth off **only if** you supplied an SSH key | `10-system.sh` | first boot |
| 4 | Security updates only — never unattended feature upgrades | `10-system.sh` | first boot |
| 5 | `vm.swappiness=10`, inotify limits raised, `overcommit_memory=1` for Postgres | `10-system.sh` | first boot |
| 6 | Docker logs capped at 20 MB × 5 — the commonest way a self-hosted box fills its disk | `20-docker.sh` | first boot |
| 7 | Admin user added to the `docker` group — **equivalent to root**, and it warns you | `20-docker.sh` | first boot |
| 8 | Existing disks **adopted, never reformatted**, if they already hold ext4/xfs | `40-storage-pool.sh` | first boot |
| 9 | **CasaOS moved off port 80** to 8095 so Caddy can own the ingress | `50-network.sh` | first boot |
| 10 | nftables **default-deny inbound**; only 22/80/443, mDNS and the tailnet | `50-network.sh` | first boot |
| 11 | Tailscale joined with `--ssh`, a recovery path that survives a broken sshd | `50-network.sh` | first boot |
| 12 | Service secrets as **files, not environment variables** — so `docker inspect` is not a credential dump | `60-stack.sh` | first boot |
| 13 | Databases on an `internal: true` network with **no route off the host** | compose | first boot |
| 14 | Ollama has **no `edge` membership** — nothing on the LAN reaches the model server | compose | first boot |
| 15 | **Immich photo AI pinned to CPU** below 20 GB VRAM, so it cannot fight inference for the card | `hardware-detect.sh` | first boot, and again after the GPU driver installs |
| 16 | Ollama keep-alive, parallelism and context sized to your tier | `hardware-detect.sh` | first boot |
| 17 | Vaultwarden registration **closed**, invitations only | compose | first boot |
| 18 | Synapse **federation port not published** — you are not on the public Matrix network unless you choose | compose | first boot |
| 19 | Synapse telemetry off (`REPORT_STATS=no`) | compose | first boot |
| 20 | Postgres initialised with `--data-checksums`; Synapse's DB forced to C collation | compose | first boot |
| 21 | Ergo **invite-only, SASL required**, with its own leaf certificate — never the CA key | compose + `issue-service-cert.sh` | first boot |
| 22 | Odysseus telemetry off, **remote AI providers disabled** — it cannot call out even if asked | compose | first boot |
| 23 | Blinko's AI pointed at your local Ollama with a placeholder key, so it cannot reach a cloud model | compose | first boot |
| 24 | BentoPDF given a **read-only filesystem** — it serves static assets and has no reason to write | compose | first boot |
| 25 | Watchtower **label-gated**: only stateless services opt in, and it never removes volumes | compose | first boot |
| 26 | Nextcloud AIO domain validation skipped — it cannot succeed behind a private reverse proxy | compose | first boot |
| 27 | Caddy issues **local certificates from its own CA**; no public ACME, no port 80 open to the internet | Caddyfile | first boot |
| 28 | Nightly signed-tag config sync, nightly encrypted backup, weekly parity scrub | systemd timers | first boot, then ongoing |
| 29 | Update guard holds anything oversized, key-shaped, or contacting a new host | `update-guard.sh` | every night |
| 30 | The provisioning payload is **shredded** from the boot partition | `first-boot.sh` | end of first boot |

**Made by the USB maker, on your own computer, before anything boots:** the
24-word seed, the root passphrase, the disk recovery key, the recovery PDF and
`provision.json`. None of it is transmitted anywhere.

**Made by you, afterwards:** your passkey. It cannot be automated — a WebAuthn
credential is created by your own authenticator touching a browser, and anything
claiming to pre-provision one has put a password-equivalent secret on disk
instead.

</details>

<details>
<summary><b>🛡️ Design commitments — the decisions this is built around</b></summary>

<br>

### Design commitments

> [!TIP]
> **In plain English:** the promises this project makes about how it behaves, and
> what each one costs. Things like: it never sends anything anywhere, the printed
> sheet is enough to get your files back, and when something breaks it says so
> loudly instead of pretending it worked.

These are the decisions the code is built around. Each one costs something.

**Nothing phones home.** No analytics, no crash reporting, no license check. The
flasher makes zero network requests. Odysseus is configured with remote
providers disabled.

**The paper is sufficient.** The backup repository password AND a disk recovery
key are both derived from the 24-word seed with versioned HKDFs. With the
printed sheet alone you can open the disk on a machine whose passphrase has been
forgotten, and restore your data on a machine that has never heard of this
project.

**No single point of failure that is one string on one sheet.** The disk has two
independent keyslots — the root passphrase and the seed-derived recovery key.
Either opens it; neither can be computed from the other. `sambuca-recovery
verify` proves the key works *before* you need it, because a keyslot that exists
is not a keyslot that works.

**The installer refuses rather than guesses.** No hardcoded `/dev/sda`. If the
disk selection rules do not produce exactly one answer, installation stops and
tells you why.

**Failures are loud.** `restic` exit code 3 means a *partial* backup and is
reported as a failure, not logged as `done OK`. Backups are verified by
restoring a file, not by trusting an exit code. The snapraid sync aborts if an
abnormal number of files were deleted, because syncing would destroy the parity
that could undo the damage.

**The auth gate fails closed.** Until a passkey is enrolled, gated routes return
503. Passkey enrolment cannot be automated — anything claiming otherwise has
left a password-equivalent bootstrap secret on the disk — so it is one clearly
labelled attended step, and everything with its own login works in the meantime.

**Updates are followed, not obeyed.** The nightly sync fetches, verifies a
signed tag, refuses changes to disk/firewall/backup paths without human review,
applies, validates, and rolls back on failure.

---

</details>

<details>
<summary><b>📁 Repository layout</b></summary>

<br>

### Repository layout

> [!TIP]
> **In plain English:** a map of the code, for people who want to read it before
> running it. You do not need this to use Sambuca. It is here because "trust us"
> is not a security model, and you should be able to check.

```
sambuca/
├── apps/flasher/                    Cross-platform USB writer (Windows/macOS/Linux)
│   ├── src/sambuca_flasher/
│   │   ├── keys.py                  BIP-39 seed + root passphrase + backup key derivation
│   │   ├── payload.py               provision.json construction + the secret-leak guard
│   │   ├── recovery_pdf.py          the printed recovery document
│   │   ├── devices.py               removable-device enumeration (internal disks never listed)
│   │   ├── writer.py                raw image write + readback verification
│   │   └── cli.py                   the write flow, in the order it must happen
│   └── tests/
│
├── engine/
│   ├── autoinstall/                 Unattended Debian 12 installation
│   │   ├── preseed.cfg              full-disk LUKS, no hardcoded target disk
│   │   ├── disk-select.sh           resolves the target, or REFUSES — never guesses
│   │   ├── abort-countdown.sh       the 30-second fail-safe
│   │   ├── late-command.sh          stages the engine into the installed system
│   │   ├── enroll-recovery-key.sh   adds the seed-derived SECOND LUKS keyslot, in the installer
│   │   ├── luks-tpm-enroll.sh       optional TPM 2.0 auto-unlock (opt-in, with the tradeoff stated)
│   │   └── build-iso.sh             rebuild a netinst ISO with the payload embedded
│   │
│   ├── hardware-detect.sh           ★ the profiler: CPU/RAM/VRAM → tier → model set → resource limits
│   ├── first-boot.sh                ★ the provisioning orchestrator (resumable, idempotent)
│   ├── lib/common.sh                logging, error traps, atomic writes, single-instance enforcement
│   ├── profiles/tier{1..4}.env      the model catalogue, as data
│   │
│   ├── provision/                   phases, run in order by first-boot.sh
│   │   ├── 10-system.sh             base OS, users, ssh, sysctl, unattended security upgrades
│   │   ├── 20-docker.sh             Docker CE + compose plugin + daemon hardening
│   │   ├── 30-gpu-runtime.sh        NVIDIA/ROCm runtime, then RE-PROFILES the hardware
│   │   ├── 40-storage-pool.sh       MergerFS union + SnapRAID parity (never formats a disk with data)
│   │   ├── 50-network.sh            Tailscale, CasaOS port move, nftables default-deny
│   │   ├── 60-stack.sh              renders .env, validates, brings the mesh up, health-gates
│   │   ├── 70-models.sh             pulls the tier's models, then proves generation works
│   │   ├── 80-identity.sh           Pocket ID bootstrap (one attended step, honestly labelled)
│   │   └── 90-report.sh             the completion report
│   │
│   └── maintenance/
│       ├── gitops-sync.sh           signed-tag-only config sync, with a forbidden-path review gate
│       ├── backup.sh                restic, with correct exit-3 handling and restore verification
│       ├── snapraid-sync.sh         parity sync with a deletion threshold that aborts
│       ├── recovery-key.sh          sambuca-recovery {status,enrol,verify} — prove the key works
│       └── systemd/                 units and timers
│
├── compose/
│   ├── docker-compose.yml           core: Caddy, Pocket ID, oauth2-proxy, Uptime Kuma, Watchtower
│   ├── ai.yml                       Ollama + Odysseus
│   ├── cloud.yml                    Vaultwarden, Nextcloud AIO, Immich
│   ├── office.yml                   Blinko, BentoPDF
│   ├── comms.yml                    Ergo, Synapse
│   ├── gpu.<profile>.<bundle>.yml   per-bundle GPU overrides; only enabled bundles are appended
│   ├── .env.example                 image pins — see docs/IMAGES.md before releasing
│   └── config/                      Caddyfile, ircd.yaml, snapraid.conf template
│
├── apps/companion/
│   └── setup/index.html             the setup screen the owner watches while it provisions
│
├── assets/brand/
│   ├── sambuca-header.svg           README header
│   ├── sambuca-mark.svg             square mark: favicon, tray, avatar
│   └── loading-treads.*             the siege-tread loop shown during long steps
│
├── tools/
│   ├── verify-images.py             resolve every image against its registry (no docker needed)
│   └── check-upstreams.py           daily drift check across every external coupling
│
└── docs/
    ├── ARCHITECTURE.md              the three network planes, boot sequence, VRAM arbitration
    ├── SECURITY.md                  the threat model, and the tradeoffs made on purpose
    ├── MAINTENANCE.md               every coupling we do not control, and what watches it
    ├── HARDWARE.md                  what tier your machine lands in, and why
    ├── IMAGES.md                    image pinning policy
    └── design/
        └── NEXT-STAGE.md            the three development axes, as decisions
```

---

</details>

<details>
<summary><b>🚦 Status — what is proven and what is not</b></summary>

<br>

### Status

> [!TIP]
> **In plain English:** what genuinely works, what is untested, and what is still
> missing. The headline: everything is checked automatically on every change, and
> **no one has yet done a real install on real hardware.** Treat this as an early
> preview, not a finished product.

Early, but not vapour. **CI is green** — shellcheck, the flasher suite on
Linux/macOS/Windows across Python 3.11 and 3.13, Caddyfile validation, compose
rendering for all 48 GPU-profile × bundle-subset combinations, and live registry
resolution of every container image — digests recorded in
[docs/IMAGES.md](docs/IMAGES.md). 25 tests pass, including pinned derivation
vectors for both seed-derived keys.

Two things are honestly outstanding:

- **`ODYSSEUS_IMAGE` is not published yet.** Until it is public on GHCR, the
  `ai` bundle starts Ollama with no frontend. Everything else runs.
- **Images are pinned to tags, not digests.** Tags are the development
  convenience; `make pin-images` converts them to digests, which is what makes
  a flashed USB reproducible. Do that before cutting a release tag.

And the honest headline: **no machine has yet been installed end-to-end from a
flashed stick.** The install path is reviewed, syntax-checked, linted by three
independent toolchains and reasoned through, but unproven on real hardware.
Treat it accordingly until someone reports otherwise — and if you are the first
to try it, open an issue either way.

That gap is not modesty. Every serious bug found so far was found by a machine
running the code, not by anyone reading it: a `local` declaration that gave four
databases the same password, CRLF line endings that made the abort countdown
unrunnable, a `read -t` that would have hung the installer where it is not
implemented. Reading is not verification.

---

</details>

<details>
<summary><b>🧭 Where this is going — the three development axes</b></summary>

<br>

### 🧭 Where this is going — three development axes

> [!TIP]
> **In plain English:** what is being built next, and why. Three goals: make it
> simple enough for someone who has never installed an operating system, make it
> genuinely secure including when you lose your password, and never ship software
> with sloppy defaults when a safer version exists.

Everything after the first working appliance is organised along three axes. They
are not phases to be completed in order; they are standing directions, and every
change should advance one of them without regressing the other two. The full
decision document — what gets built, what gets **rejected**, and why — is
[docs/design/NEXT-STAGE.md](docs/design/NEXT-STAGE.md), and the interactive
installer has its own at
[docs/design/INSTALLER.md](docs/design/INSTALLER.md).

### 1. User-friendliness — take the novice all the way

**The hard part of de-Googling is not the server. It is the fourteen small
migrations afterwards:** contacts, calendar, photos, mail, passwords, files, the
phone, the second phone, the spouse's laptop. Every self-hosting project ships
the server and abandons you at exactly the point where the work starts. That
abandonment is the whole reason self-hosting has a reputation for being for
hobbyists.

So the target is not a flasher with a nicer window. It is a **companion that
persists after installation and drives the migration to completion** — served by
the appliance, opened from any browser, because the migrations happen on phones
and laptops, not on the machine that wrote the USB.

That split gives the desktop app exactly one job after the USB is written:
**recovery**. It is the thing you still have when the appliance will not boot.

- **A checklist with memory.** Close the tab, come back tomorrow, it knows where
  you were. The single most important feature, and the one always missing.
- **The certificate wall, solved rather than explained.** Per-platform trust
  profiles, not a paragraph about trust stores. This is where every novice
  trips first.
- **QR codes for every phone step.** Pointing a camera at a screen succeeds
  where typing a URL into a phone browser and hitting a cert warning does not.
- **Verified, not asserted.** Each step ends with the appliance *checking* the
  client actually connected and the first photo actually synced. A checklist
  that only records clicks is a checklist that lies.
- **Mail: inbound sovereign, outbound relayed.** You forward your mail to a
  provider, the appliance continuously drains it into a local archive you own,
  and sends through that provider's SMTP. The insight that makes this work: *a
  mailbox that is drained every few minutes is a transport, not a store* — so
  free tiers are fine, storage limits stop mattering, and provider trust matters
  far less because nothing sits there. Optional drain-and-delete, off by default
  and behind a dry run. **No Google Cloud Console** — Gmail needs an App
  Password, six clicks, and Takeout covers the history. We do not run an MX:
  home-IP deliverability is not controllable, and a mail server that silently
  drops mail is worse than none.
- **Signal and WhatsApp alongside IRC.** Encrypted comms that only reach other
  sambuca owners are a toy. Matrix bridges put Signal, WhatsApp and IRC in one
  client. Bridges terminate end-to-end encryption to re-encrypt into Matrix —
  which on a hosted bridge means a stranger reads your messages, and here means
  *your own hardware, on your own tailnet, on an encrypted disk*. WhatsApp
  bridging also breaks Meta's terms and carries a small but real ban risk; it is
  off by default and says so in those words before showing you a QR code.
  Bridges are **Tier 1** in the [maintenance register](docs/MAINTENANCE.md):
  they break silently, they are the one coupling no script can watch, and they
  do not ship without a health monitor and a named human tracking upstream.
- **Every stage says what is happening.** An unattended installer that prints
  nothing but log lines is indistinguishable from a hung machine, and an owner
  who power-cycles during disk provisioning corrupts the install. So each step
  states what it is doing, how long it takes, what you should do (usually
  nothing — and saying so is the point) and what comes next; on failure, what it
  means and the exact command to resume.

### 2. Security — a fortress that can also be unbricked

State of the art is not only about keeping attackers out. It is equally about
what happens when the *owner* makes a mistake, because an appliance nobody can
recover is one that eats somebody's photo archive.

- **Done: the disk has two independent keys.** Forgetting the master password
  used to mean permanent, total data loss. It now means typing the recovery key
  from the same sheet. See the design commitments above.
- **A recovery vault for when the sheet is gone too.** The desktop app keeps an
  encrypted copy of the key material, unlocked by three questions only you can
  answer, on your own machine and nowhere else. Because the answers are
  low-entropy, the key derivation is deliberately brutal — memory-hard, seconds
  per attempt. And because the vault is a second complete copy of every secret,
  the app says so plainly: whoever has your laptop *and* guesses the answers has
  your disk. Opt-in, deletable, and never an escrow that leaves your hardware.
- **Update control that is rigorous about what it swallows.** Signed tags and
  forbidden-path review already ship. Next: diff size and shape limits, refusal
  of any update introducing something key-shaped, **no new outbound host without
  human review** (supply-chain compromise almost always needs to phone
  somewhere), digest-drift detection, and a rollback path *exercised in CI
  against a deliberately poisoned update* — because a recovery path nobody has
  run is a recovery path that does not work.
- **Rescue mode on the same USB.** Boot the stick, unlock with either secret,
  and get a menu: repair the bootloader, re-run a phase, copy the data off,
  reset the passphrase. Today the answer is "boot Debian rescue and know what
  you are doing", which is not an answer for this audience.

### 3. Perfected setup — hardened variants, never stock

Where a stock component's defaults are wrong for a machine holding confidential
work, the appliance ships a hardened variant instead of the stock one.

**The worked example is the generative stack.** Stock ComfyUI accumulates inputs
and outputs on disk forever, executes custom nodes fetched at runtime, and
reaches the internet freely. For a box holding client documents that is three
unacceptable defaults in one container. The sambuca variant:

- **Ephemeral by container lifecycle, not by timer.** One disposable container
  per session: `--rm`, read-only rootfs, inputs and outputs on tmpfs *inside*
  it. The session ends, the container dies, and the I/O is gone by construction.
  A purge timer is a promise; a destroyed container is a fact. Deletion is the
  default and retention the exception — the inverse of every stock setup.
- **No runtime node installation** — nodes pinned at build time. A generative
  server that can `pip install` from a workflow file is remote code execution
  with a nice UI.
- **No egress** — models are fetched and verified during provisioning and
  mounted read-only; at runtime the container has no network namespace at all. A
  workflow cannot phone home because there is nowhere to phone from.
- **The cost, stated:** a cold model load per session, tens of seconds for a
  large checkpoint. That is the correct side of the bargain when the alternative
  is a client's document sitting in an output folder six months later.
- **Under the same VRAM arbitration as everything else** — inference first,
  generative second, background photo ML last, rather than a fourth actor that
  believes it owns the card.

**And the machine runs as lean as it can.** Nothing idles that was not asked
for; spare RAM is spent on making storage fast — tmpfs scratch and zram, sized
from measured hardware by the same profiler that sizes everything else, with a
hard floor so a large render cannot squeeze Postgres into the OOM killer. "Lean"
is a number in `profile.env` you can check, not an adjective in a README.

### Where to help

The Call to Action below asks for UI polish, Bash scripting and hardware
testers. Mapped onto the axes: **UI polish is axis 1** (the companion's
checklist and migration flows — the highest-leverage work in the project).
**Bash scripting is axes 2 and 3** (update guards, rescue mode, the memory
plan). **Hardware testing is all three at once**, and is the single thing this
project needs most: nobody has yet installed it end-to-end from a flashed stick.

---

</details>

## 🚀 Call to Action

Do not wait for a tech company to tell you where North is. Do not wait for a regulator to rubber-stamp your tools.

* **Educate Yourself:** Learn the difference between closed-code cloud APIs and open-weights local hosting.
* **Experiment:** Flash a Sambuca drive and reclaim your discarded hardware.
* **Build:** Contribute to the hive mind. We need UI polish, Bash scripting, and hardware testers.

Humanity stands at a critical fork in history. A choice between unprecedented empowerment on a corporate dog leash, or true, sovereign capability. The siege engine is built. The bridge is primed.

**Drop the ramp.**

---

---

## Licence

AGPL-3.0-or-later. Software that exists to keep you out of someone else's walled
garden should not be usable to build one: the network-use clause means a company
cannot take Sambuca, run it as a hosted product, and give nothing back.
