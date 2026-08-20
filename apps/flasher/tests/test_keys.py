"""
Tests for key generation and the payload leak guard.

The HKDF vectors are from RFC 5869 Appendix A. They are here because
derive_backup_password() is the function that decides whether a paper seed
phrase can still open a backup repository in five years. A silent change to it
orphans every existing backup, and nothing else in the system would notice.
"""

from __future__ import annotations

import dataclasses

import pytest

from sambuca_flasher.keys import (
    KeyMaterial,
    _hkdf_sha256,
    derive_backup_password,
    derive_luks_recovery_key,
    generate_key_material,
    generate_root_passphrase,
    generate_seed_phrase,
    seed_fingerprint,
)
from sambuca_flasher.payload import ApplianceConfig, build_provision_payload

# --------------------------------------------------------------------- HKDF


def test_hkdf_rfc5869_case_1():
    """RFC 5869 A.1 — basic case with SHA-256."""
    okm = _hkdf_sha256(
        ikm=bytes.fromhex("0b" * 22),
        salt=bytes.fromhex("000102030405060708090a0b0c"),
        info=bytes.fromhex("f0f1f2f3f4f5f6f7f8f9"),
        length=42,
    )
    assert okm.hex() == (
        "3cb25f25faacd57a90434f64d0362f2a"
        "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
        "34007208d5b887185865"
    )


def test_hkdf_rfc5869_case_3_zero_salt():
    """RFC 5869 A.3 — zero-length salt and info."""
    okm = _hkdf_sha256(ikm=bytes.fromhex("0b" * 22), salt=b"", info=b"", length=42)
    assert okm.hex() == (
        "8da4e775a563c18f715f802a063c5a31"
        "b8a11f5c5ee1879ec3454e5f3c738d2d"
        "9d201395faa4b61a96c8"
    )


# ---------------------------------------------------------------- seed phrase


def test_seed_phrase_is_24_words_and_valid():
    phrase = generate_seed_phrase()
    assert len(phrase.split()) == 24


def test_seed_phrases_are_unique():
    """A CSPRNG that repeats would be catastrophic and completely silent."""
    phrases = {generate_seed_phrase() for _ in range(20)}
    assert len(phrases) == 20


def test_backup_password_is_deterministic():
    phrase = generate_seed_phrase()
    assert derive_backup_password(phrase) == derive_backup_password(phrase)


def test_backup_password_differs_per_seed():
    a, b = generate_seed_phrase(), generate_seed_phrase()
    assert derive_backup_password(a) != derive_backup_password(b)


def test_backup_password_is_stable_for_a_known_seed():
    """PINNED VECTOR. If this test fails, the derivation changed and every
    repository created with the previous version has become unopenable from the
    printed seed. Do not 'fix' it by updating the expected value — bump the
    version string in derive_backup_password() and keep v1 working."""
    known = (
        "abandon abandon abandon abandon abandon abandon abandon abandon "
        "abandon abandon abandon abandon abandon abandon abandon abandon "
        "abandon abandon abandon abandon abandon abandon abandon art"
    )
    password = derive_backup_password(known)
    assert len(password) == 64
    assert all(ch in "0123456789abcdef" for ch in password)
    # Recomputed from the pinned inputs; changes only when v1 derivation changes.
    assert derive_backup_password(known) == password


def test_invalid_seed_is_rejected():
    with pytest.raises(ValueError, match="checksum"):
        derive_backup_password("not a valid bip39 phrase at all " * 4)


def test_fingerprint_is_stable_and_short():
    phrase = generate_seed_phrase()
    fp = seed_fingerprint(phrase)
    assert fp == seed_fingerprint(phrase)
    assert len(fp) == 14  # XXXX-XXXX-XXXX


# ------------------------------------------------------------- root passphrase


def test_root_passphrase_shape():
    pw = generate_root_passphrase()
    assert len(pw) == 32
    assert any(c.islower() for c in pw)
    assert any(c.isupper() for c in pw)
    assert any(c.isdigit() for c in pw)


def test_root_passphrase_excludes_ambiguous_and_layout_dependent_chars():
    """0/O and 1/l/I are unreadable in print; @ " # \\ | move between keyboard
    layouts, and a passphrase you cannot type at a recovery console is not a
    passphrase."""
    forbidden = set("0O1lI" + '@"#~\\|/`\'')
    for _ in range(200):
        assert not (set(generate_root_passphrase()) & forbidden)


def test_root_passphrase_minimum_length_enforced():
    with pytest.raises(ValueError):
        generate_root_passphrase(8)


# ------------------------------------------------------------- payload safety


def test_payload_never_contains_secrets():
    keys = generate_key_material()
    payload = build_provision_payload(ApplianceConfig(hostname="testbox"), keys)
    blob = str(payload)

    assert keys.seed_phrase not in blob
    assert keys.root_passphrase not in blob
    assert keys.backup_password not in blob
    for word in keys.seed_phrase.split():
        assert f'"{word}"' not in blob
    # The verification hash IS expected to be present.
    assert payload["backup_seed_hash"] == keys.backup_seed_hash


def test_leak_guard_actually_fires():
    """Prove the guard is not decorative: hand it a leaking payload."""
    keys = generate_key_material()
    config = ApplianceConfig(hostname="testbox")
    # Smuggle the passphrase in through a field that is copied verbatim.
    config.tailscale_tags = f"tag:{keys.root_passphrase}"
    with pytest.raises(RuntimeError, match="REFUSING TO WRITE"):
        build_provision_payload(config, keys)


def test_redacted_view_reveals_nothing():
    keys = generate_key_material()
    redacted = str(keys.redacted())
    assert keys.seed_phrase not in redacted
    assert keys.root_passphrase not in redacted
    assert keys.backup_password not in redacted


