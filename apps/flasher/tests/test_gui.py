"""The window's decisions, tested without a window.

THE POINT OF SPLITTING plan() OUT is that this file can exist. Widget code
needs a display and a running event loop; which screen comes next, and what it
says, is ordinary data. Every real bug in this project was found by executing
logic, and logic buried in a button callback cannot be executed without a
screen — so it never is.

THE SAFETY PROPERTY THESE GUARD is not cosmetic. tkinter is stdlib on paper and
genuinely absent in practice: Debian splits it into python3-tk, and a frozen
binary only has it if the build bundled Tcl/Tk. A module-level import would
take the WHOLE application down at startup — `list`, `verify-sheet`,
`open-vault`, all of it — for want of a window nobody asked for.

That exact failure already happened here once, with keys.py: it raised
SystemExit at import time when the BIP-39 package was missing, so `--version`
died demanding a seed-phrase library.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from sambuca_flasher import gui  # noqa: E402


class TestImportingIsAlwaysSafe:
    def test_the_module_imports_with_no_toolkit(self, monkeypatch):
        """The whole reason for the lazy import.

        Simulated by making the import fail, because uninstalling tkinter to
        prove a point is not a test anyone can re-run.
        """
        import builtins
        real = builtins.__import__

        def no_tk(name, *a, **k):
            if name == "tkinter":
                raise ImportError("No module named 'tkinter'")
            return real(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", no_tk)
        ok, why = gui.available()
        assert ok is False
        assert why, "a refusal must say why, or the fallback looks like a bug"

    def test_no_toolkit_import_happens_at_module_scope(self):
        """Read the source rather than trusting that it stayed that way.

        A future edit adding `import tkinter` at the top would restore exactly
        the failure this design exists to prevent, and nothing else here would
        notice — the tests would still pass on a machine that has it.
        """
        src = pathlib.Path(gui.__file__).read_text(encoding="utf-8")
        body = src.split('"""', 2)[-1]          # skip the module docstring
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped.startswith(("import tkinter", "from tkinter")):
                assert line.startswith((" ", "\t")), (
                    "tkinter must be imported INSIDE a function; at module "
                    "scope it kills every command in the application")


class TestThePlan:
    def test_reachability_comes_before_anything_is_written(self):
        """An appliance nobody can find is not an appliance, and finding that
        out after the card is written is the worst possible moment."""
        steps = gui.plan(has_tailscale=False, has_imager=True)
        keys = [s.key for s in steps]
        assert keys.index("reachability") < keys.index("write")

    def test_the_imager_step_appears_only_when_it_is_missing(self):
        with_it = [s.key for s in gui.plan(has_tailscale=True, has_imager=True)]
        without = [s.key for s in gui.plan(has_tailscale=True, has_imager=False)]
        assert "imager" not in with_it
        assert "imager" in without

    def test_an_installed_tailscale_is_not_offered_again(self):
        """Offering to install something already installed reads as a program
        that has not looked."""
        steps = {s.key: s for s in gui.plan(has_tailscale=True, has_imager=True)}
        assert "Install" not in steps["reachability"].continue_label

    def test_the_destructive_step_says_so_in_capitals(self):
        """The one choice that must stay human gets MORE words, not fewer."""
        steps = {s.key: s for s in gui.plan(has_tailscale=True, has_imager=True)}
        assert "ERASED" in steps["write"].body

    def test_the_recovery_step_appears_only_where_a_document_exists(self):
        """A SCREEN THAT PROMISES PAPER THAT NEVER APPEARS IS WORSE THAN NONE.

        The Pi flow generates no key material, writes no PDF and offers no
        vault — the appliance has no encrypted root to be locked out of. The
        x86 installer does all three. The first draft showed "print it now,
        nobody can recover it for you" on both, which was a plain lie about
        one of them.
        """
        pi = {s.key for s in gui.plan(has_tailscale=True, has_imager=True)}
        assert "recovery" not in pi

        x86 = {s.key: s for s in gui.plan(has_tailscale=True, has_imager=True,
                                          makes_recovery_document=True)}
        assert "recovery" in x86
        assert "nobody can recover it" in x86["recovery"].body.lower()

    def test_the_flow_ends_by_pointing_forward_not_by_stopping(self):
        """The failure this project keeps fixing: a flow that ends in silence.
        The last screen must hand over to something."""
        steps = gui.plan(has_tailscale=True, has_imager=True)
        assert steps[-1].key == "done"
        assert "watch" in steps[-1].continue_label.lower()

    @pytest.mark.parametrize("ts,im", [(True, True), (True, False),
                                       (False, True), (False, False)])
    def test_every_combination_produces_a_walkable_flow(self, ts, im):
        """No dead ends: every step but the last has a successor, and no key
        repeats (which would make next_key loop)."""
        steps = gui.plan(has_tailscale=ts, has_imager=im)
        keys = [s.key for s in steps]
        assert len(keys) == len(set(keys)), f"duplicate steps: {keys}"
        for s in steps[:-1]:
            assert gui.next_key(steps, s.key) is not None
        assert gui.next_key(steps, steps[-1].key) is None

    def test_no_step_asks_the_owner_for_a_secret(self):
        """Sambuca must never collect the wifi key or the disk password — the
        Imager's own screen does, which is the whole reason the registry
        pre-fill carries an allowlist.

        MATCHES THE ASKING, NOT THE WORD. The first version flagged the welcome
        screen for saying the appliance holds "files, photos and passwords" —
        which names a password MANAGER, not a secret being demanded. A check
        that cannot tell a service from a credential would push the copy into
        contortions to stay green.
        """
        import re
        asking = re.compile(
            r"\b(enter|type|give|provide|supply)\b[^.]{0,40}\b"
            r"(your )?(password|passphrase|wi-?fi key|secret)\b", re.I)
        for ts in (True, False):
            for s in gui.plan(has_tailscale=ts, has_imager=True):
                text = f"{s.title} {s.body}"
                for m in asking.finditer(text):
                    around = text[max(0, m.start() - 90):m.end() + 90].lower()
                    assert "into the imager" in around or "never sees" in around, (
                        f"step {s.key} appears to ask for a secret: "
                        f"{m.group(0)!r}")


