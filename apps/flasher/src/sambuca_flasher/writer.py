"""
sambuca :: payload injection.

ONE operation now. `write_image()` used to live here and has been deleted:
Raspberry Pi Imager writes the image, correctly, on three platforms, and
reimplementing it cost five failed attempts at a single Windows raw write
before anyone got a byte onto a card.

What remains is the half that was always ours — putting the sambuca directory
(preseed, engine, provision.json) onto the boot partition after something else
has written it. That is also the half that carries secrets, which is why it was
always kept separate from the writing.
"""

from __future__ import annotations

import contextlib
import platform
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from .devices import DeviceError

_CHUNK = 4 * 1024 * 1024   # 4 MiB: large enough to saturate USB 3, small enough
                           # that progress reporting stays responsive.

ProgressFn = Callable[[int, int], None]


def inject_payload(
    boot: Path,
    payload_dir: Path,
) -> Path:
    """Copy the sambuca payload onto an already-written boot partition.

    Takes the MOUNTED PARTITION, not a device. Sambuca no longer writes images,
    so it never holds a device handle — rpi-imager wrote the card and
    pi.find_boot_partition() located the result.

    Returns the destination directory.
    """
    boot = Path(boot)
    payload_dir = Path(payload_dir)
    if not payload_dir.is_dir():
        raise DeviceError(f"payload directory not found: {payload_dir}")
    if not boot.is_dir():
        raise DeviceError(
            f"boot partition not mounted at {boot}.\n"
            "Re-insert the stick, wait for it to appear, then run:\n"
            "  sambuca-flasher provision-pi"
        )

    dest = boot / "sambuca"
    # Copy over the top rather than removing first: on Windows a directory
    # removal can still be pending when the next mkdir runs, and the result is
    # ERROR_ACCESS_DENIED on a path that was fine a moment earlier. Observed on
    # a real card, while elevated.
    shutil.copytree(payload_dir, dest, dirs_exist_ok=True)

    # provision.json carries a single-use Tailscale key and, in unattended mode,
    # the preseed beside it carries the disk passphrase. 0600 is meaningless on
    # FAT32, which is why the recovery document says the stick is a key rather
    # than relying on file permissions that the filesystem cannot express.
    for sensitive in ("provision.json", "preseed.cfg"):
        p = dest / sensitive
        if p.exists():
            # FAT32 cannot express Unix permissions, so this is expected to
            # fail on the boot partition. Suppressed rather than logged because
            # the security story does not rest on it — the recovery document
            # tells the owner to treat the stick itself as a key.
            with contextlib.suppress(OSError, NotImplementedError):
                p.chmod(0o600)

    _sync()
    return dest


# ---------------------------------------------------------------------------


def _sync() -> None:
    if platform.system() != "Windows":
        try:
            subprocess.run(["sync"], timeout=60, check=False)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
