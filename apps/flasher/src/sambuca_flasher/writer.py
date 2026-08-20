"""
sambuca :: raw image writing and payload injection.

Two operations, deliberately separated:

  write_image()    raw-copies the Debian netinst ISO to the target device.
  inject_payload() mounts the resulting boot partition and adds the sambuca
                   directory (preseed, engine, provision.json).

They are separate because injection is the part that carries secrets and the
part most likely to fail on a platform quirk. If injection fails after a
successful write, the stick is still a valid Debian installer — recoverable,
rather than half-written garbage.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import platform
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from .devices import DeviceError, RemovableDevice

_CHUNK = 4 * 1024 * 1024   # 4 MiB: large enough to saturate USB 3, small enough
                           # that progress reporting stays responsive.

ProgressFn = Callable[[int, int], None]


def write_image(
    image: Path,
    device: RemovableDevice,
    *,
    progress: ProgressFn | None = None,
    verify: bool = True,
) -> None:
    """Raw-write `image` to `device`, then read it back and compare digests.

    VERIFICATION IS ON BY DEFAULT and costs a second pass. A USB stick that
    reports a successful write and then produces a corrupt installer at 2am in
    a server room is exactly the failure this project exists to not have.
    """
    image = Path(image)
    if not image.is_file():
        raise DeviceError(f"image not found: {image}")

    total = image.stat().st_size
    if total > device.size_bytes:
        raise DeviceError(
            f"image is {total / 1024**3:.1f} GB but {device.path} holds only "
            f"{device.size_human}"
        )

    _require_privileges()
    _unmount(device)

    digest = hashlib.sha256()
    written = 0

    try:
        with open(image, "rb") as src, _open_device(device.path, "wb") as dst:
            while chunk := src.read(_CHUNK):
                dst.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                if progress:
                    progress(written, total)
            dst.flush()
            os.fsync(dst.fileno())
    except PermissionError as exc:
        raise DeviceError(
            f"permission denied writing to {device.path}.\n"
            + _privilege_hint()
        ) from exc
    except OSError as exc:
        raise DeviceError(f"write failed at byte {written}: {exc}") from exc

    # Give the kernel/USB stack a moment before reading back, or the verify
    # pass reads from a cache that has not been invalidated yet.
    _sync()
    time.sleep(2)

    if not verify:
        return

    readback = hashlib.sha256()
    remaining = total
    try:
        with _open_device(device.path, "rb") as dev:
            while remaining > 0:
                chunk = dev.read(min(_CHUNK, remaining))
                if not chunk:
                    raise DeviceError(
                        f"device returned EOF after {total - remaining} of {total} bytes"
                    )
                readback.update(chunk)
                remaining -= len(chunk)
                if progress:
                    progress(total - remaining, total)
    except OSError as exc:
        raise DeviceError(f"verification read failed: {exc}") from exc

    if readback.hexdigest() != digest.hexdigest():
        raise DeviceError(
            "VERIFICATION FAILED — the data read back does not match what was "
            "written. The stick is faulty or was removed mid-write. Do not use it."
        )


def inject_payload(
    device: RemovableDevice,
    payload_dir: Path,
    *,
    mount_hint: Path | None = None,
) -> Path:
    """Copy the sambuca payload onto the installer's boot partition.

    Returns the destination directory. The caller is responsible for having
    written the base ISO first.
    """
    payload_dir = Path(payload_dir)
    if not payload_dir.is_dir():
        raise DeviceError(f"payload directory not found: {payload_dir}")

    mount = mount_hint or _find_mount(device)
    if mount is None:
        raise DeviceError(
            f"could not locate the mounted boot partition of {device.path}.\n"
            "Re-insert the stick, wait for it to appear, then run:\n"
            f"  sambuca-flasher inject --device {device.path} --mount <path>"
        )

    dest = Path(mount) / "sambuca"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(payload_dir, dest)

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


def _open_device(path: str, mode: str):
    if platform.system() == "Windows":
        # Windows refuses buffered writes to a raw physical drive; unbuffered
        # binary mode with sector-aligned chunks is the only path that works.
        return open(path, mode, buffering=0)
    return open(path, mode, buffering=0)


def _require_privileges() -> None:
    system = platform.system()
    if system == "Windows":
        try:
            import ctypes

            if not ctypes.windll.shell32.IsUserAnAdmin():
                raise DeviceError(_privilege_hint())
        except AttributeError:
            pass
    elif os.geteuid() != 0:
        raise DeviceError(_privilege_hint())


def _privilege_hint() -> str:
    if platform.system() == "Windows":
        return "Writing a raw device requires Administrator. Re-run this from an elevated terminal."
    return "Writing a raw device requires root. Re-run with sudo."


def _unmount(device: RemovableDevice) -> None:
    """Unmount every partition of the target before writing to it."""
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(
                ["diskutil", "unmountDisk", device.path.replace("/dev/r", "/dev/")],
                capture_output=True, timeout=30, check=False,
            )
        elif system == "Linux":
            subprocess.run(["umount", f"{device.path}*"], capture_output=True,
                           timeout=30, check=False, shell=False)
        elif system == "Windows":
            num = device.path.rsplit("PhysicalDrive", 1)[-1]
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Get-Disk {num} | Get-Partition | "
                 "Where-Object DriveLetter | ForEach-Object "
                 "{ Remove-PartitionAccessPath -DiskNumber $_.DiskNumber "
                 "-PartitionNumber $_.PartitionNumber -AccessPath "
                 "($_.DriveLetter + ':\\') -ErrorAction SilentlyContinue }"],
                capture_output=True, timeout=30, check=False,
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # Not fatal: the write itself will fail loudly if the device is busy,
        # and that error is clearer than one invented here.
        pass


def _sync() -> None:
    if platform.system() != "Windows":
        try:
            subprocess.run(["sync"], timeout=60, check=False)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass


def _find_mount(device: RemovableDevice) -> Path | None:
    """Locate the mounted boot partition, waiting briefly for the OS to remount."""
    system = platform.system()
    for _ in range(15):
        if system == "Darwin":
            base = device.path.replace("/dev/r", "/dev/")
            out = subprocess.run(
                ["diskutil", "info", "-plist", f"{base}s1"],
                capture_output=True, check=False,
            ).stdout
            if out:
                import plistlib

                # A partition that is not mounted YET produces malformed or
                # partial plist output; that is the normal case on the first
                # few polls, not an error worth surfacing.
                with contextlib.suppress(Exception):
                    mp = plistlib.loads(out).get("MountPoint")
                    if mp:
                        return Path(mp)
        elif system == "Linux":
            out = subprocess.run(
                ["lsblk", "-nro", "MOUNTPOINT", device.path],
                capture_output=True, text=True, check=False,
            ).stdout
            for line in out.splitlines():
                if line.strip():
                    return Path(line.strip())
        elif system == "Windows":
            num = device.path.rsplit("PhysicalDrive", 1)[-1]
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Partition -DiskNumber {num} | "
                 "Where-Object DriveLetter | Select-Object -First 1).DriveLetter"],
                capture_output=True, text=True, check=False,
            ).stdout.strip()
            if out:
                return Path(f"{out}:\\")
        time.sleep(2)
    return None
