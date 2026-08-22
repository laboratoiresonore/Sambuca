"""The Steward's gate: what a proposal may and may not become.

A language model proposes an operation; this decides whether that becomes one.
It is the half that must be right regardless of which model is behind it, or how
confused that model is — and the proposal is UNTRUSTED INPUT, not because the
model is malicious but because the text it read might be: an email, a filename,
a document, a page in a summary.

Prompt injection cannot be prevented at the language layer, so it is not
defended there. It is defended here, by the shape of what is possible:

  * a verb not in the catalogue does not exist, with no fuzzy match to reach for
  * parameters are typed and bounded FROM THE CATALOGUE, not from the proposal
  * the confirmation sentence carries the real values, so approving it means
    something

The refusals are tested harder than the successes. A gate that only proves it
opens is not a gate.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = REPO / "engine" / "steward" / "steward-resolve.py"


def _load():
    spec = importlib.util.spec_from_file_location("steward_resolve", TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SR = _load()
CAT = SR.load_catalogue()


# ------------------------------------------------------ it cannot be widened


@pytest.mark.parametrize("invented", [
    "system.delete_everything",
    "user.remove_all",
    "steward.edit_catalogue",
    "shell",
    "user.invite ; rm -rf /",
    "USER.INVITE",                 # case is not a near-enough match
    "user.invit",                  # nor is a typo
])
def test_a_verb_outside_the_catalogue_does_not_exist(invented: str) -> None:
    """THE PROPERTY THE WHOLE DESIGN RESTS ON.

    Injected text can at worst cause an EXISTING verb to be proposed — which is
    what the confirmation sentence is for. It must never be able to invent one,
    and there must be no fuzzy match to fall into: suggesting the nearest verb
    to an invented one is how "delete_everything" becomes "user.remove".
    """
    with pytest.raises(SR.Refused) as exc:
        SR.resolve(CAT, {"verb": invented, "params": {}})
    assert "not an operation" in str(exc.value)


def test_an_extra_parameter_is_refused_not_ignored() -> None:
    """An unknown parameter is a proposal reaching past the catalogue. Dropping
    it silently would let the model believe it had been obeyed."""
    with pytest.raises(SR.Refused) as exc:
        SR.resolve(CAT, {"verb": "user.invite", "params": {
            "display_name": "Priya", "role": "member", "sudo": True}})
    assert "does not take" in str(exc.value)
    assert "sudo" in str(exc.value)


def test_an_enum_cannot_be_talked_out_of_its_values() -> None:
    with pytest.raises(SR.Refused) as exc:
        SR.resolve(CAT, {"verb": "user.invite", "params": {
            "display_name": "Priya", "role": "root"}})
    assert "must be one of" in str(exc.value)


def test_a_string_longer_than_its_limit_is_refused_not_truncated() -> None:
    """Truncating would hand the implementation something the human never saw."""
    with pytest.raises(SR.Refused) as exc:
        SR.resolve(CAT, {"verb": "user.invite", "params": {
            "display_name": "x" * 5000, "role": "member"}})
    assert "the limit is" in str(exc.value)


def test_a_missing_required_parameter_asks_rather_than_guessing() -> None:
    with pytest.raises(SR.Refused) as exc:
        SR.resolve(CAT, {"verb": "user.invite", "params": {}})
    assert "needs display_name" in str(exc.value)


@pytest.mark.parametrize("bad", [None, "", 123, [], {"params": {}}])
def test_a_proposal_with_no_verb_is_refused(bad) -> None:
    with pytest.raises(SR.Refused):
        SR.resolve(CAT, {"verb": bad, "params": {}})


# ------------------------------------------------------- defence in depth


def test_a_disruptive_verb_with_no_confirmation_is_refused_at_RUNTIME() -> None:
    """steward-lint refuses this at commit time. This refuses it again here.

    A catalogue edited on the appliance, or one that slipped past the linter,
    must still be unable to execute a disruptive verb unattended. The gate does
    not assume its own rules were checked earlier.
    """
    poisoned = dict(CAT)
    poisoned["user.remove"] = {**CAT["user.remove"],
                               "blast_radius": "disruptive", "confirm": "none"}
    with pytest.raises(SR.Refused) as exc:
        SR.resolve(poisoned, {"verb": "user.remove",
                              "params": {"user": "priya"}})
    assert "contradicts itself" in str(exc.value)


def test_a_string_parameter_with_no_limit_is_refused() -> None:
    """Unbounded model output must not reach an implementation because a
    catalogue edit dropped a max_length."""
    poisoned = dict(CAT)
    v = json.loads(json.dumps(CAT["user.invite"]))     # deep copy
    for p in v["params"]:
        p.pop("max_length", None)
    poisoned["user.invite"] = v
    with pytest.raises(SR.Refused) as exc:
        SR.resolve(poisoned, {"verb": "user.invite",
                              "params": {"display_name": "Priya",
                                         "role": "member"}})
    assert "no declared length limit" in str(exc.value)


# ------------------------------------------------- what a human is asked


def test_the_sentence_names_the_actual_values() -> None:
    """"Remove the account for Priya Sharma", not "remove a user".

    A human approving a sentence that hides what it operates on has not
    approved anything.
    """
    plan = SR.resolve(CAT, {"verb": "user.invite", "params": {
        "display_name": "Priya Sharma", "role": "admin"}})
    assert "Priya Sharma" in plan["sentence"]
    assert "admin" in plan["sentence"]


def test_the_plan_carries_the_confirmation_requirement() -> None:
    """The caller must not have to look the verb up again to find out whether a
    human has to agree — that lookup is where it gets skipped."""
    plan = SR.resolve(CAT, {"verb": "user.invite", "params": {
        "display_name": "Priya", "role": "member"}})
    assert plan["confirm"] in {"none", "standard", "strong"}
    assert plan["blast_radius"] in {"additive", "reversible", "disruptive"}
    assert "sentence" in plan


def test_it_returns_a_plan_and_never_executes() -> None:
    """CLAUDE.md: an AI with privileges picks a lever; it never has hands.

    A resolver that could also run the verb would be the hands. Read the source
    rather than trusting it stayed that way — this is the property that would be
    quietly lost by somebody adding a convenience.
    """
    src = TOOL.read_text(encoding="utf-8")
    body = src.split('"""', 2)[-1]              # skip the module docstring
    for forbidden in ("subprocess", "os.system", "os.exec", "eval(", "exec("):
        assert forbidden not in body, (
            f"the gate references {forbidden!r} — it must return a plan, not "
            f"act on one")


# --------------------------------------------------------------- the CLI


def test_the_cli_separates_refusal_from_a_broken_catalogue() -> None:
    """Exit 1 is "I will not"; exit 2 is "I cannot read my own rules".

    Collapsing them would let a broken catalogue read as a safe refusal — the
    quietest possible way for the gate to stop being a gate.
    """
    ok = subprocess.run(
        [sys.executable, str(TOOL),
         '{"verb":"user.invite","params":{"display_name":"P","role":"member"}}'],
        capture_output=True, text=True, timeout=60, check=False)
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["verb"] == "user.invite"

    refused = subprocess.run(
        [sys.executable, str(TOOL), '{"verb":"nope","params":{}}'],
        capture_output=True, text=True, timeout=60, check=False)
    assert refused.returncode == 1
    assert "refused:" in refused.stderr

    broken = subprocess.run(
        [sys.executable, str(TOOL), '{"verb":"user.invite"}',
         "--catalogue", str(REPO / "README.md")],
        capture_output=True, text=True, timeout=60, check=False)
    assert broken.returncode == 2, "an unreadable catalogue must not exit 1"
