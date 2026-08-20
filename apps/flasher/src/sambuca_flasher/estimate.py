"""
sambuca :: what will this machine actually be able to do?

Answers the question people ask BEFORE they commit a machine or spend money:
"is the old office PC in my cupboard good enough, and if not, what is?"

It applies the SAME thresholds as engine/hardware-detect.sh, against declared
specs rather than measured ones. The tier boundaries are duplicated here as
constants and pinned by a test that reads them back out of the shell script — if
the two ever disagree, the estimator is lying to someone deciding what to buy,
which is worse than not having one.

Deliberately offline. No hardware database to fetch, no lookup service, no
telemetry about what people are considering. A table of common machines and a
parser for free text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .console import ascii_safe

# ---------------------------------------------------------------------------
# Mirrors engine/hardware-detect.sh. tests/test_estimate.py parses the shell
# script and asserts these still match.
# ---------------------------------------------------------------------------
TIER1_VRAM_MB = 24000
TIER2_VRAM_MB = 11500
TIER3_CPU_CORES = 8
TIER3_RAM_MB = 24000
IMMICH_GPU_MIN_VRAM_MB = 20000


@dataclass
class Spec:
    cores: int = 0
    ram_mb: int = 0
    vram_mb: int = 0
    gpu: str = ""
    arch: str = "x86_64"
    label: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class Estimate:
    tier: int
    tier_name: str
    chat_model: str
    speed: str
    photo_ai: str
    pictures: str
    steward: str
    spec: Spec
    caveats: list[str] = field(default_factory=list)


# Common consumer cards, by VRAM. Only what changes the answer.
GPU_VRAM = {
    "5090": 32768, "4090": 24576, "3090 ti": 24576, "3090": 24576,
    "4080": 16384, "5080": 16384, "4070 ti super": 16384, "4060 ti 16": 16384,
    "3080 ti": 12288, "3080": 10240, "4070": 12288, "3060 12": 12288,
    "4060 ti": 8192, "4060": 8192, "3070": 8192, "3060 ti": 8192,
    "2080 ti": 11264, "1080 ti": 11264, "1080": 8192, "a4000": 16384,
    "a5000": 24576, "p40": 24576, "mi100": 32768, "7900 xtx": 24576,
    "7900 xt": 20480, "6800 xt": 16384, "arc a770": 16384,
}

# Machines people actually have or buy, so the common cases need no parsing.
PRESETS: dict[str, Spec] = {
    "raspberry pi 5 16gb": Spec(4, 16384, 0, "", "aarch64", "Raspberry Pi 5 (16 GB)",
                                ["No GPU acceleration — CPU inference only."]),
    "raspberry pi 5 8gb": Spec(4, 8192, 0, "", "aarch64", "Raspberry Pi 5 (8 GB)",
                               ["No GPU acceleration — CPU inference only."]),
    "raspberry pi 5 4gb": Spec(4, 4096, 0, "", "aarch64", "Raspberry Pi 5 (4 GB)",
                               ["4 GB is tight. 8 GB or more is strongly recommended."]),
    "raspberry pi 4 8gb": Spec(4, 8192, 0, "", "aarch64", "Raspberry Pi 4 (8 GB)",
                               ["Noticeably slower than a Pi 5; USB storage only."]),
    "optiplex": Spec(6, 16384, 0, "", "x86_64", "ex-office small-form-factor desktop"),
    "elitedesk": Spec(6, 16384, 0, "", "x86_64", "ex-office small-form-factor desktop"),
    "thinkcentre": Spec(6, 16384, 0, "", "x86_64", "ex-office small-form-factor desktop"),
    "nuc": Spec(4, 16384, 0, "", "x86_64", "mini PC"),
    "old laptop": Spec(4, 8192, 0, "", "x86_64", "an old laptop"),
}

RPI_GUIDANCE = """
  A Raspberry Pi is a genuinely good sambuca machine for the cloud half —
  files, photos, passwords, calendar and chat all behave exactly as they do on
  a large box. Only the AI is slow.

  What you need:
    - Raspberry Pi 5, 8 GB or 16 GB   (4 GB works but is cramped)
    - The official 27 W USB-C power supply. Under-powering a Pi 5 causes
      failures that look like software bugs and waste an evening.
    - An NVMe HAT and an NVMe drive, or at minimum a good A2 microSD.
      SD cards wear out under database writes; NVMe is the difference
      between "fine for years" and "corrupt in eight months".
    - Active cooling. A Pi 5 running photo indexing will throttle without it.

  Buy from an official reseller rather than a marketplace — counterfeit power
  supplies are common and are the single most frequent cause of instability:
    https://www.raspberrypi.com/products/raspberry-pi-5/
    https://www.raspberrypi.com/resellers/
