"""Taking the key back off the installer USB — and never off anything else.

The stick carries the disk passphrase (d-i reads it from preseed.cfg during an
unattended install), the LUKS recovery key and the backup password. Nothing
removed them: `first-boot.sh` shreds /boot/sambuca/provision.json on the
INSTALLED machine, and no code in the engine writes to /cdrom at all. Owners
were told the opposite, on the console and on the printed sheet.

So this module erases them. Which makes it the most dangerous code in the
flasher, and the tests below are mostly about what it must REFUSE to touch:

  * a volume without the payload marker             — somebody's photos
  * a Sambuca stick built for a DIFFERENT appliance — still a live key
  * a payload whose fingerprint cannot be read      — unknown is not a match

The identification is by marker file, never by "is it removable". The marker is
the same path the installer itself reads, so "a volume that would install" and
"a volume that qualifies" are the same set by construction rather than by two
lists that can drift apart.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from sambuca_flasher import stick

FINGERPRINT = "a1b2c3d4"
OTHER = "99887766"


def _make_stick(root: pathlib.Path, *, fingerprint: str = FINGERPRINT,
                files=tuple(stick.SECRET_FILES), payload_text: str | None = None
                ) -> pathlib.Path:
    payload = root / stick.PAYLOAD_DIR
    payload.mkdir(parents=True, exist_ok=True)
    for name in files:
        if name == stick.MARKER:
            continue
        (payload / name).write_text(f"secret material for {name}\n",
                                    encoding="utf-8")
    if stick.MARKER in files:
        (payload / stick.MARKER).write_text(
            payload_text if payload_text is not None
            else json.dumps({"fingerprint": fingerprint, "hostname": "x"}),
            encoding="utf-8")
    return root


# ── identification ──────────────────────────────────────────────────────────

def test_a_sambuca_stick_is_recognised(tmp_path) -> None:
    root = _make_stick(tmp_path / "usb")
    found = stick.inspect(root)
    assert found is not None
    assert found.fingerprint == FINGERPRINT
    assert set(found.present) == set(stick.SECRET_FILES)


def test_an_ordinary_volume_is_not(tmp_path) -> None:
    """THE ONE THAT MATTERS MOST. Everything else is a convenience; this is
    the difference between a cleanup and a catastrophe."""
    ordinary = tmp_path / "photos"
    (ordinary / "DCIM").mkdir(parents=True)
    (ordinary / "DCIM" / "holiday.jpg").write_bytes(b"\xff\xd8\xff")
    assert stick.inspect(ordinary) is None


def test_a_volume_with_the_directory_but_no_marker_is_not_a_stick(tmp_path
                                                                  ) -> None:
    """A folder called `sambuca` is not a payload. Somebody's project
    directory, a copy of the repo, a backup of the docs — all plausible, none
    of them an installer."""
    root = tmp_path / "usb"
    (root / stick.PAYLOAD_DIR).mkdir(parents=True)
    (root / stick.PAYLOAD_DIR / "notes.txt").write_text("mine", encoding="utf-8")
    assert stick.inspect(root) is None


# ── the fingerprint filter ──────────────────────────────────────────────────

def test_a_stick_for_another_appliance_is_skipped(tmp_path) -> None:
    """Two sticks in two ports. Wiping the wrong one destroys a live key for a
    machine that is not being handed over."""
    mine = _make_stick(tmp_path / "mine")
    theirs = _make_stick(tmp_path / "theirs", fingerprint=OTHER)
    found = stick.find([mine, theirs], FINGERPRINT)
    assert [f.root for f in found] == [mine]


def test_an_unreadable_payload_is_not_treated_as_a_match(tmp_path) -> None:
    """Corrupt JSON yields an empty fingerprint. Empty must not compare equal
    to the appliance's — 'I could not tell' is not 'yes'."""
    root = _make_stick(tmp_path / "usb", payload_text="{ this is not json")
    assert stick.inspect(root).fingerprint == ""
    assert stick.find([root], FINGERPRINT) == []


def test_without_a_fingerprint_every_stick_still_qualifies(tmp_path) -> None:
    """No watch file is a normal case (provisioned from another machine). The
    filter then does nothing and the owner's confirmation is the only gate —
    which is why the caller names the drive and asks."""
    a = _make_stick(tmp_path / "a")
    b = _make_stick(tmp_path / "b", fingerprint=OTHER)
    assert len(stick.find([a, b], "")) == 2


def test_a_missing_mount_point_does_not_raise(tmp_path) -> None:
    """Volumes disappear mid-run — somebody pulls the stick while reading the
    output. An exception here would abort a successful handover."""
    assert stick.find([tmp_path / "gone", tmp_path / "also-gone"]) == []


# ── removal ─────────────────────────────────────────────────────────────────

def test_it_removes_every_secret_file(tmp_path) -> None:
    root = _make_stick(tmp_path / "usb")
    found = stick.inspect(root)
    result = stick.neutralise(found)
    assert result.ok
    assert set(result.removed) == set(stick.SECRET_FILES)
    assert stick.verify(found) == []


