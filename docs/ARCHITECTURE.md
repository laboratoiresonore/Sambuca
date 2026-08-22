# Architecture

> **Every coupling to something outside this repository is registered in
> [MAINTENANCE.md](MAINTENANCE.md)** — what breaks, whether it fails loudly or
> silently, how fast it moves, and what watches it. Components marked ⚠ below
> have an entry there. Adding a new external dependency means adding a row in
> the same pull request.

## The three planes

```
                    ┌──────────────────────────────────────────┐
   your laptop ────▶│  Tailscale (WireGuard, no open ports)    │
   your phone       └────────────────────┬─────────────────────┘
                                         │  tailscale serve, ports 443/8443-8452
   your LAN ─────────────────────────────┤  (loopback only)
   (subdomains, internal CA)             │
                                    ┌────▼─────┐
                                    │  Caddy   │   the only ingress
                                    └────┬─────┘
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
              ┌─────▼─────┐        ┌─────▼─────┐        ┌─────▼──────┐
              │   edge    │        │    ai     │        │    data    │
              │  network  │        │  network  │        │ internal:  │
              │           │        │           │        │    true    │
              │ odysseus  │        │  ollama   │        │ postgres   │
              │ immich    │        │ odysseus  │        │ redis      │
              │ nextcloud │        │  blinko   │        │ immich-ml  │
              │ vaultwarden│       └───────────┘        └────────────┘
              │ pocket-id │
              │ oauth2-px │        Ollama has NO edge         no route
              │ blinko    │        membership: nothing        off the
              │ bentopdf  │        reaches the model          host, at
              │ uptime    │        server that should not     all
              └───────────┘
```

The host itself runs only: Docker ⚠, Tailscale ⚠, CasaOS ⚠ (on 8095, moved off
80), MergerFS/SnapRAID, and the sambuca systemd timers. Everything else is a
container. The host stays thin and auditable.

**⚠ CasaOS is the weakest link in the project.** It is installed by piping an
unpinned, unsigned script into a root shell — the only remote-execution point
here that is not signature-verified. Whoever controls that URL controls every
appliance at install time. It is tolerated because CasaOS has no packaged
distribution and the dashboard is optional; Caddy serves every service without
it. See [MAINTENANCE.md](MAINTENANCE.md) Tier 2 for the fix, in preference
order.

**⚠ The messaging bridges (Signal, WhatsApp, IRC) are Tier 1** — they speak
protocols owned by companies that do not want them spoken, they break silently,
and WhatsApp bridging carries a real if small account-suspension risk for the
owner. They are their own bundle, excluded from unattended updates, and must not
ship without a health monitor and a named human watching upstream.

## Boot to working appliance

```
  USB inserted
      │
      ├─ disk-select.sh      resolve the target, or REFUSE
      ├─ abort-countdown.sh  30s, showing the disk's current contents
      │
      ├─ debian-installer    LUKS full-disk, headless base
      ├─ late-command.sh     stage /opt/sambuca, arm systemd, DO NOT provision
      │
      ▼  reboot
  sambuca-first-boot.service
      │
      ├─ ingest provision.json ──▶ /etc/sambuca/provision.env (0600)
      │                            then SHRED it from /boot
      ├─ hardware-detect ────────▶ /etc/sambuca/profile.env
      │
      ├─ 10-system      users, ssh, sysctl, security upgrades
      ├─ 20-docker      Docker CE, log rotation, validated daemon.json
      ├─ 30-gpu-runtime driver + toolkit ──▶ RE-RUN hardware-detect
      ├─ 40-storage     mergerfs union, snapraid parity (never formats data)
      ├─ 50-network     tailscale up, casaos port, nftables default-deny
      ├─ 60-stack       render .env, VALIDATE, compose up, HEALTH GATE
      ├─ 70-models      pull for the tier, then PROVE generation works
      ├─ 80-identity    pocket-id, fail-closed gate, one attended step
      └─ 90-report      completion report → MOTD
```

Every phase is idempotent and records completion in `/var/lib/sambuca/state`. A
failure halts with a precise error and a resume command; it does not roll back,
because a half-rolled-back storage pool is worse than a stopped one.

## Why hardware-detect runs three times

1. **First boot, pre-driver.** `lspci` can see an NVIDIA card exists but nothing
   can read its VRAM. The profiler reports `VRAM_UNKNOWN=1` and classifies from
   CPU/RAM. It does not guess a tier from the card's marketing name.
2. **After 30-gpu-runtime.** `nvidia-smi` now answers. The tier settles, the
   model set is chosen, the GPU overlay is selected. This re-run is the reason
   the stack starts with correct resource limits rather than conservative ones.
3. **Every boot after.** Cards get added, removed, and fail. A profile derived
   once at install time is a profile that becomes a lie.

## The VRAM arbitration

Two processes want the same card: the inference engine, and Immich's face
recognition and CLIP indexer. Neither can see the other's allocations. On a
16 GB card, a resident 14B model (≈9 GB + KV cache) plus a CLIP batch is an OOM
during a library import — at 3am, unattended, taking the container down with it.

