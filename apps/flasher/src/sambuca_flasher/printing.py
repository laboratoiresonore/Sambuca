"""
sambuca :: get the recovery sheet onto paper, not into a folder.

A PDF sitting in Downloads is not a recovery document. It is on the machine
whose disk it exists to unlock, in a folder nobody will find in eight months,
under a filename nobody will recognise. If the appliance is what fails, the
sheet is fine; if the LAPTOP is what fails, the only copy of the seed phrase
goes with it.

So the flow offers to print it, opens the print dialogue itself, and — the part
that matters — asks whether paper actually came out. A print job that silently
failed is the most likely outcome nobody checks: no printer, wrong printer,
out of paper, driver asleep.

WHY NOT JUST PRINT IT. Because sending an unprompted job containing somebody's
disk passphrase to whatever printer happens to be default is a genuinely bad
idea — it might be a shared office device, or a print-to-PDF that writes ANOTHER
copy somewhere. The owner picks the printer in their own dialogue.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path


def can_print() -> bool:
    """Is there any way to reach a print dialogue on this platform?"""
    system = platform.system()
    if system == "Windows":
        return True          # ShellExecute "print" is always available
    if system == "Darwin":
        return Path("/usr/bin/lpr").exists() or Path("/usr/sbin/lpr").exists()
    import shutil

    return shutil.which("lpr") is not None or shutil.which("xdg-open") is not None


def open_print_dialog(pdf: Path) -> bool:
    """Open the platform's print dialogue for this file.

    DELIBERATELY A DIALOGUE, not a silent job. The file contains a disk
    passphrase and a seed phrase; sending it unprompted to whatever printer is
    default could put it on a shared office device, or through a print-to-PDF
    driver that writes a second copy somewhere nobody is tracking.

    Returns whether the dialogue was reached — NOT whether anything printed.
    Nothing on any platform reliably reports that, which is exactly why the
    caller has to ask a human.
    """
    pdf = Path(pdf)
    if not pdf.is_file():
        return False

    system = platform.system()
    try:
        if system == "Windows":
            import os

            # `print` verb hands the file to the registered PDF handler, which
            # shows the user's own dialogue.
            os.startfile(str(pdf), "print")  # noqa: S606 - a local file we wrote
            return True

        if system == "Darwin":
            subprocess.run(["open", "-a", "Preview", str(pdf)],
                           timeout=30, check=False)
            return True

        import shutil

        if shutil.which("xdg-open"):
            subprocess.run(["xdg-open", str(pdf)], timeout=30, check=False)
            return True
    except (OSError, subprocess.SubprocessError):
        return False

    return False


def open_folder(path: Path) -> bool:
    """Show the file in a file manager, for when printing is not an option.

    The fallback matters: somebody with no printer needs to get this file onto
    a USB stick or another machine, and telling them a path they must retype is
    the guidance stopping one step early.
    """
    path = Path(path)
    target = path if path.is_dir() else path.parent
    system = platform.system()
    try:
        if system == "Windows":
            subprocess.run(["explorer", "/select,", str(path)],
                           timeout=20, check=False)
            return True
        if system == "Darwin":
            subprocess.run(["open", "-R", str(path)], timeout=20, check=False)
            return True
        import shutil

        if shutil.which("xdg-open"):
            subprocess.run(["xdg-open", str(target)], timeout=20, check=False)
            return True
    except (OSError, subprocess.SubprocessError):
        return False
    return False
