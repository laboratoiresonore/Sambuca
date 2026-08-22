"""The audit log the catalogue already promised.

verbs.yml says of the verbs that return one — an enrolment link, a reset link,
a device key — that "the audit log records that a reset link was issued, for
whom, and when — not the link itself". It also lists "The audit log" among the
subjects the Steward may never reach.

Both were true statements about a file that did not exist.

THE PROPERTY THAT MATTERS MOST is that the secret cannot be logged BECAUSE IT
CANNOT BE PASSED. record() has no parameter for it. That is not the same as
redacting: redaction is a filter somebody must remember to apply, and it fails
the first time a secret arrives in a shape the pattern did not anticipate —
which is how a key with its value on the next line got published once already.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = REPO / "engine" / "steward" / "steward-audit.py"


def _load():
    spec = importlib.util.spec_from_file_location("steward_audit", TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SA = _load()

PLAN = {
    "verb": "user.reset_access",
    "params": {"user": "priya"},
    "blast_radius": "reversible",
    "confirm": "standard",
    "returns_secret": True,
    "sentence": "Send someone a new way in (user: priya)",
}


# ------------------------------------------------- the secret cannot be logged


def test_there_is_no_parameter_for_the_secret() -> None:
    """THE WHOLE ARGUMENT, checked mechanically.

    A rule enforced by a signature needs no discipline to hold. If a `secret`
    or `value` parameter ever appears here, the log becomes a place a secret
    can be written by mistake, under deadline, or by somebody adding it "just
    for debugging".
    """
    params = set(inspect.signature(SA.record).parameters)
    for forbidden in ("secret", "value", "token", "link", "key", "result"):
        assert forbidden not in params, (
            f"record() accepts {forbidden!r} — a secret now has a way in")


def test_a_secret_returning_verb_records_that_it_happened_not_what(tmp_path) -> None:
    log = tmp_path / "audit.jsonl"
    SA.record(PLAN, outcome="applied", actor="owner", log=log)
    entry = json.loads(log.read_text(encoding="utf-8").strip())
    assert entry["returned_secret"] is True
    assert entry["verb"] == "user.reset_access"
    assert entry["params"] == {"user": "priya"}, "for WHOM is the point of it"
    assert entry["ts"].endswith("Z"), "and WHEN"


def test_a_stray_secret_on_the_plan_is_not_written(tmp_path) -> None:
    """An allowlist, for the same reason the beacon has one: a field added to a
    plan later must not start being written here because nobody excluded it."""
    log = tmp_path / "audit.jsonl"
    SA.record({**PLAN, "secret": "sk-live-DO-NOT-LOG-ME",
               "enrolment_link": "https://example.invalid/x/DO-NOT-LOG"},
              outcome="applied", actor="owner", log=log)
    text = log.read_text(encoding="utf-8")
    assert "DO-NOT-LOG" not in text, "a plan field leaked into the log"
    assert set(json.loads(text.strip())) <= set(SA.ENTRY_FIELDS)


def test_the_writer_only_ever_appends() -> None:
    """A writer that can seek is a writer that can edit history."""
    src = TOOL.read_text(encoding="utf-8")
    body = src.split('"""', 2)[-1]
    assert 'open(log, "a"' in body, "the log is not opened in append mode"
    for forbidden in ('open(log, "w"', 'open(log, "r+"', ".seek(", ".truncate("):
        assert forbidden not in body, f"the writer can {forbidden!r}"


# --------------------------------------------------------- tamper evidence


def test_the_chain_links_each_entry_to_the_one_before(tmp_path) -> None:
    log = tmp_path / "audit.jsonl"
    for i in range(3):
        SA.record({**PLAN, "params": {"user": f"person{i}"}},
                  outcome="applied", actor="owner", log=log)
    ok, detail = SA.verify(log)
    assert ok, detail
    assert "3 entries" in detail


@pytest.mark.parametrize("attack", ["delete", "edit", "reorder"])
def test_tampering_is_visible(tmp_path, attack: str) -> None:
    """It cannot PREVENT tampering — root can do anything — so it makes it
    visible instead. That is the honest thing an on-device log can offer."""
    log = tmp_path / "audit.jsonl"
    for i in range(4):
        SA.record({**PLAN, "params": {"user": f"person{i}"}},
                  outcome="applied", actor="owner", log=log)
    lines = log.read_text(encoding="utf-8").splitlines()

    if attack == "delete":
        del lines[1]
    elif attack == "edit":
        e = json.loads(lines[1])
        e["params"] = {"user": "somebody-else"}
        lines[1] = json.dumps(e, sort_keys=True, separators=(",", ":"))
    else:
        lines[1], lines[2] = lines[2], lines[1]

    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, detail = SA.verify(log)
    assert not ok, f"{attack} went undetected"
    assert "removed, reordered or edited" in detail


def test_an_empty_log_is_intact_not_broken(tmp_path) -> None:
    """Nothing has happened yet is a valid state. Reporting it as tampering
    would teach people to ignore the one that matters."""
    ok, detail = SA.verify(tmp_path / "nothing.jsonl")
    assert ok
    assert "empty" in detail


def test_the_chain_survives_reserialisation(tmp_path) -> None:
    """Two dicts with the same content and different key order must hash the
    same, or the chain breaks on a rewrite that changed nothing."""
    log = tmp_path / "audit.jsonl"
    SA.record(PLAN, outcome="applied", actor="owner", log=log)
    SA.record(PLAN, outcome="applied", actor="owner", log=log)
    entries = [json.loads(ln) for ln in
               log.read_text(encoding="utf-8").splitlines()]
    shuffled = [json.dumps({k: e[k] for k in reversed(list(e))}) for e in entries]
    log.write_text("\n".join(shuffled) + "\n", encoding="utf-8")
    ok, detail = SA.verify(log)
    assert ok, f"key order alone broke the chain: {detail}"
