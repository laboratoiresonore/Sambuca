"""The recovery key that is PRINTED must be the recovery key that is ENROLLED.

This is the unbrick half of "a fortress that can also be unbricked", and it has
a property nothing was checking: the string on the paper and the bytes in the
LUKS keyslot are produced by two different programs, on two different machines,
at two different times.

  the flasher   derives it, writes it to luks-recovery.key as ASCII with no
                trailing newline, and prints it on the recovery document
  the appliance reads that file, strips \\r \\n space and tab, and enrols
                whatever remains as the passphrase

They agree today. They agree because the key is grouped with HYPHENS, and the
appliance's normalisation removes whitespace but not hyphens. Format the key
with spaces instead — a one-character change in a formatting expression, the
kind nobody would think to test — and the appliance enrols a string the printed
sheet does not show. The keyslot would exist, `luksDump` would look correct, and
the key would never open the disk.

Nobody would find out until a real recovery, which is the worst possible moment
and the one this whole mechanism exists for.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps/flasher/src"))

from sambuca_flasher import keys  # noqa: E402

ENROLL = REPO / "engine/autoinstall/enroll-recovery-key.sh"

# A fixed phrase, so this tests the CHAIN rather than the randomness.
PHRASE = ("zebra " * 24).strip()


def _appliance_normalisation(raw: bytes) -> bytes:
    """Exactly what enroll-recovery-key.sh does: tr -d '\\r\\n \\t'."""
    return bytes(b for b in raw if b not in b"\r\n \t")


def test_the_normalisation_here_matches_the_installer() -> None:
    """If the installer's tr set changes, this test's model of it must too —
    otherwise the test goes on passing about a normalisation nobody performs."""
    src = ENROLL.read_text(encoding="utf-8")
    assert "tr -d '\\r\\n \\t'" in src, (
        "enroll-recovery-key.sh no longer strips exactly \\r\\n space tab; the "
        "model in this test is now wrong and every assertion below is about "
        "something that does not happen")


def test_the_printed_key_survives_the_appliance_untouched() -> None:
    """THE INVARIANT. What the owner types must be what was enrolled."""
    printed = keys.derive_luks_recovery_key(PHRASE)
    on_the_stick = printed.encode("ascii")            # cli.py writes exactly this
    enrolled = _appliance_normalisation(on_the_stick)
    assert enrolled.decode("ascii") == printed, (
        "the appliance enrols a different string from the one printed")


def test_the_key_contains_nothing_the_appliance_would_strip() -> None:
    """The trap, stated directly.

    Grouping the key with spaces instead of hyphens is a one-character change
    in a formatting expression. It would look better on paper and it would
    silently break every recovery, because the appliance removes spaces before
    enrolling and the sheet still shows them.
    """
    printed = keys.derive_luks_recovery_key(PHRASE)
    for ch in ("\r", "\n", " ", "\t"):
        assert ch not in printed, (
            f"the printed key contains {ch!r}, which the appliance strips "
            f"before enrolling — the sheet and the keyslot would disagree")


def test_the_key_is_typable_at_a_console_at_a_bad_moment() -> None:
    """base32 excludes 0/1/8/9 and the glyph pairs that get misread off paper.
    This is a string somebody types one character at a time, on a rescue
    console, having already had a bad day."""
    printed = keys.derive_luks_recovery_key(PHRASE)
    assert re.fullmatch(r"[A-Z2-7]{4}(-[A-Z2-7]{4})*", printed), printed
    # 0/1/8/9 ONLY. base32 is A-Z2-7, so 6 and 7 are legitimate characters —
    # my first version listed 6 as confusable and failed a correct key, which
    # is the more dangerous direction for a test to be wrong in: a false alarm
    # here invites somebody to "fix" a derivation that was never broken.
    for confusable in "0189":
        assert confusable not in printed, (
            f"{confusable!r} is not in the base32 alphabet and should be "
            f"impossible; the encoding has changed")


def test_it_is_deterministic_across_calls() -> None:
    """A recovery document printed today must match a keyslot enrolled today —
    and the same phrase must give the same key on a different machine, months
    later, when somebody enrols it by hand with sambuca-recovery enrol."""
    a = keys.derive_luks_recovery_key(PHRASE)
    b = keys.derive_luks_recovery_key(PHRASE)
    assert a == b


def test_a_different_phrase_gives_a_different_key() -> None:
    other = " ".join(["abandon"] * 23 + ["art"])       # a valid BIP-39 phrase
    assert keys.derive_luks_recovery_key(PHRASE) != \
        keys.derive_luks_recovery_key(other)


def test_the_backup_password_is_not_the_disk_key() -> None:
    """Sharing one derivation would mean disclosing a backup password also
    hands over the disk. The info strings differ for exactly this reason."""
    assert keys.derive_backup_password(PHRASE) != \
        keys.derive_luks_recovery_key(PHRASE)


def test_a_bad_phrase_is_refused_rather_than_silently_derived() -> None:
    """Deriving from an invalid phrase would produce a plausible key that opens
    nothing — the same failure as enrolling the wrong string, arrived at from
    the other end."""
    with pytest.raises(ValueError):
        keys.derive_luks_recovery_key(" ".join(["abandon"] * 24))


def test_the_installer_still_writes_the_key_without_a_newline() -> None:
    """cryptsetup uses the file's ENTIRE contents as the passphrase. A trailing
    newline enrols "KEY\\n", which nobody can type because the terminal strips
    the newline that submits the line."""
    src = (REPO / "apps/flasher/src/sambuca_flasher/cli.py").read_text(
        encoding="utf-8")
    assert 'write_bytes(\n            keys.luks_recovery_key.encode("ascii"))' in src \
        or "luks_recovery_key.encode(\"ascii\"))" in src, (
        "the key file write changed shape — check it still adds no newline")
    assert "luks_recovery_key.encode" in src
