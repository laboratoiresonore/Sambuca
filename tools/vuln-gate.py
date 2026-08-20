#!/usr/bin/env python3
"""
sambuca :: tools/vuln-gate.py

Decide whether a vulnerability scan should fail the build.

══════════════════════════════════════════════════════════════════════════════
A CORRECTION TO AN EARLIER ASSUMPTION

The scan was originally gated on "any fixable HIGH/CRITICAL", on the reasoning
that fixable means an upstream patch exists, so bumping the pin is a real
remedy. Running it proved that wrong.

"Fixable" means the PACKAGE has a patch. It does not mean the image PUBLISHER
has rebuilt. Once we are already on the newest published tag, a fixable CVE in
that image is not actionable by us at all — there is nothing to bump to. Gating
on it produces a job that is red every single day, and a job that is red every
day is a job everybody clicks past. That is worse than no job, because it also
consumes the attention a real regression would need.

So the gate measures REGRESSION, not absolute state:

    fail if an image got worse than its recorded baseline
    fail if a newly added image arrives carrying criticals
    never fail for a finding we have already looked at and cannot act on

The baseline is committed, so every change to it appears in a diff and someone
has to consciously accept it. Improvements are reported loudly and the baseline
is NOT auto-lowered — tightening it is a deliberate act via `make vuln-baseline`,
because a baseline that silently follows reality downward can also silently
follow it upward.
══════════════════════════════════════════════════════════════════════════════

Usage:
    vuln-gate.py <scan-dir> [--baseline PATH] [--update]

Exit:
    0  no regression
    1  an image got worse, or a new image arrived with criticals
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPO / "tools" / "vuln-baseline.json"


def repo_key(name: str) -> str:
    """Collapse a report filename to the image REPOSITORY, dropping the tag.

    The baseline has to survive a version bump, or every upgrade would look
    like a brand-new image and fail the gate for the wrong reason.
    """
    n = re.sub(r"\.txt$|\.json$", "", name)
    n = re.sub(r"_v?\d[\d.]*(-[a-z0-9]+)?$", "", n)
    return n


def read_scan(scan_dir: Path) -> dict[str, dict[str, int]]:
    """Read Trivy JSON reports into {repo: {high, critical}}."""
    found: dict[str, dict[str, int]] = {}
    for f in sorted(scan_dir.glob("*.json")):
        # An unparseable report is a scanner problem, not a vulnerability, and
        # must not be counted as either a finding or a clean result.
        data = None
        with contextlib.suppress(Exception):
            data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        if data is None:
            continue
        high = crit = 0
        for result in data.get("Results") or []:
            for v in result.get("Vulnerabilities") or []:
                sev = (v.get("Severity") or "").upper()
                if sev == "CRITICAL":
                    crit += 1
                elif sev == "HIGH":
                    high += 1
        found[repo_key(f.name)] = {"high": high, "critical": crit}
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scan_dir", type=Path)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--update", action="store_true",
                    help="rewrite the baseline from this scan (a deliberate act)")
    args = ap.parse_args()

    current = read_scan(args.scan_dir)
    if not current:
        print("no parseable scan reports found", file=sys.stderr)
        return 1

    baseline: dict[str, dict[str, int]] = {}
    if args.baseline.is_file():
        baseline = json.loads(args.baseline.read_text(encoding="utf-8")).get("images", {})

    if args.update:
        args.baseline.write_text(json.dumps(
            {
                "_comment": "Recorded vulnerability floor per image REPOSITORY. The gate "
                            "fails on regression against these numbers, not on their "
                            "absolute value — see tools/vuln-gate.py. Regenerate "
                            "deliberately with `make vuln-baseline` and review the diff.",
                "images": dict(sorted(current.items())),
            }, indent=2) + "\n", encoding="utf-8")
        print(f"baseline written: {args.baseline} ({len(current)} images)")
        return 0

    regressions: list[str] = []
    improvements: list[str] = []
    newcomers: list[str] = []

    for repo, counts in sorted(current.items()):
        base = baseline.get(repo)
        c, h = counts["critical"], counts["high"]
        if base is None:
            status = "NEW"
            if c > 0:
                # A new image arriving with criticals is a decision, not a drift.
                newcomers.append(f"{repo}: new image with {c} critical")
        elif c > base["critical"]:
            status = "WORSE"
            regressions.append(f"{repo}: critical {base['critical']} -> {c}")
        elif c < base["critical"]:
            status = "better"
            improvements.append(f"{repo}: critical {base['critical']} -> {c}")
        else:
            status = "same"
        print(f"  {status:<8} {repo:<46} high={h:<4} critical={c}")

    print()
    if improvements:
        print("IMPROVED since the baseline:")
        for i in improvements:
            print(f"  {i}")
        print("  Tighten the floor deliberately:  make vuln-baseline")
        print()

    if regressions or newcomers:
        print("REGRESSION — this is actionable:")
        for r in regressions + newcomers:
            print(f"  {r}")
        print()
        print("  An image got worse than the floor we accepted. Either a newer")
        print("  tag exists (bump it), or the publisher shipped a worse build")
        print("  (hold the old pin). Do not raise the baseline to make this pass")
        print("  without understanding which.")
        return 1

    print("No regression. Absolute counts are reported, not gated — see the")
    print("correction at the top of tools/vuln-gate.py for why.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
