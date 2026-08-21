"""One version per image, in three files that must agree.

`compose/.env.example` GOVERNS. Its `*_IMAGE=` lines are copied verbatim into
the generated `/opt/sambuca/compose/.env` by engine/provision/60-stack.sh, and
CI validates the compose chain with `--env-file .env.example`. The `${VAR:-…}`
defaults inside the compose files apply only when no env file is supplied.

THAT IS WHY THIS TEST EXISTS. The first version of it read the compose defaults
— the file that does NOT govern — and passed. The two sources had drifted on 14
of 22 images: Vaultwarden, a password manager, was five minor versions apart
(1.32.7 vs 1.37.1), and Pocket ID, the appliance's identity provider, read
v0.53 in one file and v2.5.0 in the other. Nothing was watching, because the
only check pointed at the shadow.

docs/IMAGES.md is the third copy: it carries the manifest digest for every
reference, resolved from the live registries by tools/verify-images.py.

WHAT THIS STILL DOES NOT CHECK: the digests are RECORDED but not APPLIED. Every
reference ships as a bare tag, and a tag is mutable by whoever controls the
repository — so the digest column is currently evidence, not enforcement.
Applying them is task #14, and stating the gap here is the alternative to
letting a passing test imply it is closed.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
ENV_FILE = REPO / "compose" / ".env.example"
IMAGES_DOC = REPO / "docs" / "IMAGES.md"

ENV_RE = re.compile(r"^(?P<var>[A-Z0-9_]+_IMAGE)=(?P<ref>.+)$")
YML_RE = re.compile(r"image:\s*\$\{(?P<var>\w+):-(?P<ref>[^}]+)\}")
DOC_RE = re.compile(
    r"\|\s*`(?P<var>[A-Z0-9_]+_IMAGE)`\s*\|\s*`(?P<ref>[^`]+)`\s*\|\s*`?(?P<digest>[^|`]*)`?\s*\|"
)

MOVING = {"latest", "main", "master", "edge", "stable"}

# Images allowed to float, each with the reason. A bare list becomes a dumping
# ground; requiring a sentence means the next person justifies an entry rather
# than appending to it.
ALLOWED_FLOATING = {
    "NEXTCLOUD_AIO_IMAGE": (
        "The AIO mastercontainer is an updater: it chooses and upgrades the "
        "versions of the containers it manages. Pinning it freezes that "
        "machinery, so the inner Nextcloud stops receiving updates while "
        "still looking maintained."
    ),
}

# Broken for a reason already tracked, so it reports as KNOWN rather than
# either failing CI or passing silently.
KNOWN_UNPUBLISHED = {
    "ODYSSEUS_IMAGE": "first-party, not published to GHCR yet — task #14",
}

EXCEPTED = {**ALLOWED_FLOATING, **KNOWN_UNPUBLISHED}


def _env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        m = ENV_RE.match(line.strip())
        if m:
            out[m.group("var")] = m.group("ref").strip()
    return out


def _compose() -> dict[str, tuple[str, str]]:
    """var -> (reference, file it was found in)."""
    out: dict[str, tuple[str, str]] = {}
    for path in sorted((REPO / "compose").glob("*.yml")):
        for m in YML_RE.finditer(path.read_text(encoding="utf-8")):
            out[m.group("var")] = (m.group("ref").strip(), path.name)
    return out


def _doc() -> dict[str, tuple[str, str]]:
    """var -> (reference, digest)."""
    out: dict[str, tuple[str, str]] = {}
    for m in DOC_RE.finditer(IMAGES_DOC.read_text(encoding="utf-8")):
        out[m.group("var")] = (m.group("ref").strip(), m.group("digest").strip())
    return out


def test_all_three_sources_were_actually_read() -> None:
    """A regex matching nothing passes every assertion below it."""
    assert len(_env()) >= 20, f"parsed only {len(_env())} refs from {ENV_FILE.name}"
    assert len(_compose()) >= 20, f"parsed only {len(_compose())} compose defaults"
    assert len(_doc()) >= 20, f"parsed only {len(_doc())} rows from IMAGES.md"


def test_compose_defaults_match_the_governing_file() -> None:
    """The drift that hid for 14 images, including a password manager."""
    env, comp = _env(), _compose()
    drift = [
        f"{var}: .env.example={env[var]} but {fname}={ref}"
        for var, (ref, fname) in sorted(comp.items())
        if var in env and env[var] != ref
    ]
    assert not drift, "compose defaults disagree with .env.example:\n  " + "\n  ".join(drift)


def test_every_compose_image_is_declared() -> None:
    """A compose default with no env entry is a version nobody is tracking."""
    env, comp = _env(), _compose()
    orphans = sorted(set(comp) - set(env))
    assert not orphans, f"used in compose but absent from .env.example: {orphans}"


def test_documented_versions_match_the_governing_file() -> None:
    env, doc = _env(), _doc()
    drift = [
        f"{var}: .env.example={env[var]} but IMAGES.md={ref}"
        for var, (ref, _digest) in sorted(doc.items())
        if var in env and env[var] != ref
    ]
    assert not drift, "the digest table has drifted:\n  " + "\n  ".join(drift)


def test_every_image_has_a_recorded_digest() -> None:
    """The digest is not enforced yet, but losing it would be a step backwards.

    Three ComfyUI images were missing from the table entirely, so nothing knew
    what bytes they were supposed to be.
    """
    env, doc = _env(), _doc()
    missing = [
        var
        for var in sorted(env)
        if var not in EXCEPTED
        and not doc.get(var, ("", ""))[1].startswith("sha256:")
    ]
    assert not missing, f"no manifest digest recorded for: {missing}"


def test_no_image_floats_on_a_moving_tag() -> None:
    env = _env()
    unexplained = {
        var: ref
        for var, ref in env.items()
        if ref.rsplit(":", 1)[-1] in MOVING and var not in EXCEPTED
    }
    assert not unexplained, (
        "these float on a moving tag with no recorded reason: "
        + ", ".join(f"{v} ({r})" for v, r in sorted(unexplained.items()))
    )


def test_every_exception_carries_a_reason() -> None:
    for var, reason in EXCEPTED.items():
        assert reason.strip(), f"{var} is excepted but no reason is recorded"


def test_exceptions_still_exist() -> None:
    """A stale exception excuses a variable nothing uses, and hides the next."""
    env = _env()
    for var in EXCEPTED:
        assert var in env, f"{var} is excepted but {ENV_FILE.name} does not define it"


def test_no_image_line_is_built_by_concatenation() -> None:
    """The reference that gets CHECKED must be the reference that gets USED.

    cloud.yml pulled `${IMMICH_ML_IMAGE}${IMMICH_ML_IMAGE_SUFFIX}`. Every tool
    verified IMMICH_ML_IMAGE, which resolves perfectly well on its own — and
    nothing verified base+suffix, which is what compose actually pulled. On AMD
    the suffix was `-rocm`, published on no release tag, so the container never
    started and no check could see it.

    A reference assembled at pull time is a reference nobody validated. It also
    makes digest pinning impossible: nothing can be appended to "@sha256:…".
    60-stack.sh now resolves the finished string into .env.
    """
    offenders = []
    for path in sorted((REPO / "compose").glob("*.yml")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "image:" not in stripped:
                continue
            # Anything following the closing brace of the image variable.
            if re.search(r"image:\s*\$\{[^}]+\}\s*\S", stripped):
                offenders.append(f"{path.name}:{i}: {stripped}")
    assert not offenders, (
        "image references assembled by concatenation:\n  " + "\n  ".join(offenders)
    )


def test_immich_ml_never_selects_a_variant_upstream_does_not_publish() -> None:
    """hardware-detect chose `-rocm` on AMD, and that image does not exist.

    ghcr.io/immich-app/immich-machine-learning 404s for BOTH v1.128.0-rocm and
    v1.119.1-rocm — verified against the live registry. Only `main-rocm` is
    published, and `main` is Immich's development branch. So every AMD appliance
    asked for an image that was not there: the container never started, and
    photo search and face recognition did not work.

    It was invisible to every existing check because verify-images.py resolves
    IMMICH_ML_IMAGE, which is fine on its own. Nothing verified base+suffix,
    which is what compose actually pulls. This asserts the invariant directly:
    every suffix the engine can assign must name a variant that exists.
    """
    src = (REPO / "engine" / "hardware-detect.sh").read_text(encoding="utf-8")
    assigned = set(re.findall(r'IMMICH_ML_IMAGE_SUFFIX="([^"]*)"', src))
    publishes = {"", "-cuda", "-openvino"}
    bad = sorted(assigned - publishes)
    assert not bad, (
        f"hardware-detect.sh can select {bad}, which upstream does not publish "
        "on a release tag — the container would fail to pull"
    )


def test_pinned_tags_look_like_versions() -> None:
    env = _env()
    suspicious = []
    for var, ref in env.items():
        if var in EXCEPTED:
            continue
        tag = ref.rsplit(":", 1)[-1] if ":" in ref else ""
        if not tag:
            suspicious.append(f"{var} has no tag at all ({ref})")
        elif not re.search(r"\d", tag):
            suspicious.append(f"{var} tag carries no digits ({tag})")
    assert not suspicious, "; ".join(suspicious)