class TestTheWizard:
    """The widgets, driven for real.

    A withdrawn Tk window needs no visible display, so this constructs the
    actual wizard and clicks its actual buttons — not a mock of them. On a
    machine without a toolkit the whole class skips rather than failing, which
    is the same honesty the `window` command shows its owner.
    """

    @pytest.fixture
    def wiz(self):
        ok, why = gui.available()
        if not ok:
            pytest.skip(f"no toolkit here: {why}")
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        w = gui.Wizard(gui.plan(has_tailscale=True, has_imager=True), root=root)
        try:
            yield w
        finally:
            w.close()

    def test_it_starts_on_the_first_screen_with_no_way_back(self, wiz):
        """A Back button on screen one can only disappoint.

        str() around cget is not decoration: ttk returns a Tcl_Obj, and
        `== "disabled"` is False against it while str() comparison is True.
        The first version of this test failed while the widget was correct.
        """
        assert wiz.current.key == "welcome"
        assert str(wiz.back_btn.cget("state")) == "disabled"
        assert "1 of" in wiz.progress.cget("text")

    def test_the_button_says_what_this_step_does(self, wiz):
        """"Continue" everywhere tells somebody nothing about what happens
        next; "Open the Imager" does."""
        assert str(wiz.next_btn.cget("text")) == wiz.current.continue_label

    def test_clicking_forward_moves_on_and_enables_back(self, wiz):
        wiz.forward()
        assert wiz.current.key != "welcome"
        assert str(wiz.back_btn.cget("state")) == "normal"

    def test_back_returns_to_the_previous_screen(self, wiz):
        wiz.forward()
        here = wiz.current.key
        wiz.back()
        assert wiz.current.key != here
        assert wiz.index == 0

    def test_a_failing_action_does_NOT_advance(self):
        """THE BUG THIS PREVENTS. If installing Tailscale or starting the
        Imager fails and the wizard moves on regardless, the next screen's
        instructions assume work that never happened — and are quietly wrong at
        the moment somebody is trusting them most."""
        ok, why = gui.available()
        if not ok:
            pytest.skip(why)
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        steps = gui.plan(has_tailscale=False, has_imager=True)
        w = gui.Wizard(steps, actions={
            "welcome": lambda: (False, "could not do the thing")}, root=root)
        try:
            w.forward()
            assert w.current.key == "welcome", "it advanced past a failure"
            assert "could not" in w.status.cget("text")
        finally:
            w.close()

    def test_an_action_that_raises_is_shown_not_thrown(self):
        """A traceback in a window is something this audience cannot act on."""
        ok, why = gui.available()
        if not ok:
            pytest.skip(why)
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        def boom():
            raise RuntimeError("the disk went away")
        w = gui.Wizard(gui.plan(has_tailscale=True, has_imager=True),
                       actions={"welcome": boom}, root=root)
        try:
            w.forward()               # must not raise
            assert w.current.key == "welcome"
            assert "disk went away" in w.status.cget("text")
        finally:
            w.close()

    def test_a_succeeding_action_advances(self):
        ok, why = gui.available()
        if not ok:
            pytest.skip(why)
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        w = gui.Wizard(gui.plan(has_tailscale=True, has_imager=True),
                       actions={"welcome": lambda: (True, "done")}, root=root)
        try:
            w.forward()
            assert w.current.key == "reachability"
        finally:
            w.close()

    def test_every_screen_is_reachable_by_clicking_forward(self, wiz):
        """No dead ends in the real widget path, not just in the data."""
        seen = [wiz.current.key]
        for _ in range(len(wiz.steps) - 1):
            wiz.forward()
            seen.append(wiz.current.key)
        assert seen == [s.key for s in wiz.steps]


