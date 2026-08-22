#!/usr/bin/env bash
#
# sambuca :: tests/test-network-fallback.sh
#
# A BLOCKED TAILSCALE MUST NOT KILL THE INSTALL.
#
# 50-network.sh had four `die`s on the path that ACQUIRES tailscale: the signing
# key, the apt update, the package install, and starting the daemon. first-boot
# stops the whole run on a failing phase (`run_phase … || { rc=1; break; }`), so
# on a network that blocks pkgs.tailscale.com — corporate, school, a few ISPs,
# the exact populations the roadmap names — the machine installed Debian, booted,
# died at phase 50, and never provisioned the stack, the certificates or the
# setup page. It powered on and did nothing, on the networks least equipped to
# work out why.
#
# The asymmetry is what exposed it: `tailscale up` failing was ALREADY a
# warn-and-continue in the same file, 60-stack.sh guards its serve block with
# `sb_have tailscale`, and 90-report.sh prints "remote NOT CONFIGURED". Every
# consumer coped with tailscale being absent. Only obtaining it was fatal — and
# the project documents LAN-only as a supported mode.
#
# WHAT THESE CHECKS ARE, HONESTLY: they read the phase's structure. They do not
# boot a machine on a firewalled network. Driving the real code would mean
# extracting the ladder into a function the way sb_ml_image_ref was extracted
# for test-ml-variant.sh — the right eventual shape, and worth saying plainly
# that it has not been done rather than implying this proves more than it does.
# What they DO catch is the regression that actually happened: a `die` on the
# acquisition path.

set -uo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1

pass=0; fail=0
ok_()  { printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad_() { printf '  FAIL  %s\n' "$1"; fail=$((fail+1)); }

PHASE=engine/provision/50-network.sh

echo
echo "obtaining tailscale is not fatal"

# The acquisition block: from the tailscale banner to the state probe.
block="$(sed -n '/^# --- tailscale ---/,/^ts_state=/p' "$PHASE")"

[[ -n $block ]] \
    && ok_ "the tailscale block is still findable by this test" \
    || bad_ "the block markers moved — this whole file is now checking nothing"

if printf '%s' "$block" | grep -qE '\|\|[[:space:]]*die|^[[:space:]]*die '; then
    bad_ "a 'die' is back on the tailscale acquisition path: $(printf '%s' "$block" | grep -nE '\|\|[[:space:]]*die|^[[:space:]]*die ' | head -3)"
else
    ok_ "no 'die' anywhere in obtaining tailscale"
fi

printf '%s' "$block" | grep -q 'ts_ok=0' \
    && ok_ "failure is recorded in a flag rather than exiting" \
    || bad_ "nothing records that tailscale could not be obtained"

echo
echo "the owner is told what they lost, and what still works"

printf '%s' "$block" | grep -qi "WITHOUT remote access" \
    && ok_ "it names the actual loss" \
    || bad_ "the warning does not say remote access is unavailable"

printf '%s' "$block" | grep -qi "Everything else installs normally" \
    && ok_ "it says the rest of the install is unaffected" \
    || bad_ "it does not reassure that provisioning continues"

printf '%s' "$block" | grep -q 'tailscale up --hostname=' \
    && ok_ "it gives the command to add remote access later" \
    || bad_ "no recovery instruction"

# IT MUST NOT PROMISE A NAME, and the first draft of this warning did.
# "this machine will be reachable on your own network" reads as sambuca.local,
# and that is not true: no package list installs an mDNS responder, and mDNS
# cannot serve the per-service SUBDOMAINS the handover hands out even if one
# were installed. Writing it would have made the fix for a false promise a
# fresh false promise, in the same file, in the same commit.
#
# MATCH A LINE THAT SPEAKS, NOT A LINE THAT MENTIONS. The first version of this
# check scanned the whole block and flagged the comment directly above, which
# quotes the old wording in order to explain it. That is the "appears anywhere"
# fault this repository has now hit five times — a zram check matching a
# comment, steward-lint matching prose, the Tk guard matching its own detection
# line. The rule each time: match the SYNTAX OF A USE.
#
# Here a use is a warn/printf that reaches the owner's screen.
if printf '%s' "$block" | grep -vE '^\s*#' \
     | grep -qiE '(warn|printf|log)[^#]*(reachable by name|reachable[^#]*\.local|https?://[a-z.]*\.local)'; then
    bad_ "the fallback promises a name nothing on this machine publishes"
else
    ok_ "the fallback points at the address, not an unresolvable name"
fi

printf '%s' "$block" | grep -q 'ADDRESS' \
    && ok_ "it tells the owner to use the address" \
    || bad_ "it does not say how to actually reach the machine"

echo
echo "it does not leave a broken apt source behind"

# An unreachable repo in sources.list.d makes EVERY later apt-get update fail —
# turning one blocked host into a machine that cannot install anything at all,
# including the security updates 10-system.sh just switched on.
printf '%s' "$block" | grep -q 'rm -f /etc/apt/sources.list.d/tailscale.list' \
    && ok_ "the tailscale apt source is removed when the install fails" \
    || bad_ "a failed install leaves an unreachable repo in sources.list.d"

echo
echo "the join is skipped when there is nothing to join"

printf '%s' "$block" | grep -q 'if \[\[ \$ts_ok == 0 \]\]; then' \
    && ok_ "the join ladder is guarded by the flag" \
    || bad_ "the join runs even when tailscale was never obtained"

echo
echo "everything downhill already tolerates tailscale being absent"

# The fix is only safe because these were already true. If a later change makes
# a consumer REQUIRE tailscale, this fallback silently becomes a broken install
# again — one phase further along, which is harder to diagnose, not easier.
grep -q 'sb_have tailscale' engine/provision/60-stack.sh \
    && ok_ "60-stack guards its tailscale serve block" \
    || bad_ "60-stack no longer checks whether tailscale exists"

grep -qi 'NOT CONFIGURED' engine/provision/90-report.sh \
    && ok_ "the report tells the owner remote access is not set up" \
    || bad_ "the completion report no longer mentions missing remote access"

echo
printf '  %d passed, %d failed\n\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
