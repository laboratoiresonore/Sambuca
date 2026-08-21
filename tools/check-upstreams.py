#!/usr/bin/env python3
"""
sambuca :: tools/check-upstreams.py

Probe every external coupling recorded in docs/MAINTENANCE.md and report drift.

This exists because "the dev team monitors it" is a promise, and a promise is
not a monitor. Anything in the register that a machine *can* check is checked
here, daily, so the couplings that genuinely need a human (protocol bridges) are
the only ones relying on one.

Checks, in order of how badly they fail:

  images    every *_IMAGE reference still resolves in its registry
  models    every model in every tier profile still exists in the Ollama library
  apt       the apt repositories added during provisioning still serve metadata
  scripts   remote-execution endpoints are reachable, and their content hash is
            reported so an unpinned installer changing under us is at least
            VISIBLE even though it is not verified

Exit codes:
    0   nothing has drifted
    1   something a user would hit is broken
    2   only known-pending items (first-party image not yet published)
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT = 30
UA = {"User-Agent": "sambuca-upstream-check/1"}

REPO = Path(__file__).resolve().parents[1]

# Remote code fetched during provisioning. The hash is reported, not enforced —
# there is nothing to enforce against until CasaOS is pinned (see MAINTENANCE.md
# Tier 2). Reporting it at least turns a silent change into a visible one.
REMOTE_SCRIPTS = {
    "casaos-installer": "https://get.casaos.io",
}

# BOTH SUITES, because the appliance picks one at RUNTIME. 20-docker.sh and
# 50-network.sh read VERSION_CODENAME from /etc/os-release, and pi.py does the
# same on the card — bookworm is only a fallback. The Pi image this project
# ships is now trixie (2026-06-18-raspios-trixie-arm64-lite), so probing
# bookworm alone was watching a suite the appliance no longer installs: if
# either publisher dropped trixie, this check would have stayed green while
# every new install failed at apt.
APT_REPOS = {
    "docker (bookworm)": "https://download.docker.com/linux/debian/dists/bookworm/Release",
    "docker (trixie)": "https://download.docker.com/linux/debian/dists/trixie/Release",
    "tailscale (bookworm)": "https://pkgs.tailscale.com/stable/debian/dists/bookworm/Release",
    "tailscale (trixie)": "https://pkgs.tailscale.com/stable/debian/dists/trixie/Release",
    "nvidia-container-toolkit":
        "https://nvidia.github.io/libnvidia-container/stable/deb/amd64/Packages",
}

OLLAMA_TAG_API = "https://registry.ollama.ai/v2/library/{name}/manifests/{tag}"


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


def fetch(url: str, *, head: bool = False, accept: str | None = None):
    _http_only(url)
    req = urllib.request.Request(url, method="HEAD" if head else "GET",  # noqa: S310 - scheme checked by _http_only
                                 headers=dict(UA))
    if accept:
        req.add_header("Accept", accept)
    return urllib.request.urlopen(req, timeout=TIMEOUT)  # noqa: S310 - scheme checked by _http_only


# --------------------------------------------------------------------- images


def check_images() -> tuple[int, int, list[str]]:
    """Delegate to verify-images.py so there is ONE implementation of the
    registry protocol, not two that can disagree."""
    script = REPO / "tools" / "verify-images.py"
    env_file = REPO / "compose" / ".env.example"
    # check=False deliberately: verify-images.py uses its exit code to say WHAT
    # drifted (1 = broken third-party, 2 = first-party unpublished), and raising
    # on it would throw away the distinction this whole tool exists to make.
    proc = subprocess.run(
        [sys.executable, str(script), str(env_file)],
        capture_output=True, text=True, timeout=600, check=False,
    )
    broken = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip().startswith("BROKEN")]
    ok_n = proc.stdout.count("  OK ")
    return proc.returncode, ok_n, broken


# --------------------------------------------------------------------- models


# The tier profiles hold BOTH model names and size estimates, and both start
# with MODEL_. Matching the prefix alone treats `MODEL_SET_EST_MB=18700` as a
# model called "18700" and reports permanent, bogus drift — which is how a
# monitor teaches people to ignore it. Match the name variables explicitly.
MODEL_VARS = ("MODEL_CHAT", "MODEL_CHAT_XL", "MODEL_CODE", "MODEL_VISION", "MODEL_EMBED")


def read_models() -> dict[str, str]:
    """Every model reference across all tier profiles."""
    pattern = re.compile(
        r'^\s*(' + "|".join(MODEL_VARS) + r')\s*=\s*"?([^"#\s]*)"?'
    )
    models: dict[str, str] = {}
    for prof in sorted((REPO / "engine" / "profiles").glob("tier*.env")):
        for line in prof.read_text(encoding="utf-8").splitlines():
            m = pattern.match(line)
            # An empty value is legitimate: tier 4 ships no code or vision model.
            if m and m.group(2):
                models[m.group(2)] = prof.name
    return models


def check_model(ref: str) -> tuple[bool, str]:
    name, _, tag = ref.partition(":")
    tag = tag or "latest"
    # Namespaced models (org/model) live outside library/.
    path = name if "/" in name else f"library/{name}"
    url = OLLAMA_TAG_API.format(name=path.removeprefix("library/"), tag=tag)
    if "/" in name:
        url = f"https://registry.ollama.ai/v2/{name}/manifests/{tag}"
    try:
        with fetch(url, head=True,
                   accept="application/vnd.docker.distribution.manifest.v2+json") as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}"


# ----------------------------------------------------------------------- apt


def check_url(url: str) -> tuple[bool, str]:
    try:
        with fetch(url, head=True) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        # Some mirrors reject HEAD but serve GET. If the retry also fails we
        # want the ORIGINAL status reported, not the retry's — suppressing here
        # keeps the more informative error.
        if e.code in (403, 405):
            with contextlib.suppress(Exception), fetch(url) as r:
                return True, f"HTTP {r.status} (GET)"
        return False, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}"


def hash_remote(url: str) -> tuple[bool, str]:
    try:
        with fetch(url) as r:
            body = r.read(4 * 1024 * 1024)
        return True, hashlib.sha256(body).hexdigest()[:16]
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}"


# ---------------------------------------------------------------------- main


def check_os_list() -> tuple[bool, list[str]]:
    """The Pi write path, end to end: list -> content type -> the image itself.

    MAINTENANCE.md names this as the coupling that fails SILENTLY: rpi-imager
    ignores a list served as text/plain and shows an EMPTY DEVICE PICKER with no
    error, indistinguishable from a broken file. It was documented as a risk and
    probed by nothing.

    Three ways it breaks, so three checks:
      - the list stops resolving (jsDelivr, or the branch ref moves)
      - it comes back as text/plain, which rpi-imager discards in silence
      - the image URL inside it 404s, because Raspberry Pi rotates and REMOVES
        old image paths — the list stays valid while the download dies
    """
    import sys as _sys
    sys.path.insert(0, str(REPO / "apps/flasher/src"))
    try:
        from sambuca_flasher import imager
        url = imager.default_repo()
    except Exception as exc:                       # noqa: BLE001
        return False, [f"cannot determine the OS-list URL: {exc.__class__.__name__}"]
    finally:
        if str(REPO / "apps/flasher/src") in _sys.path:
            _sys.path.remove(str(REPO / "apps/flasher/src"))

    problems: list[str] = []
    req = urllib.request.Request(_http_only(url), headers=UA)  # noqa: S310 - scheme checked by _http_only
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - scheme checked by _http_only
            ctype = (r.headers.get("Content-Type") or "").lower()
            body = r.read(1 << 20)
    except Exception as exc:                       # noqa: BLE001
        return False, [f"os-list unreachable: {exc.__class__.__name__}"]

    if "application/json" not in ctype:
        problems.append(
            f"os-list served as {ctype!r}, not application/json — "
            "rpi-imager discards it and shows an empty picker with no error")
    try:
        entries = json.loads(body).get("os_list", [])
    except Exception as exc:                       # noqa: BLE001
        return False, [f"os-list is not valid JSON: {exc.__class__.__name__}"]

    if not entries:
        problems.append("os-list has no entries — the device picker would be empty")

    for entry in entries:
        img = entry.get("url") or ""
        if not img:
            problems.append(f"{entry.get('name', '?')}: no image url")
            continue
        ok, detail = check_url(img)
        if not ok:
            problems.append(f"image gone: {img} ({detail})")
    return not problems, problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", dest="json_out", type=Path)
    ap.add_argument("--skip-images", action="store_true",
                    help="skip the registry pass (it is slow)")
    args = ap.parse_args()

    report: dict = {}
    broken: list[str] = []
    pending: list[str] = []

    print("sambuca upstream drift check")
    print("register: docs/MAINTENANCE.md\n")

    # --- images ---
    if not args.skip_images:
        print("[images]")
        rc, ok_n, bad = check_images()
        report["images"] = {"exit": rc, "ok": ok_n, "broken": bad}
        if rc == 1:
            broken.append("container images")
            print(f"  BROKEN — {len(bad)} reference(s) do not resolve")
        elif rc == 2:
            pending.append("first-party image unpublished")
            print(f"  {ok_n} resolve; first-party image not yet published (known)")
        else:
            print(f"  all {ok_n} resolve")

    # --- models ---
    print("\n[models]")
    models = read_models()
    report["models"] = {}
    for ref, prof in sorted(models.items()):
        ok, detail = check_model(ref)
        report["models"][ref] = {"ok": ok, "detail": detail, "profile": prof}
        if ok:
            print(f"  ok      {ref}")
        else:
            # A withdrawn model breaks FRESH INSTALLS ONLY — an existing
            # appliance keeps running it — so this drift is otherwise invisible.
            print(f"  MISSING {ref}  ({detail}, from {prof})")
            broken.append(f"model {ref}")

    # --- apt ---
    print("\n[apt repositories]")
    report["apt"] = {}
    for name, url in APT_REPOS.items():
        ok, detail = check_url(url)
        report["apt"][name] = {"ok": ok, "detail": detail}
        print(f"  {'ok     ' if ok else 'BROKEN '} {name:<26} {detail}")
        if not ok:
            broken.append(f"apt repo {name}")

    # --- the Pi write path ---
    print("\n[raspberry pi os list]")
    ok, problems = check_os_list()
    report["os_list"] = {"ok": ok, "problems": problems}
    if ok:
        print("  ok      os-list resolves, is application/json, and its image exists")
    else:
        for pr in problems:
            print(f"  BROKEN  {pr}")
        broken.append("raspberry pi os list")

    # --- remote scripts ---
    print("\n[remote code executed at install time]")
    report["scripts"] = {}
    for name, url in REMOTE_SCRIPTS.items():
        ok, detail = hash_remote(url)
        report["scripts"][name] = {"ok": ok, "sha256_prefix": detail}
        if ok:
            print(f"  reachable  {name:<20} sha256:{detail}…")
            print(f"             {url}")
            print("             UNPINNED AND UNVERIFIED — this hash is reported so a")
            print("             change is visible, not because anything enforces it.")
        else:
            print(f"  BROKEN     {name} — {detail}")
            broken.append(f"install script {name}")

    # --- bridges ---
    print("\n[bridges — Tier 1]")
    print("  Not machine-checkable. Protocol bridges break in ways only a human")
    print("  notices, which is exactly why they are Tier 1 in the register.")
    print("  Weekly human review of upstream releases is the control.")
    report["bridges"] = {"automated": False, "control": "weekly human review"}

    # --- summary ---
    print("\n" + "=" * 62)
    if broken:
        print("DRIFT DETECTED — an installation today would hit these:")
        for b in broken:
            print(f"  - {b}")
    elif pending:
        print("No drift. Known-pending only:")
        for p in pending:
            print(f"  - {p}")
    else:
        print("No drift. Every machine-checkable coupling resolves.")
    print("=" * 62)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nreport: {args.json_out}")

    if broken:
        return 1
    if pending:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
