"""Every command this project tells somebody to run must actually exist.

THIS GUARDS A FAILURE THAT ALREADY HAPPENED, TWICE. The recovery flow printed
"run: sambuca-flasher verify-sheet" for a while before that command existed, and
the menu told people to run `write-pi --image <path>` after --image had stopped
being required. Both read as helpful. Both sent somebody to a dead end.

It is the cheapest possible check and it catches the most embarrassing possible
bug: a project whose own instructions do not work.
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

REPO = pathlib.Path(__file__).resolve().parents[3]

# Words that follow "sambuca-flasher" in ORDINARY PROSE rather than as a
# command — "sambuca-flasher needs the 'mnemonic' package". Deliberately a
# short, explicit list: anything not here has to be a real command.
PROSE = {"needs", "requires", "is", "was", "will", "can", "does", "and", "to"}

SEARCH_DIRS = ["apps", "engine", "docs"]
SEARCH_FILES = ["README.md"]
SUFFIXES = {".py", ".sh", ".md", ".txt"}


def _real_commands() -> set[str]:
    """Ask argparse itself, rather than maintaining a second list here.

    A hand-kept copy would drift from the parser, and then this test would be
    checking one piece of fiction against another.
    """
    import sambuca_flasher.cli as cli

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.suppress(SystemExit):
        cli.main(["--help"])
    m = re.search(r"\{([a-z,\-]+)\}", buf.getvalue())
    assert m, "could not read the command list out of --help"
    return set(m.group(1).split(","))


def _mentions() -> dict[str, list[str]]:
    """Every "sambuca-flasher <word>" written anywhere a person will read."""
    found: dict[str, list[str]] = {}
    paths: list[pathlib.Path] = [REPO / f for f in SEARCH_FILES]
    for d in SEARCH_DIRS:
        base = REPO / d
        if base.is_dir():
            paths += [p for p in base.rglob("*") if p.suffix in SUFFIXES]

    pattern = re.compile(r"sambuca-flasher ([a-z][a-z-]*)")
    for p in paths:
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for word in pattern.findall(text):
            found.setdefault(word, []).append(str(p.relative_to(REPO)))
    return found


def test_every_command_the_project_names_actually_exists():
    real = _real_commands()
    broken = {
        word: sorted(set(where))
        for word, where in _mentions().items()
        if word not in real and word not in PROSE
    }
    assert not broken, (
        "These are written as commands but no such command exists.\n"
        "Somebody following the instructions would hit a usage error:\n"
        + "\n".join(f"  sambuca-flasher {w}  <- {', '.join(f)}"
                    for w, f in broken.items())
    )


def test_the_prose_allowlist_has_not_gone_stale():
    """An allowlist that quietly shadows a real command is worse than none.

    If a word here ever becomes an actual subcommand, this test's exemption
    would hide a genuine check rather than permitting harmless prose.
    """
    overlap = PROSE & _real_commands()
    assert not overlap, (
        f"{sorted(overlap)} are exempted as prose but are now real commands - "
        "remove them from PROSE so they are checked properly")


def test_the_check_can_actually_see_the_files():
    """A search that matches nothing would pass this suite silently.

    The most likely way for these tests to rot is a moved directory turning
    them into an expensive no-op that still reports green.
    """
    mentions = _mentions()
    assert len(mentions) >= 5, (
        f"only found {len(mentions)} mentions - the search paths are probably "
        "wrong, which would make the test above meaningless")
