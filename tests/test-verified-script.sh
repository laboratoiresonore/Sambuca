#!/usr/bin/env bash
#
# sambuca :: tests/test-verified-script.sh
#
# sb_verify_and_run replaced `curl … | bash`, which docs/MAINTENANCE.md
# correctly called the weakest link in the project: whoever controlled that URL
# controlled every appliance at install time, as root.
#
# A checksum gate is only worth something if it REFUSES, so the refusal paths
# are driven first and hardest. A gate observed only saying yes has not been
# tested at all.
#
# NO NETWORK. The download half is deliberately a separate function, because
# testing them together needed either local HTTPS or a flag to relax
# --proto '=https' for tests — and a security control with a test-only bypass
# is not a control. The https-only restriction is asserted by reading the
# source instead, below.

set -uo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1

# shellcheck source=engine/lib/common.sh
SB_QUIET=1 source engine/lib/common.sh

pass=0; fail=0
ok_()  { printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad_() { printf '  FAIL  %s\n' "$1"; fail=$((fail+1)); }

tmpdir="$(mktemp -d)"
trap 'rm -rf -- "$tmpdir" 2>/dev/null || true' EXIT

cat > "$tmpdir/payload.sh" <<'PAYLOAD'
#!/usr/bin/env bash
echo "ran with: $*" > "$SENTINEL"
PAYLOAD
good_sha="$(sha256sum -- "$tmpdir/payload.sh" | cut -d' ' -f1)"
zeros="$(printf '0%.0s' $(seq 64))"

echo
echo "sb_verify_and_run — the gate"

# --- 1. a WRONG checksum must not execute anything --------------------------
export SENTINEL="$tmpdir/s1"; rm -f "$SENTINEL"
SB_QUIET=1 sb_verify_and_run "$tmpdir/payload.sh" "$zeros" >/dev/null 2>&1
rc=$?
[[ -f $SENTINEL ]] && bad_ "IT RAN THE SCRIPT ANYWAY — the gate is decorative" \
                   || ok_ "a wrong checksum does not execute the script"
((rc == 3)) && ok_ "a mismatch is distinguishable (rc=3)" \
            || bad_ "mismatch returned rc=$rc — callers cannot tell it apart"

# --- 2. a matching checksum runs it, and arguments get through --------------
export SENTINEL="$tmpdir/s2"; rm -f "$SENTINEL"
if SB_QUIET=1 sb_verify_and_run "$tmpdir/payload.sh" "$good_sha" --no-color >/dev/null 2>&1; then
    ok_ "a matching checksum runs the script"
else
    bad_ "a matching checksum did NOT run the script"
fi
if [[ -f $SENTINEL ]] && grep -q -- "--no-color" "$SENTINEL"; then
    ok_ "arguments reach the script"
else
    bad_ "arguments did not reach the script"
fi

# --- 3. tampering after pinning is caught -----------------------------------
export SENTINEL="$tmpdir/s3"; rm -f "$SENTINEL"
printf '\necho EXTRA\n' >> "$tmpdir/payload.sh"      # upstream changed under us
SB_QUIET=1 sb_verify_and_run "$tmpdir/payload.sh" "$good_sha" >/dev/null 2>&1
[[ -f $SENTINEL ]] && bad_ "A MODIFIED SCRIPT STILL RAN" \
                   || ok_ "a modified script no longer matches and is refused"

# --- 4. a failing script is not reported as success -------------------------
printf '#!/usr/bin/env bash\nexit 7\n' > "$tmpdir/bad.sh"
bad_sha="$(sha256sum -- "$tmpdir/bad.sh" | cut -d' ' -f1)"
SB_QUIET=1 sb_verify_and_run "$tmpdir/bad.sh" "$bad_sha" >/dev/null 2>&1
(($? != 0)) && ok_ "a script that fails is a failure" \
            || bad_ "a failing script was reported as success"

# --- 5. called wrong, or pointed at nothing ---------------------------------
SB_QUIET=1 sb_verify_and_run "" "" >/dev/null 2>&1
(($? != 0)) && ok_ "empty arguments refuse" || bad_ "empty arguments accepted"
SB_QUIET=1 sb_verify_and_run "$tmpdir/does-not-exist" "$good_sha" >/dev/null 2>&1
(($? != 0)) && ok_ "a missing file refuses" || bad_ "a missing file accepted"

echo
echo "the source itself"

# --- 6. the download must stay https-only -----------------------------------
if grep -q -- "--proto '=https'" engine/lib/common.sh; then
    ok_ "downloads are https-only"
else
    bad_ "the https-only restriction is gone"
fi

# --- 7. nothing anywhere pipes a download into a shell ----------------------
# Comments are excluded: this file and 50-network.sh both DESCRIBE the pattern
# they removed, and matching prose would make the check permanently red — which
# is how a real check gets deleted for being noisy.
hits="$(grep -rnE "curl[^|#]*\|[[:space:]]*(ba)?sh\b" engine/ --include='*.sh' \
        | grep -vE ':[[:space:]]*#' || true)"
if [[ -n $hits ]]; then
    bad_ "something still pipes curl into a shell:"
    printf '        %s\n' "$hits"
else
    ok_ "no curl-into-shell anywhere in the engine"
fi

echo
printf '  %d passed, %d failed\n\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
