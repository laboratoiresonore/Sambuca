#!/usr/bin/env python3
"""
sambuca :: the Steward's audit log.

WHAT THE CATALOGUE ALREADY PROMISED. Four verbs return a secret — an enrolment
link, a reset link, a device key — and verbs.yml says of them, in as many words,
that "the audit log records that a reset link was issued, for whom, and when —
not the link itself". It also lists "The audit log" among the subjects the
Steward may never reach.

Both were true statements about a file that did not exist.

═══════════════════════════════════════════════════════════════════════════
THE SECRET CANNOT BE LOGGED BECAUSE IT CANNOT BE PASSED.

record() takes a PLAN and an outcome. There is no parameter for the value a
verb returned, so there is no call that writes one — not by mistake, not under
deadline, not by somebody adding "just for debugging". A rule enforced by a
signature needs no discipline to hold.

That is the difference between this and a redaction pass. Redaction is a filter
somebody has to remember to apply, and it fails the first time a secret arrives
in a shape the pattern did not anticipate — which is how a key with its value on
the next line got published once already in this project's history.

APPEND-ONLY, AND CHAINED. Each entry carries the hash of the one before it, so
removing or editing an entry breaks every link after it. This does not prevent
tampering — root can do anything — it makes tampering VISIBLE, which is the
honest thing an on-device log can offer.
═══════════════════════════════════════════════════════════════════════════

Usage:
    steward-audit.py record  <plan.json> --outcome applied --actor owner
    steward-audit.py verify  [--log PATH]
    steward-audit.py show    [--log PATH] [-n 20]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

DEFAULT_LOG = pathlib.Path(
    os.environ.get("SAMBUCA_AUDIT_LOG", "/var/log/sambuca/steward-audit.jsonl"))

GENESIS = "0" * 64

# What an entry may contain. An allowlist, for the same reason the beacon has
# one: a field added to a plan later must not start being written here because
# nobody remembered to exclude it.
ENTRY_FIELDS = ("ts", "actor", "verb", "params", "blast_radius", "confirm",
                "outcome", "returned_secret", "prev")


def _digest(entry: dict) -> str:
    """The hash of an entry, over its canonical form.

    sort_keys matters: two dicts with the same content and different insertion
    order must hash the same, or the chain breaks on a re-serialisation that
    changed nothing.
    """
    return hashlib.sha256(
        json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _read(log: pathlib.Path) -> list[dict]:
    if not log.is_file():
        return []
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def record(plan: dict, *, outcome: str, actor: str,
           log: pathlib.Path = DEFAULT_LOG, now: str | None = None) -> dict:
    """Append one entry. THERE IS NO PARAMETER FOR THE SECRET.

    A verb that returns one records THAT it did — `returned_secret: true` — and
    the log says who asked, for what, and when. The value itself never arrives
    here, so it can never leave here.
    """
    entries = _read(log)
    prev = _digest(entries[-1]) if entries else GENESIS

    entry = {
        "ts": now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actor": str(actor),
        "verb": str(plan.get("verb", "")),
        # The plan's params are already typed and bounded by the gate. They
        # describe WHO or WHAT was operated on, which is the point of an audit
        # log — "a reset link was issued" is useless without "for whom".
        "params": plan.get("params") or {},
        "blast_radius": plan.get("blast_radius"),
        "confirm": plan.get("confirm"),
        "outcome": str(outcome),
        "returned_secret": bool(plan.get("returns_secret")),
        "prev": prev,
    }
    entry = {k: entry[k] for k in ENTRY_FIELDS if k in entry}

    log.parent.mkdir(parents=True, exist_ok=True)
    # APPEND. Never "r+", never a rewrite: a writer that can seek is a writer
    # that can edit history, and this file's whole value is that it cannot.
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
    try:
        os.chmod(log, 0o640)          # root:adm, like the provisioning logs
    except OSError:                   # pragma: no cover - not fatal
        pass
    return entry


def verify(log: pathlib.Path = DEFAULT_LOG) -> tuple[bool, str]:
    """Walk the chain. Returns (intact, what-is-wrong)."""
    entries = _read(log)
    if not entries:
        return True, "the log is empty"
    expected = GENESIS
    for i, entry in enumerate(entries):
        if entry.get("prev") != expected:
            return False, (
                f"entry {i + 1} does not follow the one before it — an entry "
                f"has been removed, reordered or edited")
        expected = _digest(entry)
    return True, f"{len(entries)} entries, chain intact"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The Steward's audit log.")
    ap.add_argument("command", choices=("record", "verify", "show"))
    ap.add_argument("plan", nargs="?", help="path to a plan, or - for stdin")
    ap.add_argument("--outcome", default="applied")
    ap.add_argument("--actor", default="unknown")
    ap.add_argument("--log", type=pathlib.Path, default=DEFAULT_LOG)
    ap.add_argument("-n", type=int, default=20)
    args = ap.parse_args(argv)

    if args.command == "record":
        if not args.plan:
            print("steward-audit: record needs a plan", file=sys.stderr)
            return 2
        raw = sys.stdin.read() if args.plan == "-" else \
            pathlib.Path(args.plan).read_text(encoding="utf-8")
        entry = record(json.loads(raw), outcome=args.outcome,
                       actor=args.actor, log=args.log)
        print(json.dumps(entry, sort_keys=True))
        return 0

    if args.command == "verify":
        ok, detail = verify(args.log)
        print(detail)
        return 0 if ok else 1

    for entry in _read(args.log)[-args.n:]:
        secret = " (issued a secret)" if entry.get("returned_secret") else ""
        print(f"{entry.get('ts')}  {entry.get('actor')}  "
              f"{entry.get('verb')}  {entry.get('outcome')}{secret}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
