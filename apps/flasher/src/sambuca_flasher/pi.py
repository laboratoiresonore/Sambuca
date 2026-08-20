"""
sambuca :: Raspberry Pi / arm64 target.

The x86 path and this one are genuinely different shapes, and pretending
otherwise is how you get a card that looks written and does not boot:

    x86     Debian netinst ISO -> USB -> the INSTALLER runs -> preseed answers
            its questions -> a system is installed onto the internal disk.

    Pi      Raspberry Pi OS .img.xz -> SD card. There is no installer. The
            image IS the installed system, and the card IS the disk. All
            provisioning therefore happens at FIRST BOOT, from the FAT32
            partition, because that is the only part of the card a non-Linux
            machine can write to.

Three consequences that shape everything below:

  1. The image is a compressed raw disk image, not an ISO. It is ~500 MiB
     compressed and ~3 GiB expanded, so it is decompressed AS IT IS WRITTEN.
     Materialising 3 GiB on the operator's laptop to then copy it is a waste of
     their disk and their time.

  2. There is no preseed. Provisioning is a `firstrun.sh` that the kernel is
     told to execute, via an append to `cmdline.txt`. This is the same
     mechanism Raspberry Pi Imager uses — it is not a trick.

  3. THE RESULTS COME BACK ON THE CARD. firstrun.sh writes its output to the
     FAT32 partition, which means a headless Pi with no network, no monitor
     and no keyboard can still tell you exactly what happened: you put the card
     back in the reader and read the file. For a Pi Zero 2 W, which has no
     ethernet port at all, this is the difference between a testable device and
     a blinking LED.
"""

from __future__ import annotations

import lzma
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .devices import DeviceError, RemovableDevice

_CHUNK = 4 * 1024 * 1024

ProgressFn = Callable[[int, int], None]

# Trixie and later put the FAT partition here. Older releases used /boot.
BOOT_MOUNT = "/boot/firmware"

# What the kernel is told to run on the very first boot. `systemd.run_success_action=reboot`
# makes the Pi reboot into normal operation once provisioning finishes, which
# is what turns this from "a script ran" into "the machine came up configured".
_CMDLINE_APPEND = (
    " systemd.run=/boot/firmware/firstrun.sh"
    " systemd.run_success_action=reboot"
    " systemd.unit=kernel-command-line.target"
)


class PiError(DeviceError):
    """A Raspberry Pi target problem."""


@dataclass(frozen=True)
class ImageKind:
    kind: str          # "raspios-xz" | "raw" | "iso"
    compressed: bool
    label: str


def identify_image(path: Path) -> ImageKind:
    """Work out what we have been handed, by magic bytes rather than by name.

    A file called `.img` that is actually xz-compressed, written raw to a card,
    produces a device that is not bootable and gives no clue why.
    """
    if not path.is_file():
        raise PiError(f"no such image: {path}")

    with path.open("rb") as fh:
        magic = fh.read(6)

    if magic.startswith(b"\xfd7zXZ\x00"):
        return ImageKind("raspios-xz", True, "xz-compressed disk image")
    if magic.startswith(b"CD001") or path.suffix.lower() == ".iso":
        return ImageKind("iso", False, "ISO 9660 image")
    return ImageKind("raw", False, "raw disk image")


def open_image_stream(path: Path, kind: ImageKind):
    """A binary stream of the UNCOMPRESSED image, decompressed on the fly."""
    if kind.compressed:
        return lzma.open(path, "rb")
    return path.open("rb")


