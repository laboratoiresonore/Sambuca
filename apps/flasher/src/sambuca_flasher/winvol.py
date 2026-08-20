"""
sambuca :: finding the boot partition of a card someone else just wrote.

WHAT SURVIVED, AND WHY. This is the residue of winraw.py, which implemented raw
device writing on Windows and has been deleted — Raspberry Pi Imager does that,
correctly, on three platforms. What did NOT go away is the problem of finding
the card AFTERWARDS so Sambuca can put its provisioning on the boot partition.

That is genuinely ours, and it is where a real bug lived: a freshly written
Raspberry Pi card comes up with its FAT32 partition mounted but WITHOUT A DRIVE
LETTER. Anything scanning lettered volumes finds nothing, forever, on a card
that is perfectly healthy. The same blind spot broke volume locking in the old
writer and then broke boot-partition discovery straight after.

So: rescan, hand out a letter if Windows did not, and only then look.
"""

from __future__ import annotations

import platform
import re
import subprocess

# Below this, a partition is the boot partition rather than the root filesystem.
# Raspberry Pi OS ships a 512 MiB FAT32 boot partition; the root is gigabytes.
_BOOT_PARTITION_MAX_BYTES = 2 * 1024**3


def _ps(script: str) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (subprocess.SubprocessError, OSError) as exc:
        return 1, str(exc)


def disk_number(path: str) -> int | None:
    """Extract N from a `\\\\.\\PhysicalDriveN` path."""
    m = re.search(r"PhysicalDrive(\d+)", path, re.IGNORECASE)
    return int(m.group(1)) if m else None


def rescan_disks() -> None:
    """Make Windows re-read partition tables.

    After something else has rewritten a card, the in-memory view is stale and
    the new partitions may never surface — the card looks empty or wrong in
    every tool, including this one.
    """
    if platform.system() != "Windows":
        return
    _ps("Update-HostStorageCache -ErrorAction SilentlyContinue")


def assign_boot_letter(disk: int | None = None) -> str | None:
    """Give an unlettered FAT boot partition a drive letter.

    With no disk number, every disk is considered — which is what is wanted
    after rpi-imager wrote a card we never chose and do not have a handle for.
    Restricted to small partitions so this can never hand a letter to somebody's
    unmounted data volume.

    Returns the letter, or None.
    """
    if platform.system() != "Windows":
        return None

    scope = (f"Get-Partition -DiskNumber {disk} -ErrorAction SilentlyContinue"
             if disk is not None else
             "Get-Partition -ErrorAction SilentlyContinue")

    rc, out = _ps(
        f"$p = {scope} | "
        f"  Where-Object {{ -not $_.DriveLetter -and $_.Size -lt {_BOOT_PARTITION_MAX_BYTES} "
        f"                  -and $_.Type -ne 'Reserved' }} | "
        f"  Select-Object -First 1; "
        f"if ($p) {{ "
        f"  Add-PartitionAccessPath -DiskNumber $p.DiskNumber "
        f"    -PartitionNumber $p.PartitionNumber -AssignDriveLetter "
        f"    -ErrorAction SilentlyContinue; "
        f"  (Get-Partition -DiskNumber $p.DiskNumber "
        f"    -PartitionNumber $p.PartitionNumber).DriveLetter }}"
    )
    if rc != 0:
        return None
    for line in out.splitlines():
        s = line.strip()
        if len(s) == 1 and s.isalpha():
            return s
    return None


def lettered_volumes() -> list[str]:
    """Drive letters currently mounted."""
    if platform.system() != "Windows":
        return []
    rc, out = _ps(
        "Get-Volume | Where-Object { $_.DriveLetter } | "
        "ForEach-Object { $_.DriveLetter }"
    )
    if rc != 0:
        return []
    return [c for c in out.split() if len(c) == 1 and c.isalpha()]