class TestTheActions:
    """What the buttons do, without doing it.

    Every action is exercised with its underlying module patched, because the
    real ones install software and erase cards. What is being tested is the
    DECISION each action makes about the result — which is where the bugs are.
    """

    def test_they_reuse_the_console_flow_functions(self):
        """NOT A PARALLEL IMPLEMENTATION. Two code paths for one job drift
        until only one of them still works; this asserts the window calls the
        same functions the console does."""
        import inspect
        src = inspect.getsource(gui.build_actions)
        for fn in ("tailnet.status", "imager.find_imager", "imager.try_install",
                   "imager.launch", "pi.find_boot_partition",
                   "pi.provision_boot_partition"):
            assert fn in src, f"the window does not go through {fn}"

    def test_no_import_happens_when_the_map_is_built(self):
        """Building the map must be free of side effects and of import cycles
        — cli imports gui, so gui importing cli at module scope would loop."""
        actions = gui.build_actions()
        assert set(actions) >= {"reachability", "imager", "write", "provision"}

    def test_failing_to_install_tailscale_is_not_a_failure(self, monkeypatch):
        """A LAN-only appliance is a supported outcome. Reporting it as an
        error would push somebody into abandoning a perfectly good install."""
        from sambuca_flasher import tailnet
        monkeypatch.setattr(tailnet, "status",
                            lambda: type("S", (), {"installed": False})())
        monkeypatch.setattr(tailnet, "install_here", lambda: False)
        ok, msg = gui.build_actions()["reachability"]()
        assert ok is True
        assert "home network only" in msg

    def test_failing_to_install_the_imager_IS_a_failure(self, monkeypatch):
        """Without it nothing can be written, so advancing would put somebody
        on a screen telling them to use a program they do not have."""
        from sambuca_flasher import imager
        monkeypatch.setattr(imager, "find_imager", lambda: None)
        monkeypatch.setattr(imager, "try_install", lambda: False)
        ok, msg = gui.build_actions()["imager"]()
        assert ok is False
        assert "yourself" in msg

    def test_an_already_open_imager_is_not_an_error(self, monkeypatch):
        """It refused to start twice for exactly this reason, and the owner
        said so twice."""
        import pathlib

        from sambuca_flasher import imager
        monkeypatch.setattr(imager, "find_imager", lambda: pathlib.Path("x"))
        monkeypatch.setattr(imager, "already_running", lambda: True)
        ok, msg = gui.build_actions()["write"]()
        assert ok is True
        assert "already open" in msg

    def test_a_missing_card_explains_the_eject_rather_than_erroring(self, monkeypatch):
        """rpi-imager dismounts the card when it finishes. That is normal, and
        no amount of rescanning brings it back — it has to be re-seated."""
        from sambuca_flasher import pi
        monkeypatch.setattr(pi, "find_boot_partition", lambda: None)
        ok, msg = gui.build_actions()["provision"]()
        assert ok is False
        assert "put it back in" in msg

    def test_a_write_failure_is_reported_not_raised(self, monkeypatch, tmp_path):
        from sambuca_flasher import pi
        monkeypatch.setattr(pi, "find_boot_partition", lambda: tmp_path)
        monkeypatch.setattr(pi, "provision_boot_partition",
                            lambda *a, **k: (_ for _ in ()).throw(
                                OSError("card is write-protected")))
        ok, msg = gui.build_actions()["provision"]()
        assert ok is False
        assert "write-protected" in msg


