#!/usr/bin/env bash
#
# sambuca :: tools/shell-sources.sh
#
# Print every shell source in the engine, one per line.
#
# ONE DEFINITION, because there were three and they disagreed.
#
#   .github/workflows/ci.yml   shellcheck $(find engine -name '*.sh')
#   tools/preflight.sh         the same find, independently written
#   tools/preflight.sh         a bash -n list naming first-boot, hardware-detect,
#                              provision/*.sh and lib/*.sh — and not maintenance/
#
# All three enumerate. engine/image/sambuca-image is a bash script with no .sh
# extension, so it was linted by NOTHING: not shellcheck in CI, not shellcheck
# in preflight, not bash -n. It is symlinked into /usr/local/bin and is the
# command an owner types to ask for a picture.
#
# That is the same shape as the chmod bug it sits next to — the installer also
# decided executability by directory glob and also missed this file. An
# enumerated list is where the next addition gets silently dropped, so this asks
# the FILE what it is instead: a shell shebang, or a .sh name.
#
# Usage: tools/shell-sources.sh [root]     (default: engine)
set -euo pipefail

root="${1:-engine}"

find "$root" -type f -print0 2>/dev/null \
| while IFS= read -r -d '' f; do
    case "$f" in
        *.sh) printf '%s\n' "$f"; continue ;;
    esac
    # A shell shebang. `sh`, `bash`, and `/usr/bin/env bash` all count; a
    # python3 shebang deliberately does not — shellcheck would have opinions
    # about a language it is not reading.
    case "$(head -n 1 "$f" 2>/dev/null)" in
        '#!'*sh|'#!'*sh\ *) printf '%s\n' "$f" ;;
    esac
done | sort
