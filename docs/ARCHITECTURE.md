# Architecture

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

The host itself runs only: Docker, Tailscale, CasaOS (on 8095, moved off 80),
MergerFS/SnapRAID, and the sambuca systemd timers. Everything else is a
container. The host stays thin and auditable.

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
