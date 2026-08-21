#!/usr/bin/env bash
#
# sambuca :: engine/maintenance/update-guard.sh
#
# Decide whether an incoming update may be applied unattended.
#
# ══════════════════════════════════════════════════════════════════════════
# WHY THIS IS SEPARATE FROM gitops-sync.sh
#
# Because a guard you cannot test is a guard you do not have. This runs against
# any two git revisions, takes no action, and returns a verdict — so CI can feed
# it deliberately poisoned updates and assert that it refuses them. The rollback
# path and the refusal path both get exercised on every push, rather than being
# discovered during the incident they exist for.
#
# It is also the reason the checks live here rather than inline: an inline check
# inside a 200-line sync script gets read once and trusted forever.
# ══════════════════════════════════════════════════════════════════════════
#
# Usage:   update-guard.sh <old-rev> <new-rev> [--json PATH]
#
# Exit:    0  APPLY  — nothing suspicious, safe to apply unattended
#          1  HOLD   — needs a human; reasons printed
#          2  usage error
#
set -uo pipefail

SB_TAG="update-guard"
_SB_SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=engine/lib/common.sh
source "${_SB_SELF_DIR}/../lib/common.sh"
sb_trap_err

# ---------------------------------------------------------------------------
# Thresholds. Named, and overridable from /etc/sambuca/gitops.env — a limit
# tuned by whoever is watching beats a limit hardcoded by whoever wrote it.
# ---------------------------------------------------------------------------
: "${GUARD_MAX_FILES:=200}"          # files touched in one update
: "${GUARD_MAX_ADDED_LINES:=6000}"   # lines added in one update
: "${GUARD_MAX_GROWTH_PCT:=140}"     # new tree size vs old, percent
: "${GUARD_ALLOW_BINARIES:=0}"       # added binary files

OLD="${1:-}"; NEW="${2:-}"; shift 2 2>/dev/null || true
JSON_OUT=""
while (($# > 0)); do
    case "$1" in
        --json) JSON_OUT="${2:-}"; shift ;;
        *) SB_EXIT_CODE=2 die "unknown argument: $1" ;;
    esac
    shift
done
[[ -n $OLD && -n $NEW ]] || { printf 'usage: update-guard.sh <old-rev> <new-rev> [--json PATH]\n' >&2; exit 2; }

git rev-parse --verify --quiet "$OLD" >/dev/null || { SB_EXIT_CODE=2 die "unknown revision: $OLD"; }
git rev-parse --verify --quiet "$NEW" >/dev/null || { SB_EXIT_CODE=2 die "unknown revision: $NEW"; }

REASONS=()
hold() { REASONS+=("$1"); }

# ---------------------------------------------------------------------------
# 1. SHAPE — a legitimate configuration update is small.
#
# Not a security check on its own; a tripwire. Every supply-chain compromise
# that ships a payload makes the diff abnormal in at least one dimension, and
# "abnormal" is cheap to detect even when "malicious" is not.
# ---------------------------------------------------------------------------
files_changed="$(git diff --name-only "$OLD" "$NEW" | grep -c . || true)"
added_lines="$(git diff --numstat "$OLD" "$NEW" | awk '$1 != "-" {s += $1} END {print s + 0}')"

((files_changed > GUARD_MAX_FILES)) && \
    hold "touches ${files_changed} files (limit ${GUARD_MAX_FILES})"
((added_lines > GUARD_MAX_ADDED_LINES)) && \
    hold "adds ${added_lines} lines (limit ${GUARD_MAX_ADDED_LINES})"

