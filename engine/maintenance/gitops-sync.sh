#!/usr/bin/env bash
#
# sambuca :: engine/maintenance/gitops-sync.sh
#
# Nightly configuration sync from github.com/laboratoiresonore/Sambuca.
#
# ══════════════════════════════════════════════════════════════════════════
# WHAT THIS DELIBERATELY DOES NOT DO
#
# It does not run arbitrary code from the internet against a machine holding
# the owner's passwords, photos and client documents. That is what "nightly
# auto-update from a git repository" usually means, and it is a supply-chain
# compromise waiting for one bad commit or one stolen maintainer token.
#
# Instead:
#   1. FETCH ONLY. Never a blind `git pull` onto the running tree.
#   2. Verify the tip is a SIGNED TAG (or an allow-listed signed commit).
#   3. DIFF the incoming change against what is running, and refuse categories
#      of change that must never arrive unattended (see FORBIDDEN_PATHS).
#   4. Apply configuration; re-render; validate; roll back on failure.
#   5. Never touch data, secrets or database schemas.
#
# The appliance follows a repository. It does not obey it.
# ══════════════════════════════════════════════════════════════════════════
#
set -uo pipefail

SB_TAG="gitops-sync"
_SB_SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=engine/lib/common.sh
source "${_SB_SELF_DIR}/../lib/common.sh"
sb_trap_err

sb_require_root
sb_single_instance "gitops-sync" 30
sb_require git docker

# shellcheck source=/dev/null
[[ -r "${SB_ETC}/gitops.env" ]] && source "${SB_ETC}/gitops.env"

: "${SAMBUCA_GITOPS_REPO:=https://github.com/laboratoiresonore/Sambuca.git}"
: "${SAMBUCA_GITOPS_REF:=main}"
: "${SAMBUCA_GITOPS_REQUIRE_SIGNED:=1}"
: "${SAMBUCA_GITOPS_AUTO_APPLY:=1}"
: "${SAMBUCA_INSTALL_ROOT:=/opt/sambuca}"

COMPOSE_DIR="${SAMBUCA_INSTALL_ROOT}/compose"
DRY=0
[[ ${1:-} == "--check" ]] && DRY=1

# The forbidden-path list now lives in update-guard.sh, alongside every other
# check and the test suite that proves each one fires. Keeping a second copy
# here would give two lists that drift apart — and the one that drifts is
# always the one nobody is testing.

cd "$SAMBUCA_INSTALL_ROOT" || die "install root ${SAMBUCA_INSTALL_ROOT} is not a directory"
git rev-parse --git-dir >/dev/null 2>&1 || die "${SAMBUCA_INSTALL_ROOT} is not a git checkout"

current="$(git rev-parse HEAD)"
log "current revision ${current:0:12}"

# ---------------------------------------------------------------------------
# 1. Fetch only.
# ---------------------------------------------------------------------------
log "fetching ${SAMBUCA_GITOPS_REPO} (${SAMBUCA_GITOPS_REF})"
sb_retry 3 10 git fetch --tags --prune origin "$SAMBUCA_GITOPS_REF" \
    || die "fetch failed — no network, or the remote rejected us. Nothing changed."

incoming="$(git rev-parse "origin/${SAMBUCA_GITOPS_REF}")"
if [[ $incoming == "$current" ]]; then
    ok "already current at ${current:0:12}"
    printf '{"last_check":"%s","status":"current","revision":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$current" \
        | sb_atomic_write "${SB_LIB}/gitops-state.json" 0644
    exit 0
fi
log "incoming revision ${incoming:0:12}"

# ---------------------------------------------------------------------------
# 2. Signature verification.
# ---------------------------------------------------------------------------
if [[ $SAMBUCA_GITOPS_REQUIRE_SIGNED == 1 ]]; then
    tag="$(git describe --exact-match --tags "$incoming" 2>/dev/null || true)"
    if [[ -n $tag ]]; then
        if git verify-tag "$tag" >/dev/null 2>&1; then
            ok "signed tag verified: ${tag}"
        else
            die "tag ${tag} is NOT validly signed — refusing to apply. Nothing changed."
        fi
    elif git verify-commit "$incoming" >/dev/null 2>&1; then
        ok "signed commit verified: ${incoming:0:12}"
    else
        err "incoming revision ${incoming:0:12} carries no valid signature."
        err "  A signed release tag is required. Set SAMBUCA_GITOPS_REQUIRE_SIGNED=0"
        err "  in ${SB_ETC}/gitops.env ONLY if you are tracking your own fork."
        die "refusing unsigned update"
    fi
