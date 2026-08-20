"""The manifest's tier thresholds must match the profiler that enforces them.

REPLACES a parity test that pinned the deleted estimator against
engine/hardware-detect.sh. The estimator is gone — it asked a novice to type
the specs of a DIFFERENT computer and guessed from the sentence — but the
property that test protected is now more important, not less.

The manifest is FETCHED LIVE. Its tier numbers are what the project tells
people before they commit hardware, and hardware-detect.sh is what the machine
actually enforces on first boot. If they drift, the project misinforms someone
who is deciding what to buy, and it does so from a file that updates itself
without anyone reviewing a diff.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "manifest" / "sambuca-manifest.json"
PROFILER = ROOT / "engine" / "hardware-detect.sh"

# manifest key -> shell variable
PAIRS = {
    "min_ram_mb": "MIN_RAM_MB",
    "tier1_vram_mb": "TIER1_VRAM_MB",
    "tier2_vram_mb": "TIER2_VRAM_MB",
    "tier3_cpu_cores": "TIER3_CPU_CORES",
    "tier3_ram_mb": "TIER3_RAM_MB",
    "immich_gpu_min_vram_mb": "IMMICH_GPU_MIN_VRAM_MB",
    "image_min_vram_mb": "IMAGE_MIN_VRAM_MB",
}


def _shell_default(name: str) -> int:
    """Read a `: "${NAME:=123}"` default out of the profiler."""
    text = PROFILER.read_text(encoding="utf-8")
    m = re.search(rf'{name}:=(\d+)', text)
    assert m, f"{name} is not defined in {PROFILER.name}"
    return int(m.group(1))


def _manifest_tiers() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["tiers"]


@pytest.mark.parametrize(("key", "shell_var"), sorted(PAIRS.items()))
def test_threshold_matches_the_profiler(key, shell_var):
    tiers = _manifest_tiers()
    assert key in tiers, f"manifest is missing the {key!r} threshold"
    assert tiers[key] == _shell_default(shell_var), (
        f"manifest {key}={tiers[key]} but {PROFILER.name} uses "
        f"{shell_var}={_shell_default(shell_var)}. One of them is lying to "
        f"someone about what their hardware will do."
    )


def test_every_manifest_threshold_is_checked():
    """A new threshold must not slip in unpinned.

    Adding one to the manifest without adding it here would let it drift
    silently, which is exactly the failure this file exists to prevent.
    """
    numeric = {k for k, v in _manifest_tiers().items() if isinstance(v, int)}
    unchecked = numeric - set(PAIRS)
    assert not unchecked, (
        f"these manifest thresholds are not pinned to the profiler: "
        f"{sorted(unchecked)}. Add them to PAIRS."
    )


def test_the_memory_floor_is_actually_enforced():
    """The floor is the one threshold that REFUSES a machine.

    It is also the newest, and the one most likely to be quietly relaxed
    because someone wants their small board to work.
    """
    assert _shell_default("MIN_RAM_MB") >= 3000, (
        "the memory floor has been lowered below 3 GB. The file server alone "
        "wants ~2 GB and the smallest chat model ~2.5 GB; below this the "
        "appliance does not come up, and refusing is the honest outcome."
    )