def test_it_leaves_everything_else_alone(tmp_path) -> None:
    """The stick stays a stick. Reformatting would need elevation, would
    destroy a medium somebody may want to reuse, and would be no more thorough
    on flash storage."""
    root = _make_stick(tmp_path / "usb")
    (root / "holiday.jpg").write_bytes(b"\xff\xd8\xff")
    (root / stick.PAYLOAD_DIR / "engine").mkdir()
    (root / stick.PAYLOAD_DIR / "late-command.sh").write_text("#!/bin/sh\n",
                                                             encoding="utf-8")
    stick.neutralise(stick.inspect(root))
    assert (root / "holiday.jpg").exists()
    assert (root / stick.PAYLOAD_DIR / "late-command.sh").exists()
    assert (root / stick.PAYLOAD_DIR / "engine").is_dir()


def test_the_bytes_are_overwritten_before_the_unlink(tmp_path) -> None:
    """Not a guarantee on flash, and the caller says so out loud — but it
    defeats undelete, which is the realistic threat for a stick in a drawer."""
    root = _make_stick(tmp_path / "usb")
    target = root / stick.PAYLOAD_DIR / "luks-recovery.key"
    original = target.read_bytes()
    seen = {}

    real_open = open

    def watching(path, *a, **kw):                       # noqa: ANN001
        fh = real_open(path, *a, **kw)
        if pathlib.Path(path) == target and "r+b" in a:
            seen["opened"] = True
        return fh

    import builtins
    builtins.open = watching
    try:
        stick.neutralise(stick.inspect(root))
    finally:
        builtins.open = real_open

    assert seen.get("opened"), "the file was unlinked without being overwritten"
    assert not target.exists()
    assert original  # the fixture really did write something to overwrite


def test_a_partial_removal_is_reported_not_swallowed(tmp_path, monkeypatch
                                                     ) -> None:
    """A delete that fails and reports success would leave the owner believing
    a live key is dead — the exact silent-success shape this project keeps
    finding, at its worst possible site."""
    root = _make_stick(tmp_path / "usb")
    found = stick.inspect(root)
    real_unlink = pathlib.Path.unlink

    def stubborn(self, *a, **kw):                       # noqa: ANN001
        if self.name == "preseed.cfg":
            raise OSError("read-only file system")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "unlink", stubborn)
    result = stick.neutralise(found)
    assert not result.ok
    assert [n for n, _ in result.failed] == ["preseed.cfg"]
    assert stick.verify(found) == ["preseed.cfg"]


def test_verify_is_independent_of_what_neutralise_claimed(tmp_path) -> None:
    """verify() re-reads the filesystem rather than trusting the report. If
    the two ever disagree, the filesystem wins."""
    root = _make_stick(tmp_path / "usb")
    found = stick.inspect(root)
    stick.neutralise(found)
    (found.payload / "preseed.cfg").write_text("came back", encoding="utf-8")
    assert stick.verify(found) == ["preseed.cfg"]


def test_removing_twice_is_harmless(tmp_path) -> None:
    """Somebody runs handover again. It must not error on the second pass."""
    root = _make_stick(tmp_path / "usb")
    found = stick.inspect(root)
    stick.neutralise(found)
    again = stick.neutralise(found)
    assert again.ok and again.removed == []


# ── the list itself ─────────────────────────────────────────────────────────

def test_every_secret_file_the_flasher_stages_is_in_the_list() -> None:
    """THE ENUMERATION TRAP, pre-empted. A future secret staged onto the stick
    and not added here would be left behind in silence — this project's most
    repeated failure shape (chmod globs, shellcheck find, the bundle matrix).

    Derived from cli.py's staging code, so adding a fifth file without adding
    it here fails HERE rather than on somebody's drawer in two years.

    The first version of this test matched only `*.key`, which passed while
    claiming to catch "any future secret" — a file staged as `token.pem` would
    have sailed through. It now takes EVERY explicitly-named staged file and
    demands each one be classified, because the question is not "does it end
    in .key" but "does anyone know whether this is a secret".
    """
    import re
    src = (pathlib.Path(__file__).resolve().parents[3]
           / "apps/flasher/src/sambuca_flasher/cli.py").read_text(
        encoding="utf-8")
    staged = set(re.findall(r'\(staging / "([^"]+)"\)', src))
    assert staged, "the staging pattern no longer matches; this test is vacuous"

    # Staged files that are deliberately NOT secrets. Empty today: all four
    # named files carry secret material. A new entry here is a claim somebody
    # has to make on purpose, which is the point.
    HARMLESS: set[str] = set()

    unclassified = staged - set(stick.SECRET_FILES) - HARMLESS
    assert not unclassified, (
        f"cli.py stages {sorted(unclassified)} onto the USB and stick.py "
        f"neither removes it nor declares it harmless — classify it")


def test_each_named_file_has_a_plain_english_explanation() -> None:
    """The owner is told what is on the stick, by name. "4 files" is not a
    reason to agree to anything."""
    for name, why in stick.SECRET_FILES.items():
        assert why and why[0].islower() and len(why) > 10, name


@pytest.mark.parametrize("name", sorted(stick.SECRET_FILES))
def test_the_marker_and_the_secrets_agree_with_the_installer(name) -> None:
    """These names are read by shell in three places. A rename on one side
    only would leave the file behind (or the installer unable to find it)."""
    engine = pathlib.Path(__file__).resolve().parents[3] / "engine"
    hits = [p for p in engine.rglob("*")
            if p.is_file() and p.suffix in (".sh", ".cfg")
            and name in p.read_text(encoding="utf-8", errors="ignore")]
    assert hits or name == "preseed.cfg", (
        f"{name} is removed from the stick but no installer script mentions "
        f"it; either it is obsolete or the name has drifted")
