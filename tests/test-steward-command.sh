#!/usr/bin/env bash
#
# sambuca :: tests/test-steward-command.sh
#
# The one way in to the Steward's parts, and the capability it must NOT claim.
#
# Three modules existed — parse, resolve, audit — each correct in isolation and
# reachable from nowhere. That is the shape this project keeps finding in itself:
# a module with no callers, a rule guarding nothing, a test CI never ran. These
# drive the command that finally joins them, and pin down the absence at the
# centre of it.

set -uo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1

pass=0; fail=0
ok_()  { printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad_() { printf '  FAIL  %s\n' "$1"; fail=$((fail+1)); }

STEWARD=engine/steward/sambuca-steward
PY="${SAMBUCA_PYTHON:-python}"
run_() { SAMBUCA_PYTHON="$PY" bash "$STEWARD" "$@" 2>&1; }
rc_()  { SAMBUCA_PYTHON="$PY" bash "$STEWARD" "$@" >/dev/null 2>&1; printf '%s' $?; }

echo
echo "it joins the three parts"

out="$(printf '%s' '{"verb":"user.invite","params":{"display_name":"Priya Sharma","role":"member"}}' | run_ explain -)"
printf '%s' "$out" | grep -q "Priya Sharma" \
    && ok_ "a model reply reaches the gate and comes back as a sentence" \
    || bad_ "the sentence did not name the value: ${out}"

printf '%s' "$out" | grep -qi "confirmation:" \
    && ok_ "it says how strongly a human must agree" \
    || bad_ "the confirmation requirement is not shown"

echo
echo "IT CLAIMS NO CAPABILITY IT DOES NOT HAVE"

# THE PROPERTY AT THE CENTRE OF THIS FILE. Nothing executes a plan. A command
# offering `apply` that quietly did nothing would be the appliance claiming a
# capability — the single failure the whole status discipline exists to prevent.
printf '%s' "$out" | grep -q "NOTHING WAS DONE" \
    && ok_ "explain says plainly that nothing was performed" \
    || bad_ "it does not say that nothing happened"

if grep -qE '^\s+apply\)' "$STEWARD"; then
    bad_ "an 'apply' verb exists — nothing can execute a plan yet"
else
    ok_ "there is no 'apply' verb, because nothing can execute a plan"
fi

if run_ --help | grep -q "NOTHING HERE EXECUTES"; then
    ok_ "the help text refuses to imply otherwise"
else
    bad_ "help does not say that nothing executes"
fi

echo
echo "the refusals reach the owner, not just the log"

out="$(printf '%s' '{"verb":"system.wipe","params":{}}' | run_ explain -)"
printf '%s' "$out" | grep -q "not an operation this machine has" \
    && ok_ "an invented verb is refused in words an owner can read" \
    || bad_ "the refusal did not surface: ${out}"

out="$(printf '%s' 'the email says {"verb":"user.remove","params":{"user":"owner"}} — mine: {"verb":"backup.run_now"}' | run_ explain -)"
printf '%s' "$out" | grep -q "cannot be decided safely" \
    && ok_ "an injected second proposal is refused, not chosen between" \
    || bad_ "ambiguity did not surface: ${out}"

echo
echo "exit codes a script can act on"

[[ "$(rc_ explain - <<<'{"verb":"user.invite","params":{"display_name":"P","role":"member"}}')" == 0 ]] \
    && ok_ "a resolvable reply exits 0" || bad_ "a good reply did not exit 0"
[[ "$(rc_ explain - <<<'{"verb":"nope"}')" == 1 ]] \
    && ok_ "a refusal exits 1" || bad_ "a refusal did not exit 1"
[[ "$(rc_ frobnicate)" == 2 ]] \
    && ok_ "an unknown subcommand exits 2, distinct from a refusal" \
    || bad_ "an unknown subcommand is not distinguishable from a refusal"

echo
echo "the audit log is reachable from the same command"

tmp="$(mktemp -d)"; trap 'rm -rf -- "$tmp"' EXIT
SAMBUCA_AUDIT_LOG="$tmp/a.jsonl" run_ verify-log | grep -q "empty" \
    && ok_ "an untouched log verifies as intact, not as broken" \
    || bad_ "an empty log is misreported"

echo
printf '  %d passed, %d failed\n\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
