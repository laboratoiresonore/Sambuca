#!/usr/bin/env bash
#
# sambuca :: tools/scan-images.sh
#
# Scan the images this appliance actually runs for known vulnerabilities.
#
# The same check CI runs daily, available on the machine itself — because the
# appliance keeps running whatever it pulled on install day, and an owner who
# never looks at CI still deserves to know their photo server has a fixable
# critical.
#
# FIXABLE HIGH/CRITICAL ONLY, deliberately. A base image always carries a long
# tail of unfixable CVEs; reporting them produces a wall of red with no
# available action, and a report nobody can act on is a report everybody learns
# to skip. Fixable means an upstream patch exists, which means bumping the pin
# is a real remedy.
#
# Usage:
#   scan-images.sh                 scan every image in the rendered .env
#   scan-images.sh --json PATH     also write a machine-readable report
#   scan-images.sh --quiet         only report findings
#
set -uo pipefail

SB_TAG="image-scan"
_SB_SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=engine/lib/common.sh
source "${_SB_SELF_DIR}/../engine/lib/common.sh"
sb_trap_err

JSON_OUT=""
COMPOSE_DIR="${SAMBUCA_COMPOSE_DIR:-/opt/sambuca/compose}"

while (($# > 0)); do
    case "$1" in
        --json)  JSON_OUT="${2:-}"; shift ;;
        --quiet) SB_QUIET=1 ;;
        --compose-dir) COMPOSE_DIR="${2:-}"; shift ;;
        -h|--help)
            sed -n '3,20p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
            exit 0 ;;
        *) SB_EXIT_CODE=2 die "unknown argument: $1" ;;
    esac
    shift
done

sb_require docker

# Prefer the RENDERED .env — that is what is actually running. Fall back to the
# declared defaults so this is still useful on a checkout.
ENV_FILE="${COMPOSE_DIR}/.env"
[[ -r $ENV_FILE ]] || ENV_FILE="${COMPOSE_DIR}/.env.example"
[[ -r $ENV_FILE ]] || die "no environment file at ${COMPOSE_DIR}"
log "image list from ${ENV_FILE}"

mapfile -t IMAGES < <(
    grep -E '^[A-Z0-9_]+_IMAGE=' "$ENV_FILE" | cut -d= -f2- | grep -v '^$' | sort -u
)
((${#IMAGES[@]} > 0)) || die "no image references found in ${ENV_FILE}"
log "scanning ${#IMAGES[@]} image(s) — the first run downloads a vulnerability database"

# Cache the DB between runs. Without this every scan re-downloads a few hundred
# megabytes, which on a metered or slow connection turns a health check into a
# problem of its own.
CACHE="${SB_LIB}/trivy-cache"
install -d -m 0755 "$CACHE"

findings=0
scanned=0
skipped=0
declare -A RESULT

for img in "${IMAGES[@]}"; do
    printf '  %-52s ' "$img" >&2
    out="$(docker run --rm \
            -v /var/run/docker.sock:/var/run/docker.sock \
            -v "${CACHE}:/root/.cache/trivy" \
            aquasec/trivy:latest image \
              --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed \
              --format json --quiet --timeout 10m "$img" 2>/dev/null || true)"

    if [[ -z $out ]] || ! printf '%s' "$out" | jq -e . >/dev/null 2>&1; then
        # Unreachable or unpublished. Already covered by check-upstreams.py, so
        # it is not counted as a security finding here.
        printf 'skipped (not scannable)\n' >&2
        RESULT[$img]="skipped"
        ((skipped++))
        continue
    fi

    n="$(printf '%s' "$out" | jq '[.Results[]?.Vulnerabilities // [] | length] | add // 0')"
    ((scanned++))
    RESULT[$img]="$n"
    if [[ ${n:-0} -gt 0 ]]; then
        printf '%s fixable HIGH/CRITICAL\n' "$n" >&2
        ((findings += n))
        printf '%s' "$out" | jq -r '
            .Results[]?.Vulnerabilities // [] | .[]
            | "      \(.VulnerabilityID)  \(.PkgName) \(.InstalledVersion) -> \(.FixedVersion)"' \
            2>/dev/null | head -n 8 >&2
    else
        printf 'clean\n' >&2
    fi
done

printf '\n' >&2
if ((findings > 0)); then
    err "${findings} fixable HIGH/CRITICAL finding(s) across ${scanned} image(s)"
    err "  Each one has an upstream patch. Bump the pin in compose/.env.example,"
    err "  then: sambuca-first-boot --only 60-stack --force"
else
    ok "no fixable HIGH/CRITICAL findings across ${scanned} image(s)"
fi
((skipped > 0)) && log "${skipped} image(s) not scannable (unpublished or unreachable)"

if [[ -n $JSON_OUT ]]; then
    {
        printf '{\n  "scanned_at": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '  "images_scanned": %d,\n  "images_skipped": %d,\n' "$scanned" "$skipped"
        printf '  "fixable_high_critical": %d,\n  "images": {\n' "$findings"
        first=1
        for img in "${!RESULT[@]}"; do
            ((first)) || printf ',\n'; first=0
            printf '    "%s": "%s"' "$(sb_json_escape "$img")" "${RESULT[$img]}"
        done
        printf '\n  }\n}\n'
    } | sb_atomic_write "$JSON_OUT" 0644
    log "report: ${JSON_OUT}"
fi

# Exit 1 on findings so a systemd timer marks the unit failed and the alerting
# ladder picks it up as "informational" — worth telling the owner, never worth
# taking the machine offline for.
((findings > 0)) && exit 1
exit 0