The rule is one-directional and has no fallback path:

> **The inference engine owns the GPU. Background ML is a guest.**

- Below `IMMICH_GPU_MIN_VRAM_MB` (20 GB) of *measured* VRAM, the Immich ML image
  suffix is empty — the CPU variant is pulled. There is no "try GPU, fall back"
  path, because a fallback that triggers under memory pressure triggers exactly
  when the machine can least afford the churn.
- Above it, Immich gets a device reservation, and `OLLAMA_KEEP_ALIVE` is bounded
  so idle VRAM is actually returned rather than camped on.
- Both get cgroup memory ceilings derived from real RAM, so a runaway worker
  hits its own limit instead of the kernel OOM killer picking whatever has the
  largest RSS — which is usually Postgres.

Forcing a tier with `--force-tier` does **not** override this. The tier selects
models; the arbitration reads measured VRAM. An operator can ask for a bigger
model than is wise; they cannot talk the machine into believing it has memory it
does not have.

## Failure philosophy

Three rules the code is built around, each traceable to a specific class of
self-hosted disaster:

**A success code is a claim, not evidence.** `backup.sh` handles `restic` exit 3
(partial backup) as a loud warning with a file count, and then verifies by
restoring a file and comparing. A wrapper that logs `done OK` over 17 of 966
files is worse than no backup, because it removes the anxiety that would have
made someone check.

**Refuse rather than guess.** `disk-select.sh` stops if its rules do not produce
exactly one answer. `40-storage-pool.sh` refuses any device carrying an
unexpected signature. `gitops-sync.sh` refuses an unsigned tag.

**Destroying recovery state requires a human.** `snapraid-sync.sh` aborts when
an abnormal number of files were deleted, because syncing would overwrite the
parity that could undo them. `restic prune` is opt-in. `WATCHTOWER_REMOVE_VOLUMES`
is false.

**Fatal is reserved for what is actually fatal** — added 2026-08-22, after
getting it wrong. `first-boot.sh` runs the phases in order and stops on the first
failure (`run_phase … || { rc=1; break; }`), which is right: provisioning half a
machine and calling it done is how you get a file server with no certificates.
The consequence is that **every `die` in a phase is a decision to abandon the
whole appliance**, and that is a much higher bar than "this step did not work".

`50-network.sh` failed it. Obtaining Tailscale had four `die`s — signing key, apt
update, package install, daemon start — so a network that blocks
`pkgs.tailscale.com` (corporate, school, some ISPs) meant the machine installed
Debian, booted, died at phase 50, and never provisioned the stack, the
certificates or the setup page. It powered on and did nothing, on exactly the
networks least able to diagnose it. Meanwhile `tailscale up` failing twenty lines
below was already a warning, every consumer downstream guarded on `sb_have
tailscale`, and LAN-only was a documented mode. Only *acquiring* the optional
thing was treated as life-or-death.

So the test for a `die` is not "did this fail" but **"is the appliance worth
having without it"**. A missing disk is fatal. A missing Docker is fatal. A
missing remote-access convenience is a warning that names what was lost, what
still works, and how to add it later — and, because a half-added apt repository
breaks every subsequent `apt-get update`, it cleans up after itself on the way
out.

## The CA private key never leaves the host

Caddy's internal CA signs everything on the LAN, so its key is the most
load-bearing secret on the appliance after the disk key: whoever holds it can
mint certificates that every device the owner set up will trust, for every
service.

**No container is ever given it, for any reason.** Services that do not speak
HTTP — IRC today, anything similar later — get a *dedicated leaf certificate*
minted on the host by `engine/maintenance/issue-service-cert.sh`, which reads
the CA key as root, uses it once, and writes out only the leaf and its own key.

This rule exists because the obvious shortcut was taken and shipped: Ergo was
mounted Caddy's CA directory and told to serve `root.crt`/`root.key` as its TLS
certificate. That handed a chat container the CA private key — one container
escape away from total MITM of the whole appliance — and it could not have
worked anyway, since a CA root has no hostname SAN and asserts `CA:TRUE`.
Registered in [MAINTENANCE.md](MAINTENANCE.md) Tier 5.

## Every stage tells the owner what is happening

An unattended installer that prints nothing but log lines is indistinguishable
from a hung machine, and a non-technical owner watching `exec: apt-get` scroll
for forty minutes will eventually power-cycle it — during disk provisioning,
which is how installs get corrupted.

So every stage announces, before it starts: **what is happening, how long it
usually takes, what the owner should do** (usually nothing, and saying so is the
point), and **what comes next**. On failure it says what it means, that nothing
after it has run, and the exact command to resume. The helpers are `sb_stage`,
`sb_stage_ok` and `sb_stage_failed` in `engine/lib/common.sh`; the per-phase
wording lives in the `STAGE_INFO` table in `first-boot.sh`, in the owner's
language rather than ours.