"""


def parse(text: str) -> Spec:
    """Turn free text into a spec. Generous, and honest about what it guessed."""
    t = (text or "").strip().lower()
    spec = Spec(label=text.strip())

    for name, preset in PRESETS.items():
        if name in t:
            spec = Spec(**{**preset.__dict__, "notes": list(preset.notes)})
            spec.label = text.strip() or preset.label
            break

    # RAM. Two bugs lived here, both found by running the estimator on the
    # machines it is meant to warn people about.
    #
    # 1. MEGABYTES WERE NEVER PARSED. The pattern matched only gb|g, so
    #    "512MB" fell through to the 8 GB default and the machine was reported
    #    as comfortably installable. Every machine the memory floor exists to
    #    refuse — a Pi Zero, a thin client, a small VM — is specified in MB.
    #
    # 2. `if ram and "ram" in t or (ram and not spec.ram_mb)` binds as
    #    (A and B) or (A and C). When a preset had already set ram_mb, an
    #    explicit figure the user typed was discarded: "OptiPlex 8 cores 32GB"
    #    kept the preset's 16 GB and reported the wrong tier. A number someone
    #    typed themselves must always win over a default we guessed for them.
    ram_gb = re.search(r"(\d+)\s*(?:gb|g)\b(?!\s*(?:vram|video))", t)
    ram_mb = re.search(r"(\d+)\s*mb\b(?!\s*(?:vram|video))", t)
    if ram_mb:
        spec.ram_mb = int(ram_mb.group(1))
    elif ram_gb:
        spec.ram_mb = int(ram_gb.group(1)) * 1024

    cores = re.search(r"(\d+)\s*(?:core|cores|cpu)", t)
    if cores:
        spec.cores = int(cores.group(1))

    vram = re.search(r"(\d+)\s*(?:gb|g)\s*(?:vram|video)", t)
    if vram:
        spec.vram_mb = int(vram.group(1)) * 1024

    for name, mb in sorted(GPU_VRAM.items(), key=lambda kv: -len(kv[0])):
        if name in t:
            spec.gpu = name.upper()
            spec.vram_mb = spec.vram_mb or mb
            break

    if re.search(r"\bno (graphics|gpu|video card)\b|integrated|onboard", t):
        spec.vram_mb = 0
        spec.gpu = ""

    if re.search(r"raspberry|\brpi\b|\bpi 5\b|\bpi 4\b", t):
        spec.arch = "aarch64"

    if not spec.cores:
        spec.cores = 4
        spec.notes.append("Assumed 4 CPU cores — say '8 cores' to be exact.")
    if not spec.ram_mb:
        spec.ram_mb = 8192
        spec.notes.append("Assumed 8 GB of RAM — say '16GB RAM' to be exact.")
    return spec


def estimate(spec: Spec) -> Estimate:
    """Apply the same rules hardware-detect.sh applies on the real machine."""
    caveats = list(spec.notes)

    if spec.vram_mb >= TIER1_VRAM_MB:
        tier, name = 1, "heavy-gpu"
        chat = "32B (70B above 40 GB of VRAM)"
        speed = "Fast — generates faster than you read"
    elif spec.vram_mb >= TIER2_VRAM_MB:
        tier, name = 2, "mid-gpu"
        chat, speed = "14B", "Comfortable — roughly 20-40 words a second"
    elif spec.cores >= TIER3_CPU_CORES and spec.ram_mb >= TIER3_RAM_MB:
        tier, name = 3, "cpu-capable"
        chat, speed = "8B", "Slow but usable — roughly 5-12 words a second"
    else:
        tier, name = 4, "low-resource"
        chat, speed = "3B", "Patient — about a sentence at a time"

    # Picture generation, matching the rules in engine/profiles/tierN.env.
    # The README leads with "it draws"; an estimator that stays silent about it
    # is telling a reader less than the front page does.
    if tier in (1, 2):
        pictures = "Yes — seconds per picture" if tier == 1 else "Yes — a little slower"
    elif tier == 3:
        pictures = "Optional, and slow — MINUTES per picture (off by default)"
    else:
        pictures = "No — not offered on this class of machine"

    # The Steward is present on every tier; only how you drive it changes.
    steward = {
        1: "Free-form speech",
        2: "Free-form speech",
        3: "Speech, narrower phrasings",
        4: "Guided menus with a search box",
    }[tier]

    photo = "on the GPU" if spec.vram_mb >= IMMICH_GPU_MIN_VRAM_MB else "on the CPU"
    if 0 < spec.vram_mb < IMMICH_GPU_MIN_VRAM_MB:
        caveats.append(
            f"You have a GPU, but at {spec.vram_mb // 1024} GB the photo AI stays on "
            "the CPU. The inference engine gets the whole card — two things "
            "competing for it is how a self-hosted box runs out of memory mid-import."
        )

    if spec.arch == "aarch64":
        caveats.append(
            "ARM (Raspberry Pi): the engine is x86-64 today, so this is NOT yet "
            "installable. Support is planned — see docs/design/NEXT-STAGE.md."
        )
    if spec.ram_mb < 3500:
        caveats.append(
            f"REFUSED: {_human_mb(spec.ram_mb)} of RAM is below the hard floor of "
            "3.5 GB, and the installer will decline this machine. The file server "
            "alone wants ~2 GB, the photo library ~4 GB with its database, and the "
            "smallest chat model ~2.5 GB. This is not a slow install, it is one "
            "that will not come up."
        )
    elif spec.ram_mb < 8192:
        caveats.append(
            f"{_human_mb(spec.ram_mb)} of RAM is below the practical floor. "
            "8 GB is the minimum for the cloud services to be comfortable."
        )
    return Estimate(tier, name, chat, speed, photo, pictures, steward, spec, caveats)


def _human_mb(mb: int) -> str:
    """Render a memory figure without lying about small ones.

    `mb // 1024` turns 512 MB into "0 GB". Reporting that a machine has zero
    memory is both wrong and self-defeating: it appears in the very warning
    that is trying to be taken seriously.
    """
    if mb < 1024:
        return f"{mb} MB"
    if mb % 1024 == 0:
        return f"{mb // 1024} GB"
    return f"{mb / 1024:.1f} GB"


def report(text: str) -> str:
    spec = parse(text)
    est = estimate(spec)
    gpu = f"{spec.gpu} ({spec.vram_mb // 1024} GB)" if spec.vram_mb else "none detected"

    out = [
        "",
        "=" * 68,
        f"  WHAT SAMBUCA CAN DO ON: {spec.label or 'this machine'}",
        "=" * 68,
        "",
        f"  Read as        : {spec.cores} cores, {_human_mb(spec.ram_mb)} RAM, GPU: {gpu}",
        f"  Tier           : {est.tier} ({est.tier_name})",
        "",
        f"  Chat model     : {est.chat_model}",
        f"  Chat speed     : {est.speed}",
        f"  Photo AI runs  : {est.photo_ai}",
        f"  Make pictures  : {est.pictures}",
        f"  Run it by voice: {est.steward}",
        "",
        "  WHAT YOU GET, REGARDLESS OF TIER",
        "  " + "-" * 64,
        "    Files, calendar and contacts   (Nextcloud)",
        "    Photos with face recognition   (Immich)",
        "    Passwords                      (Vaultwarden)",
        "    Notes, PDF tools, encrypted chat",
        "    Encrypted remote access from anywhere",
        "",
        "    These behave identically on every tier. Only the AI gets slower.",
        "    If you are here to leave Google rather than to run a 70B model,",
        "    even tier 4 is genuinely enough.",
        "",
    ]

    if est.caveats:
        out += ["  WORTH KNOWING", "  " + "-" * 64]
        for c in est.caveats:
            for i, line in enumerate(_wrap(c, 62)):
                out.append(("    - " if i == 0 else "      ") + line)
        out.append("")

    if spec.arch == "aarch64":
        out += [RPI_GUIDANCE.rstrip(), ""]

    if est.tier >= 3 and spec.vram_mb == 0:
        out += [
            "  IF YOU WANT FASTER AI",
            "  " + "-" * 64,
            "    Graphics memory is what decides this — not the card's name, and",
            "    not the CPU. A second-hand 12 GB card moves you to tier 2; 24 GB",
            "    reaches tier 1 and also lets the photo AI use the GPU.",
            "",
            "    Everything else about the appliance stays exactly the same.",
            "",
        ]

    out += ["=" * 68, ""]
    return ascii_safe("\n".join(out))


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines
