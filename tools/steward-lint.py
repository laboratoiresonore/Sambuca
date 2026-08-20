#!/usr/bin/env python3
"""Validate the Steward's verb catalogue.

The catalogue is the only thing standing between "speak to your server" and
"a language model with a shell". Its safety properties are therefore checked
mechanically on every commit, not asserted in a comment and hoped for.

What this refuses to let through, and why each one is a real failure and not a
style preference:

  * a disruptive verb that does not confirm
        The whole design rests on a human reading a resolved sentence before
        anything is interrupted or removed. One `confirm: none` on a
        destructive verb silently deletes that property.

  * a verb that claims to be reversible but names no reversal
        "Reversible" is the reason several verbs are allowed to exist at all.
        If nothing undoes it, it is not reversible, and it needs a stronger
        confirmation than it is currently getting.

  * a reversal pointing at a verb that does not exist
        A typo here produces a promise the appliance cannot keep, and it would
        only be discovered by an owner trying to undo something.

  * a read-only verb with a blast radius
        Contradictory metadata. The model is told a verb is safe to run without
        asking; the catalogue simultaneously says it changes things.

  * an enum default that is not one of its own values
        Produces a verb that fails validation at the moment it is used, which
        is the worst time to find out.

  * a verb that hands back a secret without documenting how
        `returns_secret` means an auth key or an enrolment link exists in the
        world. How it reaches the human, and what the audit log records
        instead, has to be written down.

Exit codes: 0 clean, 1 findings, 2 the catalogue could not be read.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("steward-lint: pyyaml is required (pip install pyyaml)", file=sys.stderr)
    raise SystemExit(2)

CATALOGUE = Path(__file__).resolve().parent.parent / "engine" / "steward" / "verbs.yml"

BLAST = {"additive", "reversible", "disruptive"}
CONFIRM = {"none", "standard", "strong"}
PARAM_TYPES = {
    "string", "bool", "enum", "duration",
    "user_ref", "device_ref", "share_ref", "service_ref", "path_ref",
}


def lint(doc: dict) -> list[str]:
    findings: list[str] = []
    verbs = doc.get("verbs") or []
    names = {v.get("name") for v in verbs}

    if doc.get("version") != 1:
        findings.append(f"catalogue version is {doc.get('version')!r}, expected 1")

    if not verbs:
        findings.append("catalogue defines no verbs")

    seen: set[str] = set()
    for v in verbs:
        name = v.get("name", "<unnamed>")

        if name in seen:
            findings.append(f"{name}: defined more than once")
        seen.add(name)

        for required in ("summary", "blast_radius", "confirm"):
            if not v.get(required):
                findings.append(f"{name}: missing required field {required!r}")

        blast = v.get("blast_radius")
        confirm = v.get("confirm")
        if blast is not None and blast not in BLAST:
            findings.append(f"{name}: blast_radius {blast!r} is not one of {sorted(BLAST)}")
        if confirm is not None and confirm not in CONFIRM:
            findings.append(f"{name}: confirm {confirm!r} is not one of {sorted(CONFIRM)}")

        # The load-bearing rule.
        if blast == "disruptive" and confirm == "none":
            findings.append(
                f"{name}: disruptive verbs must confirm — this one interrupts "
                f"service or removes access with no human in the loop"
            )

        # "Reversible" has to mean something.
        if blast == "reversible" and "reversible_by" not in v:
            findings.append(
                f"{name}: declared reversible but names no reversal. Either add "
                f"reversible_by, or raise blast_radius to disruptive"
            )
        rev = v.get("reversible_by")
        if rev is not None and rev not in names:
            findings.append(f"{name}: reversible_by names {rev!r}, which is not a verb")

        if v.get("read_only"):
            if blast != "additive":
                findings.append(f"{name}: read_only but blast_radius is {blast!r}")
            if confirm != "none":
                findings.append(f"{name}: read_only but asks for confirmation ({confirm!r})")

        if v.get("returns_secret") and not v.get("notes"):
            findings.append(
                f"{name}: returns a secret but has no notes explaining how it "
                f"reaches the human and what the audit log records instead"
            )

        if not v.get("read_only") and not v.get("speech_examples"):
            findings.append(
                f"{name}: no speech_examples. The model needs grounding for what "
                f"phrasings map here, and a reviewer needs to see them"
            )

        for p in v.get("params") or []:
            pname = p.get("name", "<unnamed>")
            ptype = p.get("type")
            if not p.get("name"):
                findings.append(f"{name}: a parameter has no name")
            if ptype not in PARAM_TYPES:
                findings.append(
                    f"{name}.{pname}: type {ptype!r} is not one of {sorted(PARAM_TYPES)}"
                )
            if ptype == "enum":
                values = p.get("values") or []
                if not values:
                    findings.append(f"{name}.{pname}: enum with no values")
                elif "default" in p and p["default"] not in values:
                    findings.append(
                        f"{name}.{pname}: default {p['default']!r} is not among {values}"
                    )
            if ptype == "string" and "max_length" not in p:
                findings.append(
                    f"{name}.{pname}: string parameter with no max_length — "
                    f"unbounded model output reaches the implementation"
                )

    excluded = doc.get("excluded") or []
    if not excluded:
        findings.append(
            "no excluded list. The catalogue is only meaningful alongside an "
            "explicit statement of what is deliberately out of reach"
        )
    for e in excluded:
        if not e.get("subject") or not e.get("reason"):
            findings.append(f"excluded entry {e!r} needs both a subject and a reason")

    return findings


def main() -> int:
    if not CATALOGUE.is_file():
        print(f"steward-lint: no catalogue at {CATALOGUE}", file=sys.stderr)
        return 2
    try:
        doc = yaml.safe_load(CATALOGUE.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print(f"steward-lint: {CATALOGUE.name} does not parse: {exc}", file=sys.stderr)
        return 2

    findings = lint(doc)
    verbs = doc.get("verbs") or []

    if findings:
        print(f"steward-lint: {len(findings)} finding(s) in {CATALOGUE.name}\n")
        for f in findings:
            print(f"  FAIL  {f}")
        return 1

    confirmed = sum(1 for v in verbs if v.get("confirm") != "none")
    secrets = sum(1 for v in verbs if v.get("returns_secret"))
    print(
        f"steward-lint: {len(verbs)} verbs clean "
        f"({confirmed} require confirmation, {secrets} issue a secret, "
        f"{len(doc.get('excluded') or [])} subjects excluded)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
