"""Regressions in the estimator's free-text parser.

The estimator is the first thing the README tells a reader to run, and it is
what people use to decide whether to buy hardware. Both bugs below made it give
a confidently wrong answer, and both were found by running it rather than
reading it.
"""

from __future__ import annotations

import pytest

from sambuca_flasher.estimate import estimate, parse


class TestMegabytesAreParsed:
    """MB was never matched, so every sub-gigabyte machine read as 8 GB.

    That is exactly the class of machine the memory floor exists to refuse — a
    Pi Zero, a thin client, a small VM — so the warning could never fire on the
    hardware it was written for.
    """

    @pytest.mark.parametrize(("text", "expected_mb"), [
        ("512MB", 512),
        ("512 MB", 512),
        ("Raspberry Pi Zero 2 W 512MB", 512),
        ("1024mb ram", 1024),
    ])
    def test_megabytes(self, text, expected_mb):
        assert parse(text).ram_mb == expected_mb

    def test_gigabytes_still_work(self):
        assert parse("16GB").ram_mb == 16384
        assert parse("32 GB RAM").ram_mb == 32768

    def test_a_tiny_machine_is_refused(self):
        est = estimate(parse("Raspberry Pi Zero 2 W 512MB"))
        assert any("REFUSED" in c for c in est.caveats), (
            "512 MB is below the hard floor the installer enforces. Saying so "
            "here is the whole point: the alternative is someone discovering it "
            "after buying the board and burning an evening."
        )

    def test_a_normal_machine_is_not_refused(self):
        est = estimate(parse("desktop, 16GB RAM"))
        assert not any("REFUSED" in c for c in est.caveats)


class TestExplicitValuesBeatPresets:
    """`if ram and "ram" in t or (ram and not spec.ram_mb)` binds as
    (A and B) or (A and C), so a preset that had already set ram_mb discarded
    the figure the user typed.

    A number someone typed themselves must always win over one we guessed.
    """

    def test_typed_ram_overrides_a_preset(self):
        spec = parse("Dell OptiPlex 8 cores 32GB")
        assert spec.ram_mb == 32768, (
            "an explicit 32GB was replaced by a preset default"
        )

    def test_that_wrong_ram_produced_the_wrong_tier(self):
        """The bug's actual consequence, not just its mechanism."""
        est = estimate(parse("Dell OptiPlex 8 cores 32GB"))
        assert est.tier == 3, (
            "8 cores and 32 GB is tier 3. Reporting tier 4 understates the "
            "machine and tells the reader to expect a 3B model instead of an 8B"
        )

    def test_typed_cores_are_respected(self):
        assert parse("OptiPlex 8 cores 32GB").cores == 8


class TestVramIsNotConfusedWithRam:
    """The RAM pattern must not swallow a GPU's memory, or every gaming PC
    reports its VRAM as system memory."""

    def test_vram_is_not_read_as_ram(self):
        spec = parse("RTX 3060 12GB VRAM, 16GB RAM")
        assert spec.ram_mb == 16384
        assert spec.vram_mb == 12288

    def test_a_card_name_alone_still_sets_vram(self):
        assert parse("RTX 4090").vram_mb == 24576


class TestTheAdvertisedCapabilitiesAppear:
    """The README leads with talks / draws / runs-the-machine. An estimator
    silent on two of the three tells the reader less than the front page."""

    @pytest.mark.parametrize(("text", "tier", "draws"), [
        ("RTX 4090", 1, True),
        ("RTX 3060 12GB", 2, True),
        ("8 cores 32GB no graphics card", 3, False),
        ("4 cores 8GB", 4, False),
    ])
    def test_picture_generation_matches_the_tier(self, text, tier, draws):
        est = estimate(parse(text))
        assert est.tier == tier
        assert ("Yes" in est.pictures) is draws

    def test_every_tier_reports_a_steward(self):
        for text in ("RTX 4090", "RTX 3060 12GB", "8 cores 32GB", "4 cores 8GB"):
            assert estimate(parse(text)).steward