class TestTheDoubleClickPath:
    """What somebody who double-clicks the icon actually gets.

    This is the whole complaint that started the window: they opened an
    application and got a text menu in a black rectangle.
    """

    def test_double_click_prefers_the_window(self):
        """Read the entry point, because the behaviour only shows up when the
        binary is double-clicked and no test can do that."""
        import inspect

        from sambuca_flasher import cli
        src = inspect.getsource(cli.main)
        i_dbl = src.index("launched_by_double_click")
        i_win = src.index("_cmd_window")
        i_menu = src.index("_interactive()")
        assert i_dbl < i_win < i_menu, (
            "the window must be tried BEFORE falling back to the console menu")

    def test_the_console_menu_survives_as_the_fallback(self):
        """Deleting it would strand anyone on a headless box, or with no
        toolkit — which is most Linux installs until python3-tk is present."""
        import inspect

        from sambuca_flasher import cli
        assert callable(cli._interactive)
        assert "_interactive()" in inspect.getsource(cli.main)

    def test_the_menu_offers_the_window_without_losing_its_reassurance(self):
        """MY EDIT HERE WAS WRONG AND AN EXISTING TEST CAUGHT IT.

        I rewrote the preamble to drop "You opened this by double-clicking",
        reasoning that the menu is now only a fallback. It is not: _interactive
        is reached ONLY from the double-click branch, so everyone who sees it
        did double-click, and the line was accurate all along. It also sits
        beside a deliberate reassurance — nothing touches a disk without asking
        — that a nervous first-time reader needs before the options that do.

        Changing text that was already correct, and taking a reassurance with
        it, is the failure. The window is mentioned ALONGSIDE it now.
        """
        from sambuca_flasher import cli
        assert "double-clicking" in cli._MENU
        assert "touches a disk until it asks you first" in cli._MENU
        assert "window" in cli._MENU.lower()


class TestTheAvailabilityCheck:
    """`window --check` — the only way to learn whether the window survives
    packaging.

    Whether PyInstaller bundles Tcl/Tk cannot be determined by reading source;
    only a built binary knows. And `window` itself cannot be smoke-tested,
    because it opens a real window and blocks on its event loop forever.
    """

    def test_it_answers_rather_than_crashing_with_no_toolkit(self, monkeypatch,
                                                             capsys):
        """THE PROPERTY THE WHOLE LAZY IMPORT EXISTS FOR. A traceback here
        would mean a missing toolkit takes the entire binary down, which is
        exactly the failure keys.py caused once with the BIP-39 library."""
        import builtins

        from sambuca_flasher import cli
        real = builtins.__import__

        def no_tk(name, *a, **k):
            if name == "tkinter":
                raise ImportError("No module named 'tkinter'")
            return real(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", no_tk)
        rc = cli.main(["window", "--check"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "window available: no" in out
        assert "reason:" in out, "a refusal must say why"

    def test_it_reports_yes_where_a_window_can_open(self, capsys):
        ok, why = gui.available()
        if not ok:
            pytest.skip(why)
        from sambuca_flasher import cli
        rc = cli.main(["window", "--check"])
        assert rc == 0
        assert "window available: yes" in capsys.readouterr().out

    def test_check_never_opens_a_window(self):
        """It must not block. The smoke test runs it on a headless runner with
        no way to close anything."""
        import inspect

        from sambuca_flasher import cli
        src = inspect.getsource(cli._cmd_window)
        check_at = src.index('getattr(args, "check", False)')
        run_at = src.index("wizard.run()")
        assert check_at < run_at, "the check must return before anything opens"
