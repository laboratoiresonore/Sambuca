"""Every container image must name a version somebody chose.

`:latest` on an appliance is not a version, it is a promise to run whatever
upstream pushed while nobody was looking — on a machine holding the owner's
passwords, photos and client documents. CLAUDE.md says dated tags, not
`:latest`, and this is the check that makes that true rather than aspirational.

WHAT THIS DOES NOT YET CHECK: digests. None of the images are pinned by
`@sha256:` — a tag is mutable, so `:v1.119.1` can be repointed at different
bytes by anyone who controls the repository. Tag discipline is the floor, not
the ceiling, and the gap is recorded in the task list rather than papered over
here.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = REPO / "compose"

# `image: ${SOME_VAR:-the/default:tag}`
IMAGE_RE = re.compile(r"image:\s*\$\{(?P<var>\w+):-(?P<default>[^}]+)\}")

# Images allowed to float, each with the reason. A bare list would become a
# dumping ground; requiring a sentence means the next person has to justify an
# entry rather than append to it. Adding one edits this file, which shows up in
# review — that is the point.
ALLOWED_FLOATING = {
    "NEXTCLOUD_AIO_IMAGE": (
        "The AIO mastercontainer is an updater: it chooses and upgrades the "
        "versions of the containers it manages. Pinning it freezes that "
        "machinery, so the inner Nextcloud stops receiving updates while "
        "looking maintained. Dated tags exist upstream but are snapshots of "
        "the updater, not of the deployment."
    ),
}

# Images that are broken for a reason already tracked, so the check reports
# them as KNOWN rather than either failing CI or silently passing.
KNOWN_UNPUBLISHED = {
    "ODYSSEUS_IMAGE": "not published yet — task #14 (releasable v0.1.0)",
}


def _images() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(COMPOSE.glob("*.yml")):
        for m in IMAGE_RE.finditer(path.read_text(encoding="utf-8")):
            found[m.group("var")] = m.group("default").strip()
    return found


def test_compose_files_were_actually_read() -> None:
    """A regex that matches nothing passes every test below it."""
    images = _images()
    assert len(images) >= 20, f"only found {len(images)} images — the parser is wrong"


def test_no_image_floats_on_latest() -> None:
    images = _images()
    floating = {
        var: ref
        for var, ref in images.items()
        if ref.rsplit(":", 1)[-1] in {"latest", "main", "master", "edge", "stable"}
    }
    unexplained = {
        var: ref
        for var, ref in floating.items()
        if var not in ALLOWED_FLOATING and var not in KNOWN_UNPUBLISHED
    }
    assert not unexplained, (
        "these images float on a moving tag with no recorded reason: "
        + ", ".join(f"{v} ({r})" for v, r in sorted(unexplained.items()))
    )


def test_every_exception_carries_a_reason() -> None:
    """An allowlist entry with an empty reason is just a floating image."""
    for var, reason in {**ALLOWED_FLOATING, **KNOWN_UNPUBLISHED}.items():
        assert reason.strip(), f"{var} is excepted but no reason is recorded"


def test_exceptions_still_exist() -> None:
    """A stale exception hides the next real one.

    If an image is renamed or dropped, its entry here would silently keep
    excusing a variable nothing uses — and the list would drift into fiction.
    """
    images = _images()
    for var in {**ALLOWED_FLOATING, **KNOWN_UNPUBLISHED}:
        assert var in images, f"{var} is excepted but no compose file uses it"


def test_pinned_images_look_like_versions() -> None:
    """A tag that is not obviously a version is worth a second look."""
    images = _images()
    suspicious = []
    for var, ref in images.items():
        if var in ALLOWED_FLOATING or var in KNOWN_UNPUBLISHED:
            continue
        tag = ref.rsplit(":", 1)[-1] if ":" in ref else ""
        if not tag:
            suspicious.append(f"{var} has no tag at all ({ref})")
        elif not re.search(r"\d", tag):
            suspicious.append(f"{var} tag carries no digits ({tag})")
    assert not suspicious, "; ".join(suspicious)
