"""Commands the appliance TELLS ITS OWNER TO RUN must actually exist.

THE BUG THAT PROMPTED THIS was step 4 of the ONE attended step in the whole
install. The completion report — the last thing most owners ever read — said:

    4.  sambuca identity set-client <client-id>

There is no `sambuca` command. The binaries are hyphenated. So the single
manual step required to arm the security gate named something that does not
exist, and the owner would meet "command not found" at the precise moment they
were being cooperative.

IT MATCHES INSTRUCTIONS, NOT PROSE, and that distinction took a rewrite.
The first version flagged every `sambuca <word>` anywhere and produced four
false positives immediately — all of them usage-header TITLES of the form
"sambuca first-boot — provision the appliance", which are documentation, not
commands. Its companion check flagged an ASCII logo, two build artefacts and a
filename.

Widening an exclusion list to silence those would have been the wrong repair:
a growing allowlist is where the next real bug hides. So the rule is narrower
instead — a line only counts if it READS AS AN INSTRUCTION.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"

# An instruction, not a description: "run: X", "Then run X", "$ X", or a
# numbered step "1. X". A usage title ("sambuca hardware-detect — profile…")
# matches none of these.
INSTRUCTION = re.compile(
    r"(?:\brun(?:\s+as\s+root)?\s*:?\s+|^\s*\$\s+|^\s*\d+\.\s+|^\s{4,})"
    r"(sambuca[ -][a-z][a-z-]*)",
    re.M,
)

INSTALLED = re.compile(r"/usr/local/bin/(sambuca[a-z-]*)")


def _installed() -> set[str]:
    names: set[str] = set()
    for f in ENGINE.rglob("*.sh"):
        names |= set(INSTALLED.findall(f.read_text(encoding="utf-8", errors="ignore")))
    return names


def _instructions() -> list[tuple[str, str, int]]:
    out = []
    for f in ENGINE.rglob("*.sh"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in INSTRUCTION.finditer(text):
            line = text[:m.start()].count("\n") + 1
            out.append((m.group(1).strip(), str(f.relative_to(ROOT)), line))
    return out


def test_no_instruction_uses_a_space_where_a_hyphen_belongs():
    """`sambuca identity …` reads perfectly and does not exist.

    There is no `sambuca` dispatcher, so a space where a hyphen belongs turns a
    working instruction into "command not found" — the easiest typo to make and
    the hardest to catch by reading.
    """
    bad = [f"{f}:{n}  '{cmd}'" for cmd, f, n in _instructions() if " " in cmd]
    assert not bad, (
        "there is no `sambuca` command — these instructions need a hyphen:\n  "
        + "\n  ".join(bad))


def test_every_command_an_instruction_names_is_installed():
    installed = _installed()
    # The flasher runs on the owner's computer, not the appliance, so it is
    # correctly absent from /usr/local/bin here. Its own promise test covers it.
    external = {"sambuca-flasher"}
    broken = [
        f"{f}:{n}  {cmd}" for cmd, f, n in _instructions()
        if " " not in cmd and cmd not in installed and cmd not in external
    ]
    assert not broken, (
        "instructions name commands that are never installed:\n  "
        + "\n  ".join(sorted(set(broken))))


def test_the_check_still_sees_instructions_and_binaries():
    """The recurring failure of every audit in this repository: a moved
    directory or a tightened regex turns it into a no-op that reports green.
    The narrowing above is exactly the kind of change that could do it."""
    assert len(_installed()) >= 4, f"found almost no binaries: {sorted(_installed())}"
    assert len(_instructions()) >= 3, "found almost no instructions — regex too tight?"
