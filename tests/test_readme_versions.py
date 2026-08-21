"""The README's component table must match what compose actually ships.

THE TABLE THIS GUARDS IS THE MOST IMPORTANT ONE IN THE README. It is what
somebody reads while deciding whether to put their family photos and their
client files on this — and on 2026-08-20 it was wrong for TEN of fourteen
components, including Pocket ID, where it claimed 2.5.0 while compose shipped
v0.53. Not a point release: a different major version, in the identity provider
that gates every other service.

NOTHING WAS WATCHING. tools/check-upstreams.py verifies that each image
reference still RESOLVES in its registry, which an old pinned tag does happily
forever. Resolving and being the version you advertised are different claims,
and only the first had a check.

WHY A TEST RATHER THAN CARE. The table is hand-written, the tags live in seven
compose files, and they drift the moment either is touched — which is exactly
what happened. A promise to keep two lists in sync is not a mechanism.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose"
README = ROOT / "README.md"

# Which compose variable backs each row of the README table. Explicit, because
# guessing from the name is how "pgvecto-rs" and IMMICH_DB_IMAGE end up
# silently unmatched and a mismatch passes as "not found".
ROW_TO_VAR = {
    "Caddy": "CADDY_IMAGE",
    "Ollama": "OLLAMA_IMAGE",
    "Immich": "IMMICH_SERVER_IMAGE",
    "Vaultwarden": "VAULTWARDEN_IMAGE",
    "Blinko": "BLINKO_IMAGE",
    "BentoPDF": "BENTOPDF_IMAGE",
    "Ergo": "ERGO_IMAGE",
    "Synapse": "SYNAPSE_IMAGE",
    "Uptime Kuma": "UPTIME_KUMA_IMAGE",
    "Pocket ID": "POCKET_ID_IMAGE",
    "oauth2-proxy": "OAUTH2_PROXY_IMAGE",
    "Valkey": "REDIS_IMAGE",
    "pgvecto-rs": "IMMICH_DB_IMAGE",
    "Watchtower": "WATCHTOWER_IMAGE",
    "PostgreSQL": "POSTGRES_IMAGE",
}

# Rows that deliberately carry no version. Each one is a documented decision,
# not an oversight — see docs/MAINTENANCE.md.
EXEMPT = {
    "Nextcloud AIO",   # :latest by design; a mastercontainer that self-updates
    "Odysseus",        # unpublished; the table says so
    "Debian", "Docker CE", "Tailscale", "CasaOS", "MergerFS", "SnapRAID",
    "restic",          # distribution packages, not images we pin
}


def _compose_tags() -> dict[str, str]:
    tags: dict[str, str] = {}
    for f in sorted(COMPOSE.glob("*.yml")):
        for m in re.finditer(r"image:\s*\$\{([A-Z0-9_]+):-([^}]+)\}",
                             f.read_text(encoding="utf-8")):
            ref = m.group(2)
            tags.setdefault(m.group(1), ref.rsplit(":", 1)[-1] if ":" in ref else "")
    return tags


def _readme_rows() -> dict[str, str]:
    rows = {}
    for label, ver in re.findall(
            r"^\|\s*\*\*([A-Za-z0-9 ._-]+)\*\*\s*\|\s*([^|]+?)\s*\|",
            README.read_text(encoding="utf-8"), re.M):
        rows.setdefault(label.strip(), ver.strip())
    return rows


def _norm(v: str) -> str:
    """Compare versions, not decoration: a leading v, an -alpine suffix."""
    v = v.strip().strip("*").lstrip("v")
    return re.sub(r"-(alpine|slim)$", "", v)


def test_every_advertised_version_is_the_one_that_ships():
    tags, rows = _compose_tags(), _readme_rows()
    wrong = []
    for label, var in ROW_TO_VAR.items():
        if label not in rows:
            continue
        if var not in tags:
            wrong.append(f"{label}: README lists it, but {var} is in no compose file")
            continue
        if _norm(rows[label]) != _norm(tags[var]):
            wrong.append(f"{label}: README says {rows[label]!r}, "
                         f"compose ships {tags[var]!r}")
    assert not wrong, (
        "The README advertises versions that are not what installs. This is the "
        "table people read to decide whether to trust the project:\n  "
        + "\n  ".join(wrong))


def test_the_mapping_still_covers_the_table():
    """A row added to the README without an entry here would be UNCHECKED, and
    the test above would pass while saying nothing about it."""
    rows = _readme_rows()
    # Only consider rows that look like component rows (a version-ish column).
    candidates = {k for k, v in rows.items()
                  if re.match(r"^[v0-9*]", v) or v.startswith("*")}
    unchecked = candidates - set(ROW_TO_VAR) - EXEMPT
    assert not unchecked, (
        f"these component rows are checked by nothing: {sorted(unchecked)} — "
        "add them to ROW_TO_VAR, or to EXEMPT with a reason")


@pytest.mark.parametrize("var", sorted(set(ROW_TO_VAR.values())))
def test_the_mapping_points_at_something_real(var):
    """Guards the other direction: a renamed compose variable would make the
    check above silently skip that component."""
    assert var in _compose_tags(), f"{var} is in the mapping but in no compose file"
