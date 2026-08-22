"""No surface may tell the owner that the installer USB cleans itself up.

Because two of them did, and one was the printed sheet.

  cli.py         "This USB now carries the disk passphrase … the appliance
                 erases it on first boot."
  recovery_pdf.py "The appliance erases it on first boot. Until then, treat the
                 USB as this sheet"

Both false. `first-boot.sh` shreds `/boot/sambuca/provision.json` — the copy on
the INSTALLED machine's boot partition. Nothing in the engine writes to /cdrom;
every reference to the installation medium only reads from it. The stick keeps
the disk passphrase, the LUKS recovery key and the backup password for as long
as it exists.

The bug was not the sentence being inaccurate. It was telling somebody they
could stop treating a key like a key — and saying it on the one artefact they
keep, where the wrong version outlives every other copy.

The near-miss worth recording: the console message was found first, fixed, and
that felt like the end of it. The sheet was found only by grepping for the
CLAIM across every surface instead of the site. Fixing one copy of a false
statement is how the shadow survives, and this project has now done that
enough times to write a test about it.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]

# Everything that puts words in front of an owner about the stick.
OWNER_FACING = (
    REPO / "apps/flasher/src/sambuca_flasher/cli.py",
    REPO / "apps/flasher/src/sambuca_flasher/recovery_pdf.py",
    REPO / "apps/flasher/src/sambuca_flasher/window.py",
    REPO / "README.md",
)

# A claim that the MEDIUM erases itself. Deliberately matched as a claim about
# a subject, not as the bare word "erase": the disk-selection screen says "ONLY
# that one is erased" about the target disk, which is true and must not trip.
SELF_ERASING_CLAIM = re.compile(
    r"(usb|stick|medium)[^.\n]{0,90}\b(is|are|gets?|will be)\s+"
    r"(erased|wiped|shredded|cleaned)"
    r"|(appliance|installer|it)\s+(erases|wipes|shreds)\s+it\b",
    re.I)


def _lines(path: pathlib.Path) -> list[tuple[int, str]]:
    if not path.exists():
        return []
    return list(enumerate(path.read_text(encoding="utf-8").splitlines(), 1))


def test_nothing_claims_the_installer_medium_erases_itself() -> None:
    """THE REGRESSION. Both original wordings match this pattern."""
    hits = [
        f"{p.relative_to(REPO)}:{n}: {ln.strip()}"
        for p in OWNER_FACING for n, ln in _lines(p)
        if SELF_ERASING_CLAIM.search(ln)
        # The comments recording the fix quote the old wording on purpose.
        and "USED TO SAY" not in ln and not ln.lstrip().startswith("#")
    ]
    assert not hits, (
        "something tells the owner the installation medium is erased. It is "
        "not — first-boot.sh shreds only /boot/sambuca/provision.json on the "
        "installed machine:\n  " + "\n  ".join(hits))


def test_the_console_says_the_stick_stays_a_key() -> None:
    src = (REPO / "apps/flasher/src/sambuca_flasher/cli.py").read_text(
        encoding="utf-8")
    assert "Nothing erases it" in src, (
        "the post-write message no longer tells the owner the stick keeps its "
        "secrets")


def test_the_printed_sheet_says_it_too() -> None:
    """The sheet matters more than the console: it is the copy that survives
    the session, and it is what somebody re-reads a year later."""
    src = (REPO / "apps/flasher/src/sambuca_flasher/recovery_pdf.py").read_text(
        encoding="utf-8")
    assert "NOTHING ERASES IT" in src, (
        "the recovery document no longer warns that the USB stays a key")


def test_the_claim_that_is_true_is_not_broken_by_this() -> None:
    """`first-boot.sh` really does shred the payload from the installed boot
    partition, and the README says so. A test that forced that sentence out
    would be trading one false statement for another."""
    src = (REPO / "engine/first-boot.sh").read_text(encoding="utf-8")
    assert "shred -u -n 3" in src and "PROVISION_JSON" in src


def test_the_pattern_would_have_caught_the_original_wording() -> None:
    """A regression test written after the fix can pass because the pattern is
    too narrow to have ever matched. Check it against the real historical
    strings, verbatim."""
    for original in (
        "  This USB now carries the disk passphrase. Treat it as a key until",
        "  installation finishes; the appliance erases it on first boot.",
        '"The appliance erases it on first boot. Until then, treat the USB as this",',
    ):
        if SELF_ERASING_CLAIM.search(original):
            break
    else:
        raise AssertionError(
            "the pattern matches none of the wordings it was written for")


def test_the_true_disk_erase_message_still_passes() -> None:
    """"ONLY that one is erased" is about the target disk and is correct. A
    pattern that flagged it would push somebody to soften a warning that needs
    to stay loud."""
    assert not SELF_ERASING_CLAIM.search(
        "        - then choose your USB stick. ONLY that one is erased.")
