# Hardware

## What runs it

Anything x86-64 with 8 GB of RAM and a 128 GB disk will produce a working
appliance. Everything above that changes how good the AI half is, not whether
the cloud half works.

| | minimum | comfortable | tier 1 |
|---|---|---|---|
| CPU | 2 cores | 8 cores | 8+ cores |
| RAM | 8 GB | 32 GB | 64 GB |
| System disk | 128 GB | 512 GB NVMe | 1 TB NVMe |
| GPU | none | 12–16 GB | 24 GB+ |

The system disk holds the OS, containers and models. Photos and files belong on
the storage pool, which is separate — see below.

## Which tier you land in

`engine/hardware-detect.sh` decides. Check before you buy anything:

```bash
./engine/hardware-detect.sh --print --dry-run --no-lock
```

| Tier | Trigger | Chat | Code | Vision | Photo ML |
|---|---|---|---|---|---|
| 1 | ≥ 24 GB VRAM | 32B q4 (70B above 40 GB) | 14B | 11B | GPU |
| 2 | 12–23 GB VRAM | 14B q4 | 7B | 7B | CPU |
| 3 | ≥ 8 cores + ≥ 24 GB RAM | 8B q4 | 7B | 1.8B | CPU |
| 4 | anything else | 3B q4 | — | — | CPU |

Tiers are decided on **total** VRAM across same-vendor cards, because Ollama
splits a model across them. `SAMBUCA_VRAM_LARGEST_MB` is also recorded for
models that cannot split.

An unreadable VRAM value never rounds up. A machine whose driver has not loaded
is classified from CPU and RAM, and re-profiled after the driver installs.

## GPU support, stated accurately

**NVIDIA** — full support via the Container Toolkit. Anything Ollama supports.

**AMD** — ROCm via `/dev/kfd`. Officially supported cards work. Consumer RDNA
cards usually need `HSA_OVERRIDE_GFX_VERSION` set to the nearest supported gfx
target; a wrong value segfaults rather than degrading, so it is an explicit
override rather than something guessed.

**Intel Arc** — treated as CPU-tier for inference. Ollama ships no supported
SYCL backend, and claiming acceleration that does not exist would be worse than
being slow. Immich *can* use OpenVINO on Intel — set
`IMMICH_ML_IMAGE_SUFFIX=-openvino` in `/etc/sambuca/profile.local.env`.

**Apple Silicon** — not supported. This is an x86-64 appliance installer.

## Storage

One disk works fine and needs no configuration.

Multiple disks become a MergerFS union with optional SnapRAID parity. Declare
them in the flasher config as `/dev/disk/by-id/` paths:

```json
{
  "data_disks":   ["/dev/disk/by-id/ata-WDC_WD40EFRX_...",
                   "/dev/disk/by-id/ata-ST8000VN004_..."],
  "parity_disks": ["/dev/disk/by-id/ata-ST12000VN008_..."]
}
```

Rules the installer enforces, not suggests:

- **The parity disk must be at least as large as the largest data disk.**
- Disks may be mismatched sizes and mismatched ages — that is the point of
  choosing MergerFS over RAID.
- A disk carrying an existing ext4/xfs filesystem is **adopted, not
  reformatted**. Anything carrying another signature (LVM, LUKS, NTFS, ZFS)
  causes the phase to abort rather than assume.
- The root/boot disk is excluded, twice, independently.
- Pull a disk out of a MergerFS pool and its files are still there, on a plain
  filesystem, readable in any machine. No proprietary format, no rebuild.

## Reclaimed hardware notes

- **Old office desktops (i5/i7 6th–8th gen, 8–16 GB):** tier 3 or 4. Excellent
  appliances. The cloud services are fully responsive; generation is slow but
  real.
- **Laptops:** work, but check that the lid-close suspend setting is disabled —
  a suspended appliance is an offline appliance.
- **Anything without AVX2:** llama.cpp will run but slowly. `SAMBUCA_CPU_AVX2=0`
  in the profile is your warning.
- **Mixed-generation multi-GPU:** `SAMBUCA_GPU_HOMOGENEOUS=0` in the profile.
  Tier is still computed on total VRAM, but expect the slowest card to set the
  pace when a model is split.
