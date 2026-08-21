#!/usr/bin/env python3
"""
sambuca :: tools/verify-images.py

Resolve every `*_IMAGE` reference in compose/.env.example against its registry
and report the manifest digest.

Talks the OCI distribution API directly rather than shelling out to
`docker buildx imagetools`, because the machine building a release is often not
the machine running Docker — and a release check that only works where a daemon
happens to be installed is a check that gets skipped.

Exit codes:
    0   every reference resolved
    1   at least one THIRD-PARTY reference is broken
    2   only first-party references are unresolved (expected before publication)

The 1-vs-2 split matters: `ghcr.io/laboratoiresonore/*` not resolving means
"we have not published it yet", which is a known state of the project. A broken
upstream reference means the installer would fail on a user's machine. Collapsing
both into "failed" trains people to ignore the check.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

FIRST_PARTY_PREFIXES = ("ghcr.io/laboratoiresonore/",)

ACCEPT = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)

TOKEN_ENDPOINTS = {
    "registry-1.docker.io": (
        "https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
    ),
    "ghcr.io": "https://ghcr.io/token?scope=repository:{repo}:pull",
    "quay.io": "https://quay.io/v2/auth?service=quay.io&scope=repository:{repo}:pull",
}

TIMEOUT = 30


def _http_only(url: str) -> str:
    """Refuse anything but http(s).

    These tools read URLs out of the manifest, and urlopen honours file:.
    A tampered manifest could point this at a local file and have it read
    and reported as if it were fetched.
    """
    from urllib.parse import urlsplit
    if urlsplit(url).scheme.lower() not in ('http', 'https'):
        raise SystemExit(f'refusing non-http URL: {url}')
    return url
def parse_ref(ref: str) -> tuple[str, str, str]:
    """Split an image reference into (registry host, repository, tag-or-digest)."""
    if "@" in ref:
        name, tag = ref.split("@", 1)
        # `repo:tag@sha256:…` is legal, and it is the form worth shipping: the
        # tag stays readable for a human, the digest is what actually gets
        # fetched. Without this, the tag stays glued to the NAME and the
        # repository becomes "library/caddy:2.11.4-alpine", so every lookup
        # 404s — the verifier would fail on precisely the references that are
        # pinned hardest.
        if ":" in name.rsplit("/", 1)[-1]:
            name = name.rsplit(":", 1)[0]
    elif ":" in ref.rsplit("/", 1)[-1]:
        name, tag = ref.rsplit(":", 1)
    else:
        name, tag = ref, "latest"

    parts = name.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        return parts[0], "/".join(parts[1:]), tag

    # Bare names are Docker Hub, and single-segment names live under library/.
    repo = name if "/" in name else f"library/{name}"
    return "registry-1.docker.io", repo, tag


def get_token(host: str, repo: str) -> str | None:
    endpoint = TOKEN_ENDPOINTS.get(host)
    if not endpoint:
        return None
    try:
        with urllib.request.urlopen(endpoint.format(repo=repo), timeout=TIMEOUT) as r:  # noqa: S310 - scheme checked by _http_only
            body = json.load(r)
        return body.get("token") or body.get("access_token")
    except Exception:  # noqa: BLE001 - an anonymous registry needs no token
        return None


def resolve(ref: str) -> tuple[bool, str]:
    host, repo, tag = parse_ref(ref)
    token = get_token(host, repo)
    req = urllib.request.Request(f"https://{host}/v2/{repo}/manifests/{tag}", method="HEAD")
    req.add_header("Accept", ACCEPT)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 - scheme checked by _http_only
            return True, r.headers.get("Docker-Content-Digest") or "(no digest header)"
    except urllib.error.HTTPError as e:
        # GHCR answers 401 for both "private" and "does not exist". Either way an
        # appliance pulling anonymously cannot get it, which is what we test.
        hint = " (private or nonexistent — not anonymously pullable)" if e.code == 401 else ""
        return False, f"HTTP {e.code}{hint}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def is_first_party(ref: str) -> bool:
    return ref.startswith(FIRST_PARTY_PREFIXES)


def read_refs(env_file: Path) -> dict[str, str]:
    refs: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.endswith("_IMAGE") and value:
            refs[key] = value
    return refs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("env_file", nargs="?", default="compose/.env.example", type=Path)
    ap.add_argument("--json", dest="json_out", type=Path,
                    help="write a machine-readable report here")
    ap.add_argument("--pin", action="store_true",
                    help="rewrite the env file, pinning each reference to its digest")
    args = ap.parse_args()

    if not args.env_file.is_file():
        print(f"error: {args.env_file} not found", file=sys.stderr)
        return 1

    refs = read_refs(args.env_file)
    if not refs:
        print(f"error: no *_IMAGE references in {args.env_file}", file=sys.stderr)
        return 1

    report: dict[str, dict] = {}
    broken: list[str] = []
    unpublished: list[str] = []

    print(f"resolving {len(refs)} image reference(s) from {args.env_file}\n")
    for key, ref in refs.items():
        ok, info = resolve(ref)
        if ok:
            status = "OK"
        elif is_first_party(ref):
            status = "UNPUBLISHED"
            unpublished.append(key)
        else:
            status = "BROKEN"
            broken.append(key)

        report[key] = {"ref": ref, "status": status, "detail": info}
        print(f"  {status:<12} {key:<24} {ref}")
        print(f"  {'':<12} -> {info}")

    resolved = len(refs) - len(broken) - len(unpublished)
    print(f"\n{resolved}/{len(refs)} resolved")

    if args.pin:
        # ONE implementation of resolution, reused. The Makefile target used to
        # shell out to `docker buildx imagetools inspect`, which needs a running
        # daemon — the exact dependency this tool exists to avoid, and a fair
        # explanation for why nothing was ever pinned: the release step only
        # worked on a machine that happened to have Docker.
        #
        # Only references that RESOLVED are rewritten. Pinning an unpublished
        # or broken one to whatever a failed lookup returned would bake a lie
        # into the file that installs the appliance.
        text = args.env_file.read_text(encoding="utf-8")
        pinned = 0
        for key, info in report.items():
            if info["status"] != "OK" or not str(info["detail"]).startswith("sha256:"):
                continue
            base = info["ref"].split("@", 1)[0]
            # NEVER PIN A MOVING TAG. This needs no exception list, because it
            # follows from the reference itself: a tag chosen for its mobility
            # is one you must not freeze. The first run of --pin pinned
            # nextcloud/all-in-one:latest, and that is the mastercontainer —
            # an updater whose whole job is choosing versions for the
            # containers it manages. Pinning it stops Nextcloud receiving
            # updates while leaving it looking maintained.
            if base.rsplit(":", 1)[-1] in {"latest", "main", "master", "edge", "stable"}:
                continue
            new = f"{key}={base}@{info['detail']}"
            old_line = f"{key}={info['ref']}"
            if old_line in text and new != old_line:
                text = text.replace(old_line, new, 1)
                pinned += 1
        if pinned:
            args.env_file.write_text(text, encoding="utf-8", newline="\n")
        print(f"pinned {pinned} reference(s) in {args.env_file}")
        skipped = sorted(set(broken) | set(unpublished))
        if skipped:
            print(f"  left unpinned (did not resolve): {', '.join(skipped)}")

    if unpublished:
        print("\nUNPUBLISHED (first-party, expected before release):")
        for k in unpublished:
            print(f"  {k} — publish it publicly to GHCR, then re-run this check")

    if broken:
        print("\nBROKEN (an appliance would fail to pull these):")
        for k in broken:
            print(f"  {k} = {report[k]['ref']}")
        print("\nDo not tag a release until every third-party reference resolves.")

    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nreport: {args.json_out}")

    if broken:
        return 1
    if unpublished:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
