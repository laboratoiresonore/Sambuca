"""The recovery vault: a second way back in when the paper is gone.

THIS FILE IS MOSTLY ABOUT ONE FEAR. The vault will be opened, if ever, YEARS
after it was written — by the same person, on a different machine, typing the
same three facts a slightly different way. Every normalisation rule exists
because a correct answer would otherwise fail then, and a vault that refuses a
correct answer is worse than no vault: it is a promise that fails at the only
moment it was ever needed.

So the normalisation has a PINNED VECTOR, exactly like the seed derivations do.
If a future change alters what these inputs produce, this test fails rather
than silently bricking every vault already written.

The KDF is deliberately ~1 GiB and several seconds, so most tests run it at toy
parameters. One does not — because parameters that only ever run at toy sizes
are parameters nobody has proven work.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from sambuca_flasher import vault  # noqa: E402

pytest.importorskip("cryptography", reason="the vault needs an AEAD")

SECRETS = {
    "seed_phrase": "abandon " * 23 + "art",
    "root_passphrase": "correct-horse-battery-staple",
    "luks_recovery_key": "0123456789abcdef",
}
QUESTIONS = [
    "What street did the house with the green door stand on?",
    "What did we call the car that broke down in France?",
    "Which pub did we go to after the registry office?",
]
ANSWERS = ["Marchmont Terrace", "The Biscuit Tin", "The Old Bell on Fleet"]


@pytest.fixture
def fast(monkeypatch):
    """Toy KDF parameters. The real ones are exercised once, below."""
    monkeypatch.setattr(vault, "SCRYPT_N", 2 ** 8)
    monkeypatch.setattr(vault, "SCRYPT_R", 8)


class TestNormalisation:
    """The rules that decide whether a correct answer works in 2034."""

    @pytest.mark.parametrize("written,typed", [
        ("Marchmont Terrace", "marchmont terrace"),      # capitals
        ("Marchmont Terrace", "  Marchmont   Terrace "),  # stray whitespace
        ("Marchmont Terrace", "Marchmont Terrace."),      # trailing full stop
        ("Marchmont Terrace", "'Marchmont Terrace'"),     # quoted
        ("José's bar", "jose's bar"),                     # accent dropped
        ("Straße", "strasse"),                            # casefold, not lower
    ])
    def test_the_same_answer_typed_differently_still_opens_it(self, written, typed):
        assert vault.normalise(written) == vault.normalise(typed)

    def test_a_precomposed_accent_matches_a_combining_one(self):
        """Which one you get depends on the keyboard and the OS, and the person
        typing has no idea there is a difference."""
        assert vault.normalise("José") == vault.normalise("José")

    def test_genuinely_different_answers_stay_different(self):
        assert vault.normalise("Marchmont") != vault.normalise("Marchmond")

    def test_the_pinned_vector(self):
        """THE ONE THAT MATTERS MOST, and the reason it is pinned.

        If a future change to these rules alters what any of these produce,
        every vault already written stops opening — silently, and only
        discovered by somebody who has already lost their paper copy. This test
        failing means: bump NORM_VERSION, do not edit version 1.
        """
        vectors = {
            "  Marchmont   Terrace. ": "marchmont terrace",
            "José's Bar!": "jose's bar",
            "Straße": "strasse",
            "THE OLD BELL, on Fleet": "the old bell, on fleet",
            "Ångström": "angstrom",
        }
        for raw, expected in vectors.items():
            assert vault.normalise(raw) == expected, f"normalisation drifted for {raw!r}"


class TestCanonicalEncoding:
    def test_answers_cannot_be_re_split_into_the_same_key(self, fast):
        """["ab","c"] and ["a","bc"] must not derive the same key.

        With a plain delimiter they would, so two different answer sets would
        open one vault. Small, real, and free to avoid with length prefixes.
        """
        salt = b"x" * 16
        a = vault.derive_key(["ab", "c", "dddd"], salt)
        b = vault.derive_key(["a", "bc", "dddd"], salt)
        assert a != b


class TestRoundTrip:
    def test_the_right_answers_return_every_secret(self, fast, tmp_path):
        p = vault.create(tmp_path / "v.json", SECRETS, QUESTIONS, ANSWERS,
                         fingerprint="AAAA-BBBB-CCCC")
        assert vault.open_vault(p, ANSWERS) == SECRETS

    def test_answers_typed_untidily_years_later_still_work(self, fast, tmp_path):
        p = vault.create(tmp_path / "v.json", SECRETS, QUESTIONS, ANSWERS)
        sloppy = ["  marchmont terrace.", "THE BISCUIT TIN", "the old bell on fleet"]
        assert vault.open_vault(p, sloppy) == SECRETS

    def test_wrong_answers_are_refused(self, fast, tmp_path):
        p = vault.create(tmp_path / "v.json", SECRETS, QUESTIONS, ANSWERS)
        with pytest.raises(vault.WrongAnswers):
            vault.open_vault(p, ["Marchmont Terrace", "The Biscuit Tin", "wrong"])

    def test_nothing_readable_is_left_in_the_file(self, fast, tmp_path):
        """The questions are stored in the clear by necessity. Nothing else is."""
        p = vault.create(tmp_path / "v.json", SECRETS, QUESTIONS, ANSWERS)
        raw = p.read_text(encoding="utf-8")
        assert "abandon" not in raw
        assert "correct-horse" not in raw
        assert "0123456789abcdef" not in raw
        assert QUESTIONS[0] in raw          # deliberately visible

    def test_no_temporary_file_survives(self, fast, tmp_path):
        vault.create(tmp_path / "v.json", SECRETS, QUESTIONS, ANSWERS)
        assert not list(tmp_path.glob("*.tmp"))


class TestTampering:
    def test_editing_the_salt_breaks_it(self, fast, tmp_path):
        """The header is authenticated. Swapping the salt or lowering the KDF
        cost — to make a brute-force cheap — must not silently work."""
        p = vault.create(tmp_path / "v.json", SECRETS, QUESTIONS, ANSWERS)
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["kdf"]["salt"] = "00" * 16
        p.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(vault.WrongAnswers):
            vault.open_vault(p, ANSWERS)

    def test_editing_a_question_breaks_it(self, fast, tmp_path):
        p = vault.create(tmp_path / "v.json", SECRETS, QUESTIONS, ANSWERS)
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["questions"][0] = "something else entirely"
        p.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(vault.WrongAnswers):
            vault.open_vault(p, ANSWERS)

    def test_a_future_schema_is_refused_rather_than_guessed_at(self, fast, tmp_path):
        p = vault.create(tmp_path / "v.json", SECRETS, QUESTIONS, ANSWERS)
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["schema"] = 99
        p.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(vault.VaultError, match="schema"):
            vault.open_vault(p, ANSWERS)


class TestItOpensWithItsOwnParameters:
    def test_raising_the_cost_does_not_brick_existing_vaults(self, tmp_path,
                                                             monkeypatch):
        """Written cheap, then the constants go up. It must still open.

        Otherwise strengthening the KDF for new vaults would silently destroy
        every old one — the exact class of self-inflicted loss this whole
        module exists to prevent.
        """
        monkeypatch.setattr(vault, "SCRYPT_N", 2 ** 8)
        p = vault.create(tmp_path / "v.json", SECRETS, QUESTIONS, ANSWERS)
        monkeypatch.setattr(vault, "SCRYPT_N", 2 ** 10)   # cost raised later
        assert vault.open_vault(p, ANSWERS) == SECRETS


class TestAnswerQuality:
    def test_too_few_answers(self):
        assert not vault.check_answers(QUESTIONS, ["a", "b"]).ok

    def test_a_very_short_answer_is_refused(self):
        v = vault.check_answers(QUESTIONS, ["ok", "The Biscuit Tin", "Fleet Street"])
        assert not v.ok and "short" in v.reason

    def test_two_identical_answers_are_refused(self):
        v = vault.check_answers(QUESTIONS, ["Marchmont", "Marchmont", "Fleet Street"])
        assert not v.ok

    def test_two_identical_questions_are_refused(self):
        v = vault.check_answers(["same", "same", "other"], ANSWERS)
        assert not v.ok

    def test_good_answers_pass(self):
        assert vault.check_answers(QUESTIONS, ANSWERS).ok


@pytest.mark.slow
def test_the_real_parameters_actually_work(tmp_path):
    """~1 GiB and several seconds, run once.

    Parameters that are only ever exercised at toy sizes are parameters nobody
    has proven work. maxmem in particular is a C long, so 1 GiB fits only just
    on Windows and an over-large value raises OverflowError there and nowhere
    else — precisely the failure that shows up on somebody else's machine.
    """
    p = vault.create(tmp_path / "real.json", SECRETS, QUESTIONS, ANSWERS)
    assert vault.open_vault(p, ANSWERS) == SECRETS
