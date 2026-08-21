#!/usr/bin/env bash
#
# sambuca :: tests/test-zram.sh
#
# Compressed swap, sized from measured hardware.
#
# THE README PROMISED THIS AND NOTHING PROVIDED IT — in the same paragraph whose
# last line reads "'Lean' is a number in profile.env you can check, not an
# adjective in a README". zram was an adjective in a README. The only mention of
# it anywhere in engine/ was a filter in disk-select.sh that SKIPS zram devices.
#
# These drive the real profiler rather than a copy of its arithmetic. Every
# threshold is overridable, which is what makes all three branches reachable on
# one machine with a fixed amount of RAM.

set -uo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1

pass=0; fail=0
ok_()  { printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad_() { printf '  FAIL  %s\n' "$1"; fail=$((fail+1)); }

zram_of() {
    # shellcheck disable=SC2086
    env $1 bash engine/hardware-detect.sh --print --no-lock --quiet 2>/dev/null \
        | sed -n 's/^ZRAM_SIZE_MB=//p'
}

echo
echo "the profiler sizes it from measured hardware"

v="$(zram_of '')"
[[ $v =~ ^[0-9]+$ ]] && ok_ "ZRAM_SIZE_MB is emitted and numeric (${v})" \
                     || bad_ "not emitted, or not a number: '${v}'"

# A machine with plenty gets nothing, deliberately: a compressed swap device
# that is never touched is a kernel thread and some accounting for no benefit.
v="$(zram_of 'ZRAM_MAX_RAM_MB=1000')"
[[ $v == 0 ]] && ok_ "a machine with plenty of RAM is given none" \
              || bad_ "expected 0 above the threshold, got '${v}'"

v="$(zram_of 'ZRAM_CAP_MB=2048')"
[[ $v == 2048 ]] && ok_ "the cap bounds it (2048)" \
                 || bad_ "cap not applied, got '${v}'"

# The cap wins over the floor. That precedence is deliberate — a ceiling that a
# floor can push through is not a ceiling.
v="$(zram_of 'ZRAM_MIN_MB=12000 ZRAM_CAP_MB=8192')"
[[ $v == 8192 ]] && ok_ "the cap outranks the floor" \
                 || bad_ "floor pushed through the cap, got '${v}'"

v="$(zram_of 'ZRAM_MIN_MB=99999 ZRAM_CAP_MB=99999')"
[[ $v -gt 0 ]] && ok_ "the floor lifts a small allocation (${v})" \
               || bad_ "floor not applied, got '${v}'"

echo
echo "and it is actually consumed"

# THE FAILURE THIS GUARDS. The value could be computed, emitted, and read by
# nothing — which is the state the whole picture plane was in yesterday, and
# what a dead knob is by definition.
# A READ, not a mention. The first version of this grepped for the name
# anywhere in the file and passed while the read was replaced by a constant —
# the comment above it still said "ZRAM_SIZE_MB". That is the same
# "appears anywhere" fault that has miscalibrated an audit in this project
# every single time it has been written carelessly.
# `.*`, NOT `[^\n]*`. In a POSIX bracket expression `[^\n]` means "not a
# backslash and not the letter n" — and the line it has to match contains
# profile.env, so the pattern could never succeed. It failed CLOSED, which is
# the safer direction, but a check that cannot pass is not a check.
if grep -qE 'sb_env_get.*ZRAM_SIZE_MB' engine/provision/10-system.sh; then
    ok_ "a provisioning phase actually READS ZRAM_SIZE_MB from the profile"
else
    bad_ "nothing reads ZRAM_SIZE_MB — it is a number the profiler emits to nobody"
fi

if grep -q 'zram-generator.conf' engine/provision/10-system.sh; then
    ok_ "it writes a real zram configuration"
else
    bad_ "no zram configuration is written anywhere"
fi

# Zero must mean "do not configure", not "configure a zero-sized device".
if grep -qE '\(\(zram_mb > 0\)\)' engine/provision/10-system.sh; then
    ok_ "zero means do not configure it at all"
else
    bad_ "a measured 0 is not guarded — a zero-sized device would be created"
fi

# A machine without compressed swap is slower under pressure. A machine that
# refuses to finish provisioning over it is broken.
if grep -q 'warn "zram configured but the device did not start' engine/provision/10-system.sh; then
    ok_ "a failure to start degrades rather than aborting the install"
else
    bad_ "a zram failure could abort provisioning"
fi

echo
printf '  %d passed, %d failed\n\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
