"""Regressions for bugs that 110 passing tests did not see.

ALL THREE WERE FOUND BY A LINTER, WHICH IS THE UNCOMFORTABLE PART. The suite
was green the whole time, because none of it executed the paths where these
live. A test that never runs a command cannot notice the command is broken.

They share a shape: the code READS correctly. A name that is never bound, a
value computed and dropped, a function replaced by a later one with the same
name — every one of them looks fine on the page and fails only when run.
"""

from __future__ import annotations

import inspect
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from sambuca_flasher import cli, safeurl  # noqa: E402

# UNDEFINED NAMES ARE NOT TESTED HERE, DELIBERATELY.
#
# `repo_root` was referenced twice in _cmd_write and never bound, so the whole
# x86 installer path raised NameError at step 2 of 6 on every run. The obvious
# reaction is to write a test for it — and the first attempt did, walking the
# symbol table looking for referenced-but-unassigned names.
#
# It produced 32 false positives immediately: this file uses function-local
# imports throughout (deliberately, so `list` and `--version` do not die
# demanding reportlab), and symtable does not report an import binding the way
# that check assumed.
#
# ruff's F821 already finds this, correctly, and now runs over this exact path
# in CI. A hand-rolled reimplementation of a linter rule is worse than the
# linter: it is less accurate, and being green would mean less.
#
# The lesson kept, rather than a bad test: the bug was invisible because
# nothing ever EXECUTED `write`. That is a coverage gap, not a missing
# assertion, and it is fixed by running the command — which the frozen-binary
# smoke test now does.


class TestNoSilentShadowing:
    def test_no_function_name_is_defined_twice(self):
        """Two functions were called _stage_engine.

        One staged the full engine+compose trees for x86; the other, defined
        300 lines later, staged the small subset a FAT partition can hold.
        Python does not warn — the later definition simply replaced the first,
        so every call resolved to the wrong one, with a different signature and
        a different return type.
        """
        import ast

        tree = ast.parse(pathlib.Path(cli.__file__).read_text(encoding="utf-8"))
        names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"defined more than once, later wins silently: {dupes}"


class TestTheTailnetKeySurvives:
    def test_settle_reachability_result_is_not_dropped(self):
        """It was assigned to a local and thrown away.

        The owner was walked through installing Tailscale, signing in, and
        minting a pre-auth key — and the key never reached the card. The step
        that runs FIRST, because an appliance nobody can find is not an
        appliance, produced an appliance that never joined the tailnet.

        The symptom is the cruel kind: somebody does everything right, cannot
        reach their machine, and nothing on screen suggests why.
        """
        src = inspect.getsource(cli._cmd_write_pi)
        assert "_settle_reachability" in src, "reachability step vanished"
        assert "args.tailscale_key = _settle_reachability" in src, (
            "the minted key must be stored where provisioning reads it "
            "(args.tailscale_key), not bound to a local and discarded")

    def test_provisioning_reads_the_key_from_args(self):
        """The other half of the contract. If provisioning stopped reading it,
        the assignment above would be writing into the void."""
        src = inspect.getsource(cli._cmd_provision_pi)
        assert 'getattr(args, "tailscale_key"' in src


class TestURLsAreRestricted:
    """A manifest decides which URLs this program opens, and a manifest is
    remote data. urlopen honours file:, so a tampered one could name
    file:///etc/shadow and have it fetched, checksummed and saved as an .iso."""

    @pytest.mark.parametrize("url", [
        "file:///etc/shadow",
        "file://C:/Users/someone/.ssh/id_rsa",
        "ftp://example.invalid/x.iso",
        "data:text/plain;base64,aGk=",
        "gopher://example.invalid/1",
        "/etc/passwd",
        "http:///etc/passwd",          # empty host slips past a scheme-only check
    ])
    def test_dangerous_urls_are_refused(self, url):
        with pytest.raises(safeurl.UnsafeURL):
            safeurl.check(url)

    @pytest.mark.parametrize("url", [
        "https://cdimage.debian.org/debian-cd/13.6.0/amd64/iso-cd/x.iso",
        "http://127.0.0.1:8765/progress",
        "https://sambuca.local/ca.crt",
    ])
    def test_ordinary_urls_pass(self, url):
        assert safeurl.check(url) == url

    def test_every_fetching_module_routes_through_the_guard(self):
        """An allowlist that one module forgets to call is not an allowlist.

        Checked by inspection because the alternative is trusting that whoever
        adds the next fetch remembers — which is exactly how the four existing
        ones ended up needing this retrofitted.
        """
        root = pathlib.Path(cli.__file__).parent
        for name in ("ca.py", "download.py", "handover.py", "manifest.py"):
            src = (root / name).read_text(encoding="utf-8")
            if "urlopen" not in src:
                continue
            assert "safeurl.check" in src, (
                f"{name} opens URLs without routing them through safeurl.check")
