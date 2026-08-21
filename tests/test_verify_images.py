"""The image verifier must understand the references the appliance ships.

tools/verify-images.py is what proves, before a release, that every container
reference actually resolves against a live registry. It had no test.

The specific hole this closes: `repo:tag@sha256:…` — a tag AND a digest, which
is the form worth shipping, because the tag stays readable for a human while
the digest is what gets fetched. `parse_ref` split on "@" and left the tag glued
to the name, so the repository became `library/caddy:2.11.4-alpine` and the
lookup 404'd. The verifier would have failed on exactly the references that are
pinned hardest, and the failure would have looked like a broken upstream rather
than a broken parser.

Found by asking what would break BEFORE applying digests, rather than applying
them and reading the wreckage.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "verify-images.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_images", TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


VI = _load()

DOCKERHUB = "registry-1.docker.io"

CASES = [
    # plain tag, single-segment name -> the implicit library/ namespace
    ("caddy:2.11.4-alpine", (DOCKERHUB, "library/caddy", "2.11.4-alpine")),
    # plain tag, two-segment name
    ("ollama/ollama:0.32.15", (DOCKERHUB, "ollama/ollama", "0.32.15")),
    # a registry host is recognised by the dot in its first segment
    ("ghcr.io/pocket-id/pocket-id:v2.5.0", ("ghcr.io", "pocket-id/pocket-id", "v2.5.0")),
    ("quay.io/oauth2-proxy/oauth2-proxy:v7.14.2",
     ("quay.io", "oauth2-proxy/oauth2-proxy", "v7.14.2")),
    # digest only
    ("ghcr.io/pocket-id/pocket-id@sha256:1549", ("ghcr.io", "pocket-id/pocket-id", "sha256:1549")),
    # THE REGRESSION: tag AND digest together
    ("caddy:2.11.4-alpine@sha256:5f5c", (DOCKERHUB, "library/caddy", "sha256:5f5c")),
    ("ghcr.io/pocket-id/pocket-id:v2.5.0@sha256:1549",
     ("ghcr.io", "pocket-id/pocket-id", "sha256:1549")),
    # no tag at all defaults to latest, as the registry does
    ("nextcloud/all-in-one", (DOCKERHUB, "nextcloud/all-in-one", "latest")),
]


@pytest.mark.parametrize(("ref", "expected"), CASES)
def test_parse_ref(ref: str, expected: tuple[str, str, str]) -> None:
    assert VI.parse_ref(ref) == expected


def test_digest_wins_over_the_tag() -> None:
    """When both are present the DIGEST is what gets fetched.

    If the tag were returned instead, a pinned reference would silently resolve
    by mutable tag — pinning that does nothing, which is worse than no pinning
    because it looks like protection.
    """
    _host, _repo, ref = VI.parse_ref("caddy:2.11.4-alpine@sha256:deadbeef")
    assert ref == "sha256:deadbeef"


def test_file_urls_are_refused() -> None:
    """urlopen honours file:, and these tools read URLs out of a manifest.

    It exits rather than raising — deliberate for a CLI, and asserted here as
    SystemExit specifically so that if it were ever softened into a warning the
    test would notice. A blind `Exception` would have passed either way.
    """
    with pytest.raises(SystemExit):
        VI._http_only("file:///etc/passwd")
