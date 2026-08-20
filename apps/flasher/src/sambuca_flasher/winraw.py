"""
sambuca :: raw physical-device access on Windows.

WHY THIS FILE EXISTS. `open(r"\\\\.\\PhysicalDrive6", "wb")` does not work. It
raises `OSError: [Errno 22] Invalid argument`, because "wb" asks the C runtime
to CREATE and TRUNCATE, and neither verb means anything to a physical disk.
The previous implementation did exactly that, with a comment explaining that
unbuffered binary mode "is the only path that works" — it had been reasoned
through and never once executed. It was found the first time anyone ran the
flasher against real hardware.

Writing a raw disk on Windows needs three things the CRT will not do for you:

  1. CreateFileW with OPEN_EXISTING and both share flags. Anything else either
     fails outright or blocks every other handle to the disk.

  2. THE VOLUMES MUST BE LOCKED AND DISMOUNTED FIRST. Windows owns the mounted
     filesystems on that disk and will refuse writes to the sectors underneath
     them. Without this you get partial success: the partition table and the
     unallocated tail are written, the mounted regions silently are not, and
     the card fails to boot with no error anywhere. That is a far worse failure
     than an exception.

  3. Writes must be whole sectors. A final chunk of 1234 bytes is rejected, so
     the last write is padded to a 512-byte boundary.

Everything here is ctypes against documented Win32. No third-party dependency
is added for a platform-specific detail on one of three supported platforms.
"""

from __future__ import annotations

import ctypes
import platform
import re
import subprocess
from ctypes import wintypes

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_FLAG_WRITE_THROUGH = 0x80000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

FSCTL_LOCK_VOLUME = 0x00090018
FSCTL_DISMOUNT_VOLUME = 0x00090020
FSCTL_ALLOW_EXTENDED_DASD_IO = 0x00090083

SECTOR = 512


class WinRawError(OSError):
    """A raw-device operation failed, with the Win32 reason attached."""


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    k.CreateFileW.restype = wintypes.HANDLE
    k.DeviceIoControl.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    k.DeviceIoControl.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    return k


def _win_error(prefix: str) -> WinRawError:
    code = ctypes.get_last_error()
    return WinRawError(f"{prefix} (win32 error {code}: {ctypes.FormatError(code)})")


def disk_number(path: str) -> int | None:
    """Extract N from a `\\\\.\\PhysicalDriveN` path."""
    m = re.search(r"PhysicalDrive(\d+)", path, re.IGNORECASE)
    return int(m.group(1)) if m else None


