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


# ── one level down: the SUBVERB an instruction names ────────────────────────
#
# The checks above prove `sambuca-gitops` exists. They cannot tell you that
# `sambuca-gitops apply` does — and that gap has already cost one bug, on the
# worst possible path: the held-update message told owners to run
# `sambuca-gitops apply --force` to release a held update, `apply` did not
# exist, so following the instruction re-ran the sync, hit the same guard, and
# printed the same instruction. A loop, on the one mechanism that stops an
# appliance drifting years behind on security patches.
#
# That was fixed at its site. The lesson — "when a test names a specific thing,
# ask what it would take to make it name the category" — was not applied, so
# nothing stops the next one.

# Commands reach /usr/local/bin two different ways, and a check that knew only
# the first would silently skip `sambuca-identity` — which is precisely the
# command in the bug this file was created for.
_SYMLINKED = re.compile(
    r'ln -sf "\$INSTALL_ROOT/([^"]+)"\s+/usr/local/bin/(sambuca[a-z-]*)')
_GENERATED = re.compile(r'sb_atomic_write /usr/local/bin/(sambuca[a-z-]*)')

_SUBVERB = re.compile(
    r"(?:\brun(?:\s+as\s+root)?\s*:?\s+|^\s*\$\s+|^\s*\d+\.\s+|^\s{4,})"
    r"(sambuca-[a-z][a-z-]*)[ \t]+([a-z][a-z-]*)",
    re.M,
)


def _command_sources() -> dict[str, pathlib.Path]:
    """command name -> the file whose `case` block dispatches it.

    For a symlink that is the target script. For a command GENERATED inline
    (80-identity.sh writes one with sb_atomic_write) the dispatch lives in the
    generating file, so that is where the verbs are read from.
    """
    out: dict[str, pathlib.Path] = {}
    for f in ENGINE.rglob("*.sh"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        for target, name in _SYMLINKED.findall(text):
            out[name] = ROOT / target
        for name in _GENERATED.findall(text):
            out.setdefault(name, f)
    return out


def _dispatched_verbs(script: pathlib.Path) -> set[str] | None:
    """Every verb any `case` block in the file accepts, or None if it has none.

    EVERY block, not the first. Reading only the first missed `help` in two
    scripts — which are dispatched by a separate earlier block — and produced a
    confident false positive against correct code. A test that manufactures
    findings is worse than no test; this repository has been burnt by one
    already (`test_readme_versions.py` "corrected" the README to match a stale
    value).
    """
    if not script.exists():
        return None
    text = script.read_text(encoding="utf-8", errors="ignore")
    blocks = re.findall(r'\ncase\s+"[^"]*"\s+in\n(.*?)\n\s*esac', text, re.S)
    verbs: set[str] = set()
    for block in blocks:
        for pattern in re.findall(r'^[ \t]*([A-Za-z0-9_"|.*-]+)\)', block, re.M):
            for part in pattern.split("|"):
                part = part.strip().strip('"')
                if part and not part.startswith("-") and part != "*":
                    verbs.add(part)
    return verbs or None


def _named_subverbs() -> list[tuple[str, str, str, int]]:
    found = []
    for f in list(ENGINE.rglob("*.sh")) + list(ENGINE.rglob("*.py")):
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in _SUBVERB.finditer(text):
            line = text[:m.start()].count("\n") + 1
            found.append((m.group(1), m.group(2), str(f.relative_to(ROOT)), line))
    return found


def test_every_subverb_an_instruction_names_is_actually_dispatched():
    sources = _command_sources()
    broken = []
    for cmd, sub, f, n in _named_subverbs():
        script = sources.get(cmd)
        if script is None:
            continue                      # the command check above owns this
        verbs = _dispatched_verbs(script)
        if verbs and sub not in verbs:
            broken.append(f"{f}:{n}  {cmd} {sub}  (it accepts: "
                          f"{', '.join(sorted(verbs))})")
    assert not broken, (
        "instructions name subcommands that do not exist — following one would "
        "print 'unknown command' at the moment somebody was cooperating:\n  "
        + "\n  ".join(sorted(set(broken))))


def test_this_subverb_check_is_not_vacuous():
    """It found zero problems on the day it was written, which is either good
    news or a regex that matches nothing. Distinguish the two.

    Three separate ways this could silently become a no-op: the instruction
    shape stops matching, the install mechanisms are renamed, or the `case`
    parser stops finding blocks. All three are asserted, because the first
    draft of this check DID quietly lose `help` and reported a false positive.
    """
    pairs = _named_subverbs()
    assert len(pairs) >= 4, f"almost no subverb instructions matched: {pairs}"

    sources = _command_sources()
    assert len(sources) >= 5, f"install mechanisms not found: {sorted(sources)}"
    # Both mechanisms, named. A rename of either would otherwise skip whole
    # commands in silence — and the generated one is `sambuca-identity`, the
    # command this entire file exists because of.
    assert "sambuca-gitops" in sources, "the symlink mechanism stopped matching"
    assert "sambuca-identity" in sources, "the generated mechanism stopped matching"

    parsed = {c: _dispatched_verbs(p) for c, p in sources.items()}
    assert sum(1 for v in parsed.values() if v) >= 3, (
        f"the case-block parser found verbs for almost nothing: {parsed}")
    # THE SPECIFIC REGRESSION, and the first version of this assertion could
    # not have caught it. Two scripts dispatch in two blocks: an early one for
    # help, and the real verb table later. Reading only the first block keeps
    # `help` and loses everything else — so asserting on `help` proves nothing.
    #
    # It has to be a verb from a LATER block. Truncating the parser then makes
    # this fail directly, instead of surfacing as a false positive against a
    # correct instruction elsewhere — which is how it actually showed up, and
    # is the direction that gets somebody to "fix" working code.
    gitops = parsed.get("sambuca-gitops") or set()
    assert {"help", "apply"} <= gitops, (
        f"the parser is reading only one case block again: {sorted(gitops)}")
