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
    # SAME REASONING AS THE CHAT MODEL BELOW. The stack is already up; this is
    # one container out of nineteen failing to answer. Killing the run here
    # denies the owner the completion report for every service that IS working,
    # and leaves them with no addresses to visit and no idea anything succeeded.
    warn "container '${OLLAMA_CONTAINER}' is not answering after 120s"
    warn "  logs: docker logs ${OLLAMA_CONTAINER} --tail 50"
    warn "  No models can be pulled without it, so the AI assistant will be"
    warn "  missing. Everything else on this machine is unaffected."
    warn "  Retry once it is healthy:  sambuca-first-boot --only 70-models --force"
    printf 'inference engine unreachable\n' > "${SB_LIB}/chat-model-missing" 2>/dev/null || true
    # NOT `exit 0`. The image plane further down is ComfyUI, which never speaks
    # to Ollama — exiting here would let an unreachable chat engine silently
    # take picture generation with it. Skip what depends on Ollama; run the rest.
    OLLAMA_OK=0
else
    OLLAMA_OK=1
    ok "inference engine reachable"
fi

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

# NOT FATAL, AND IT USED TO BE — `|| die "the primary chat model could not be
# provisioned"`.
#
# This is phase 70. By the time it runs, 60-stack has brought the ENTIRE
# appliance up: the file server, the photo library, the password manager, the
# calendar, the certificates. 90-report has not run yet. So a chat model that
# would not download — a registry outage, a network blip, a full disk, any of
# which is nobody's fault and all of which are temporary — threw away a finished
# machine at the reporting step. The owner got no addresses, no bookmarks, no
# completion report, and a screen saying the install failed, while nine working
# services sat there unmentioned.
#
# The README is unambiguous that this is the wrong trade: "None of that depends
# on the AI... If you came here to stop paying Google, you can stop reading at
# this table." The secondary models in this very file already return 0 on
# failure. Only the chat model was treated as the appliance itself.
#
# So it is recorded and surfaced, and provisioning continues to the report.
if ((OLLAMA_OK == 0)); then
    log "skipping model downloads — the inference engine is not answering"
elif ! pull_model "$SAMBUCA_MODEL_CHAT" "chat"; then
    warn "the primary chat model (${SAMBUCA_MODEL_CHAT}) could not be provisioned."
    warn "  EVERYTHING ELSE ON THIS MACHINE IS UP AND WORKING — files, photos,"
    warn "  passwords, calendar and chat between people. Only the AI assistant"
    warn "  is missing, and it can be added at any time with:"
    warn "    sambuca-first-boot --only 70-models --force"
    printf '%s\n' "$SAMBUCA_MODEL_CHAT" > "${SB_LIB}/chat-model-missing" 2>/dev/null || true
fi
if ((OLLAMA_OK == 1)); then
    pull_model "$SAMBUCA_MODEL_EMBED"  "embed"
    pull_model "$SAMBUCA_MODEL_CODE"   "code"
    pull_model "$SAMBUCA_MODEL_VISION" "vision"
fi

# --- warm + smoke test ------------------------------------------------------
# Load the chat model once so the owner's first message is not a 60-second wait,
# and prove end-to-end that generation actually works on this hardware.
if [[ -f "${SB_LIB}/chat-model-missing" ]]; then
    # SKIP THE TEST, NOT THE PHASE. The first version of this guard was
    # `exit 0`, which would also have skipped the image-model block below —
    # and the image plane is ComfyUI, which does not go anywhere near Ollama.
    # A missing chat model would have silently taken picture generation with
    # it, which is precisely the over-broad failure this whole pass is about,
    # committed while fixing it.
    #
    # Do not smoke-test a model that is known to be absent either: a second
    # warning about one fact buries the first.
    log "skipping the generation smoke test — no chat model to test"
elif docker exec "$OLLAMA_CONTAINER" \
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

if ((OLLAMA_OK == 1)); then
    installed="$(docker exec "$OLLAMA_CONTAINER" ollama list 2>/dev/null | awk 'NR>1{print $1}' | tr '\n' ' ' || true)"
    ok "models installed: ${installed:-none}"
