"""Nothing printed may be unprintable on the console it is printed to.

THE FAILURE THIS GUARDS ALREADY HAPPENED, and CLAUDE.md records it: a `✗` in a
lint tool crashed the tool that was supposed to report the finding. The Windows
console is cp1252. A character outside it does not degrade to a question mark —
`print()` raises UnicodeEncodeError, and the program dies at the moment it was
trying to tell somebody what was wrong.

The flasher is a WINDOWS program. Its whole audience is on the platform where
this breaks.

WHAT THIS DELIBERATELY ALLOWS: em dash, ellipsis and middle dot all encode
cleanly in cp1252, and they are used throughout. The rule is not "ASCII only" —
it is "nothing that cannot be encoded". Checked by encoding, not by a hand-kept
list of characters somebody thought were safe.

Found while auditing for the opposite conclusion: a first pass printed the
offending characters THROUGH the terminal it was diagnosing, so every one came
back as `?` and all three looked broken. Report codepoints, not glyphs, when the
channel is the thing under test.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

# Everything whose output reaches a console an owner is looking at.
SOURCES = sorted(
    [*(REPO / "tools").glob("*.py"),
     *(REPO / "apps/flasher/src/sambuca_flasher").glob("*.py")]
)

PRINTS = re.compile(r"\bprint\(|\b_say\(|\bsys\.stdout\.write\(|\bsys\.stderr\.write\(")


def _offenders(path: pathlib.Path) -> list[str]:
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not PRINTS.search(line):
            continue
        for ch in line:
            if ord(ch) < 128:
                continue
            try:
                ch.encode("cp1252")
            except UnicodeEncodeError:
                out.append(f"{path.name}:{i}: U+{ord(ch):04X}")
    return out


def test_the_audit_actually_reads_something() -> None:
    """A glob that matches nothing passes every assertion below it."""
    assert len(SOURCES) >= 10, f"only found {len(SOURCES)} source files"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_printed_lines_survive_the_windows_console(path: pathlib.Path) -> None:
    bad = _offenders(path)
    assert not bad, (
        "these would raise UnicodeEncodeError on a cp1252 console, killing the "
        "program mid-message: " + "; ".join(bad)
    )


def test_the_check_can_actually_fail() -> None:
    """Proves the detector, not the code.

    Every audit in this project that was written carelessly reported a clean
    sweep it had not earned. This one asserts it recognises a real offender —
    the exact character from the incident CLAUDE.md records.
    """
    probe = pathlib.Path(__file__).parent / "_encoding_probe.py.txt"
    probe.write_text('print("✗ failed")\n', encoding="utf-8")
    try:
        assert _offenders(probe) == [f"{probe.name}:1: U+2717"]
    finally:
        probe.unlink()
