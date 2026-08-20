#!/usr/bin/env bash
# sambuca :: sb_atomic_write must fail loudly and leave nothing behind.
#
# WHY THIS EXISTS. Every step in sb_atomic_write was unchecked — cat, chmod and
# mv could all fail and it still returned 0. A profile that never landed
# reported success and the caller carried on as though it had. That is the same
# silent-success shape this project already documents as a failure mode: an
# operation that exits 0 on a failed write hides itself from every exit-code
# check watching it.
#
# The nastiest case was not a failure at all. `mv file dir` does NOT fail — it
# moves the file INTO the directory under its temporary name. So writing a
# profile to a directory path returned 0 and produced a randomly-named file
# nothing would ever read. It was found by writing a test for the failure
# branch and discovering there was not one.

set -uo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "${ROOT}/engine/lib/common.sh" 2>/dev/null

WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT

pass=0
fail=0

check() {
    local label="$1" expected="$2" actual="$3"
    if [[ $expected == "$actual" ]]; then
        printf '  ok    %s\n' "$label"; pass=$((pass + 1))
    else
        printf '  FAIL  %s (expected %s, got %s)\n' "$label" "$expected" "$actual"
        fail=$((fail + 1))
    fi
}

# --- the happy path still works --------------------------------------------
echo "content" | sb_atomic_write "${WORK}/good.txt" 0644 >/dev/null 2>&1
check "writes a file" "0" "$?"
check "content is right" "content" "$(cat "${WORK}/good.txt" 2>/dev/null)"

# --- a directory target must be REFUSED, not silently absorbed -------------
mkdir -p "${WORK}/adir"
echo "content" | sb_atomic_write "${WORK}/adir" 0644 >/dev/null 2>&1
check "refuses a directory target" "1" "$?"
check "nothing was moved into the directory" "0" \
    "$(find "${WORK}/adir" -type f 2>/dev/null | wc -l | tr -d ' ')"

# --- an uncreatable path must fail -----------------------------------------
echo "content" | sb_atomic_write "/proc/definitely/not/here.txt" 0644 >/dev/null 2>&1
check "fails on an uncreatable path" "1" "$?"

# --- and NEVER leave a temp file behind ------------------------------------
check "no temp files abandoned" "0" \
    "$(find "$WORK" -name '*.tmp.*' 2>/dev/null | wc -l | tr -d ' ')"

# --- overwriting an existing file is still atomic --------------------------
echo "first"  | sb_atomic_write "${WORK}/over.txt" 0644 >/dev/null 2>&1
echo "second" | sb_atomic_write "${WORK}/over.txt" 0644 >/dev/null 2>&1
check "overwrites cleanly" "second" "$(cat "${WORK}/over.txt" 2>/dev/null)"

printf '\n  %d passed, %d failed\n' "$pass" "$fail"
exit $((fail > 0 ? 1 : 0))
