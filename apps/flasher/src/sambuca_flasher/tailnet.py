"""
sambuca :: settle how you will reach the appliance BEFORE building it.

THIS RUNS FIRST, AND THAT IS THE POINT.

It used to print a tip halfway through the write, after the engine was staged
and the Imager was about to launch. By then the decision had already been made
for the owner: if they had no key, they discovered it after committing to the
flow, and the consequence of skipping was not visible until the card was
finished and the machine could not be reached.

Reachability is a PREREQUISITE, not a footnote. An appliance you cannot find is
not an appliance.

HOW MUCH CAN BE DONE FOR THEM. More than a URL, less than everything:

  * Whether Tailscale is on this computer at all — detected.
  * Whether it is logged in, and to which tailnet — detected, and named back to
    them, so they can see they are about to enrol into the right one.
  * Installing it here if it is missing — done, through the manifest.
  * Opening the page where the key is created — done.
  * MINTING the key — cannot be done. It requires their account, and an
    installer that could mint tailnet credentials on its own would be a worse
    thing to hand somebody than a manual step.

So the key itself is the guided half, and everything around it is automatic.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

KEY_PAGE = "https://login.tailscale.com/admin/settings/keys"

_WINDOWS_PATHS = (
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
)
_MACOS_PATHS = (
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
)


@dataclass(frozen=True)
class Status:
    installed: bool
    running: bool
    tailnet: str = ""
    self_name: str = ""

    @property
    def ready(self) -> bool:
        return self.installed and self.running


def find_cli() -> Path | None:
    system = platform.system()
    paths = _WINDOWS_PATHS if system == "Windows" else _MACOS_PATHS if system == "Darwin" else ()
    for p in paths:
        if Path(p).is_file():
            return Path(p)
    found = shutil.which("tailscale")
    return Path(found) if found else None


def status() -> Status:
    """What Tailscale on THIS computer can tell us.

    Failure is reported, never raised: this is the first thing the installer
    does, and it must not be the reason someone cannot write a card.
    """
    cli = find_cli()
    if cli is None:
        return Status(installed=False, running=False)

    try:
        out = subprocess.run(
            [str(cli), "status", "--json"],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout
        doc = json.loads(out)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError, ValueError):
        return Status(installed=True, running=False)

    running = str(doc.get("BackendState", "")).lower() == "running"
    tailnet = str((doc.get("CurrentTailnet") or {}).get("Name", ""))
    self_name = str((doc.get("Self") or {}).get("DNSName", "")).rstrip(".")
    return Status(installed=True, running=running, tailnet=tailnet, self_name=self_name)


def install_here() -> bool:
    """Install Tailscale on the operator's own machine.

    Worth doing even though the appliance is the thing being built: without it
    here, the tailnet name the appliance joins is unreachable from the computer
    that made it, which is the same defect one step removed.
    """
    system = platform.system()
    if system == "Windows":
        cmd = ["winget", "install", "--id", "tailscale.tailscale",
               "--accept-package-agreements", "--accept-source-agreements"]
    elif system == "Darwin":
        cmd = ["brew", "install", "--cask", "tailscale"]
    else:
        cmd = ["sudo", "apt-get", "install", "-y", "tailscale"]

    if not shutil.which(cmd[0]):
        return False
    try:
        subprocess.run(cmd, timeout=900, check=False)
    except (subprocess.SubprocessError, OSError):
        return False
    return find_cli() is not None


def open_key_page() -> bool:
    """Open the page where a pre-auth key is created.

    The ONE outbound thing the installer does on the owner's behalf, in their
    own browser, to a URL shown to them first.
    """
    import webbrowser

    try:
        return webbrowser.open(KEY_PAGE)
    except Exception:  # noqa: BLE001 - a browser refusing to open is not fatal
        return False


def valid_key(key: str) -> bool:
    """Catch the paste that will not work, before the card is written.

    A wrong key is not discovered until first boot, on a headless machine, with
    no screen — the worst place to learn anything.
    """
    key = (key or "").strip()
    return key.startswith("tskey-") and len(key) > 20