fi

# ---------------------------------------------------------------------------
# IMAGE PLANE
#
# Not an ollama pull — a plain HTTPS fetch of a 16 GiB file, which is the
# largest single thing this appliance ever downloads and the one most likely to
# arrive damaged. Three properties matter, and none of them are automatic:
#
#   RESUMABLE   `curl -C -` continues a partial file. A domestic connection
#               dropping at 14 GiB should cost minutes, not the whole fetch.
#   VERIFIED    the digest is pinned in the tier profile. An exit code of 0
#               from curl means bytes arrived, not that the right bytes did.
#   ATOMIC      it lands on a .part path and is renamed only once the digest
#               matches, so ComfyUI can never open a half-written checkpoint.
#
# A failure here NEVER fails the install. Every other part of the appliance
# works without picture generation, and an owner who loses their file server
# because an optional 16 GiB download timed out would be right to be furious.
# ---------------------------------------------------------------------------
: "${SAMBUCA_IMAGE_ENABLED:=0}"

if [[ $SAMBUCA_IMAGE_ENABLED == 1 ]]; then
    ckpt_dir="${SAMBUCA_APPDATA:-/var/lib/sambuca/appdata}/comfyui/models/checkpoints"
    ckpt_path="${ckpt_dir}/${SAMBUCA_IMAGE_CHECKPOINT_FILE}"
    mkdir -p "$ckpt_dir"

    verify_ckpt() {
        [[ -f $ckpt_path ]] || return 1
        if [[ -z ${SAMBUCA_IMAGE_CHECKPOINT_SHA256:-} ]]; then
            warn "no digest pinned for ${SAMBUCA_IMAGE_CHECKPOINT_FILE} — cannot verify"
            return 0
        fi
        local actual
        actual="$(sha256sum "$ckpt_path" | awk '{print $1}')"
        [[ $actual == "$SAMBUCA_IMAGE_CHECKPOINT_SHA256" ]]
    }

    if verify_ckpt; then
        ok "image model: ${SAMBUCA_IMAGE_MODEL_NAME} already present and verified"
    else
        if [[ -f $ckpt_path ]]; then
            warn "image model present but its digest does not match — refetching"
            mv -f "$ckpt_path" "${ckpt_path}.bad"
        fi
        log "image model: fetching ${SAMBUCA_IMAGE_MODEL_NAME} (~$((SAMBUCA_IMAGE_SET_EST_MB / 1024)) GiB) — this is the long one"

        if sb_retry 3 30 curl -fL --progress-bar -C - \
                -o "${ckpt_path}.part" "$SAMBUCA_IMAGE_CHECKPOINT_URL"; then
            mv -f "${ckpt_path}.part" "$ckpt_path"
            if verify_ckpt; then
                ok "image model: ${SAMBUCA_IMAGE_CHECKPOINT_FILE} verified"
                rm -f "${ckpt_path}.bad"
            else
                err "image model: DIGEST MISMATCH after download"
                err "  expected ${SAMBUCA_IMAGE_CHECKPOINT_SHA256}"
                err "  got      $(sha256sum "$ckpt_path" 2>/dev/null | awk '{print $1}')"
                err "  refusing to install it. The appliance is fine; picture generation is off."
                rm -f "$ckpt_path"
                SAMBUCA_IMAGE_ENABLED=0
            fi
        else
            warn "image model: download failed after retries — continuing without picture generation"
            warn "  retry later with: sambuca-first-boot --only 70-models --force"
            rm -f "${ckpt_path}.part"
            SAMBUCA_IMAGE_ENABLED=0
        fi
    fi

    if [[ $SAMBUCA_IMAGE_ENABLED == 0 ]]; then
        printf 'SAMBUCA_IMAGE_ENABLED=0\n' >>"${SB_ETC}/profile.local.env"
        warn "image plane recorded as OFF in profile.local.env"
    fi
fi
