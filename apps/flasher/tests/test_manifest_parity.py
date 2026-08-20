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


class TestFlowContinuity:
    """Each step must have what it needs from the one before it.

    Written after auditing the flow for what every step receives and hands on.
    Three seams were broken, and the first stopped the flow dead:

      1. write-pi REQUIRED --image, a file nobody has and nobody needs, because
         rpi-imager downloads it. The first command anyone runs refused to run.
      2. rpi-imager releases the card when it finishes, so provisioning went
         looking for a partition the OS had already let go of.
      3. Nothing ensured the appliance could reach any network at all — and a
         Pi Zero 2 W has no ethernet, so everything downhill depended on a
         wi-fi setting nobody was told was required.
    """

    def test_write_pi_does_not_demand_an_image(self):
        """The image is rpi-imager's job to fetch, from our own OS list."""
        from sambuca_flasher import cli

        parser_src = __import__("inspect").getsource(cli.main)
        assert '"--image", type=Path,\n' in parser_src or "required=True" not in (
            parser_src[parser_src.index('"--image"'):parser_src.index('"--device"')]
        ), "write-pi must not require an image the user does not have"

    def test_provisioning_handles_a_released_card(self):
        """The Imager dismounts the card; the flow must ask for it back."""
        from sambuca_flasher import cli

        src = __import__("inspect").getsource(cli._cmd_provision_pi)
        assert "released the card" in src, (
            "provisioning must explain that the card was released and ask for "
            "it to be re-seated — silently failing to find it is the seam"
        )
        assert "Press Enter" in src

    def test_wifi_is_stated_as_a_prerequisite(self):
        """A headless appliance on no network cannot be reached or asked why."""
        from sambuca_flasher import cli

        src = __import__("inspect").getsource(cli._cmd_write_pi)
        assert "wifi_configured" in src, (
            "nothing checks whether the appliance will be able to reach a "
            "network, and everything downhill depends on it"
        )

    def test_wifi_check_never_returns_the_value(self):
        """Presence only. The SSID and key are the owner's, not ours."""
        import inspect

        from sambuca_flasher import customisation

        src = inspect.getsource(customisation.wifi_configured)
        assert "wifiSSID" in src, "it must look at the right field"

        # Behaviour, not the annotation: `from __future__ import annotations`
        # makes signatures strings, so checking the annotation proves nothing
        # about what actually comes back.
        result = customisation.wifi_configured()
        assert isinstance(result, bool), (
            f"returned {type(result).__name__}, not a bool — a presence check "
            f"that hands back the value defeats the point of not reading it"
        )

        # And the PowerShell must not emit the value in any branch.
        assert "$k.wifiSSID }" not in src and "Write-Output $k.wifiSSID" not in src, (
            "the SSID itself must never leave PowerShell — only whether it is set"
        )
