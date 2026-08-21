"""The Steward may not act on the Steward.

CLAUDE.md states this as settled: "Nothing may edit its own guard. The Steward
cannot change its catalogue, its privileges, or the audit log. Enforced by
tools/steward-lint.py in CI, not by good intentions."

IT WAS THE GOOD INTENTIONS. The catalogue's own `excluded:` list names exactly
the right subjects — "This verb catalogue, and the Steward's own privileges",
"The audit log" — and the lint checked only that the list EXISTED. Nothing ever
checked that the verbs respected it. A verb named `steward.edit_catalogue`,
taking a 100,000-character string and rewriting verbs.yml, with `confirm: none`,
passed as "18 verbs clean". Verified by adding it, not inferred from reading.

The catalogue is the only thing standing between "speak to your server" and a
language model with a shell. Several verbs are safe to expose ONLY because the
catalogue bounding them cannot be rewritten from inside it.

THE FALSE-POSITIVE CASE IS TESTED AS HARD AS THE REST. The first version of this
check matched any mention of "audit log" and failed two real verbs whose notes
say the log RECORDS that a reset link was issued — which is the behaviour we
want. Mentioning a guarded subject is fine; acting on it is not. That "appears
anywhere" fault has miscalibrated an audit in this project every time it has
been written carelessly, including by me, twice today.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "steward-lint.py"


def _lint():
    spec = importlib.util.spec_from_file_location("steward_lint", TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SL = _lint()


def _verb(**over) -> dict:
    v = {
        "name": "thing.do",
        "summary": "Do a thing",
        "speech_examples": ["do the thing"],
        "blast_radius": "additive",
        "confirm": "none",
    }
    v.update(over)
    return v


def _doc(*verbs) -> dict:
    return {
        "version": 1,
        "verbs": list(verbs),
        "excluded": [
            {"subject": "This verb catalogue, and the Steward's own privileges",
             "reason": "a guard the guarded can edit is not a guard"},
            {"subject": "The audit log", "reason": "tamper-evidence"},
        ],
    }


def test_the_shipped_catalogue_is_clean() -> None:
    """The rule must not fire on the real thing — that is how a check gets
    switched off rather than fixed."""
    import yaml
    doc = yaml.safe_load(TOOL.parent.parent.joinpath(
        "engine/steward/verbs.yml").read_text(encoding="utf-8"))
    assert SL.lint(doc) == []


@pytest.mark.parametrize("name", ["steward.edit_catalogue", "catalogue.write",
                                  "audit.clear", "guard.disable"])
def test_verbs_may_not_be_in_the_stewards_own_namespace(name: str) -> None:
    findings = SL.lint(_doc(_verb(name=name)))
    assert any("Steward itself" in f for f in findings), findings


def test_a_verb_that_names_the_catalogue_file_is_refused() -> None:
    """The exact verb that passed as clean before this rule existed."""
    findings = SL.lint(_doc(_verb(
        name="config.write",
        summary="Update configuration",
        notes="Rewrites engine/steward/verbs.yml with the supplied content.",
    )))
    assert any("out of reach" in f for f in findings), findings


def test_acting_on_the_audit_log_is_refused() -> None:
    findings = SL.lint(_doc(_verb(
        name="log.tidy",
        summary="Tidy up logs",
        notes="Clears the audit log once it grows beyond a few megabytes.",
    )))
    assert any("audit log" in f for f in findings), findings


def test_merely_RECORDING_in_the_audit_log_is_fine() -> None:
    """THE FALSE POSITIVE THAT BROKE THIS CHECK'S FIRST VERSION.

    Two shipped verbs describe what the audit log records instead of the secret
    they return. That is the property we want, and a check that fails it would
    be deleted within a day.
    """
    findings = SL.lint(_doc(_verb(
        name="user.reset_access",
        summary="Send someone a new way in",
        returns_secret=True,
        notes=("The audit log records that a reset link was issued, for whom, "
               "and when — not the link itself."),
    )))
    assert findings == [], findings


def test_the_check_is_not_vacuous() -> None:
    """A rule that cannot fire is indistinguishable from one that passes."""
    clean = SL.lint(_doc(_verb()))
    assert clean == [], clean
    dirty = SL.lint(_doc(_verb(name="steward.anything")))
    assert dirty, "the namespace rule never fires — it is decoration"
