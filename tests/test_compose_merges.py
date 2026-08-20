"""Compose overlays, checked without needing docker.

WHY THIS EXISTS AS WELL AS THE CI LOOP. The workflow already validates every
GPU profile against every bundle subset with `docker compose config`, and that
is the authority. But it only runs in CI, on a machine with docker, and when it
failed it took three commits to find out why — because the check discarded
stderr and reported only that something was wrong.

This runs anywhere, in a second, and names the exact problem.

THE BUG IT WAS WRITTEN FOR: gpu.amd.image.yml repeated `no-new-privileges:true`,
which image.yml already sets. Compose APPENDS list values when merging overlays
rather than replacing them, so the entry appeared twice, and compose rejects a
security_opt list containing duplicates. That invalidated the ENTIRE project —
not merely that service — so every AMD-plus-image combination refused to start.
It sat there undiagnosed because the only thing anyone saw was "FAIL".
"""

from __future__ import annotations

import collections
import itertools
import pathlib

import pytest
import yaml

COMPOSE = pathlib.Path(__file__).resolve().parents[1] / "compose"
BUNDLES = ["ai", "cloud", "office", "comms", "image"]
GPUS = ["cpu", "nvidia", "amd"]

# Keys where a repeat is not merely untidy but INVALID to compose, or changes
# meaning. security_opt is the one that bit; the capability lists are the same
# shape and would fail the same way.
STRICT_LIST_KEYS = ("security_opt", "cap_drop", "cap_add")


def _load(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _chains():
    """Every (gpu, bundles, files) combination CI validates."""
    for gpu in GPUS:
        for r in range(len(BUNDLES) + 1):
            for sel in itertools.combinations(BUNDLES, r):
                files = [COMPOSE / "docker-compose.yml"]
                files += [COMPOSE / f"{b}.yml" for b in sel]
                files += [COMPOSE / f"gpu.{gpu}.{b}.yml" for b in sel
                          if (COMPOSE / f"gpu.{gpu}.{b}.yml").is_file()]
                yield gpu, sel, files


def _merged_string_lists(files) -> dict[tuple[str, str], list[str]]:
    """Approximate compose's merge for lists of plain strings: append."""
    merged: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for f in files:
        for name, spec in (_load(f).get("services") or {}).items():
            for key, val in (spec or {}).items():
                if isinstance(val, list) and all(isinstance(x, str) for x in val):
                    merged[(name, key)].extend(val)
    return merged


def test_no_overlay_repeats_a_value_the_base_already_sets():
    problems = []
    for gpu, sel, files in _chains():
        for (svc, key), vals in _merged_string_lists(files).items():
            if key not in STRICT_LIST_KEYS:
                continue
            dupes = [v for v, n in collections.Counter(vals).items() if n > 1]
            if dupes:
                problems.append(
                    f"gpu={gpu} bundles={list(sel)}: {svc}.{key} would contain "
                    f"{dupes} twice — compose appends, it does not replace")
    assert not problems, (
        "these merges produce duplicate entries and compose rejects the whole "
        "project, not just the service:\n  " + "\n  ".join(problems[:10]))


@pytest.mark.parametrize("gpu", GPUS)
def test_every_gpu_overlay_only_extends_services_that_exist(gpu):
    """The failure this repository already shipped once.

    A GPU overlay naming a service the selected bundles do not define
    invalidates the entire project — that is why image.yml is separate from
    ai.yml at all.
    """
    for bundle in BUNDLES:
        overlay = COMPOSE / f"gpu.{gpu}.{bundle}.yml"
        bundle_file = COMPOSE / f"{bundle}.yml"
        if not overlay.is_file():
            continue
        assert bundle_file.is_file(), f"{overlay.name} extends a bundle that does not exist"
        defined = set((_load(bundle_file).get("services") or {}))
        defined |= set((_load(COMPOSE / "docker-compose.yml").get("services") or {}))
        named = set((_load(overlay).get("services") or {}))
        missing = named - defined
        assert not missing, (
            f"{overlay.name} configures {sorted(missing)}, which "
            f"{bundle}.yml does not define — this invalidates the whole project")


def test_the_amd_image_overlay_still_relaxes_seccomp():
    """A guard on the fix, not just on the bug.

    The duplicate was removed by deleting a line. Deleting the WRONG line would
    silently drop the seccomp relaxation ROCm needs, and nothing else here would
    notice — the project would validate cleanly and ComfyUI would fail on a
    machine nobody testing this owns.
    """
    overlay = COMPOSE / "gpu.amd.image.yml"
    opts = ((_load(overlay).get("services") or {}).get("comfyui") or {}).get("security_opt", [])
    assert "seccomp:unconfined" in opts, "ROCm needs this; losing it breaks AMD silently"
    assert "no-new-privileges:true" not in opts, (
        "image.yml already sets it; repeating it here is the duplicate that "
        "broke every AMD+image combination")
