#!/usr/bin/env bash
#
# sambuca :: tools/preflight.sh — run every CI check that works off a runner.
#
# THIS EXISTS BECAUSE "LOCAL GREEN" MEANT NOTHING FOR MOST OF A DAY. Three
# commits went out reporting passing tests while the build was failing, and the
# step that was failing — ruff — was the one finding real bugs: a NameError that
# broke the whole x86 installer path, a duplicate function definition silently
# shadowing another, and a minted Tailscale key computed and thrown away.
#
# The gap was never conceptual. It was that the checks lived in a YAML file
# nobody could run, so "did you check?" meant "did you remember all seven?"
#
# IT SAYS WHAT IT CANNOT CHECK, out loud, at the end. Two CI jobs need Docker —
# the compose matrix and the Caddyfile validation — and a preflight that
# quietly omits them would be worse than useless: it would read as full
# coverage. One of those two caught a real regression (gpu.amd.image.yml) that
# nothing here would have seen.
#
# Usage:  bash tools/preflight.sh
# Exit:   0 only if every reproducible check passed.

set -uo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1

fail=0
skipped=()

run() {
    local label="$1"; shift
    printf '  %-34s' "$label"
    if "$@" >/tmp/preflight.$$ 2>&1; then
        printf 'ok\n'
    else
        printf 'FAILED\n'
        sed 's/^/      | /' /tmp/preflight.$$ | tail -12
        fail=1
    fi
    rm -f /tmp/preflight.$$
}

need() { command -v "$1" >/dev/null 2>&1; }

echo
echo "sambuca preflight — the CI checks that do not need a runner"
echo

# --- the flasher -----------------------------------------------------------
if need ruff; then
    run "ruff (pinned 0.16.4)" ruff check apps/flasher/src apps/flasher/tests tools tests
else
    skipped+=("ruff is not installed — pip install 'ruff==0.16.4'")
fi

run "pytest (both trees)" python -m pytest apps/flasher/tests tests -q -m "not slow"

# --- the engine ------------------------------------------------------------
if need shellcheck; then
    # shellcheck disable=SC2046  # word splitting is the point: a file list
    run "shellcheck" shellcheck --severity=warning --external-sources $(find engine -name '*.sh')
else
    skipped+=("shellcheck is not installed — pip install shellcheck-py")
fi

if need dash; then
    posix_ok() {
        local f
        for f in engine/autoinstall/abort-countdown.sh \
                 engine/autoinstall/disk-select.sh \
                 engine/autoinstall/late-command.sh; do
            dash -n "$f" || return 1
        done
    }
    run "POSIX sh (installer scripts)" posix_ok
else
    skipped+=("dash is not installed — the installer runs under busybox ash, and"
              "         bash -n will NOT catch a bashism there")
fi

run "bash -n (engine)" bash -c 'bash -n engine/first-boot.sh && bash -n engine/hardware-detect.sh && for f in engine/provision/*.sh engine/lib/*.sh; do bash -n "$f" || exit 1; done'

# --- the rest --------------------------------------------------------------
run "steward catalogue lint" python tools/steward-lint.py
run "update guard" bash tests/test-update-guard.sh
run "atomic write" bash tests/test-atomic-write.sh
run "verified-script gate" bash tests/test-verified-script.sh
run "compose yaml parses" python -c "import yaml,glob; [yaml.safe_load(open(f,encoding='utf-8')) for f in glob.glob('compose/*.yml')]"
run "hardware profiler runs" bash engine/hardware-detect.sh --print --force-tier 1 --no-lock --quiet

echo
echo "  NOT CHECKED HERE — these need Docker and only run in CI:"
echo "    * compose config across every GPU profile x bundle subset"
echo "      (this is the one that caught gpu.amd.image.yml shipping broken)"
echo "    * Caddyfile validation"
echo "    * the frozen-binary smoke test (needs a PyInstaller build)"

if ((${#skipped[@]})); then
    echo
    echo "  SKIPPED, so this run is NOT equivalent to CI:"
    printf '    * %s\n' "${skipped[@]}"
    fail=1     # A partial preflight must not exit 0 and read as a pass.
fi

echo
if ((fail)); then
    echo "  PREFLIGHT FAILED — do not report this as done."
else
    echo "  All reproducible checks passed. CI still has the final word."
fi
exit "$fail"
