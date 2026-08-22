#!/usr/bin/env python3
"""
sambuca :: turning a model's reply into a proposal.

THE LINK BETWEEN THE MODEL AND THE GATE. steward-resolve.py decides whether a
proposal may become an operation. Something has to produce that proposal from
what a language model actually emits, which is prose with a JSON object
somewhere inside it, or three JSON objects, or a fenced block, or an apology.

This is the seam where injected text arrives, so it is the seam that has to be
boring.

═══════════════════════════════════════════════════════════════════════════
EXTRACTION IS NOT PARSING, and the difference is the whole file.

A model asked for one object frequently emits more than one — the example from
its own prompt, then its answer. It also summarises documents that CONTAIN
JSON, and a summarised email can carry {"verb": "user.remove", ...} written by
whoever sent the email.

So:

  AMBIGUITY IS REFUSED, NEVER RESOLVED. Two candidate objects is not "take the
  last one" — it is a refusal. Any rule for choosing between them is a rule an
  attacker can satisfy: put yours second, or wrap it in a fence, or make it the
  only one that parses.

  THE MODEL'S PROSE IS NOT CONSULTED. Only objects that look like a proposal —
  a `verb` key — are candidates. Explanations, apologies and confidence
  statements are discarded, so "I am certain you want me to" carries no weight.

  NOTHING IS GUESSED. No verb inferred from prose, no parameters scraped out of
  a sentence. If the model did not emit a well-formed proposal, the answer is
  that it did not, and a human is asked again.
═══════════════════════════════════════════════════════════════════════════

The proposal that comes out has been validated for SHAPE only. Whether the verb
exists, and whether the parameters are allowed, is steward-resolve's decision
and is deliberately not duplicated here — two places enforcing one rule is how
they drift.
"""

from __future__ import annotations

import argparse
import json
import sys

# Model output is a person-sized reply, not a data feed. Something far larger
# is a runaway generation or a document paste, and neither is a proposal.
MAX_REPLY = 64 * 1024


class NotAProposal(Exception):
    """The reply did not contain exactly one well-formed proposal."""


def _candidates(text: str) -> list[dict]:
    """Every top-level JSON object in the text that looks like a proposal.

    Scans for balanced braces rather than using a regex: a regex cannot count,
    and nested objects (params is one) defeat the obvious pattern immediately.
    String awareness matters too — a brace inside a quoted value is not
    structure, and treating it as structure is how the scan loses its place and
    starts finding objects that were never there.
    """
    found: list[dict] = []
    depth = 0
    start = -1
    in_string = False
    escape = False

    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    chunk = text[start:i + 1]
                    try:
                        obj = json.loads(chunk)
                    except ValueError:
                        obj = None
                    # Only objects SHAPED like a proposal are candidates. The
                    # model's own explanations, and any other JSON it happened
                    # to quote, are not competing answers.
                    if isinstance(obj, dict) and "verb" in obj:
                        found.append(obj)
                    start = -1
    return found


def parse(reply: str) -> dict:
    """One proposal, or a refusal that says which way it went wrong."""
    if not isinstance(reply, str):
        raise NotAProposal("the reply is not text")
    if len(reply) > MAX_REPLY:
        raise NotAProposal(
            f"the reply is {len(reply)} bytes; the limit is {MAX_REPLY}. "
            f"That is a runaway generation or a pasted document, not an answer.")

    found = _candidates(reply)

    if not found:
        raise NotAProposal(
            "the reply contains no proposal. Nothing is inferred from prose — "
            "ask again rather than guessing what was meant.")

    if len(found) > 1:
        # NEVER "take the last one". Any tie-break is a rule an attacker can
        # satisfy: put yours second, or make it the only one that parses.
        raise NotAProposal(
            f"the reply contains {len(found)} proposals. Which one was meant "
            f"cannot be decided safely, so none of them is used.")

    proposal = found[0]
    verb = proposal.get("verb")
    if not isinstance(verb, str) or not verb.strip():
        raise NotAProposal("the proposal's verb is not a name")

    params = proposal.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise NotAProposal("the proposal's params are not a mapping")

    # SHAPE ONLY. Whether this verb exists, and whether these parameters are
    # allowed, belongs to steward-resolve. Checking it twice is how two
    # enforcers drift apart and one of them quietly becomes the lenient one.
    return {"verb": verb.strip(), "params": params}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("reply", nargs="?", help="the model's reply; stdin if omitted")
    args = ap.parse_args(argv)

    raw = args.reply if args.reply is not None else sys.stdin.read()
    try:
        print(json.dumps(parse(raw), sort_keys=True))
    except NotAProposal as exc:
        print(f"not a proposal: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
