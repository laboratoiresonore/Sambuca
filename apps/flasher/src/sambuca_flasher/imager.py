"""
sambuca :: drive Raspberry Pi Imager instead of reimplementing it.

WHY THIS REPLACED 855 LINES OF OUR OWN CODE.

Sambuca shipped its own image writer: device enumeration, raw device access,
volume locking, elevation, download, checksum verification, readback
verification and a progress display. Every one of those is a solved problem,
and Raspberry Pi Imager solves all of them on Windows, macOS and Linux, is
maintained by the Raspberry Pi Foundation, is translated, and handles UAC
properly.

Ours took FIVE attempts to complete a single Windows raw write. In order: the
C runtime cannot open a physical device at all; the CRT file-descriptor
translation died mid-copy; volume locking matched zero volumes because a fresh
card's partition has no drive letter; and the lock was released before a byte
was written because the handle was closed in a `finally`. Every one of those
bugs is a bug rpi-imager fixed years ago.

The project's own rules said not to do this — "NEVER build something new before
you VERIFY it isn't already solved. Wrap, don't rewrite." This module is that
rule applied late.

WHAT SAMBUCA STILL OWNS. rpi-imager writes an image to a card. It does not know
what Sambuca needs on the boot partition afterwards — the engine, the first-boot
script, the provisioning payload. That part is genuinely ours and stays in
pi.py. The split is clean: THEY WRITE, WE PROVISION.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

# NOT A CONSTANT. The OS-list location, the install commands and the download
# page all come from the manifest, which is fetched live. A URL baked into a
# binary is wrong the day it moves, and nobody redownloads a flasher.
_FALLBACK_REPO = (
    "https://raw.githubusercontent.com/laboratoiresonore/Sambuca"
    "/main/os-list/sambuca-os-list.json"
)


def default_repo() -> str:
    """Where rpi-imager should fetch the Sambuca image list from."""
    override = os.environ.get("SAMBUCA_OS_LIST")
    if override:
        return override
    try:
        from . import manifest

        return manifest.os_list_url() or _FALLBACK_REPO
    except Exception:  # noqa: BLE001 - never block a launch on the manifest
        return _FALLBACK_REPO

_WINDOWS_PATHS = (
    r"C:\Program Files\Raspberry Pi Ltd\Imager\rpi-imager.exe",
    r"C:\Program Files (x86)\Raspberry Pi Ltd\Imager\rpi-imager.exe",
    r"C:\Program Files\Raspberry Pi Imager\rpi-imager.exe",
)

_MACOS_PATHS = (
    "/Applications/Raspberry Pi Imager.app/Contents/MacOS/rpi-imager",
)


class ImagerNotFound(RuntimeError):
    """rpi-imager is not installed, with per-platform instructions attached."""


def find_imager() -> Path | None:
    """Locate rpi-imager, or None.

    Checked by real path rather than by asking a package manager: an install
    can exist without the manager knowing, and vice versa.
    """
    system = platform.system()

    if system == "Windows":
        for p in _WINDOWS_PATHS:
            if Path(p).is_file():
                return Path(p)
        # Some installs land under a versioned directory; look one level down.
        for base in (r"C:\Program Files", r"C:\Program Files (x86)"):
            root = Path(base)
            if not root.is_dir():
                continue
            for candidate in root.glob("*/Imager/rpi-imager.exe"):
                return candidate
        return None

    if system == "Darwin":
        for p in _MACOS_PATHS:
            if Path(p).is_file():
                return Path(p)

    found = shutil.which("rpi-imager")
    return Path(found) if found else None


def install_hint() -> str:
    """How to get it, per platform. Named commands the reader can paste."""
    system = platform.system()
    if system == "Windows":
        return (
            "Raspberry Pi Imager is not installed.\n\n"
            "  Install it with:\n"
            "      winget install RaspberryPiFoundation.RaspberryPiImager\n\n"
            "  Or download it from:\n"
            "      https://www.raspberrypi.com/software/"
        )
    if system == "Darwin":
        return (
            "Raspberry Pi Imager is not installed.\n\n"
            "  Install it with:\n"
            "      brew install --cask raspberry-pi-imager\n\n"
            "  Or download it from:\n"
            "      https://www.raspberrypi.com/software/"
        )
    return (
        "Raspberry Pi Imager is not installed.\n\n"
        "  Install it with your package manager, for example:\n"
        "      sudo apt install rpi-imager\n\n"
        "  Or download it from:\n"
        "      https://www.raspberrypi.com/software/"
    )


def try_install() -> bool:
    """Offer to install it through the platform's own package manager.

    Returns True only if rpi-imager is present afterwards — the package
    manager's exit code is not taken as proof, for the same reason a write's
    exit code is not taken as proof that the right bytes landed.
    """
    # The command is DATA, from the manifest. winget ids in particular get
    # renamed, and a rename should not require every user to fetch a new binary.
    cmd: list[str] = []
    try:
        from . import manifest

        cmd = manifest.install_command("rpi_imager")
    except Exception:  # noqa: BLE001
        cmd = []

    if not cmd:
        system = platform.system()
        if system == "Windows":
            cmd = ["winget", "install", "--id",
                   "RaspberryPiFoundation.RaspberryPiImager",
                   "--accept-package-agreements", "--accept-source-agreements"]
        elif system == "Darwin":
            cmd = ["brew", "install", "--cask", "raspberry-pi-imager"]
        else:
            cmd = ["sudo", "apt-get", "install", "-y", "rpi-imager"]

    if not shutil.which(cmd[0]):
        return False

    try:
        subprocess.run(cmd, timeout=900, check=False)
    except (subprocess.SubprocessError, OSError):
        return False

    return find_imager() is not None


def already_running() -> bool:
    """Is an instance of rpi-imager already up?

    It is single-instance: launching a second one does nothing at all. Without
    this check the wrapper reports success, no window appears, and the person
    is left staring at a desktop wondering what happened — which happened
    twice during testing before it was handled.
    """
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "@(Get-Process rpi-imager -ErrorAction SilentlyContinue).Count"],
                capture_output=True, text=True, timeout=20, check=False,
            ).stdout.strip()
            return out.isdigit() and int(out) > 0

        out = subprocess.run(["pgrep", "-x", "rpi-imager"],
                             capture_output=True, text=True, timeout=20, check=False)
        return out.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def launch(repo: str | None = None, *, wait: bool = True) -> int:
    """Start Raspberry Pi Imager against the Sambuca OS list.

    `--repo` is rpi-imager's documented mechanism for a custom image list, so
    the Sambuca entry appears alongside the stock ones and the whole
    device/OS/storage/customisation/writing flow is theirs. Verified against
    v2.0.10: the window title becomes "Using data from <host>".

    ELEVATION IS THEIRS TO HANDLE. rpi-imager's manifest requests it, so
    Windows raises the UAC prompt itself. That is one more thing this module
    does not have to get right.
    """
    repo = repo or default_repo()

    exe = find_imager()
    if exe is None:
        raise ImagerNotFound(install_hint())

    if already_running():
        raise RuntimeError(
            "Raspberry Pi Imager is already open.\n"
            "  It only allows one window, so a second one will not start.\n"
            "  Close that window and try again."
        )

    args = [str(exe), "--repo", repo]

    if platform.system() == "Windows":
        # Popen cannot elevate. ShellExecute with the `runas` verb is what
        # raises the UAC prompt, and rpi-imager will not start without it.
        ps = (
            "Start-Process -FilePath '{exe}' "
            "-ArgumentList '--repo','{repo}' -Verb RunAs{wait}"
        ).format(exe=exe, repo=repo, wait=" -Wait" if wait else "")
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            check=False,
        )
        return proc.returncode

    proc = subprocess.Popen(args)
    return proc.wait() if wait else 0