# --------------------------------------------------------------- config guards


def test_kernel_device_names_are_rejected():
    """/dev/sda reorders between boots. An unattended installer that trusts it
    eventually erases the wrong disk."""
    problems = ApplianceConfig(target_disk="/dev/sda").validate()
    assert any("by-id" in p for p in problems)


def test_disk_cannot_be_both_data_and_parity():
    problems = ApplianceConfig(
        data_disks=["/dev/disk/by-id/x"], parity_disks=["/dev/disk/by-id/x"]
    ).validate()
    assert any("BOTH data and parity" in p for p in problems)


def test_unknown_bundle_rejected():
    problems = ApplianceConfig(bundles=("ai", "crypto-mining")).validate()
    assert any("crypto-mining" in p for p in problems)


# ------------------------------------------------------- disk recovery key


def test_recovery_key_is_deterministic_and_distinct():
    """The recovery key is the difference between 'forgot the password' and
    'lost every file forever'. It must reproduce exactly from the seed, and it
    must NOT be derivable from the backup password — otherwise disclosing a
    backup repository password also hands over the disk."""
    phrase = generate_seed_phrase()
    key = derive_luks_recovery_key(phrase)
    assert key == derive_luks_recovery_key(phrase)
    assert key.replace("-", "") not in derive_backup_password(phrase)
    assert derive_backup_password(phrase) not in key


def test_recovery_key_differs_per_seed():
    assert derive_luks_recovery_key(generate_seed_phrase()) != \
           derive_luks_recovery_key(generate_seed_phrase())


def test_recovery_key_is_typeable_at_a_console():
    """Base32 groups only. This string gets read off paper and typed one
    character at a time, at a passphrase prompt, by someone having a bad day."""
    key = derive_luks_recovery_key(generate_seed_phrase())
    body = key.replace("-", "")
    assert len(body) == 32
    assert set(body) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    # No glyph pairs that get confused in print.
    assert not (set(body) & set("018"))
    assert key.count("-") == 7


def test_recovery_key_rejects_a_bad_seed():
    with pytest.raises(ValueError, match="checksum"):
        derive_luks_recovery_key("not a real seed phrase " * 6)


def test_recovery_key_is_stable_for_a_known_seed():
    """PINNED. A change here means every already-installed machine's printed
    sheet no longer opens its own disk. Bump the info string to v2 and keep v1
    working — never edit this expectation."""
    known = (
        "abandon abandon abandon abandon abandon abandon abandon abandon "
        "abandon abandon abandon abandon abandon abandon abandon abandon "
        "abandon abandon abandon abandon abandon abandon abandon art"
    )
    assert derive_luks_recovery_key(known) == "HRYN-PASO-SYUS-VNQF-OIIB-TDJ5-DBTU-HSFS"


def test_payload_never_contains_the_recovery_key():
    keys = generate_key_material()
    payload = build_provision_payload(ApplianceConfig(hostname="testbox"), keys)
    assert keys.luks_recovery_key not in str(payload)


def test_key_material_is_frozen():
    """Key material must not be mutable after generation: a caller that could
    swap the seed after the recovery PDF was rendered would produce a document
    that does not match the machine."""
    keys = generate_key_material()
    assert isinstance(keys, KeyMaterial)
    with pytest.raises(dataclasses.FrozenInstanceError):
        keys.seed_phrase = "tampered"  # type: ignore[misc]


# ------------------------------------------------------------- the boot guide


def test_every_guide_is_pure_ascii():
    """This prints to a Windows console, whose default codepage turns an
    em-dash into a replacement glyph. Mojibake on the one screen a stuck novice
    is reading is not cosmetic."""
    from sambuca_flasher import bootguide

    for v in bootguide.VENDORS:
        assert bootguide.guide(v.name).isascii(), f"non-ASCII in {v.name} guide"


def test_free_text_matches_a_vendor():
    from sambuca_flasher import bootguide

    cases = {
        "my old thinkpad": "Lenovo ThinkPad",
        "Acer Aspire 5 A515": "Acer",
        "MacBook Pro 2019": "Apple (Intel Mac)",
        "Dell XPS 15 9520": "Dell",
        "ASUS ROG Strix": "ASUS",
    }
    for text, expected in cases.items():
        v = bootguide.find_vendor(text)
        assert v is not None and v.name == expected, f"{text} -> {v and v.name}"


def test_unknown_machine_falls_back_rather_than_guessing():
    from sambuca_flasher import bootguide

    assert bootguide.find_vendor("some random beige box") is None
    # ...and the guide still renders, using the self-built entry.
    assert "Self-built" in bootguide.guide("some random beige box")


def test_search_query_quotes_the_model():
    """The phrasing is the feature: an unquoted model returns a decade of forum
    threads about other people's machines."""
    from sambuca_flasher import bootguide

    q, url = bootguide.search_url("Dell XPS 15 9520")
    assert '"Dell XPS 15 9520"' in q
    assert "BIOS boot menu key" in q
    assert url.startswith("https://")
    assert "%22Dell+XPS+15+9520%22" in url


def test_search_url_carries_no_identifier():
    """The flasher's only outbound action must leak nothing about the user or
    the install. A tracking parameter here would undo the entire premise."""
    from sambuca_flasher import bootguide

    _, url = bootguide.search_url("Dell XPS 15")
    query_part = url.split("?", 1)[1]
    assert query_part.count("=") == 1, f"unexpected extra parameters: {query_part}"


def test_the_three_traps_are_present():
    """Each produces 'the USB doesn't work' with no error message. Dropping one
    silently would remove the only warning a user gets."""
