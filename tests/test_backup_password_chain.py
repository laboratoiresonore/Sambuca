"""The backup password the FLASHER derives must be the one the APPLIANCE uses.

It was not. This file exists because of the worst bug found in this project so
far, and the shape of it is worth stating plainly because it will recur:

  keys.py       derives a restic repository password from the seed phrase, and
                documents that changing the derivation "would orphan every
                existing backup"
  cli.py        offers `derive-backup-key`: "Recover the backup repository
                password from a 24-word seed phrase", then prints it and says
                "Use it with: restic -r <repository> snapshots"
  backup.sh     found no password file on the appliance and generated a RANDOM
                48-character one

Three correct-looking programs, one missing delivery step between them. The
archive was encrypted with a secret that existed in exactly one place — the
disk being backed up — so losing the machine, which is the entire event backups
exist for, lost the backups too. Meanwhile the recovery tool confidently
printed a password that opened nothing, on the day somebody needed it most.

Nothing failed. Nothing logged. Every test passed. It was found by asking what
`derive-backup-key` promises and then reading the other end of the promise.

THE GENERAL RULE, which has now cost this project twice: **wherever two
programs must agree about a string, and only one of them is tested, the
agreement is an assumption.** The disk recovery key had the same shape and got
the same treatment in test_recovery_key_chain.py.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps/flasher/src"))

from sambuca_flasher import keys  # noqa: E402

CLI = REPO / "apps/flasher/src/sambuca_flasher/cli.py"
LATE = REPO / "engine/autoinstall/late-command.sh"
BACKUP = REPO / "engine/maintenance/backup.sh"
COMMON = REPO / "engine/lib/common.sh"
PAYLOAD = REPO / "apps/flasher/src/sambuca_flasher/payload.py"

STICK_NAME = "restic-password.key"
PHRASE = ("zebra " * 24).strip()


def _password_path_the_appliance_reads() -> str:
    """Resolve backup.sh's PW_FILE the way the shell would.

    Derived rather than hard-coded: a test that repeats the literal path passes
    happily on the day somebody moves it, which is precisely the class of
    silent disagreement this file exists to catch.
    """
    pw = re.search(r'^PW_FILE="([^"]+)"', BACKUP.read_text(encoding="utf-8"),
                   re.M)
    assert pw, "backup.sh no longer defines PW_FILE in the expected shape"
    etc = re.search(r'^SB_ETC="\$\{SB_ETC:-([^}]+)\}"',
                    COMMON.read_text(encoding="utf-8"), re.M)
    assert etc, "common.sh no longer defines SB_ETC in the expected shape"
    return pw.group(1).replace("${SB_ETC}", etc.group(1))


# ── the delivery step that was missing ──────────────────────────────────────

def test_the_flasher_stages_the_password_on_the_stick() -> None:
    """Without this write, every assertion below is about a file nobody makes."""
    src = CLI.read_text(encoding="utf-8")
    assert STICK_NAME in src, (
        f"cli.py no longer stages {STICK_NAME}; the appliance will fall back "
        f"to a generated password and derive-backup-key becomes false again")
    assert "keys.backup_password.encode" in src, (
        "the staged file is no longer the derived password")


def test_the_installer_installs_it_where_backup_sh_looks() -> None:
    """THE INVARIANT. Two files, two languages, one path — and until now, no
    check that they named the same one."""
    installed_to = _password_path_the_appliance_reads()
    late = LATE.read_text(encoding="utf-8")
    assert installed_to in late, (
        f"late-command.sh does not write {installed_to}, which is where "
        f"backup.sh reads the repository password from")
    assert STICK_NAME in late, (
        f"late-command.sh does not read {STICK_NAME} from the medium")


def test_backup_sh_prefers_the_installed_password_over_generating_one() -> None:
    """The fix depends on backup.sh not clobbering what the installer put
    there. It already behaved this way — but nothing said so, and a later
    `restic key` rework could quietly reverse it."""
    src = BACKUP.read_text(encoding="utf-8")
    guard = src.index("if [[ -s $PW_FILE ]]")
    generate = src.index("sb_secret 48")
    assert guard < generate, (
        "backup.sh generates a password before checking for an installed one")


# ── the string must survive the trip ────────────────────────────────────────

def test_the_derived_password_survives_the_installer_normalisation() -> None:
    """late-command.sh strips \\r \\n space tab, exactly as the LUKS key path
    does. restic uses the file's ENTIRE contents, so a surviving newline is a
    different password — the same trap, one directory over."""
    assert "tr -d '\\r\\n \\t'" in LATE.read_text(encoding="utf-8"), (
        "late-command.sh no longer normalises the password file the way this "
        "test models; the assertion below is now about something that does "
        "not happen")
    password = keys.derive_backup_password(PHRASE)
    stripped = "".join(c for c in password if c not in "\r\n \t")
    assert stripped == password, (
        "the derived password contains whitespace the installer removes, so "
        "the appliance would use a different string from the one printed")


def test_the_password_is_deterministic_and_distinct() -> None:
    """Recovery happens months later on a different machine. And it must not
    be the disk key: disclosing a backup password would otherwise hand over
    the disk as well."""
    assert keys.derive_backup_password(PHRASE) == \
        keys.derive_backup_password(PHRASE)
    assert keys.derive_backup_password(PHRASE) != \
        keys.derive_luks_recovery_key(PHRASE)


# ── the guard this fix must not have weakened ───────────────────────────────

def test_the_password_still_never_enters_provision_json() -> None:
    """payload.py REFUSES to write a payload containing this value, and that
    refusal is correct: provision.json sits unencrypted on the boot partition
    until first boot. The fix routes around the guard — a separate file that
    lands on the encrypted root — rather than through it.

    The tempting shortcut was to add the password to the payload and delete
    this check. That would have solved the delivery problem by creating a
    worse one.
    """
    src = PAYLOAD.read_text(encoding="utf-8")
    assert "backup_password" in src, (
        "payload.py no longer guards against leaking the backup password into "
        "provision.json")


def test_the_installer_writes_it_to_the_encrypted_root_not_the_boot_partition(
) -> None:
    """/boot/sambuca is unencrypted and is where provision.json goes. The
    password must not follow it there, even for one boot."""
    late = LATE.read_text(encoding="utf-8")
    line = next(ln for ln in late.splitlines()
                if "restic_password" in ln and ">" in ln)
    assert "/boot" not in line, (
        f"the backup password is written to the unencrypted boot partition: "
        f"{line.strip()}")


# ── and the promise itself ──────────────────────────────────────────────────

def test_derive_backup_key_states_the_case_where_it_does_not_apply() -> None:
    """An interactive install stages no secret, so the appliance generates its
    own and this command's output opens nothing. A recovery tool that is right
    most of the time, silently, is worse than one that says which time it is."""
    src = CLI.read_text(encoding="utf-8")
    derive = src[src.index("def _cmd_derive()"):src.index("def _cmd_derive_recovery")]
    assert "interactive" in derive.lower(), (
        "derive-backup-key prints a password unconditionally; it must say that "
        "an interactive install generates its own instead")


def test_the_appliance_says_so_too_when_it_generates_one() -> None:
    """The other end of the same fork. Whoever installs interactively is told
    at generation time that their 24 words will not recover this repository —
    while they can still do something about it."""
    src = BACKUP.read_text(encoding="utf-8")
    block = src[src.index("sb_secret 48"):src.index("sb_secret 48") + 900]
    assert "seed" in block.lower() and "NOT" in block, (
        "backup.sh generates a password without warning that the seed phrase "
        "will not recover it")
