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

THE DIGESTS ARE NOW APPLIED, not merely recorded. Every reference ships as
`repo:tag@sha256:…`: the tag stays readable, the digest is what actually gets
fetched, and a tag repointed at different bytes by whoever controls the
repository no longer changes what installs.

Two are deliberately not pinned, and both are named in EXCEPTED below with the
reason. Nothing else may join them without one.
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


def _tag_of(ref: str) -> str:
    """The tag, with any `@sha256:…` stripped first.

    A pinned reference is `repo:tag@sha256:…`. Splitting on ":" without removing
    the digest yields a "tag" of "sha256" for every pinned image — which would
    quietly turn the moving-tag and looks-like-a-version checks into assertions
    about nothing, at the exact moment pinning made them matter.
    """
    ref = ref.split("@", 1)[0]
    return ref.rsplit(":", 1)[-1] if ":" in ref.rsplit("/", 1)[-1] else ""

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
    drift = []
    for var, (ref, digest) in sorted(doc.items()):
        if var not in env:
            continue
        env_ref, _, env_digest = env[var].partition("@")
        if env_ref != ref:
            drift.append(f"{var}: .env.example={env_ref} but IMAGES.md={ref}")
        elif env_digest and digest and env_digest != digest:
            # STRONGER NOW THAT PINNING IS REAL: the recorded digest must be
            # the installed one. A table that documents different bytes from
            # the ones that install is worse than no table — it is evidence,
            # confidently, for the wrong thing.
            drift.append(f"{var}: pinned {env_digest} but IMAGES.md records {digest}")
    assert not drift, "the digest table has drifted:\n  " + "\n  ".join(drift)


def test_every_image_is_actually_pinned_by_digest() -> None:
    """A tag is mutable by whoever controls the repository.

    This is the check that makes pinning real rather than aspirational: it
    fails if any reference falls back to a bare tag. update-guard.sh has held
    a rule about changed digests since long before any digest existed — this
    is what finally gives that rule something to guard.
    """
    env = _env()
    unpinned = sorted(
        var for var, ref in env.items()
        if var not in EXCEPTED and "@sha256:" not in ref
    )
    assert not unpinned, f"still resolved by mutable tag: {unpinned}"


def test_every_image_has_a_recorded_digest() -> None:
    """The table must document every image, not most of them.

    Three ComfyUI images were missing from it entirely, so nothing knew what
    bytes they were supposed to be.
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
        if _tag_of(ref) in MOVING and var not in EXCEPTED
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
        tag = _tag_of(ref)
        if not tag:
            suspicious.append(f"{var} has no tag at all ({ref})")
        elif not re.search(r"\d", tag):
            suspicious.append(f"{var} tag carries no digits ({tag})")
    assert not suspicious, "; ".join(suspicious)
