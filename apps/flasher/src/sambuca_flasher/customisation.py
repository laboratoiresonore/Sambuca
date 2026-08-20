"""
sambuca :: fill in Raspberry Pi Imager's Customisation step in advance.

THE RULE: do it for the user, or guide them through every step. Customisation
is one of the screens that can largely be done FOR them, so it is.

rpi-imager keeps those settings in the registry on Windows, and Sambuca can
write them before launching so the owner meets a filled-in form instead of an
empty one they have to reason about.

WHAT IS NEVER WRITTEN, AND WHY.

The same key also stores `sshUserPassword` (a password hash) and
`wifiPasswordCrypt` (a wifi pre-shared key). Sambuca does not write either, and
does not read them to "helpfully" carry them forward. Convenience is not a
reason to handle somebody's wifi key, and an installer that silently harvests
credentials from a previous run is doing something the owner did not ask for.
rpi-imager's own UI collects those, in its own process.

This mirrors the rule already in pi.py: the SSID may be noted, the key never is.

REVERSIBLE. Existing values are captured before anything is changed, so a
failed run can put them back. Someone who has already configured the Imager for
their own purposes should not silently lose that.
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass, field
from typing import Any

# PSDrive syntax: HKCU:\ with the colon. Without it PowerShell does not
# resolve the hive, and because every call carried -ErrorAction
# SilentlyContinue the read simply returned nothing — which would have
# made restore() a no-op while still claiming to be reversible.
_KEY = r"HKCU:\Software\Raspberry Pi\Raspberry Pi Imager\imagecustomization"

# Fields Sambuca will set. Everything absent from this list is left alone —
# an allowlist, not a denylist, so a new secret field appearing upstream is
# excluded by default rather than by remembering to exclude it.
SAFE_FIELDS = (
    "hostname",
    "timezone",
    "keyboard",
    "sshEnabled",
    "sshUserName",
)

# Named so that a future reader does not "tidy up" by adding them to SAFE_FIELDS.
NEVER_WRITE = (
    "sshUserPassword",
    "wifiPasswordCrypt",
    "wifiSSID",
    "wifiSsidOctetsBase64",
    "sshAuthorizedKeys",
)


@dataclass
class Customisation:
    hostname: str = "sambuca"
    timezone: str = ""
    keyboard: str = ""
    ssh_enabled: bool = True
    ssh_username: str = "sambuca"
    previous: dict[str, Any] = field(default_factory=dict)

    def as_registry(self) -> dict[str, str]:
        out = {
            "hostname": self.hostname,
            "sshEnabled": "true" if self.ssh_enabled else "false",
            "sshUserName": self.ssh_username,
        }
        if self.timezone:
            out["timezone"] = self.timezone
        if self.keyboard:
            out["keyboard"] = self.keyboard
        return out


def supported() -> bool:
    """Only Windows keeps these in the registry."""
    return platform.system() == "Windows"


def _ps(script: str) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30, check=False,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (subprocess.SubprocessError, OSError) as exc:
        return 1, str(exc)


def read_existing() -> dict[str, str]:
    """Current values for the fields Sambuca may touch.

    Deliberately reads ONLY the safe fields. There is no reason for this
    process to have the owner's password hash in memory, so it does not fetch
    it — not even to ignore it.
    """
    if not supported():
        return {}

    props = ",".join(f"'{f}'" for f in SAFE_FIELDS)
    rc, out = _ps(
        f"$k = Get-ItemProperty -Path '{_KEY}' -ErrorAction SilentlyContinue; "
        f"if ($k) {{ foreach ($n in @({props})) {{ "
        f"  if ($null -ne $k.$n) {{ Write-Output \"$n=$($k.$n)\" }} }} }}"
    )
    if rc != 0:
        return {}

    found: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            k = k.strip()
            if k in SAFE_FIELDS:
                found[k] = v.strip()
    return found


def detect_locale() -> tuple[str, str]:
    """The machine's own timezone and keyboard layout.

    Taken from the operator's computer rather than asked, because someone
    setting up a Raspberry Pi in their kitchen is not in a different timezone
    from the laptop they are doing it on. Guessing right silently beats asking
    a question with an obvious answer.
    """
    if not supported():
        return "", ""

    rc, out = _ps(
        "$tz = (Get-TimeZone).Id; "
        "$kb = (Get-WinUserLanguageList)[0].InputMethodTips[0]; "
        "Write-Output \"$tz|$kb\""
    )
    if rc != 0 or "|" not in out:
        return "", ""

    tz_windows, _, kb = out.strip().partition("|")
    # Windows timezone ids are not IANA names. Only the common cases are mapped;
    # anything else is left empty and rpi-imager asks, which is the correct
    # outcome for an unknown rather than a confident wrong answer.
    mapping = {
        "Pacific Standard Time": "America/Vancouver",
        "Mountain Standard Time": "America/Edmonton",
        "Central Standard Time": "America/Winnipeg",
        "Eastern Standard Time": "America/Toronto",
        "Atlantic Standard Time": "America/Halifax",
        "GMT Standard Time": "Europe/London",
        "W. Europe Standard Time": "Europe/Berlin",
        "Romance Standard Time": "Europe/Paris",
        "Central Europe Standard Time": "Europe/Prague",
        "AUS Eastern Standard Time": "Australia/Sydney",
    }
    layout = {"0409": "us", "0809": "gb", "1009": "ca", "040C": "fr",
              "0407": "de", "0410": "it", "040A": "es"}
    kb_code = kb.split(":")[-1][-4:].upper() if kb else ""
    return mapping.get(tz_windows.strip(), ""), layout.get(kb_code, "")


def apply(c: Customisation) -> tuple[bool, list[str]]:
    """Write the safe fields. Returns (ok, what_changed)."""
    if not supported():
        return False, []

    c.previous = read_existing()

    changed: list[str] = []
    for name, value in c.as_registry().items():
        if name in NEVER_WRITE:          # belt and braces; as_registry cannot emit these
            continue
        if c.previous.get(name) == value:
            continue
        rc, _ = _ps(
            f"New-Item -Path '{_KEY}' -Force -ErrorAction SilentlyContinue | Out-Null; "
            f"Set-ItemProperty -Path '{_KEY}' -Name '{name}' -Value '{value}' "
            f"-ErrorAction Stop"
        )
        if rc == 0:
            changed.append(f"{name}={value}")

    return bool(changed), changed


def restore(c: Customisation) -> None:
    """Put back whatever was there before. Best effort, never raises."""
    if not supported() or not c.previous:
        return
    for name, value in c.previous.items():
        _ps(f"Set-ItemProperty -Path '{_KEY}' -Name '{name}' -Value '{value}' "
            f"-ErrorAction SilentlyContinue")
