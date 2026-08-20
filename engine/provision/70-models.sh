# shellcheck shell=bash
# sambuca :: phase 70-models — pull the model set the hardware can actually run.
#
# The tier was decided in hardware-detect.sh; this phase only executes it. Pulls
# are sequential and verified: a partially downloaded 40 GiB blob that reports
# success is worse than a clean failure, because the first chat request is then
# the thing that discovers the problem.

# shellcheck source=/dev/null
for f in profile.env profile.local.env; do
    [[ -r "${SB_ETC}/${f}" ]] && source "${SB_ETC}/${f}"
done

: "${SAMBUCA_MODEL_CHAT:=}"
: "${SAMBUCA_MODEL_CODE:=}"
: "${SAMBUCA_MODEL_VISION:=}"
: "${SAMBUCA_MODEL_EMBED:=}"
: "${SAMBUCA_TIER:=4}"

OLLAMA_CONTAINER="${OLLAMA_CONTAINER:-sambuca-ollama}"

# --- reachability -----------------------------------------------------------
log "waiting for the inference engine to accept connections"
ready=0
for _ in $(seq 1 60); do
    if docker exec "$OLLAMA_CONTAINER" ollama list >/dev/null 2>&1; then ready=1; break; fi
    sleep 2
done
if ((ready == 0)); then
    err "container '${OLLAMA_CONTAINER}' is not answering after 120s"
    err "  logs: docker logs ${OLLAMA_CONTAINER} --tail 50"
    die "cannot pull models without a running engine"
fi
ok "inference engine reachable"

# --- pull -------------------------------------------------------------------
pull_model() {
    local model="$1" role="$2"
    [[ -z ${model// /} ]] && { log "no ${role} model for tier ${SAMBUCA_TIER} — skipping"; return 0; }

    if docker exec "$OLLAMA_CONTAINER" ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx -- "$model"; then
        log "${role}: ${model} already present"
        return 0
    fi

    log "${role}: pulling ${model}"
    if ! sb_retry 3 20 docker exec "$OLLAMA_CONTAINER" ollama pull "$model"; then
        err "${role}: pull FAILED for ${model}"
        # A missing optional model degrades the appliance; a missing chat model
        # breaks its headline feature. Treat them differently.
        [[ $role == "chat" ]] && return 1
        warn "continuing without the ${role} model"
        return 0
    fi

    # Verify the manifest actually resolved rather than trusting the exit code.
    if docker exec "$OLLAMA_CONTAINER" ollama show "$model" >/dev/null 2>&1; then
        ok "${role}: ${model} ready"
        return 0
    fi
    err "${role}: ${model} pulled but the manifest does not resolve — treating as failed"
    [[ $role == "chat" ]] && return 1
    return 0
}

pull_model "$SAMBUCA_MODEL_CHAT"   "chat"   || die "the primary chat model could not be provisioned"
pull_model "$SAMBUCA_MODEL_EMBED"  "embed"
pull_model "$SAMBUCA_MODEL_CODE"   "code"
pull_model "$SAMBUCA_MODEL_VISION" "vision"

# --- warm + smoke test ------------------------------------------------------
# Load the chat model once so the owner's first message is not a 60-second wait,
# and prove end-to-end that generation actually works on this hardware.
log "smoke-testing generation on ${SAMBUCA_MODEL_CHAT}"
if docker exec "$OLLAMA_CONTAINER" \
        ollama run "$SAMBUCA_MODEL_CHAT" --keepalive 5m 'Reply with the single word: ready' \
        >"${SB_LOG_DIR}/model-smoketest.txt" 2>&1; then
    ok "generation verified — $(tr -d '\n' <"${SB_LOG_DIR}/model-smoketest.txt" | head -c 80)"
else
    warn "generation smoke test FAILED. The model is downloaded but not producing output."
    warn "  Most common cause: insufficient VRAM/RAM for the selected model."
    warn "  Force a smaller tier:  echo 'SAMBUCA_TIER=4' >> ${SB_ETC}/profile.local.env"
    warn "  then: sambuca-first-boot --only 70-models --force"
    warn "  detail: $(tail -n 3 "${SB_LOG_DIR}/model-smoketest.txt" | tr '\n' ' ')"
fi

installed="$(docker exec "$OLLAMA_CONTAINER" ollama list 2>/dev/null | awk 'NR>1{print $1}' | tr '\n' ' ' || true)"
ok "models installed: ${installed:-none}"
