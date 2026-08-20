"""The CLI must work without the dependencies its commands do not use.

REGRESSION. `keys.py` raised SystemExit at IMPORT time when `mnemonic` was
absent, and `recovery_pdf.py` did the same for `reportlab`. Because `cli.py`
imported both at module level, EVERY command died — `list`, `estimate`,
`boot-guide`, `example-config`, and even `--version`.

That contradicted the README, which tells a first-time reader to run `estimate`
before committing anything: "asks your computer nothing, touches no disk, needs
no USB stick". On a source install it exited demanding a seed-phrase library.

It survived because the frozen binaries bundle both libraries, so the failure
only appeared on a source checkout. Found by running `sambuca-flasher list`
against a real card reader.

These tests hide the libraries and drive the CLI, so the import graph cannot
quietly regrow the dependency.
"""

from __future__ import annotations

import builtins
import sys

import pytest

# Commands that must run with neither optional library present.
FREE_COMMANDS = [
    ["--version"],
    ["list"],
    ["estimate", "an old Dell desktop, 16GB RAM"],
    ["boot-guide", "--list-vendors"],
    ["example-config"],
]

# Commands that legitimately need BIP-39 and must refuse CLEARLY, not crash
# with an ImportError traceback.
BIP39_COMMANDS = [
    ["derive-backup-key"],
    ["derive-recovery-key"],
]

HIDDEN = ("mnemonic", "reportlab")


@pytest.fixture
def without_optional_deps(monkeypatch):
    """Make `import mnemonic` / `import reportlab` fail, as on a bare install."""
    for name in list(sys.modules):
        if name.startswith(("sambuca_flasher", *HIDDEN)):
            monkeypatch.delitem(sys.modules, name, raising=False)

    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in HIDDEN:
            raise ImportError(f"hidden for test: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    yield


def test_cli_imports_without_optional_deps(without_optional_deps):
    """The module graph itself must not require them."""
    from sambuca_flasher import cli  # noqa: F401


@pytest.mark.parametrize("argv", FREE_COMMANDS, ids=lambda a: a[0])
def test_command_runs_without_optional_deps(without_optional_deps, argv, capsys):
    from sambuca_flasher import cli

    try:
        rc = cli.main(argv)
    except SystemExit as exc:
        # --version exits 0 through argparse, which is fine. Any OTHER
        # SystemExit here is the bug coming back.
        rc = exc.code or 0
        out = capsys.readouterr()
        combined = out.out + out.err
        for lib in HIDDEN:
            assert lib not in combined, (
                f"`{' '.join(argv)}` demanded {lib!r}, which it does not use. "
                f"Something re-added a module-level import."
            )

    # `list` returns 1 when no removable device is attached, which is not a
    # failure of this test — only the dependency behaviour is under test.
    assert rc in (0, 1)


@pytest.mark.parametrize("argv", BIP39_COMMANDS, ids=lambda a: a[0])
def test_bip39_commands_refuse_clearly(without_optional_deps, argv, capsys):
    """The refusal must survive — moved, not deleted."""
    from sambuca_flasher import cli

    with pytest.raises(SystemExit) as exc:
        cli.main(argv)

    message = str(exc.value)
    assert "mnemonic" in message, (
        "the BIP-39 guard must still name the missing package and how to "
        "install it — a bare ImportError traceback is not an acceptable "
        "failure mode for a tool that generates recovery secrets"
    )
    assert "pip install" in message


class TestDoubleClickLaunch:
    """A double-clicked console app must not vanish.

    REGRESSION. With no arguments argparse printed a usage line and exited 2.
    Launched from a file manager the console window is created for the process
    and destroyed the instant it exits, so the window appeared and disappeared
    inside a frame — reported, fairly, as "it crashed as soon as I clicked it".

    That is the first thing a non-technical owner does with a downloaded .exe,
    and the README tells them to download it and run it.
    """

    def test_menu_exists_and_names_the_safe_option_first(self):
        from sambuca_flasher import cli

        assert hasattr(cli, "_interactive")
        assert "double-clicking" in cli._MENU
        # The reassurance a nervous first-time reader needs, before the
        # options that touch a disk.
        assert "touches a disk until it asks you first" in cli._MENU

    def test_detection_helpers_are_importable_and_safe(self):
        """They must never raise — they run before anything else."""
        from sambuca_flasher.console import launched_by_double_click, pause_before_exit

        assert isinstance(launched_by_double_click(), bool)
        # A no-op when not double-clicked; must not block a test run.
        pause_before_exit()

    def test_explicit_argv_never_triggers_the_menu(self, capsys):
        """Passing argv means a caller drove this deliberately."""
        from sambuca_flasher import cli

        try:
            cli.main(["boot-guide", "--list-vendors"])
        except SystemExit:
            pass
        assert "double-clicking" not in capsys.readouterr().out