def volumes_on_disk(disk: int) -> list[str]:
    """Drive letters whose volume lives on this physical disk."""
    ps = (
        f"Get-Partition -DiskNumber {disk} -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.DriveLetter }} | ForEach-Object {{ $_.DriveLetter }}"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    return [c for c in out.split() if len(c) == 1 and c.isalpha()]


def _ps(script: str) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (subprocess.SubprocessError, OSError) as exc:
        return 1, str(exc)


def set_disk_offline(disk: int, offline: bool) -> bool:
    """Take the whole disk offline, or bring it back.

    THIS IS THE ONE THAT ACTUALLY MATTERS, and it was learned the hard way.

    Locking volumes by drive letter is the documented approach, and here it
    silently does nothing: a freshly imaged card's FAT32 partition often has NO
    drive letter, so filtering on DriveLetter matches zero volumes, reports
    success, and locks nothing at all. The write then sails through the
    partition table and the first few megabytes — which no filesystem claims —
    and dies with ERROR_ACCESS_DENIED at the first sector Windows believes
    belongs to a mounted volume. Observed exactly that: two 4 MiB chunks
    written, the third denied.

    An offline disk has no mounted volumes by definition, so nothing is left
    holding those sectors. It also closes the race where Windows remounts a
    volume mid-write because something poked Explorer.
    """
    flag = "$true" if offline else "$false"
    rc, _ = _ps(f"Set-Disk -Number {disk} -IsOffline {flag} -ErrorAction Stop")
    if rc == 0 and not offline:
        # Returning online, a card can come back read-only, which would fail
        # the provisioning step with a confusing permissions error.
        _ps(f"Set-Disk -Number {disk} -IsReadOnly $false -ErrorAction SilentlyContinue")
    return rc == 0


def volume_paths_on_disk(disk: int) -> list[str]:
    """Volume GUID paths on this disk, INCLUDING volumes with no drive letter.

    A volume GUID path is the only handle Windows offers to a partition it has
    not assigned a letter to — which is precisely the case that broke this.
    """
    rc, out = _ps(
        f"Get-Partition -DiskNumber {disk} -ErrorAction SilentlyContinue | "
        f"Get-Volume -ErrorAction SilentlyContinue | "
        f"ForEach-Object {{ $_.UniqueId }}"
    )
    if rc != 0:
        return []
    paths = []
    for line in out.splitlines():
        s = line.strip()
        if "Volume{" in s:
            paths.append(s.rstrip("\\"))
    return paths


def lock_and_dismount(disk: int) -> tuple[list[str], list[int]]:
    """Lock and dismount every volume on the disk, and KEEP THE HANDLES OPEN.

    Returns (what_was_locked, open_handles). The caller MUST hold those handles
    until the write is finished.

    THE HANDLE LIFETIME IS THE ENTIRE POINT. FSCTL_LOCK_VOLUME is released the
    moment its handle closes. An earlier version of this function locked each
    volume and then closed the handle in a `finally` before returning — which
    dutifully acquired the lock and dropped it again before a single byte was
    written. The symptom was identical to not locking at all: the first
    megabytes write fine because no filesystem claims them, then the first
    write into the partition fails with ERROR_ACCESS_DENIED. Two 4 MiB chunks
    through, third one denied, every time.
    """
    k = _kernel32()
    done: list[str] = []
    handles: list[int] = []

    # BOTH forms. Drive letters alone miss exactly the case that broke this —
    # a partition Windows mounted but never assigned a letter to.
    targets = [f"\\\\.\\{c}:" for c in volumes_on_disk(disk)]
    targets += volume_paths_on_disk(disk)

    for target in targets:
        handle = k.CreateFileW(
            target, GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE, None,
            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None,
        )
        if handle == INVALID_HANDLE_VALUE:
            continue

        returned = wintypes.DWORD(0)
        locked = k.DeviceIoControl(
            handle, FSCTL_LOCK_VOLUME, None, 0, None, 0,
            ctypes.byref(returned), None)
        dismounted = k.DeviceIoControl(
            handle, FSCTL_DISMOUNT_VOLUME, None, 0, None, 0,
            ctypes.byref(returned), None)

        if locked or dismounted:
            done.append(target)
            # Deliberately NOT closed here. See the docstring.
            handles.append(handle)
        else:
            k.CloseHandle(handle)

    return done, handles


def open_raw(path: str, write: bool):
    """Open a physical device for raw access, returning a Python file object.

    On a write, the disk's volumes are locked and dismounted first — without
    that, Windows silently declines to write the sectors under a mounted
    filesystem and the result is a card that looks written and will not boot.
    """
    if platform.system() != "Windows":
        return open(path, "rb+" if write else "rb", buffering=0)

    held: list[int] = []
    if write:
        n = disk_number(path)
        if n is not None:
            # These handles stay open for the whole write. Closing one releases
            # its volume lock and Windows immediately starts refusing writes to
            # the sectors underneath it.
            _, held = lock_and_dismount(n)

    k = _kernel32()
    access = GENERIC_READ | GENERIC_WRITE if write else GENERIC_READ
    flags = FILE_ATTRIBUTE_NORMAL | (FILE_FLAG_WRITE_THROUGH if write else 0)

    handle = k.CreateFileW(
        path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
        OPEN_EXISTING, flags, None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise _win_error(f"cannot open {path}")

    if write:
        # Permits writes to the whole device including the sectors a
        # filesystem believes it owns. Advisory: failure is not fatal.
        returned = wintypes.DWORD(0)
        k.DeviceIoControl(handle, FSCTL_ALLOW_EXTENDED_DASD_IO,
                          None, 0, None, 0, ctypes.byref(returned), None)

    return _RawDevice(handle, k, held)


class _RawDevice:
    """A file-like wrapper over a Win32 device handle.

    The obvious implementation — msvcrt.open_osfhandle() then os.fdopen() — got
    two 4 MiB chunks in and then failed with EBADF partway through a 2.77 GiB
    copy. Handing a device handle to the C runtime and asking it to behave like
    a stream introduces a translation layer that has no reason to exist here, so
    this calls ReadFile/WriteFile directly. Fewer moving parts, and the errors
    that do occur are the actual Win32 ones rather than a CRT interpretation of
    them.
    """

    def __init__(self, handle, k, held_locks=None):
        self._h = handle
        self._k = k
        # Volume locks that must outlive every write on this device.
        self._held = list(held_locks or ())
        self._closed = False
        k.WriteFile.argtypes = [
            wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
        ]
        k.WriteFile.restype = wintypes.BOOL
        k.ReadFile.argtypes = [
            wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
        ]
        k.ReadFile.restype = wintypes.BOOL
        k.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        k.FlushFileBuffers.restype = wintypes.BOOL

    def write(self, data: bytes) -> int:
        written = wintypes.DWORD(0)
        buf = (ctypes.c_char * len(data)).from_buffer_copy(data)
        if not self._k.WriteFile(self._h, buf, len(data),
                                 ctypes.byref(written), None):
            raise _win_error(f"write of {len(data)} bytes failed")
        if written.value != len(data):
            raise WinRawError(
                f"short write: {written.value} of {len(data)} bytes. The device "
                f"is probably full or failing."
            )
        return written.value

    def read(self, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        got = wintypes.DWORD(0)
        if not self._k.ReadFile(self._h, buf, size, ctypes.byref(got), None):
            raise _win_error(f"read of {size} bytes failed")
        return buf.raw[:got.value]

    def flush(self) -> None:
        if not self._closed:
            self._k.FlushFileBuffers(self._h)

    def fileno(self) -> int:
        # Deliberately unsupported: there is no CRT descriptor behind this, and
        # returning a fake one would let os.fsync() appear to succeed.
        raise OSError("raw device handle has no file descriptor")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._k.FlushFileBuffers(self._h)
        self._k.CloseHandle(self._h)
        # Release the volume locks LAST, so Windows cannot remount and start
        # writing its own metadata over the tail of the image.
        for h in self._held:
            self._k.CloseHandle(h)
        self._held = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def pad_to_sector(chunk: bytes) -> bytes:
    """Whole sectors only. A short final write is rejected outright."""
    remainder = len(chunk) % SECTOR
    if remainder == 0:
        return chunk
    return chunk + b"\x00" * (SECTOR - remainder)


def rescan_disks() -> None:
    """Tell Windows to re-read the partition tables.

    After a raw write the in-memory view is stale: Windows still believes the
    old layout is on the disk. Without a rescan the new partitions may never
    surface, and the card looks empty or wrong in every tool including this one.
    """
    _ps("Update-HostStorageCache -ErrorAction SilentlyContinue")


def assign_boot_letter(disk: int) -> str | None:
    """Give the FAT32 boot partition a drive letter, if it has none.

    A freshly imaged Raspberry Pi card mounts its FAT partition without a
    letter. Everything that looks for the partition by letter then finds
    nothing, on a card that is completely healthy — the same class of blind
    spot that made the volume lock silently match zero volumes.

    Returns the letter, or None if Windows would not assign one.
    """
    rc, out = _ps(
        f"$p = Get-Partition -DiskNumber {disk} -ErrorAction SilentlyContinue | "
        f"  Where-Object {{ -not $_.DriveLetter -and $_.Size -lt 2GB }} | "
        f"  Select-Object -First 1; "
        f"if ($p) {{ "
        f"  Add-PartitionAccessPath -DiskNumber {disk} "
        f"    -PartitionNumber $p.PartitionNumber -AssignDriveLetter "
        f"    -ErrorAction SilentlyContinue; "
        f"  (Get-Partition -DiskNumber {disk} "
        f"    -PartitionNumber $p.PartitionNumber).DriveLetter }}"
    )
    if rc != 0:
        return None
    for line in out.splitlines():
        s = line.strip()
        if len(s) == 1 and s.isalpha():
            return s
    return None
