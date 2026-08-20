"""
sambuca :: flasher key generation.

Everything in this module happens on the operator's own machine, offline. No
value produced here is ever transmitted, and the only durable copy of the seed
phrase and root passphrase is the printed recovery document.

The two artefacts and what each is for:

  SEED PHRASE (24 words, BIP-39)
      Deterministically derives the backup repository password. This is what
      makes the paper document sufficient for disaster recovery: with the seed
      alone you can decrypt a restic repository from a machine that no longer
      exists. It is NOT the disk passphrase — a 256-bit secret you must type at
      a console during a recovery is a secret you will get wrong.

  ROOT PASSPHRASE (32 characters)
      The LUKS disk passphrase and the console account password. Typed by a
      human at a physical keyboard, so it avoids characters that move on
      non-US layouts (the classic way an encrypted disk becomes unopenable
      because the recovery console came up with a different keymap).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
from dataclasses import dataclass

try:
    from mnemonic import Mnemonic
except ImportError as exc:  # pragma: no cover - dependency is declared
    raise SystemExit(
        "sambuca-flasher requires the 'mnemonic' package for BIP-39 generation.\n"
        "Install it with:  pip install mnemonic\n"
        "A hand-rolled wordlist is not acceptable here: a subtly wrong list "
        "produces a seed that no standard tool can ever recover."
    ) from exc


# Deliberately excludes: 0/O, 1/l/I (indistinguishable in most print fonts),
# and every character whose position moves between US/UK/FR/DE keyboards
# (@ " # ~ \ | / etc). A passphrase you cannot type at a recovery console in a
# hotel room is not a passphrase.
_PASSPHRASE_ALPHABET = (
    "abcdefghijkmnopqrstuvwxyz"
    "ABCDEFGHJKLMNPQRSTUVWXYZ"
    "23456789"
    "-_.+="
)

_PASSPHRASE_LENGTH = 32
_SEED_STRENGTH_BITS = 256  # 256 bits -> 24 words


@dataclass(frozen=True)
class KeyMaterial:
    """The complete set of secrets for one appliance. Never serialised whole."""

    seed_phrase: str          # 24 BIP-39 words, space separated
    root_passphrase: str      # 32 characters, typed by a human
    backup_password: str      # derived from the seed; never printed on paper
    backup_seed_hash: str     # sha256 of the seed, for verification only
    fingerprint: str          # short, non-secret; identifies this key set

    def redacted(self) -> dict[str, str]:
        """A representation safe to log, print to a terminal, or attach to a bug."""
        return {
            "fingerprint": self.fingerprint,
            "seed_phrase": f"<24 words, {len(self.seed_phrase.split())} present>",
            "root_passphrase": f"<{len(self.root_passphrase)} chars>",
            "backup_password": "<derived, not shown>",
            "backup_seed_hash": self.backup_seed_hash[:16] + "...",
        }


def generate_seed_phrase(language: str = "english") -> str:
    """
    Generate a 24-word BIP-39 mnemonic from the OS CSPRNG.

    `secrets.token_bytes` reads the kernel entropy pool. We do not accept
    user-supplied entropy, dice rolls or "improved" mixing: every historical
    wallet-seed catastrophe traces back to somebody's clever entropy source.
    """
    mnemo = Mnemonic(language)
    entropy = secrets.token_bytes(_SEED_STRENGTH_BITS // 8)
    phrase = mnemo.to_mnemonic(entropy)

    # Verify the checksum we just produced. A seed that fails its own checksum
    # is unrecoverable by every standard tool, and the failure would only be
    # discovered during a restore.
    if not mnemo.check(phrase):
        raise RuntimeError(
            "generated mnemonic failed its own BIP-39 checksum — refusing to continue"
        )
    if len(phrase.split()) != 24:
        raise RuntimeError(f"expected 24 words, produced {len(phrase.split())}")
    return phrase


def generate_root_passphrase(length: int = _PASSPHRASE_LENGTH) -> str:
    """
    Generate the typed-by-a-human root passphrase.

    Rejection sampling guarantees at least one character from each class, so the
    result cannot land on an all-lowercase string that a policy checker
    downstream rejects after the disk is already encrypted with it.
    """
    if length < 16:
        raise ValueError("root passphrase must be at least 16 characters")

    lower = set("abcdefghijkmnopqrstuvwxyz")
    upper = set("ABCDEFGHJKLMNPQRSTUVWXYZ")
    digit = set("23456789")
    symbol = set("-_.+=")

    for _ in range(1000):
        candidate = "".join(secrets.choice(_PASSPHRASE_ALPHABET) for _ in range(length))
        chars = set(candidate)
        if chars & lower and chars & upper and chars & digit and chars & symbol:
            return candidate

    raise RuntimeError("could not generate a conforming passphrase in 1000 attempts")


def derive_backup_password(seed_phrase: str, *, passphrase: str = "") -> str:
    """
    Derive the restic repository password from the seed phrase.

    BIP-39 -> 64-byte seed (PBKDF2-HMAC-SHA512, 2048 rounds, per the standard),
    then HKDF-SHA256 with a versioned info string to a repository password.

    The version string is load-bearing: if the derivation ever changes, v1
    repositories must remain openable. Changing it silently would orphan every
    existing backup.
    """
    normalised = unicodedata.normalize("NFKD", seed_phrase.strip())
    mnemo = Mnemonic("english")
    if not mnemo.check(normalised):
        raise ValueError("seed phrase failed BIP-39 checksum validation")

    bip39_seed = Mnemonic.to_seed(normalised, passphrase=passphrase)
    derived = _hkdf_sha256(
        ikm=bip39_seed,
        salt=b"sambuca-backup-salt-v1",
        info=b"sambuca/restic/repository-password/v1",
        length=32,
    )
    # Hex rather than base64: restic passwords travel through shell files,
    # systemd units and the occasional copy-paste, and '/' or '+' in any of
    # those is a bug waiting to happen.
    return derived.hex()


def seed_fingerprint(seed_phrase: str) -> str:
    """Short non-secret identifier, printed on the recovery document.

    Lets an owner confirm that a given document belongs to a given machine
    without either side revealing the seed.
    """
    normalised = unicodedata.normalize("NFKD", seed_phrase.strip())
    digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    return "-".join(digest[i : i + 4] for i in range(0, 12, 4)).upper()


def seed_hash(seed_phrase: str) -> str:
    """Full SHA-256 of the seed, for on-device verification of a typed recovery."""
    normalised = unicodedata.normalize("NFKD", seed_phrase.strip())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def generate_key_material() -> KeyMaterial:
    """Produce the complete key set for one appliance."""
    phrase = generate_seed_phrase()
    return KeyMaterial(
        seed_phrase=phrase,
        root_passphrase=generate_root_passphrase(),
        backup_password=derive_backup_password(phrase),
        backup_seed_hash=seed_hash(phrase),
        fingerprint=seed_fingerprint(phrase),
    )


def _hkdf_sha256(*, ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """HKDF-SHA256 (RFC 5869). Implemented here to keep the flasher's
    dependency surface to one package — the algorithm is thirty lines and its
    test vectors are in the RFC (see tests/test_keys.py)."""
    if length > 255 * 32:
        raise ValueError("requested key material exceeds HKDF-SHA256 limits")

    prk = hmac.new(salt, ikm, hashlib.sha256).digest()

    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]
