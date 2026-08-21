#!/usr/bin/env bash
#
# sambuca :: tests/test-health.sh
#
# The maintenance jobs already knew when they had failed. They said so with
# err(), into a journal NOBODY READS ON AN APPLIANCE — so a backup could stop
# working for months and look exactly like one that works.
#
# These drive the mechanism that fixes that. The refusal-to-nag properties are
# tested as hard as the reporting ones: an alarm that outlives its problem, or
# a banner that always says something, is how people learn to ignore both.

set -uo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1

pass=0; fail=0
ok_()  { printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad_() { printf '  FAIL  %s\n' "$1"; fail=$((fail+1)); }

export SB_HEALTH_DIR
SB_HEALTH_DIR="$(mktemp -d)"
trap 'rm -rf -- "$SB_HEALTH_DIR"' EXIT

# shellcheck source=engine/lib/common.sh
SB_QUIET=1 source engine/lib/common.sh

echo
echo "health state"

# --- 1. a healthy machine says NOTHING -------------------------------------
out="$(bash engine/maintenance/health.sh --brief 2>&1)"
rc=$?
[[ -z $out ]] && ok_ "a healthy machine prints nothing at login" \
              || bad_ "it printed something when nothing is wrong: ${out}"
((rc == 0)) && ok_ "and exits 0" || bad_ "exit was $rc"

# --- 2. a failure is recorded and surfaced ---------------------------------
sb_health_set backup fail "snapshot held only 4 paths"
out="$(bash engine/maintenance/health.sh --brief 2>&1)"; rc=$?
printf '%s' "$out" | grep -q "backup" && ok_ "a failure appears at login" \
                                      || bad_ "a failure did not appear"
((rc == 2)) && ok_ "fail exits 2, so a script can act on it" || bad_ "fail exit was $rc"

# --- 3. success CLEARS it ---------------------------------------------------
# An alarm that outlives its problem teaches people to ignore alarms, and then
# the one that matters is ignored too.
sb_health_set backup ok
out="$(bash engine/maintenance/health.sh --brief 2>&1)"
[[ -z $out ]] && ok_ "a later success clears the alarm" \
              || bad_ "the alarm survived its own fix: ${out}"

# --- 4. warn and fail are distinguishable ----------------------------------
sb_health_set snapraid warn "scrub found problems"
bash engine/maintenance/health.sh >/dev/null 2>&1
(($? == 1)) && ok_ "warn exits 1, distinct from fail" || bad_ "warn did not exit 1"

sb_health_set snapraid fail "parity sync aborted"
bash engine/maintenance/health.sh >/dev/null 2>&1
(($? == 2)) && ok_ "fail outranks warn" || bad_ "fail did not outrank warn"
sb_health_set snapraid ok

# --- 5. several components at once ------------------------------------------
sb_health_set backup warn "incomplete"
sb_health_set gitops warn "an update is HELD for review"
out="$(bash engine/maintenance/health.sh --brief 2>&1)"
if printf '%s' "$out" | grep -q backup && printf '%s' "$out" | grep -q gitops; then
    ok_ "every outstanding component is listed"
else
    bad_ "components were lost: ${out}"
fi
sb_health_set backup ok; sb_health_set gitops ok

# --- 6. it never blows up on a corrupt state file ---------------------------
# It runs at EVERY login. A crash here would meet somebody at the door.
printf 'not-a-valid-state\n' >"${SB_HEALTH_DIR}/weird"
bash engine/maintenance/health.sh --brief >/dev/null 2>&1
(($? <= 2)) && ok_ "a malformed state file does not crash the banner" \
            || bad_ "it crashed on a malformed file"
rm -f "${SB_HEALTH_DIR}/weird"

echo
echo "every maintenance job reports its verdict"

# --- 7. all three are wired -------------------------------------------------
# backup.sh knew about restic exit 3 and the 17-of-966 readback failure long
# before anything surfaced them. Being careful in private is not the same as
# telling somebody.
for job in backup snapraid-sync gitops-sync; do
    n="$(grep -c 'sb_health_set' "engine/maintenance/${job}.sh" 2>/dev/null || echo 0)"
    ((n > 0)) && ok_ "${job} records its outcome (${n} call(s))" \
              || bad_ "${job} still fails silently"
done

# --- 8. each one can also CLEAR ---------------------------------------------
# Without an ok path a job would raise an alarm it can never lower.
for job in backup snapraid-sync gitops-sync; do
    grep -qE 'sb_health_set [a-z]+ ok' "engine/maintenance/${job}.sh" \
        && ok_ "${job} clears its own alarm on success" \
        || bad_ "${job} can raise an alarm but never lower it"
done


echo
echo "commands the appliance names actually exist"

# --- 9. every `sambuca-backup <verb>` promised anywhere is real -------------
# backup.sh told owners to run `sambuca-backup init` and the health banner told
# them to run `sambuca-backup verify`, while the script accepted no arguments at
# all. Right binary, nonexistent verb — which test_engine_promises.py cannot
# see, because it only checks that the BINARY is installed.
#
# GENERALISED, because checking backup alone is what let the next one through.
# `sambuca-gitops apply --force` was printed to owners BY THE HELD-UPDATE
# MESSAGE ITSELF as the way to release a held update, while gitops-sync.sh
# accepted no verb and no --force. Following the instruction re-ran the sync,
# hit the same guard, and printed the same instruction: a loop, on the one path
# that exists to stop an appliance drifting years behind on security patches.
#
# So the mapping is read from the symlinks the installer ACTUALLY creates,
# rather than a list kept here that would drift from it.
declare -A SCRIPT_OF=()
declare -A SYMLINKED=()
while read -r target cmd; do
    SCRIPT_OF["${cmd##*/sambuca-}"]="${target#\$INSTALL_ROOT/}"
    SYMLINKED["${cmd##*/sambuca-}"]=1
done < <(grep -oE 'ln -sf "\$INSTALL_ROOT/[^"]+" +/usr/local/bin/sambuca-[a-z-]+' \
             engine/autoinstall/late-command.sh \
         | sed 's/ln -sf "//; s/" */ /')

# Not every command is a symlink. 80-identity.sh WRITES sambuca-identity, verbs
# and all, so the file implementing it is the provision script that emits it.
# Reading only the symlinks reported that command as never installed — a false
# accusation is its own kind of broken test, and would have been "fixed" by
# adding an install line for something already installed.
while read -r file cmd; do
    SCRIPT_OF["${cmd##*sambuca-}"]="$file"
done < <(grep -rl 'sb_atomic_write /usr/local/bin/sambuca-' engine/ \
         | while read -r f; do
               grep -ohE 'sb_atomic_write /usr/local/bin/sambuca-[a-z-]+' "$f" \
               | awk -v f="$f" '{print f, $2}'
           done)

((${#SCRIPT_OF[@]} > 0)) \
    && ok_ "the installed command map was read from late-command.sh (${#SCRIPT_OF[@]} commands)" \
    || bad_ "could not read the command map — every check below is vacuous"

# Commands that are not appliance scripts at all. Named, not silently skipped:
# a list of exclusions nobody can see is how the next one hides.
NOT_APPLIANCE=" flasher synapse-db "

while read -r cmd verb; do
    [[ $NOT_APPLIANCE == *" ${cmd} "* ]] && continue
    script="${SCRIPT_OF[$cmd]:-}"
    if [[ -z $script ]]; then
        bad_ "sambuca-${cmd} is named in engine/ but the installer never creates it"
        continue
    fi
    if grep -qE "^[[:space:]]*(${verb}\)|[a-z|-]*\|${verb}\)|${verb}\|)" "$script" \
       || grep -qE "VERB == \"?${verb}" "$script"; then
        ok_ "sambuca-${cmd} ${verb} is a real verb"
    else
        bad_ "sambuca-${cmd} ${verb} is named but ${script} does not implement it"
    fi
#
# README.md and docs/ are scanned TOO. The flasher's own promise test already
# reads them (apps/flasher/tests/test_promises.py); the engine side did not, so
# a command named only where owners actually read — the README — was checked by
# nothing at all.
done < <(grep -rhoE 'sambuca-[a-z-]+ [a-z][a-z-]+' engine/ README.md docs/ --include='*.sh' --include='*.md' \
         | sed 's/^sambuca-//' \
         | grep -vE ' (the|a|is|to|and|or|will|can|has|for|on|in|at|it|not|from|with|your|this|that|are|was|by)$' \
         | sort -u)

# --- 9b. every installed command can actually RUN ---------------------------
# Nothing in engine/ carries an execute bit in git — every file is committed
# 100644 — so the installer adds them. It used to do that with three directory
# globs (engine/*.sh, engine/maintenance/*.sh, engine/autoinstall/*.sh), and
# engine/image/sambuca-image sits in a fourth directory with no .sh extension.
# It was symlinked into /usr/local/bin by that same script and would have
# answered "Permission denied" to an owner typing a command the appliance had
# just recommended.
#
# The chmod is shebang-driven now, so this checks the property that makes that
# work: anything installed as a command must SAY it is executable.
for cmd in "${!SCRIPT_OF[@]}"; do
    # ONLY THE SYMLINKED ONES. sambuca-identity is not a symlink: 80-identity.sh
    # WRITES /usr/local/bin/sambuca-identity with sb_atomic_write ... 0755, so
    # the installed command carries its own mode and the generator never needs
    # one. Checking it here accused a working command — the same false-accusation
    # trap this file already warns about two checks above.
    [[ -n ${SYMLINKED[$cmd]:-} ]] || continue
    script="${SCRIPT_OF[$cmd]}"
    [[ -f $script ]] || continue
    if [[ "$(head -c 2 "$script" 2>/dev/null)" == '#!' ]]; then
        ok_ "sambuca-${cmd} carries a shebang, so the installer makes it runnable"
    else
        bad_ "sambuca-${cmd} (${script}) has no shebang — it would install unrunnable"
    fi
done

if grep -q 'head -c 2' engine/autoinstall/late-command.sh; then
    ok_ "the installer decides executability by shebang, not by directory"
else
    bad_ "the installer still uses directory globs; a new command elsewhere would"
fi

# --- 10. help works WITHOUT root -------------------------------------------
# Asking somebody to become root to find out what the commands are is a small
# cruelty, at exactly the moment a novice is least sure they are allowed to be
# doing this.
out="$(bash engine/maintenance/backup.sh --help 2>&1)"
if printf '%s' "$out" | grep -q "sambuca-backup verify"; then
    ok_ "help works without root and lists the verbs"
else
    bad_ "help needs privileges or omits the verbs: ${out}"
fi

# --- 11. an unknown verb is refused, not silently treated as a backup -------
# The dangerous default: a typo running a full backup instead of the read-only
# thing somebody meant.
out="$(bash engine/maintenance/backup.sh notaverb 2>&1)"
printf '%s' "$out" | grep -qi "unknown command" \
    && ok_ "an unknown verb is refused by name" \
    || bad_ "an unknown verb was not refused: ${out}"

# --- 12. the same two properties for gitops -------------------------------
# It had BOTH of backup's faults and a third: an unknown verb was not refused,
# it fell through to a full sync. `sambuca-gitops aply --force` (one typo) ran
# a real update against the machine.
out="$(bash engine/maintenance/gitops-sync.sh --help 2>&1)"
if printf '%s' "$out" | grep -q "apply --force"; then
    ok_ "gitops help works without root and names the override"
else
    bad_ "gitops help needs privileges or omits the override: ${out}"
fi

out="$(bash engine/maintenance/gitops-sync.sh notaverb 2>&1)"; rc=$?
if printf '%s' "$out" | grep -qi "unknown command" && ((rc == 2)); then
    ok_ "a mistyped gitops verb is refused, not run as a sync"
else
    bad_ "a mistyped gitops verb was not refused (exit ${rc}): ${out}"
fi

# --- 13. --force must not reach the signature check -------------------------
# The hold is a review decision an owner may override. An unsigned update is
# not a review decision, and no flag on this command may turn it into one.
if grep -qE 'FORCE' engine/maintenance/gitops-sync.sh \
   && ! sed -n '/SAMBUCA_GITOPS_REQUIRE_SIGNED == 1/,/^fi$/p' \
            engine/maintenance/gitops-sync.sh | grep -q 'FORCE'; then
    ok_ "--force overrides the review hold but never the signature check"
else
    bad_ "--force reaches the signature check — an unsigned update could be forced"
fi

echo
printf '  %d passed, %d failed\n\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
