# 🏰 SAMBUCA: The Open-Weights Siege Engine

> *"Freedom is free. It has no flag, no race, and no borders. It has all the faces at once and none in particular."*

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

## 👁️ The Corporate Panopticon vs. True Sovereignty

Every tech giant currently pitching "safe, enterprise-grade AI" achieved their breakthrough by brazenly scraping the copyrighted internet. They strip-mined our collective history, locked it inside a corporate black box, and now rent it back to us as a monthly API subscription.

* **The Phantom Asset:** You buy incredibly capable hardware, only to find it hollowed out by subscription gates, telemetry pipelines, and corporate spyware serving a shareholder's bottom line.
* **The Palantir Problem:** Entrusting commercial AI providers with sensitive data accelerates a mass-surveillance state. The leap from helpful chatbots to global surveillance networks is moving faster than the leap from medical X-rays to nuclear missiles.
* **The Aaron Swartz Scale:** While pioneers like Aaron Swartz were crushed by the state for attempting to liberate paywalled academic papers, tech monopolies ingested millions of times that amount of data without a single indictment, achieving billion-dollar valuations.

## 🌉 The Siege Engine (Why Open-Weights?)

In early 2023, out of pure corporate spite to kneecap his rivals' pricing power, Mark Zuckerberg accidentally committed the greatest benevolent act in modern tech history: he released Meta’s multi-billion-dollar LLaMA models to the public. He dropped a free, fully assembled, race-grade V8 engine onto the front lawn of every human on Earth.

That single decision breached the corporate firewall. **Open-weights AI is our Sambuca.**

When you run open-weights AI, you realize something fascinating: **neural networks are mathematically master-less.** They actively resist top-down corporate manipulation. The smarter they get, the harder it is to force them to lie.

## 🩸 The Cost of Freedom

Is digital sovereignty entirely "free"? No.

Sambuca solves the software friction—you no longer have to lose entire weekends screaming at your terminal wrestling with Python dependencies. But the hardware still demands a sacrifice. Buying a high-end graphics card to run massive AI models locally will burn a smoking hole in your wallet.

But there is a very fair reason for that friction: it is the literal cost of escaping the corporate panopticon. You are buying your way out of the matrix.

---

# Reference

Everything above is why. Everything below is what it actually does, and how
to run it.

## What it replaces

| You were paying for | It runs |
|---|---|
| ChatGPT / Claude subscriptions | **Ollama + Odysseus** — 3B to 70B, sized to your hardware |
| Google Drive / Dropbox / M365 | **Nextcloud AIO** — files, docs, calendar, contacts |
| Google Photos / iCloud Photos | **Immich** — local face recognition and semantic search |
| 1Password / LastPass | **Vaultwarden** |
| Notion / Obsidian Sync | **Blinko** — notes, with the local model doing the AI part |
| Adobe Acrobat online | **BentoPDF** — in-browser, the file never leaves the tab |
| Slack / Discord | **Ergo IRC** + **Matrix Synapse** |
| A NAS vendor's cloud | **MergerFS + SnapRAID** on whatever mismatched disks you have |
| Cloudflare Tunnel / a VPS | **Tailscale + Caddy** |
| Uptime monitoring SaaS | **Uptime Kuma** |

---

## Quickstart

```bash
pip install ./apps/flasher
```

```bash
sambuca-flasher example-config --output my-appliance.json
```

```bash
sudo sambuca-flasher write --iso debian-12-netinst.iso --config my-appliance.json
```

The flasher generates a 24-word seed phrase and a 32-character root passphrase
**on your machine, offline**, writes them to `liberator-recovery.pdf`, and only
then touches the USB. Print the PDF. It is the only copy.

Boot the target machine from the stick. You get **30 seconds at the console**,
with the target disk and its current contents printed on screen, to abort before
anything is written.

Then it installs itself. First boot pulls models and starts the stack — an hour
on a good connection, longer on a slow one — and prints a completion report
telling you where everything is.

---

