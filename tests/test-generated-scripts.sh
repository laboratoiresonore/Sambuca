#!/usr/bin/env bash
#
# sambuca :: tests/test-generated-scripts.sh
#
# TWO BLIND SPOTS, ONE CAUSE: text that becomes code later is not checked now.
#
# 1. A SCRIPT INSIDE A HEREDOC IS A STRING TO EVERY LINTER.
#
#    80-identity.sh writes /usr/local/bin/sambuca-identity out of a quoted
#    heredoc. shellcheck reads the generating file, sees a string, and says
#    nothing about its contents — so the one command an owner runs to finish
#    the single attended step of the install has never been linted, in CI or in
#    preflight, by anything.
#
# 2. A CONTAINER NAME WRITTEN AS A LITERAL IS A GUESS.
#
#    That heredoc told the owner `docker logs pocket-id`. The container is
#    called `sambuca-pocket-id`. The three uses in the GENERATING script all
#    went through "$POCKET_ID_CONTAINER" and were right; the copy that got
#    pasted into the heredoc lost the variable and nobody could see it, because
#    of blind spot 1. The instruction printed "No such container" at precisely
#    the moment somebody was following instructions.
#
# So: extract what will become a script and check it as one, and tie every
# literal container name to what compose actually names.

set -uo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1

pass=0; fail=0; skip=0
ok_()   { printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad_()  { printf '  FAIL  %s\n' "$1"; fail=$((fail+1)); }
skip_() { printf '  skip  %s\n' "$1"; skip=$((skip+1)); }

WORK="$(mktemp -d)"; trap 'rm -rf -- "$WORK"' EXIT

echo
echo "scripts written by other scripts are still scripts"

# A generated SHELL script, not merely a generated file with a shebang.
#
# The first version of this test asked "does it start with #!" and immediately
# flagged the nftables ruleset, which opens `#!/usr/sbin/nft -f` — a shebang,
# not a shell. It then reported the ruleset as broken shell, which it is not.
# That is the same fault this file exists to catch, committed inside the file
# itself on the first try: a check that matches the general shape of a thing
# instead of the specific thing meant. Match the interpreter, not the marker.
found=0
while IFS= read -r hit; do
    file="${hit%%:*}"
    tag="$(printf '%s' "$hit" | sed -E "s/.*cat <<'([A-Z]+)'.*/\1/")"
    body="${WORK}/$(basename "$file").${tag}.sh"
    sed -n "/cat <<'${tag}'/,/^${tag}\$/p" "$file" | sed '1d;$d' >"$body"
    head -1 "$body" 2>/dev/null \
        | grep -qE '^#!.*/(env +)?(ba|da|k|a)?sh( |$)' || continue
    found=$((found + 1))

    if bash -n "$body" 2>"${WORK}/err"; then
        ok_ "${file} :: ${tag} — the generated script parses"
    else
        bad_ "${file} :: ${tag} — generated script is not valid shell: $(head -2 "${WORK}/err")"
    fi

    if command -v shellcheck >/dev/null 2>&1; then
        if shellcheck --severity=warning "$body" >"${WORK}/sc" 2>&1; then
            ok_ "${file} :: ${tag} — shellcheck clean"
        else
            bad_ "${file} :: ${tag} — shellcheck: $(head -4 "${WORK}/sc")"
        fi
    else
        skip_ "shellcheck not installed — the generated script was NOT linted"
    fi
done < <(grep -rn "cat <<'[A-Z]*'" engine/ --include='*.sh')

# If the extraction stops finding anything, every check above silently becomes
# zero checks — the failure mode this repository keeps rediscovering.
[[ $found -ge 1 ]] \
    && ok_ "found ${found} generated script(s) to check" \
    || bad_ "no generated scripts found — the extraction has stopped working"

echo
echo "a container name in an instruction must be a container that exists"

names="$(grep -rhoE 'container_name: *[a-z0-9-]+' compose/*.yml | awk '{print $2}' | sort -u)"
[[ -n $names ]] \
    && ok_ "compose still declares container names this test can read" \
    || bad_ "no container_name found in compose — this check is now vacuous"

bad_names=0
while IFS= read -r hit; do
    [[ -z $hit ]] && continue
    ref="$(printf '%s' "$hit" | sed -E 's/.*docker (logs|exec|inspect|restart) //')"
    if printf '%s\n' "$names" | grep -qx "$ref"; then
        continue
    fi
    bad_ "${hit%%:*} names a container that does not exist: '${ref}'"
    bad_names=$((bad_names + 1))
    # Almost always the same mistake: the sambuca- prefix dropped.
    printf '%s\n' "$names" | grep -q "^sambuca-${ref}\$" \
        && printf '        (did you mean sambuca-%s ?)\n' "$ref"
done < <(grep -rnoE 'docker (logs|exec|inspect|restart) [a-z][a-z0-9-]+' \
             engine/ --include='*.sh' | grep -v '\$')

[[ $bad_names -eq 0 ]] && ok_ "every literal container name matches compose"

echo
printf '  %d passed, %d failed, %d skipped\n\n' "$pass" "$fail" "$skip"
[[ $fail -eq 0 ]]