def expanded_size(path: Path, kind: ImageKind) -> int | None:
    """Uncompressed size, if it can be known cheaply.

    xz stores it in the stream footer, and `xz --robot --list` will read it
    without decompressing. If xz is not installed we return None and the caller
    reports progress against the compressed bytes consumed instead — a real
    number, just a different one. Guessing a total and being wrong is worse
    than admitting we do not know it.
    """
    if not kind.compressed:
        return path.stat().st_size

    xz = shutil.which("xz")
    if not xz:
        return None
    try:
        out = subprocess.run(
            [xz, "--robot", "--list", str(path)],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return None

    for line in out.splitlines():
        parts = line.split("\t")
        if parts and parts[0] == "totals" and len(parts) > 4:
            try:
                return int(parts[4])
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# First-boot provisioning
# ---------------------------------------------------------------------------

def render_firstrun(
    *,
    hostname: str = "sambuca",
    run_probe: bool = True,
    enable_ssh: bool = True,
) -> str:
    """The script the kernel executes on first boot.

    Deliberately small, POSIX sh, and it writes EVERYTHING it does to the FAT
    partition. On a headless Pi Zero with no ethernet, that log is the only
    channel back to a human — the card goes into a reader and the answer is
    sitting there.

    It also removes its own hook from cmdline.txt before rebooting. A firstrun
    script that runs on every boot is a machine that reprovisions itself
    forever, and the failure looks like "the Pi keeps rebooting".
    """
    probe = ""
    if run_probe:
        probe = f"""
# ---- sambuca hardware profile -------------------------------------------
# The point of the whole exercise: run the real profiler on real arm64
# silicon and record what it decides, including a refusal.
#
# INVOKED WITH bash, NOT sh, AND THAT IS LOAD-BEARING. hardware-detect.sh is
# #!/usr/bin/env bash and uses 47 bash-only constructs — [[ ]], arrays,
# arithmetic (( )). On Raspberry Pi OS /bin/sh is dash, which parses none of
# them. Running it with `sh` produces a screen of syntax errors that look like
# a broken profiler rather than the wrong interpreter.
#
# Tested with [ -f ] rather than [ -x ]: the file lives on a FAT32 partition,
# where the execute bit is a property of the MOUNT OPTIONS, not of the file. A
# -x test can be false on a perfectly good script. Since bash is invoked
# explicitly, the execute bit does not matter anyway.
PROBE={BOOT_MOUNT}/sambuca/hardware-detect.sh
if [ -f "$PROBE" ]; then
    if command -v bash >/dev/null 2>&1; then
        log "running hardware-detect.sh under bash"
        bash "$PROBE" --print --no-lock >>"$LOG" 2>&1 \\
            || log "hardware-detect exited $?"
    else
        log "bash NOT PRESENT — cannot run the profiler (it is not POSIX sh)"
    fi
else
    log "NO PROFILER FOUND at $PROBE"
fi
"""

    ssh = ""
    if enable_ssh:
        ssh = """
systemctl enable ssh >/dev/null 2>&1 && log "ssh enabled" || log "could not enable ssh"
"""

    return f"""#!/bin/sh
# GENERATED BY sambuca-flasher. Runs once, on first boot, then removes itself.
set -u

LOG={BOOT_MOUNT}/sambuca-firstboot.log

log() {{
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >>"$LOG" 2>/dev/null
}}

# The FAT partition is mounted read-only surprisingly often. If we cannot write
# the log we have no channel back to the human at all, so say so on the console
# and keep going rather than dying silently.
mount -o remount,rw {BOOT_MOUNT} 2>/dev/null || true
: >"$LOG" 2>/dev/null || echo "sambuca: cannot write $LOG" >/dev/console

log "sambuca first boot starting"
log "hostname: $(hostname)"
log "kernel:   $(uname -srm)"
log "memory:   $(awk '/MemTotal/ {{print $2}}' /proc/meminfo 2>/dev/null) kB"
log "cpu:      $(nproc 2>/dev/null) cores"
log "model:    $(tr -d '\\0' </proc/device-tree/model 2>/dev/null)"

if [ "$(hostname)" != "{hostname}" ]; then
    echo "{hostname}" >/etc/hostname 2>/dev/null && log "hostname set to {hostname}"
fi
{ssh}{probe}
# ---- disarm ---------------------------------------------------------------
# Strip our own hook out of cmdline.txt so the next boot is a normal one.
CMDLINE={BOOT_MOUNT}/cmdline.txt
if [ -f "$CMDLINE" ]; then
    sed -i 's| systemd.run=[^ ]*||g; s| systemd.run_success_action=[^ ]*||g; s| systemd.unit=[^ ]*||g' "$CMDLINE"
    log "cmdline hook removed"
fi

log "sambuca first boot complete"
sync
exit 0
"""


def provision_boot_partition(
    boot: Path,
    *,
    payload_dir: Path | None = None,
    hostname: str = "sambuca",
    enable_ssh: bool = True,
    run_probe: bool = True,
    wifi_ssid: str | None = None,
    wifi_country: str = "GB",
) -> list[str]:
    """Write the first-boot configuration into the mounted FAT32 partition.

    Returns a list of what it did, for the operator to read.
    """
    if not boot.is_dir():
        raise PiError(f"boot partition not mounted at {boot}")

    cmdline = boot / "cmdline.txt"
    if not cmdline.is_file():
        raise PiError(
            f"{cmdline} not found — this does not look like a Raspberry Pi boot "
            f"partition. Refusing to write into it."
        )

    actions: list[str] = []

    # ORDER MATTERS, AND IT IS THE REVERSE OF THE OBVIOUS ONE.
    #
    # The payload goes down FIRST and cmdline.txt is armed LAST, because arming
    # is the irreversible step: once the kernel is told to run firstrun.sh, the
    # next boot runs it whether or not the payload it needs is present. An
    # earlier version wrote the script, armed the hook, and copied the payload
    # afterwards — and when the copy failed on a permissions error it left a
    # card that boots, runs a provisioning script, finds nothing, and reports a
    # missing profiler. Half-provisioned and silent about it.
    #
    # Same principle the x86 path already follows by writing the recovery
    # document before touching the USB: do the expensive, destructive,
    # hard-to-undo thing last.

    # 1. the payload
    if payload_dir and payload_dir.is_dir():
        dest = boot / "sambuca"
        # COPY OVER; do not delete and recreate. On Windows a directory removal
        # can still be pending when the very next makedirs runs, and the result
        # is ERROR_ACCESS_DENIED on a path that was fine a millisecond earlier —
        # observed on this exact card, while elevated. dirs_exist_ok sidesteps
        # the race entirely, and stale files are overwritten by name.
        shutil.copytree(payload_dir, dest, dirs_exist_ok=True)
        n = sum(1 for _ in dest.rglob("*") if _.is_file())
        actions.append(f"copied payload -> {dest.name}/ ({n} files)")

    # 2. the script
    firstrun = boot / "firstrun.sh"
    firstrun.write_text(
        render_firstrun(hostname=hostname, run_probe=run_probe, enable_ssh=enable_ssh),
        encoding="utf-8", newline="\n",
    )
    actions.append(f"wrote {firstrun.name}")

    # 3. ssh marker — the presence of the file is the switch
    if enable_ssh:
        (boot / "ssh").write_text("", encoding="utf-8")
        actions.append("enabled ssh (marker file)")

    # 4. wifi. NO PASSWORD IS ACCEPTED HERE and none is written. A wifi
    #    pre-shared key in a plaintext file on a FAT partition, on a card that
    #    travels between machines, is exactly the kind of thing this project
    #    exists to stop doing. The SSID is written so the owner can finish the
    #    join on the device; the secret is theirs to supply.
    if wifi_ssid:
        (boot / "sambuca-wifi.txt").write_text(
            f"# Finish this on the Pi:\n"
            f"#   sudo nmcli device wifi connect '{wifi_ssid}' --ask\n"
            f"ssid={wifi_ssid}\ncountry={wifi_country}\n",
            encoding="utf-8", newline="\n",
        )
        actions.append(f"noted wifi ssid {wifi_ssid!r} (no key written, by design)")

    # 5. LAST, and only now: arm cmdline.txt.
    #
    #    Everything above is inert — files sitting on a partition that nothing
    #    reads. This line is what makes the next boot behave differently, so it
    #    goes after every step that can fail. If the payload copy dies on a
    #    permissions error, the card is simply an unmodified Raspberry Pi OS
    #    card, which is a fine thing to be. Armed-but-incomplete is not.
    #
    #    ONE line, no trailing newline: the Pi firmware reads only the first
    #    line, and a stray newline silently truncates every parameter after it.
    text = cmdline.read_text(encoding="utf-8").strip()
    if "systemd.run=" in text:
        actions.append("cmdline.txt already armed — left alone")
    else:
        cmdline.write_text(text + _CMDLINE_APPEND, encoding="utf-8", newline="")
        actions.append("armed cmdline.txt (last, on purpose)")

    return actions


def find_boot_partition(device=None) -> Path | None:
    """Locate the FAT32 boot partition of a freshly written card.

    Takes no device by default, ON PURPOSE: rpi-imager chose the card and wrote
    it, so Sambuca never held a handle to it. The card is found by what it
    contains — a cmdline.txt — rather than by a device path nobody passed.

    The letterless case is the one that matters. A freshly written Raspberry Pi
    card mounts its boot partition WITHOUT a drive letter, so scanning lettered
    volumes finds nothing on a perfectly healthy card. Rescan, ask Windows for
    a letter, then look again.
    """
    import platform
    import time

    from .winvol import assign_boot_letter, disk_number, lettered_volumes, rescan_disks

    def scan() -> Path | None:
        if platform.system() != "Windows":
            for base in ("/media", "/run/media", "/mnt"):
                for candidate in Path(base).rglob("cmdline.txt"):
                    return candidate.parent
            return None
        for letter in lettered_volumes():
            p = Path(f"{letter}:/")
            try:
                if (p / "cmdline.txt").is_file():
                    return p
            except OSError:
                continue
        return None

    rescan_disks()
    disk = disk_number(getattr(device, "path", "") or "") if device else None

    for attempt in range(15):
        found = scan()
        if found:
            return found
        if attempt == 2:
            assign_boot_letter(disk)
        time.sleep(1)
    return None