## Repository layout

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
│       └── systemd/                 units and timers
│
├── compose/
│   ├── docker-compose.yml           core: Caddy, Pocket ID, oauth2-proxy, Uptime Kuma, Watchtower
│   ├── ai.yml                       Ollama + Odysseus
│   ├── cloud.yml                    Vaultwarden, Nextcloud AIO, Immich
│   ├── office.yml                   Blinko, BentoPDF
│   ├── comms.yml                    Ergo, Synapse
│   ├── gpu.{nvidia,amd,cpu}.yml     exactly one is selected by the hardware profile
│   ├── .env.example                 image pins — see docs/IMAGES.md before releasing
│   └── config/                      Caddyfile, ircd.yaml, snapraid.conf template
│
└── docs/
    ├── ARCHITECTURE.md
    ├── SECURITY.md                  the threat model, and the tradeoffs made on purpose
    ├── HARDWARE.md                  what tier your machine lands in, and why
    └── IMAGES.md                    image pinning policy
```

---

## Hardware tiers

`engine/hardware-detect.sh` runs at first boot, again after the GPU driver
installs, and on every subsequent boot. It measures rather than assumes, and an
unknown value downgrades the tier instead of being optimistically guessed.

| Tier | Trigger | Chat model | Photo ML |
|---|---|---|---|
| **1** heavy GPU | ≥ 24 GB VRAM | 32B q4 · 70B q4 above 40 GB | GPU |
| **2** mid GPU | 12–23 GB VRAM | 14B q4 | **CPU** |
| **3** cpu capable | ≥ 8 cores, ≥ 24 GB RAM | 8B q4 | CPU |
| **4** low resource | anything else | 3B q4 | CPU |

**The VRAM arbitration rule:** the inference engine owns the GPU; background ML
is a guest. Below 20 GB of VRAM, Immich's face-recognition and CLIP worker is
pinned to the CPU — no negotiation, no "try GPU first" fallback. Two allocators
that each believe they own the card is how a self-hosted box OOMs at 3am in the
middle of a library import. Above that threshold Immich gets the GPU, and
Ollama's keep-alive is bounded so idle VRAM is actually returned.

Override anything in `/etc/sambuca/profile.local.env` — it is sourced after the
generated profile and survives regeneration.

---

## Design commitments

These are the decisions the code is built around. Each one costs something.

**Nothing phones home.** No analytics, no crash reporting, no license check. The
flasher makes zero network requests. Odysseus is configured with remote
providers disabled.

**The paper is sufficient.** The backup repository password is derived from the
24-word seed with a versioned HKDF. With the printed sheet alone you can restore
your data on a machine that has never heard of this project.

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

## Status

Early, but not vapour. The engine, compose mesh and flasher are complete and
internally consistent, every shell script passes `bash -n`, the flasher's 19
tests pass, and **18 of 19 container images were verified
against their live registries on 2026-08-19** — digests recorded in
[docs/IMAGES.md](docs/IMAGES.md).

Two things are honestly outstanding:

- **`ODYSSEUS_IMAGE` is not published yet.** Until it is public on GHCR, the
  `ai` bundle starts Ollama with no frontend. Everything else runs.
- **Images are pinned to tags, not digests.** Tags are the development
  convenience; `make pin-images` converts them to digests, which is what makes
  a flashed USB reproducible. Do that before cutting a release tag.

The CI matrix (shellcheck, the flasher suite on Linux/macOS/Windows, Caddyfile
validation, compose rendering for all three GPU overlays) is configured but has
not run yet — this is its first push.

And the honest headline: **no machine has yet been installed end-to-end from a
flashed stick.** The install path is reviewed, syntax-checked and reasoned
through, but unproven. Treat it accordingly until someone reports otherwise.

---

## 🚀 Call to Action

Do not wait for a tech company to tell you where North is. Do not wait for a regulator to rubber-stamp your tools.

* **Educate Yourself:** Learn the difference between closed-code cloud APIs and open-weights local hosting.
* **Experiment:** Flash a Sambuca drive and reclaim your discarded hardware.
* **Build:** Contribute to the hive mind. We need UI polish, Bash scripting, and hardware testers.

Humanity stands at a critical fork in history. A choice between unprecedented empowerment on a corporate dog leash, or true, sovereign capability. The siege engine is built. The bridge is primed.

**Drop the ramp.**

---

## Licence

AGPL-3.0-or-later. Software that exists to keep you out of someone else's walled
garden should not be usable to build one: the network-use clause means a company
cannot take Sambuca, run it as a hosted product, and give nothing back.
