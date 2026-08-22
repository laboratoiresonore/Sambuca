#!/usr/bin/env python3
"""
sambuca :: the Steward's gate.

WHAT THIS IS. A language model proposes an operation; this decides whether that
proposal is allowed to become one, and turns it into a sentence a human can
approve. It is the half of the Steward that must be right regardless of which
model is behind it, or how confused that model is.

WHAT IT DELIBERATELY IS NOT. It does not execute anything. It returns a PLAN —
a verb, validated parameters, and the sentence to show — and something else acts
on it after a human agrees. CLAUDE.md puts the rule plainly: an AI with
privileges picks a lever; it never has hands. A resolver that could also run the
verb would be the hands.

═══════════════════════════════════════════════════════════════════════════
THE PROPOSAL IS UNTRUSTED INPUT. Not because the model is malicious, but
because the text it read might be: an email, a filename, a document, a web page
in a summary. Prompt injection cannot be prevented at the language layer, so it
is not defended there.

  A VERB THAT IS NOT IN THE CATALOGUE DOES NOT EXIST. There is no fallback, no
  fuzzy match, no "did you mean". Injected text can at worst cause an existing
  verb to be PROPOSED — which is what the confirmation sentence is for — and can
  never invent one.

  PARAMETERS ARE TYPED AND BOUNDED, from the catalogue rather than from the
  proposal. A string longer than its declared max_length is refused rather than
  truncated; an enum value not in its list is refused rather than coerced. The
  model does not get to widen its own inputs.

  THE CONFIRMATION SENTENCE CARRIES RESOLVED VALUES. "Remove the account for
  Priya Sharma" — not "remove a user". A human approving a sentence that hides
  what it operates on has not approved anything.
═══════════════════════════════════════════════════════════════════════════

Exit codes: 0 the proposal resolves, 1 it is refused, 2 the catalogue is
unreadable — which is not the same as a refusal and must never be mistaken for
one.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

try:
    import yaml
except ImportError as exc:                      # pragma: no cover
    print("steward-resolve: pyyaml is required", file=sys.stderr)
    raise SystemExit(2) from exc

CATALOGUE = pathlib.Path(__file__).resolve().parent / "verbs.yml"

# Reference types name something on this machine. They are bounded like any
# other string: a "user_ref" is not a licence to send a megabyte.
REF_TYPES = {"user_ref", "device_ref", "share_ref", "service_ref", "path_ref"}
REF_MAX = 256


class Refused(Exception):
    """A proposal that will not become an operation, and the reason why.

    Separate from a catalogue failure on purpose. "I will not do that" and "I
    cannot read my own rules" are different answers, and collapsing them would
    let a broken catalogue read as a safe refusal.
    """


def load_catalogue(path: pathlib.Path = CATALOGUE) -> dict:
    """The rules, or exit 2 — never a refusal.

    EVERY way of failing to read them lands here, including a file that is not
    YAML at all. The first version only handled "parsed, but wrong shape": an
    unparseable catalogue raised a YAMLError that escaped as an ordinary
    traceback and exit 1, which is the code for "I will not do that".

    A broken catalogue reading as a safe refusal is the quietest possible way
    for this gate to stop being a gate — it looks like caution.
    """
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(f"steward-resolve: cannot read the catalogue at {path}: "
              f"{exc.__class__.__name__}", file=sys.stderr)
        raise SystemExit(2) from exc
    if not isinstance(doc, dict) or not doc.get("verbs"):
        print(f"steward-resolve: {path} defines no verbs", file=sys.stderr)
        raise SystemExit(2)
    return {v["name"]: v for v in doc["verbs"] if v.get("name")}


def _check_value(verb: str, spec: dict, value):
    """One parameter, against the catalogue's declaration of it."""
    name, ptype = spec.get("name"), spec.get("type")

    if ptype == "bool":
        if not isinstance(value, bool):
            raise Refused(f"{verb}.{name} must be true or false, not {value!r}")
        return value

    if ptype == "enum":
        allowed = spec.get("values") or []
        if value not in allowed:
            raise Refused(
                f"{verb}.{name} must be one of {allowed}, not {value!r}")
        return value

    # Everything else arrives as text, and text has a length.
    if not isinstance(value, str):
        raise Refused(f"{verb}.{name} must be text, not {type(value).__name__}")

    limit = spec.get("max_length", REF_MAX if ptype in REF_TYPES else None)
    if limit is None:
        # A string parameter with no bound is a finding steward-lint already
        # refuses. Belt and braces: unbounded model output must not reach an
        # implementation just because a catalogue edit slipped past.
        raise Refused(f"{verb}.{name} has no declared length limit")
    if len(value) > limit:
        raise Refused(
            f"{verb}.{name} is {len(value)} characters; the limit is {limit}")
    if not value.strip():
        raise Refused(f"{verb}.{name} is empty")

    if ptype == "duration" and not _looks_like_duration(value):
        raise Refused(
            f"{verb}.{name} must be a duration like '30m', '2h' or '7d', "
            f"not {value!r}")
    return value


