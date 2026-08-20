#!/usr/bin/env python3
"""Generate the rpi-imager OS list from the manifest.

Two files need the same facts in different shapes: the manifest is what the
flasher reads, and the OS list is what Raspberry Pi Imager reads. Writing both
by hand means a checksum corrected in one and forgotten in the other, and the
one that would be wrong is the one that gets written to a card.

So the manifest is authoritative and this generates the other. CI runs it with
--check and fails if the committed file has drifted.

Only TESTED hardware appears. The device list is a claim about what works; a
board nobody has written a card for does not belong in it, however confident
anyone is that it would be fine.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "manifest" / "sambuca-manifest.json"
OS_LIST = ROOT / "os-list" / "sambuca-os-list.json"

# rpi-imager reads this to tell users a newer version exists. Taken from the
# upstream list so the prompt stays truthful; it is not a Sambuca version.
UPSTREAM_LATEST = "2.0.11.1"
UPSTREAM_URL = "https://www.raspberrypi.com/software/"

# Fields rpi-imager consumes. Anything else in the manifest is ours and is not
# copied across — the OS list is their format, not a dump of our data.
_DEVICE_FIELDS = ("name", "tags", "icon", "description", "matching_type", "default")


def build(manifest: dict) -> dict:
    devices = []
    for d in manifest.get("tested_devices", []):
        entry = {k: d[k] for k in _DEVICE_FIELDS if k in d}
        entry.setdefault("capabilities", [])
        devices.append(entry)

    if not devices:
        raise SystemExit(
            "build-os-list: the manifest lists no tested devices. Refusing to "
            "generate an OS list with an empty device picker — that is the bug "
            "this generator exists to prevent."
        )

    os_list = []
    for img in manifest.get("images", []):
        os_list.append({
            "name": img["name"],
            "description": img["description"],
            "icon": img.get("icon", devices[0].get("icon", "")),
            "url": img["url"],
            "extract_size": img["extract_size"],
            "extract_sha256": img["extract_sha256"],
            "image_download_size": img["image_download_size"],
            "release_date": img["release_date"],
            "init_format": img.get("init_format", "none"),
            "devices": img["devices"],
        })

    # Every image must be reachable from at least one listed device, or it is
    # invisible in the GUI — which looks identical to a broken download.
    known = {t for d in devices for t in d.get("tags", [])}
    for entry in os_list:
        if not (set(entry["devices"]) & known):
            raise SystemExit(
                f"build-os-list: image {entry['name']!r} targets "
                f"{entry['devices']}, none of which any tested device offers "
                f"({sorted(known)}). It would never appear in the picker."
            )

    return {
        "imager": {
            "latest_version": UPSTREAM_LATEST,
            "url": UPSTREAM_URL,
            "devices": devices,
        },
        "os_list": os_list,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="fail if the committed file differs from the generated one")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rendered = json.dumps(build(manifest), indent=2) + "\n"

    if args.check:
        current = OS_LIST.read_text(encoding="utf-8") if OS_LIST.is_file() else ""
        if current != rendered:
            print("build-os-list: os-list/sambuca-os-list.json is out of date.",
                  file=sys.stderr)
            print("  regenerate it:  python tools/build-os-list.py", file=sys.stderr)
            return 1
        print("build-os-list: OS list matches the manifest")
        return 0

    OS_LIST.parent.mkdir(parents=True, exist_ok=True)
    OS_LIST.write_text(rendered, encoding="utf-8", newline="\n")
    doc = json.loads(rendered)
    print(f"build-os-list: wrote {OS_LIST.relative_to(ROOT)}")
    print(f"  devices : {', '.join(d['name'] for d in doc['imager']['devices'])}")
    print(f"  images  : {', '.join(o['name'] for o in doc['os_list'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
