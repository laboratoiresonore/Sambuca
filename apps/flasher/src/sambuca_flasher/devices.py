"""
sambuca :: removable-device enumeration.

The flasher writes a raw image to a block device. Getting the device wrong
destroys whatever was on it, so this module is written to be paranoid rather
than convenient:

  * INTERNAL DISKS ARE NEVER LISTED. Not listed-and-warned — not listed. The
    only way to write to a non-removable disk is to bypass this module.
  * The system disk is excluded a second time, independently, in case a vendor
    reports an internal NVMe as removable (it happens).
  * Size sanity: anything over 512 GB is almost certainly somebody's backup
    drive, not an installer stick, and requires an explicit override.

Windows uses WMI via PowerShell; macOS uses `diskutil list -plist`; Linux uses
lsblk. Each returns the same `RemovableDevice` shape.
"""

from __future__ import annotations

import json
import platform
import plistlib
import subprocess
from dataclasses import dataclass

# A stick larger than this is probably an external backup drive. Overridable,
# but never silently.
_SANITY_MAX_BYTES = 512 * 1024**3
_MIN_BYTES = 4 * 1024**3


@dataclass(frozen=True)
class RemovableDevice:
    path: str          # \\.\PhysicalDrive2 | /dev/disk4 | /dev/sdb
    label: str         # human-readable model/description
    size_bytes: int
    is_system: bool = False

    @property
    def size_human(self) -> str:
        gb = self.size_bytes / 1024**3
        return f"{gb:.1f} GB" if gb < 1024 else f"{gb / 1024:.2f} TB"

    def describe(self) -> str:
        return f"{self.path}  {self.size_human:>10}  {self.label}"


class DeviceError(RuntimeError):
    pass


def list_removable_devices(*, allow_large: bool = False) -> list[RemovableDevice]:
    """Enumerate writable removable devices for the current platform."""
    system = platform.system()
    if system == "Windows":
        devices = _list_windows()
    elif system == "Darwin":
        devices = _list_macos()
    elif system == "Linux":
        devices = _list_linux()
    else:
        raise DeviceError(f"unsupported platform: {system}")

    out = []
    for d in devices:
        if d.is_system:
            continue
        if d.size_bytes < _MIN_BYTES:
            continue
        if d.size_bytes > _SANITY_MAX_BYTES and not allow_large:
            continue
        out.append(d)
    return sorted(out, key=lambda d: d.path)


# --------------------------------------------------------------------------- Windows


def _list_windows() -> list[RemovableDevice]:
    # MediaType 'Removable Media' plus BusType USB. Both, because a USB SSD
    # reports as fixed media and a card reader reports as removable while
    # holding a system partition.
    ps = (
        "Get-Disk | Where-Object { $_.BusType -eq 'USB' } | "
        "Select-Object Number,FriendlyName,Size,IsBoot,IsSystem | ConvertTo-Json -Depth 3"
    )
    try:
        raw = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise DeviceError(f"could not enumerate disks via PowerShell: {exc}") from exc

    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]

    return [
        RemovableDevice(
            path=f"\\\\.\\PhysicalDrive{d['Number']}",
            label=(d.get("FriendlyName") or "USB device").strip(),
            size_bytes=int(d.get("Size") or 0),
            is_system=bool(d.get("IsBoot") or d.get("IsSystem")),
        )
        for d in data
    ]


# --------------------------------------------------------------------------- macOS


def _list_macos() -> list[RemovableDevice]:
    try:
        raw = subprocess.run(
            ["diskutil", "list", "-plist", "external", "physical"],
            capture_output=True, timeout=30, check=True,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise DeviceError(f"could not enumerate disks via diskutil: {exc}") from exc

    plist = plistlib.loads(raw)
    devices = []
    for entry in plist.get("AllDisksAndPartitions", []):
        ident = entry.get("DeviceIdentifier")
        if not ident:
            continue
        info = _macos_disk_info(ident)
        devices.append(
            RemovableDevice(
                # /dev/rdiskN is the raw character device: on macOS it writes an
                # order of magnitude faster than the buffered /dev/diskN.
                path=f"/dev/r{ident}",
                label=info.get("MediaName", "external disk"),
                size_bytes=int(entry.get("Size") or info.get("TotalSize") or 0),
                is_system=bool(info.get("SystemImage")) or info.get("Internal", False),
            )
        )
    return devices


def _macos_disk_info(ident: str) -> dict:
    try:
        raw = subprocess.run(
            ["diskutil", "info", "-plist", ident],
            capture_output=True, timeout=15, check=True,
        ).stdout
        return plistlib.loads(raw)
    except Exception:  # noqa: BLE001 - best effort enrichment only
        return {}


# --------------------------------------------------------------------------- Linux


def _list_linux() -> list[RemovableDevice]:
    try:
        raw = subprocess.run(
            ["lsblk", "-J", "-b", "-d", "-o", "NAME,SIZE,MODEL,RM,TYPE,MOUNTPOINT"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise DeviceError(f"could not enumerate disks via lsblk: {exc}") from exc

    root_disk = _linux_root_disk()
    devices = []
    for d in json.loads(raw).get("blockdevices", []):
        if d.get("type") != "disk" or not d.get("rm"):
            continue
        name = d["name"]
        devices.append(
            RemovableDevice(
                path=f"/dev/{name}",
                label=(d.get("model") or "removable disk").strip(),
                size_bytes=int(d.get("size") or 0),
                is_system=(name == root_disk),
            )
        )
    return devices


def _linux_root_disk() -> str:
    try:
        src = subprocess.run(
            ["findmnt", "-no", "SOURCE", "/"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        return subprocess.run(
            ["lsblk", "-no", "PKNAME", src],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip().splitlines()[0]
    except Exception:  # noqa: BLE001 - absence of an answer must not hide devices
        return ""