fi

# ---------------------------------------------------------------------------
# 3. Change review, delegated to update-guard.sh.
#
# Separate on purpose: a guard you cannot test is a guard you do not have.
# It takes two revisions, takes no action, and returns a verdict — so CI feeds
# it deliberately poisoned updates on every push (tests/test-update-guard.sh)
# and asserts it refuses them. Inline checks inside this script would be read
# once and trusted forever.
# ---------------------------------------------------------------------------
changed="$(git diff --name-only "${current}..${incoming}")"
log "$(printf '%s' "$changed" | grep -c . || true) file(s) changed"

GUARD="${_SB_SELF_DIR}/update-guard.sh"
if [[ ! -r $GUARD ]]; then
    # Fail CLOSED. A missing guard means every check is absent, which must never
    # read as "nothing objectionable found".
    die "update-guard.sh is missing — refusing to apply an unreviewed update"
fi

if ! "$GUARD" "$current" "$incoming" --json "${SB_LIB}/update-verdict.json"; then
    err "═══════════════════════════════════════════════════════════════"
    err " UPDATE HELD. Nothing has been applied and nothing changed."
    err ""
    err "   Review it:  cd ${SAMBUCA_INSTALL_ROOT} && git log -p ${current:0:12}..${incoming:0:12}"
    err "   Then apply: sambuca-gitops apply --force"
    err "═══════════════════════════════════════════════════════════════"
    printf '{"last_check":"%s","status":"held","from":"%s","to":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$current" "$incoming" \
        | sb_atomic_write "${SB_LIB}/gitops-state.json" 0644
    exit 0
fi

if ((DRY)) || [[ $SAMBUCA_GITOPS_AUTO_APPLY != 1 ]]; then
    ok "update ${incoming:0:12} is eligible; not applying (check mode)"
    exit 0
fi

# ---------------------------------------------------------------------------
# 4. Apply, validate, roll back on failure.
# ---------------------------------------------------------------------------
rollback() {
    err "rolling back to ${current:0:12}"
    git -c advice.detachedHead=false checkout --force "$current" >/dev/null 2>&1 \
        || err "ROLLBACK FAILED — the checkout is at ${incoming:0:12}. Manual recovery needed."
    ( cd "$COMPOSE_DIR" && docker compose up -d --remove-orphans ) >/dev/null 2>&1 || true
}

log "applying ${incoming:0:12}"
if ! git -c advice.detachedHead=false checkout --force "$incoming" >/dev/null 2>&1; then
    die "checkout failed — the working tree may be dirty. Nothing was applied."
fi

# Re-render the environment: the profile is unchanged, but image pins and the
# compose chain may have moved.
if ! "${SAMBUCA_INSTALL_ROOT}/engine/first-boot.sh" --only 60-stack --force; then
    err "stack phase failed on the new revision"
    rollback
    die "update ${incoming:0:12} rolled back"
fi

# Prove the stack survived rather than assuming it.
sleep 15
unhealthy="$(cd "$COMPOSE_DIR" && docker compose ps --format '{{.Name}}\t{{.Health}}' 2>/dev/null \
    | awk -F'\t' '$2=="unhealthy"{print $1}' || true)"
if [[ -n $unhealthy ]]; then
    err "post-update health check FAILED: ${unhealthy//$'\n'/, }"
    rollback
    die "update ${incoming:0:12} rolled back after failed health check"
fi

ok "updated ${current:0:12} -> ${incoming:0:12}"
printf '{"last_check":"%s","status":"applied","from":"%s","to":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$current" "$incoming" \
    | sb_atomic_write "${SB_LIB}/gitops-state.json" 0644