# ---------------------------------------------------------------------------
# 2. BINARIES — this repository is text. A new binary is a payload until proven
#    otherwise, and it cannot be reviewed in a diff.
#
#    The brand assets are the deliberate exception; they are already committed,
#    so only NEWLY ADDED binaries outside that path count.
# ---------------------------------------------------------------------------
if ((GUARD_ALLOW_BINARIES == 0)); then
    while IFS= read -r f; do
        [[ -z $f ]] && continue
        case "$f" in assets/brand/*) continue ;; esac
        if git diff --numstat "$OLD" "$NEW" -- "$f" | awk '$1 == "-"' | grep -q .; then
            hold "adds a binary file: ${f}"
        fi
    done < <(git diff --name-only --diff-filter=A "$OLD" "$NEW")
fi

# ---------------------------------------------------------------------------
# 3. SECRET-SHAPED CONTENT — whether it is an attack or an upstream mistake, a
#    credential must never land on the appliance unattended.
# ---------------------------------------------------------------------------
added="$(git diff -U0 "$OLD" "$NEW" | grep '^+' | grep -v '^+++' || true)"

declare -A SECRET_PATTERNS=(
    ["private key block"]='-----BEGIN [A-Z ]*PRIVATE KEY-----'
    ["GitHub token"]='gh[pousr]_[A-Za-z0-9]{20,}'
    ["AWS access key"]='AKIA[0-9A-Z]{16}'
    ["Slack token"]='xox[baprs]-[A-Za-z0-9-]{10,}'
    ["Google API key"]='AIza[0-9A-Za-z_-]{35}'
    ["Tailscale auth key"]='tskey-auth-[A-Za-z0-9]{10,}'
    ["PEM certificate bundle"]='-----BEGIN OPENSSH PRIVATE KEY-----'
)
for name in "${!SECRET_PATTERNS[@]}"; do
    # `--` is load-bearing. Two of these patterns begin with a dash, and
    # without it grep parses `-----BEGIN RSA PRIVATE KEY-----` as a bundle of
    # options rather than as the pattern — so the single most important check
    # in this file silently matched nothing. The test suite caught it; reading
    # the code did not.
    if printf '%s' "$added" | grep -qE -- "${SECRET_PATTERNS[$name]}"; then
        # The value itself is NEVER printed or logged — reporting a leaked
        # credential by echoing it into a log file leaks it again.
        hold "introduces something shaped like a ${name}"
    fi
done

# ---------------------------------------------------------------------------
# 4. NEW EGRESS — the highest-signal check available.
#
#    Supply-chain compromise almost always needs to phone somewhere. An update
#    that introduces a host this repository has never contacted is held for a
#    human even when everything else about it looks ordinary.
# ---------------------------------------------------------------------------
known_hosts="$(git grep -hoE 'https?://[a-zA-Z0-9._-]+' "$OLD" -- \
                 ':!docs/' ':!*.md' 2>/dev/null \
               | sed -E 's#https?://##' | sort -u || true)"

new_hosts="$(printf '%s' "$added" | grep -oE 'https?://[a-zA-Z0-9._-]+' \
             | sed -E 's#https?://##' | sort -u || true)"

while IFS= read -r host; do
    [[ -z $host ]] && continue
    case "$host" in 127.0.0.1|localhost|0.0.0.0) continue ;; esac
    if ! printf '%s\n' "$known_hosts" | grep -qxF "$host"; then
        hold "contacts a host this repository has never used: ${host}"
    fi
done <<<"$new_hosts"

# ---------------------------------------------------------------------------
# 5. FORBIDDEN PATHS — changes to how the machine boots, who can reach it, or
#    how it is recovered. Never unattended, however small the diff.
# ---------------------------------------------------------------------------
FORBIDDEN=(
    "engine/autoinstall/"
    "engine/provision/40-"
    "engine/provision/50-"
    "engine/maintenance/backup"
    "engine/maintenance/update-guard"   # a guard that can edit itself is not a guard
    ".github/workflows/"
)
while IFS= read -r f; do
    [[ -z $f ]] && continue
    for p in "${FORBIDDEN[@]}"; do
        [[ $f == "$p"* ]] && hold "touches a path that requires review: ${f}"
    done
done < <(git diff --name-only "$OLD" "$NEW")

# ---------------------------------------------------------------------------
# 6. IMAGE DIGEST DRIFT — once pinned, a changed digest is a deliberate act.
#
# BOTH DIRECTIONS. This looked only at ADDED lines containing @sha256:, which
# catches a digest being changed or introduced and misses the one that matters
# most: REMOVING it. Unpinning back to a mutable tag produces an added line with
# no digest in it at all, so it sailed through — and unpinning is precisely how
# you arrange for the bytes to change later without another commit ever landing
# here. The guard against a swapped image could not see the setup for the swap.
#
# The diff header lines (--- a/…, +++ b/…) are excluded so a filename can never
# be mistaken for content.
# ---------------------------------------------------------------------------
if git diff -U0 "$OLD" "$NEW" -- compose/.env.example 2>/dev/null \
     | grep -E '^[+-]' | grep -vE '^(\+\+\+|---) ' | grep -q '@sha256:'; then
    hold "changes or removes a pinned image digest"
fi

# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
verdict="APPLY"
((${#REASONS[@]} > 0)) && verdict="HOLD"

log "${OLD:0:8}..${NEW:0:8}  files=${files_changed} added=${added_lines}"

if [[ $verdict == "APPLY" ]]; then
    ok "APPLY — nothing suspicious"
else
    err "HOLD — this update needs a human:"
    for r in "${REASONS[@]}"; do err "    - ${r}"; done
    err ""
    err "  Review it:  git log -p ${OLD:0:8}..${NEW:0:8}"
    err "  Then apply deliberately:  sambuca-gitops apply --force"
fi

if [[ -n $JSON_OUT ]]; then
    {
        printf '{\n  "verdict": "%s",\n' "$verdict"
        printf '  "from": "%s",\n  "to": "%s",\n' "$OLD" "$NEW"
        printf '  "files_changed": %s,\n  "added_lines": %s,\n' "$files_changed" "$added_lines"
        printf '  "reasons": ['
        for i in "${!REASONS[@]}"; do
            ((i > 0)) && printf ', '
            printf '"%s"' "$(sb_json_escape "${REASONS[$i]}")"
        done
        printf ']\n}\n'
    } | sb_atomic_write "$JSON_OUT" 0644
fi

[[ $verdict == "APPLY" ]] && exit 0
exit 1
