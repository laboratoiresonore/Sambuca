"""Tests for the pure logic in the Windows raw-device layer.

The Win32 calls themselves need a real disk and Administrator, so they are not
tested here. What IS testable is the arithmetic and parsing around them, and
both of those had bugs that only appeared against real hardware:

  * sector padding — a final chunk that is not a whole number of sectors is
    rejected outright by the device, which would fail the very last write of a
    multi-gigabyte copy.

  * disk-number parsing — this decides WHICH DISK gets locked and dismounted.
    Getting it wrong does not throw; it quietly operates on the wrong device.

The lock-lifetime bug that actually broke the first four attempts is a handle
ownership question and cannot be unit-tested without a disk. It is documented
at length in winraw.lock_and_dismount instead.
"""

from __future__ import annotations

import pytest

from sambuca_flasher import winraw


class TestSectorPadding:
    def test_exact_sector_is_untouched(self):
        data = b"x" * winraw.SECTOR
        assert winraw.pad_to_sector(data) is data or winraw.pad_to_sector(data) == data
        assert len(winraw.pad_to_sector(data)) == winraw.SECTOR

    def test_multiple_of_sector_is_untouched(self):
        data = b"y" * (winraw.SECTOR * 8192)   # a 4 MiB chunk
        assert len(winraw.pad_to_sector(data)) == len(data)

    @pytest.mark.parametrize("size", [1, 511, 513, 1234, winraw.SECTOR * 3 + 7])
    def test_short_chunk_is_padded_up(self, size):
        out = winraw.pad_to_sector(b"z" * size)
        assert len(out) % winraw.SECTOR == 0
        assert len(out) >= size
        # Padding must be zeroes, and must not disturb the real bytes.
        assert out[:size] == b"z" * size
        assert set(out[size:]) <= {0}

    def test_padding_never_grows_by_a_whole_sector(self):
        """A full extra sector of padding would mean the arithmetic is off."""
        for size in range(1, winraw.SECTOR * 2):
            out = winraw.pad_to_sector(b"a" * size)
            assert len(out) - size < winraw.SECTOR

    def test_empty_stays_empty(self):
        assert winraw.pad_to_sector(b"") == b""


class TestDiskNumber:
    @pytest.mark.parametrize(("path", "expected"), [
        (r"\\.\PhysicalDrive0", 0),
        (r"\\.\PhysicalDrive6", 6),
        (r"\\.\PhysicalDrive11", 11),
        (r"\\.\physicaldrive3", 3),          # case-insensitive
        (r"\\?\PhysicalDrive2", 2),
    ])
    def test_extracts_the_number(self, path, expected):
        assert winraw.disk_number(path) == expected

    @pytest.mark.parametrize("path", [
        "/dev/sdb",                           # a Linux path
        r"\\.\C:",                            # a volume, not a disk
        "",
        "PhysicalDrive",                      # no number
    ])
    def test_returns_none_when_there_is_no_disk_number(self, path):
        assert winraw.disk_number(path) is None

    def test_double_digit_is_not_truncated(self):
        """`PhysicalDrive1` must not match inside `PhysicalDrive10`.

        Truncating here would lock and dismount the volumes of an entirely
        different disk — silently, and with no error to notice.
        """
        assert winraw.disk_number(r"\\.\PhysicalDrive10") == 10
        assert winraw.disk_number(r"\\.\PhysicalDrive1") == 1
