"""
sambuca :: safe console output.

The flasher runs on Windows, whose default console codepage renders an em-dash
as a replacement glyph. Mojibake on the screens a stuck novice is reading — the
boot guide, the hardware estimate — is not cosmetic; it is the difference
between instructions that look authoritative and instructions that look broken.

Sanitising at the RENDER layer rather than in the data means a contributor
typing a typographic quote cannot reintroduce it. Lives here rather than in one
command's module because two commands needed it, and a second copy is a copy
that drifts.
"""

from __future__ import annotations

import platform
import sys

_ASCII_MAP = {
    "—": "-",      # em dash
    "–": "-",      # en dash
    "‘": "'",      # left single quote
    "’": "'",      # right single quote
    "“": '"',      # left double quote
    "”": '"',      # right double quote
    "…": "...",    # ellipsis
    "→": "->",     # right arrow
    "⌥": "Option", # mac option key
    "·": "-",      # middle dot
    " ": " ",      # non-breaking space
    "×": "x",      # multiplication sign
    "✓": "ok",     # check mark
    "£": "GBP ",   # pound sign
    "€": "EUR ",   # euro sign
}


def ascii_safe(text: str) -> str:
    """Reduce text to plain ASCII, mapping the typography we actually use.

    Anything still outside ASCII is replaced rather than raising — a
    UnicodeEncodeError would abort the whole guide, and a guide with one odd
    character in it is far better than no guide at all.
    """
    for bad, good in _ASCII_MAP.items():
        text = text.replace(bad, good)
    return text.encode("ascii", "replace").decode("ascii")


def launched_by_double_click() -> bool:
    """True when started from a file manager rather than an existing terminal.

    WHY THIS MATTERS MORE THAN IT LOOKS. A console program launched by
    double-click gets a console window of its own, and Windows destroys that
    window the instant the process exits. With no arguments argparse prints a
    usage line and exits 2, so the window appears and vanishes inside a
    frame — which a person reasonably reports as "it crashed as soon as I
    clicked it".

    That is the FIRST thing a non-technical owner does with a downloaded .exe,
    and the README tells them to download the app and run it. Getting this
    wrong means the project fails before it has done anything at all.

    GetConsoleProcessList reports how many processes share this console. If we
    are the only one, the console was created for us — nobody typed a command.
    """
    if platform.system() != "Windows":
        # On macOS and Linux, a double-clicked binary has no controlling
        # terminal at all, which is a cleaner signal.
        try:
            return not sys.stdin.isatty()
        except (AttributeError, ValueError):
            return False

    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        buf = (ctypes.c_uint * 16)()
        count = kernel32.GetConsoleProcessList(buf, 16)
    except (OSError, AttributeError, ImportError):
        return False

    # MEASURED, not assumed. `count == 1` was the obvious rule and it was
    # wrong for the shipped artefact: a PyInstaller one-file binary runs a
    # bootloader that unpacks itself and spawns the real process, so BOTH sit
    # in the console list. On this machine:
    #
    #     from source, own console      1        double-clicked
    #     from source, inherited        3        typed in a shell
    #     FROZEN,      own console      2        double-clicked
    #     FROZEN,      inherited        4        typed in a shell
    #
    # So the threshold depends on whether we are frozen. Getting this wrong in
    # the direction of "1" means the fix works perfectly when tested from a
    # checkout and does nothing at all in the binary people download — which is
    # precisely the trap that shipped a flasher with no engine in it.
    return count <= (2 if getattr(sys, "frozen", False) else 1)


def pause_before_exit(message: str = "Press Enter to close this window...") -> None:
    """Hold a self-created console open so the reader can actually read it."""
    if not launched_by_double_click():
        return
    try:
        print()
        input(message)
    except (EOFError, KeyboardInterrupt, OSError):
        pass
