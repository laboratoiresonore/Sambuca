"""No setting may exist that changes nothing when you change it.

THE RULE THIS ENFORCES is in CLAUDE.md: "Never ship a control that does
nothing." The canonical example there is the FLUX negative-prompt box — at
cfg 1.0 it has no effect, so exposing it would teach people the machine is
arbitrary. A dead environment variable is the same failure with less ceremony:
somebody raises SAMBUCA_IMAGE_STEPS to 8, expects slower and better pictures,
gets exactly nothing, and has no way to find out why.

THE CHECK TOOK THREE TRIES TO GET RIGHT, and the wrong versions both reported
a clean sweep — which is worth recording, because a green audit that cannot
fail is more dangerous than no audit.

  1. "Does the name appear anywhere?" — yes, in compose, as the pass-through
     that hands it to the container. Appearing is not being read.
  2. "Does anything of ours mention it?" — yes: hardware-detect.sh WRITES it
     into a profile. Writing a variable is not consuming it.
  3. Reads only ($VAR, ${VAR}, os.environ["VAR"]) and never assignments. That
     found the two real ones and nothing else.

The scope is deliberately narrow: SAMBUCA_-prefixed variables handed to a
container. A third-party image has no reason to read a name we invented, so if
nothing of ours reads it either, nothing does.
"""

from __future__ import annotations

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Known dead, with a reason and a home. NOT a way to silence the check — each
# entry is a debt that is written down somewhere a reader will find it.
KNOWN_UNREAD = {
    # Reserved for the orchestrator that would substitute them into the
    # workflow graph. That orchestrator is not built; the README status table
    # and compose/.env.example both say so in as many words.
    "SAMBUCA_IMAGE_WORKFLOW",
    "SAMBUCA_IMAGE_STEPS",
}

SOURCE_GLOBS = (
    "engine/**/*.sh", "engine/*.sh", "engine/**/*.py",
    "apps/**/*.py", "tools/*.py", "compose/config/**/*",
)


def _reads(text: str) -> set[str]:
    """Names this text READS. Assignments deliberately do not count."""
    out = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)[}:]", text))
    out |= set(re.findall(r"\$([A-Z][A-Z0-9_]{3,})\b", text))
    out |= set(re.findall(
        r"environ(?:\.get)?\(?\[?[\"']([A-Z][A-Z0-9_]*)[\"']", text))
    return out


def _our_reads() -> set[str]:
    found: set[str] = set()
    for pat in SOURCE_GLOBS:
        for f in ROOT.glob(pat):
            if f.is_file():
                try:
                    found |= _reads(f.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    pass
    return found


def _container_env() -> list[tuple[str, str, str]]:
    out = []
    for f in sorted((ROOT / "compose").glob("*.yml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for svc, spec in (doc.get("services") or {}).items():
            env = (spec or {}).get("environment") or {}
            items = (env.items() if isinstance(env, dict)
                     else [(e.split("=")[0], e) for e in env])
            for key, _ in items:
                out.append((f.name, svc, str(key)))
    return out


def test_no_sambuca_setting_is_handed_to_a_container_and_read_by_nobody():
    reads = _our_reads()
    dead = [f"{f} :: {svc}.{k}" for f, svc, k in _container_env()
            if k.startswith("SAMBUCA_") and k not in reads and k not in KNOWN_UNREAD]
    assert not dead, (
        "these settings are passed to a container that cannot read them, and "
        "nothing of ours reads them either — a knob that turns and changes "
        "nothing:\n  " + "\n  ".join(dead))


def test_the_allowlist_has_not_gone_stale():
    """An entry that BECOMES read should leave the list, or the next genuinely
    dead knob hides behind a stale exemption."""
    reads = _our_reads()
    now_read = sorted(KNOWN_UNREAD & reads)
    assert not now_read, (
        f"{now_read} are now read by something — remove them from "
        "KNOWN_UNREAD so they are checked properly")


def test_the_sweep_can_actually_see_the_files():
    """The failure mode of every audit in this repository: a moved directory
    turns it into an expensive no-op that still reports green. Two of the three
    earlier versions of this check reported a clean sweep while being wrong."""
    assert len(_container_env()) > 20, "found almost no container environment"
    assert len(_our_reads()) > 30, "found almost no variable reads in our source"
