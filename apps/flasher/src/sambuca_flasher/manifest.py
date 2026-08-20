"""
sambuca :: everything that can change without the code changing, fetched live.

A downloaded binary is frozen on the day it was built. When an image is
reissued, a checksum rotates, a dependency moves, or a board is finally tested,
a binary with those facts baked in is simply wrong — and most people never
download a new one. So the flasher carries almost no facts. It fetches them.

WHAT IS LIVE: which images to offer and their checksums, which hardware has
actually been tested, where dependencies come from and how to install them, the
tier thresholds, and every link shown to a human.

WHAT IS NOT: how to write a card (that is rpi-imager's job) and what Sambuca
puts on the boot partition afterwards (that is code, and it ships with the
code).

THE FALLBACK IS NOT OPTIONAL. This runs on a machine that may have no network,
or a network that hates it. A flasher that refuses to start because GitHub is
slow is worse than one carrying month-old checksums, so a copy is bundled and
used whenever the fetch does not work. Which copy was used is always reported,
because silently running on stale data is how someone ends up debugging a
checksum mismatch that was fixed weeks ago.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MANIFEST_URL = os.environ.get(
    "SAMBUCA_MANIFEST",
    "https://raw.githubusercontent.com/laboratoiresonore/Sambuca/main/manifest/sambuca-manifest.json",
)

# The schema this build understands. A newer manifest with a higher number is
# NOT parsed hopefully — it is refused in favour of the bundled copy, because
# guessing at a format you do not know is how you write a wrong checksum to a
# card.
SUPPORTED_SCHEMA = 1

# Short on purpose. This is on the startup path, and a person waiting on a
# window has a much lower tolerance than a background job.
FETCH_TIMEOUT = 6.0

CACHE_TTL = 3600.0

_cache: dict[str, Any] = {}
_cache_at = 0.0


class ManifestError(RuntimeError):
    """No usable manifest, live or bundled."""


def _bundled_path() -> Path | None:
    """The copy shipped inside the binary, or in the repo when run from source."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        p = Path(bundled) / "manifest" / "sambuca-manifest.json"
        if p.is_file():
            return p

    p = Path(__file__).resolve().parents[4] / "manifest" / "sambuca-manifest.json"
    return p if p.is_file() else None


def _load_bundled() -> dict[str, Any] | None:
    p = _bundled_path()
    if p is None:
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    doc["_source"] = f"bundled ({p})"
    return doc


def _fetch(url: str, timeout: float) -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "sambuca-flasher", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            doc = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None

    if doc.get("schema_version") != SUPPORTED_SCHEMA:
        # Deliberately not "best effort". An unknown schema means unknown field
        # meanings, and this file carries checksums.
        return None

    doc["_source"] = f"live ({url})"
    return doc


def load(*, refresh: bool = False, timeout: float = FETCH_TIMEOUT) -> dict[str, Any]:
    """The manifest: live if reachable, bundled otherwise.

    Never raises for a network problem — only when there is no usable copy at
    all, which means the build itself is broken.
    """
    global _cache, _cache_at

    if _cache and not refresh and (time.monotonic() - _cache_at) < CACHE_TTL:
        return _cache

    doc = _fetch(MANIFEST_URL, timeout) or _load_bundled()
    if doc is None:
        raise ManifestError(
            "no manifest available, live or bundled. This build is incomplete; "
            f"expected one at {MANIFEST_URL} or inside the application."
        )

    _cache, _cache_at = doc, time.monotonic()
    return doc


def is_live(doc: dict[str, Any] | None = None) -> bool:
    doc = doc or load()
    return str(doc.get("_source", "")).startswith("live")


def source(doc: dict[str, Any] | None = None) -> str:
    doc = doc or load()
    return str(doc.get("_source", "unknown"))


# --- typed accessors --------------------------------------------------------
# Callers get defaults rather than KeyErrors: a manifest missing a field should
# degrade one feature, not crash the application.

def link(name: str, default: str = "") -> str:
    return str(load().get("links", {}).get(name, default))


def os_list_url() -> str:
    return link(
        "os_list",
        "https://raw.githubusercontent.com/laboratoiresonore/Sambuca/main/os-list/sambuca-os-list.json",
    )


def tiers() -> dict[str, int]:
    raw = load().get("tiers", {})
    return {k: v for k, v in raw.items() if isinstance(v, int)}


def tested_devices() -> list[dict[str, Any]]:
    return list(load().get("tested_devices", []))


def images() -> list[dict[str, Any]]:
    return list(load().get("images", []))


def dependency(name: str) -> dict[str, Any]:
    return dict(load().get("dependencies", {}).get(name, {}))


def install_command(name: str) -> list[str]:
    """The platform's install command for a dependency, from the manifest.

    Held as data so a package id can be corrected without reissuing binaries —
    winget ids in particular get renamed.
    """
    key = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    cmd = dependency(name).get("install", {}).get(key, [])
    return [str(x) for x in cmd] if isinstance(cmd, list) else []
