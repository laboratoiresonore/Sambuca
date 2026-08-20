"""
sambuca :: the recovery vault — a second way back in, when the paper is gone.

THE PRINTED SHEET IS THE PRIMARY PATH AND STAYS PRIMARY. But paper gets lost,
and "you lost the sheet, everything is gone" is not an acceptable answer for
somebody who put their family photos and their client files on this. So the
flasher can keep an ENCRYPTED copy of the key material, unlocked by answering
three questions.

═══════════════════════════════════════════════════════════════════════════
THE HONEST WARNING, WHICH BELONGS AT THE TOP AND NOT IN A FOOTNOTE: this vault
is a SECOND COMPLETE COPY of every secret on the machine. Someone who steals
the laptop AND guesses the three answers has the disk.

That is why the KDF below is deliberately brutal, why the answers must have
real entropy, why it is opt-in, and why it can be deleted. The sheet alone
remains sufficient without it.
═══════════════════════════════════════════════════════════════════════════

WHY scrypt AND WHY SO SLOW. The input is low-entropy — three facts about a
person, the kind of thing a determined attacker can research or guess. The
answers cannot carry the security on their own, so the KDF has to do the work
they do not: n=2**20, r=8, p=1 is roughly 1 GiB and several seconds per
attempt. That turns a dictionary of a million guesses into a year of compute
rather than an afternoon.

WHY THE AUTH TAG IS THE ONLY CHECK. There is no stored hash of the answers to
verify a guess against. AES-256-GCM's tag says "these answers were right"
during decryption and nothing else in the file helps an attacker score an
attempt cheaply. A separate verifier would hand them exactly that.

NORMALISATION IS VERSIONED AND LOAD-BEARING. A correct answer typed years later
must derive the same key: different keyboard, different capitalisation, a
trailing full stop, an accent typed as a combining mark instead of a precomposed
character. Get this wrong and the vault is a brick that looks fine. It is pinned
by a test vector, exactly like the seed derivations are.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import unicodedata
from dataclasses import dataclass
from pathlib import Path

SCHEMA = 1

# THE NORMALISATION VERSION IS STORED IN EVERY VAULT. If this ever changes, old
# vaults keep decrypting with the rules they were written under — a vault that
# stops opening because the rules improved is a vault that failed.
NORM_VERSION = 1

# n=2**20, r=8, p=1 -> exactly 1 GiB of scratch, ~4-5 seconds on a 2026 laptop.
SCRYPT_N = 2 ** 20
SCRYPT_R = 8
SCRYPT_P = 1

# maxmem IS A C long, so on Windows it cannot exceed 2**31-1. The 1 GiB the
# parameters above need fits, but only just — passing 2**31 raises
# OverflowError on Windows and nowhere else, which is the kind of thing that
# surfaces on somebody else's machine long after it was written.
SCRYPT_MAXMEM = 2 ** 31 - 1

MIN_ANSWER_CHARS = 4
QUESTION_COUNT = 3


class VaultError(Exception):
    """Something the owner needs told plainly, never a traceback."""


class WrongAnswers(VaultError):
    """The answers did not decrypt it. Deliberately indistinguishable from a
    corrupt file to anyone probing: both are just 'it did not open'."""


def _require_aead():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:                                   # pragma: no cover
        raise VaultError(
            "The recovery vault needs the 'cryptography' package.\n"
            "  Install it with:  pip install cryptography") from exc
    return AESGCM


def normalise(answer: str, *, version: int = NORM_VERSION) -> str:
    """Reduce an answer to the form its key is derived from.

    EVERY RULE HERE EXISTS BECAUSE A CORRECT ANSWER WOULD OTHERWISE FAIL YEARS
    LATER, typed by the same person on a different day:

      NFKD + strip combining marks — "José" typed with a combining acute is a
      different byte string from the precomposed form, and which one you get
      depends on the keyboard and the operating system.
      casefold — not lower(), which mishandles ß and Turkish dotted I.
      collapse internal whitespace — double spaces are invisible.
      strip surrounding punctuation — a trailing full stop is a coin flip.
    """
    if version != 1:                                             # pragma: no cover
        raise VaultError(f"unknown normalisation version {version}")
    s = unicodedata.normalize("NFKD", answer)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold()
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(".,;:!?'\"()[]{}-_")


def _canonical(answers: list[str], *, version: int = NORM_VERSION) -> bytes:
    """Join normalised answers UNAMBIGUOUSLY.

    Length-prefixed rather than delimiter-joined. With a plain separator,
    ["ab", "c"] and ["a", "bc"] can produce the same bytes, so two different
    sets of answers would open the same vault — a small but real weakening,
    and free to avoid.
    """
    parts = []
    for a in answers:
        n = normalise(a, version=version).encode("utf-8")
        parts.append(len(n).to_bytes(4, "big") + n)
    return b"".join(parts)


def derive_key(answers: list[str], salt: bytes, *,
               version: int = NORM_VERSION) -> bytes:
    """The 32-byte AES key. Several seconds and ~1 GiB, on purpose."""
    return hashlib.scrypt(
        _canonical(answers, version=version),
        salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM, dklen=32,
    )


@dataclass
class Strength:
    ok: bool
    reason: str


def check_answers(questions: list[str], answers: list[str]) -> Strength:
    """Refuse answers that cannot carry their share, and say why.

    NOT A PASSWORD METER. The failure here is not "weak" in the usual sense —
    it is ANSWERABLE BY SOMEBODY ELSE. A mother's maiden name is a public
    record; a first pet is on somebody's social media. The check catches the
    mechanical failures and the interface has to carry the rest.
    """
    if len(answers) != QUESTION_COUNT:
        return Strength(False, f"there must be {QUESTION_COUNT} answers")

    seen = set()
    for i, a in enumerate(answers, 1):
        n = normalise(a)
        if len(n) < MIN_ANSWER_CHARS:
            return Strength(False,
                            f"answer {i} is too short — at least "
                            f"{MIN_ANSWER_CHARS} characters after tidying")
        if n in seen:
            return Strength(False, "two answers are the same, which is one "
                                   "answer wearing two hats")
        seen.add(n)

    if len(set(q.strip().casefold() for q in questions)) != len(questions):
        return Strength(False, "two questions are the same")

    total = sum(len(normalise(a)) for a in answers)
    if total < 20:
        return Strength(False, "these answers are very short taken together; "
                               "longer, more specific ones are much harder to "
                               "guess")
    return Strength(True, "")


def create(path: Path, secrets_payload: dict, questions: list[str],
           answers: list[str], *, fingerprint: str = "") -> Path:
    """Write an encrypted vault. The answers are used and immediately dropped.

    The QUESTIONS are stored in the clear — they have to be, or nobody could be
    prompted years later. That is a deliberate disclosure: a question can hint
    at its answer, which is one more reason the interface pushes for questions
    whose answers are not public facts.
    """
    AESGCM = _require_aead()

    verdict = check_answers(questions, answers)
    if not verdict.ok:
        raise VaultError(verdict.reason)

    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = derive_key(answers, salt)

    plaintext = json.dumps(secrets_payload, sort_keys=True).encode("utf-8")

    # The header is AUTHENTICATED but not encrypted. Tampering with the salt or
    # the parameters — to make a later unlock cheap, say — breaks the tag.
    header = {
        "schema": SCHEMA,
        "norm_version": NORM_VERSION,
        "fingerprint": fingerprint,
        "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
                "salt": salt.hex()},
        "questions": list(questions),
    }
    aad = json.dumps(header, sort_keys=True).encode("utf-8")
    blob = AESGCM(key).encrypt(nonce, plaintext, aad)

    doc = {**header, "nonce": nonce.hex(), "ciphertext": blob.hex()}

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass                      # Windows and some filesystems ignore this.
    tmp.replace(path)             # Atomic: never a half-written vault.
    return path


def open_vault(path: Path, answers: list[str]) -> dict:
    """Decrypt. Raises WrongAnswers if the tag does not verify."""
    AESGCM = _require_aead()

    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VaultError(f"cannot read the vault at {path}") from exc

    if doc.get("schema") != SCHEMA:
        raise VaultError(
            f"this vault says schema {doc.get('schema')}, and this build "
            f"speaks {SCHEMA}. Use a matching version rather than guessing.")

    kdf = doc.get("kdf", {})
    header = {k: doc[k] for k in
              ("schema", "norm_version", "fingerprint", "kdf", "questions")
              if k in doc}
    aad = json.dumps(header, sort_keys=True).encode("utf-8")

    # DERIVED WITH THE VAULT'S OWN PARAMETERS, not today's constants. A vault
    # written under different settings must still open — otherwise raising the
    # cost for new vaults would silently brick every old one.
    key = hashlib.scrypt(
        _canonical(answers, version=int(doc.get("norm_version", 1))),
        salt=bytes.fromhex(kdf["salt"]),
        n=int(kdf["n"]), r=int(kdf["r"]), p=int(kdf["p"]),
        maxmem=SCRYPT_MAXMEM, dklen=32,
    )

    try:
        plain = AESGCM(key).decrypt(
            bytes.fromhex(doc["nonce"]), bytes.fromhex(doc["ciphertext"]), aad)
    except Exception as exc:      # noqa: BLE001 - any failure is "did not open"
        # DELIBERATELY ONE ANSWER for wrong answers, a corrupt file, and a
        # tampered header. Distinguishing them would tell somebody probing the
        # file which of their guesses was closer.
        raise WrongAnswers(
            "Those answers did not open the vault.\n"
            "  Answers are matched loosely - capitals, accents and punctuation\n"
            "  do not matter - so this means one of them is genuinely different\n"
            "  from what was recorded.") from exc

    return json.loads(plain.decode("utf-8"))


def default_path(fingerprint: str) -> Path:
    return Path.home() / ".sambuca" / "vault" / f"recovery-{fingerprint}.json"
