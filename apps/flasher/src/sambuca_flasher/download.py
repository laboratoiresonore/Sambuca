"""
sambuca :: fetch a large file, resumably, and never hand over an unverified one.

THIS EXISTS SO THE INSTALLER STOPS ASKING PEOPLE TO GO AND FIND THINGS. The
menu used to print "Run: sambuca-flasher write --iso <path>" at somebody who
had just double-clicked an app, which is THE RULE's named failure. The Pi path
never had this problem because rpi-imager downloads its own image; the x86 path
had no equivalent, so the difference was entirely an accident of which tool
happened to be wrapped.

THREE PROPERTIES, AND EACH ONE IS LOAD-BEARING:

  RESUMABLE. This is 755 MiB over a home connection. Losing it at 90% and
  starting again is how somebody gives up on the whole project, so a partial
  download is kept and continued with an HTTP Range request.

  VERIFIED BEFORE IT IS USED, NEVER AFTER. The digest is checked while the file
  is still called .part. An ISO that fails is deleted rather than left lying
  around looking usable — a corrupt installer that reaches a disk writer is a
  bricked machine and a very confusing afternoon.

  ATOMIC. The real name appears only once the digest matches, so an interrupted
  download can never be mistaken for a finished one. The same discipline the
  engine's own checkpoint download follows, and the same discipline
  sb_atomic_write had to be FIXED to follow.

WHAT THIS DELIBERATELY DOES NOT DO: resume a file whose digest it cannot check.
A .part of unknown provenance is discarded, because appending to somebody
else's leftovers and then trusting the result is how a supply chain gets poisoned.
"""

from __future__ import annotations

import hashlib
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_CHUNK = 1024 * 256
_UA = "sambuca-flasher"


@dataclass
class Progress:
    done: int
    total: int
    rate: float          # bytes/sec, averaged over the whole transfer

    @property
    def percent(self) -> float:
        return (self.done / self.total * 100.0) if self.total else 0.0

    @property
    def eta_seconds(self) -> float:
        if self.rate <= 0 or not self.total:
            return 0.0
        return max(0.0, (self.total - self.done) / self.rate)

    def human(self) -> str:
        """One line a non-technical person can read at a glance.

        Megabytes, not mebibytes: the number should match what their browser
        and file manager would have said, not what is technically tidier.
        """
        mb_done = self.done / 1_000_000
        mb_total = self.total / 1_000_000
        speed = self.rate / 1_000_000
        if self.total:
            eta = self.eta_seconds
            mins, secs = divmod(int(eta), 60)
            left = f"{mins}m {secs:02d}s left" if mins else f"{secs}s left"
            return (f"{self.percent:5.1f}%  {mb_done:6.0f} / {mb_total:.0f} MB"
                    f"  at {speed:4.1f} MB/s  -  {left}")
        return f"{mb_done:6.0f} MB  at {speed:4.1f} MB/s"


class DownloadError(Exception):
    """Something went wrong that the owner needs to be told about plainly."""


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def free_space(path: Path) -> int:
    """Bytes free on the volume that will hold the download."""
    p = path if path.is_dir() else path.parent
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def fetch(
    url: str,
    dest: Path,
    *,
    sha256: str,
    expected_size: int = 0,
    on_progress: Callable[[Progress], None] | None = None,
    timeout: float = 60.0,
) -> Path:
    """Download `url` to `dest`, resuming if possible, verifying before use.

    Returns the path on success. Raises DownloadError with a sentence fit to
    show a novice — never a traceback, never a bare errno.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    # ALREADY HAVE IT? Verify rather than assume. A file with the right name is
    # not evidence of anything; somebody may have half-copied it, or it may be
    # last year's release.
    if dest.is_file():
        if _sha256_of(dest) == sha256.lower():
            return dest
        raise DownloadError(
            f"A file called {dest.name} is already there, but it does not match\n"
            f"  what it should be. Move or delete it, then try again.")

    # DO NOT START WHAT CANNOT FINISH. Running out of disk at 700 MB wastes the
    # download and leaves a confusing mess behind.
    if expected_size:
        have = free_space(dest)
        already = part.stat().st_size if part.is_file() else 0
        if have < (expected_size - already) * 1.05:
            raise DownloadError(
                f"Not enough free space. This needs about "
                f"{expected_size / 1_000_000_000:.1f} GB and there is "
                f"{have / 1_000_000_000:.1f} GB free.")

    resume_from = part.stat().st_size if part.is_file() else 0
    if expected_size and resume_from > expected_size:
        # A .part bigger than the target is not a resumable download, it is
        # some other file wearing the same name.
        part.unlink(missing_ok=True)
        resume_from = 0

    headers = {"User-Agent": _UA}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # A server that ignores Range answers 200 with the WHOLE file.
            # Appending that to what we already have would produce a corrupt
            # file that is exactly the right length to look plausible.
            if resume_from and resp.status != 206:
                resume_from = 0
                part.unlink(missing_ok=True)

            total = expected_size
            if not total:
                length = resp.headers.get("Content-Length")
                total = (int(length) + resume_from) if length else 0

            mode = "ab" if resume_from else "wb"
            started = time.monotonic()
            done = resume_from
            last_emit = 0.0

            with part.open(mode) as fh:
                while True:
                    block = resp.read(_CHUNK)
                    if not block:
                        break
                    fh.write(block)
                    done += len(block)

                    now = time.monotonic()
                    if on_progress and (now - last_emit) >= 0.25:
                        elapsed = max(now - started, 1e-6)
                        rate = (done - resume_from) / elapsed
                        on_progress(Progress(done, total, rate))
                        last_emit = now

    except urllib.error.HTTPError as exc:
        raise DownloadError(
            f"The download server answered {exc.code}. The address may have\n"
            f"  moved - check for a newer Sambuca release.") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        # THE PARTIAL FILE IS KEPT ON PURPOSE. A dropped connection is the most
        # ordinary failure there is, and the next attempt resumes from here.
        raise DownloadError(
            f"The download stopped: {exc}.\n"
            f"  Run this again and it will carry on from where it got to.") from exc

    # VERIFY BEFORE THE NAME CHANGES, NOT AFTER. Until this passes, the file
    # must stay unusable.
    if _sha256_of(part) != sha256.lower():
        part.unlink(missing_ok=True)
        raise DownloadError(
            "The downloaded file is damaged, so it has been deleted.\n"
            "  This is usually a bad connection. Try again.")

    part.replace(dest)
    return dest