def _looks_like_duration(value: str) -> bool:
    v = value.strip()
    return (len(v) >= 2 and v[:-1].isdigit() and v[-1] in "smhdw"
            and int(v[:-1]) > 0)


def resolve(catalogue: dict, proposal: dict) -> dict:
    """A proposal becomes a plan, or it is refused with a reason."""
    name = proposal.get("verb")
    if not isinstance(name, str) or not name:
        raise Refused("the proposal names no verb")

    verb = catalogue.get(name)
    if verb is None:
        # NO FUZZY MATCH, deliberately. Suggesting the nearest verb to an
        # invented one is how "delete_everything" becomes "user.remove".
        raise Refused(
            f"{name!r} is not an operation this machine has. The list is fixed "
            f"and published; nothing outside it can be proposed.")

    supplied = proposal.get("params") or {}
    if not isinstance(supplied, dict):
        raise Refused("parameters must be given as a mapping")

    declared = {p["name"]: p for p in (verb.get("params") or []) if p.get("name")}

    unknown = sorted(set(supplied) - set(declared))
    if unknown:
        # An extra parameter is a proposal trying to reach past the catalogue.
        raise Refused(f"{name} does not take {unknown}")

    resolved: dict = {}
    for pname, spec in declared.items():
        if pname in supplied:
            resolved[pname] = _check_value(name, spec, supplied[pname])
        elif "default" in spec:
            resolved[pname] = spec["default"]
        else:
            raise Refused(f"{name} needs {pname}: {spec.get('prompt', pname)}")

    confirm = verb.get("confirm", "strong")
    blast = verb.get("blast_radius", "disruptive")

    # DEFENCE IN DEPTH. steward-lint refuses this combination at commit time;
    # checking again here means a catalogue edited on the appliance, or shipped
    # past the linter, still cannot execute a disruptive verb unattended.
    if blast == "disruptive" and confirm == "none":
        raise Refused(
            f"{name} is disruptive but declares no confirmation — refusing to "
            f"act on a catalogue that contradicts itself")

    return {
        "verb": name,
        "params": resolved,
        "blast_radius": blast,
        "confirm": confirm,
        "reversible_by": verb.get("reversible_by"),
        "returns_secret": bool(verb.get("returns_secret")),
        "sentence": _sentence(verb, resolved),
    }


def _sentence(verb: dict, params: dict) -> str:
    """What the human is asked to approve, with the real values in it.

    A confirmation that says "remove a user" has not told anybody anything. The
    summary is the catalogue's own words; the values are appended so the
    sentence is about THIS operation rather than the category.
    """
    text = str(verb.get("summary", verb.get("name", "")))
    if params:
        detail = ", ".join(f"{k}: {v}" for k, v in sorted(params.items()))
        text = f"{text} ({detail})"
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("proposal", nargs="?",
                    help="JSON: {\"verb\": ..., \"params\": {...}}. "
                         "Reads stdin when omitted.")
    ap.add_argument("--catalogue", type=pathlib.Path, default=CATALOGUE)
    args = ap.parse_args(argv)

    raw = args.proposal if args.proposal is not None else sys.stdin.read()
    try:
        proposal = json.loads(raw)
    except ValueError as exc:
        print(f"steward-resolve: the proposal is not valid JSON: {exc}",
              file=sys.stderr)
        return 1

    catalogue = load_catalogue(args.catalogue)
    try:
        plan = resolve(catalogue, proposal)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
