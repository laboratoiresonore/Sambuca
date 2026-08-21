#!/usr/bin/env bash
#
# sambuca :: engine/hardware-detect.sh
#
# Dynamic hardware profiler.
#
# Inspects CPU, RAM, GPU/VRAM and free model storage, then emits a deterministic
# resource profile that the rest of the appliance consumes: which quantised model
# set to pull, which GPU runtime to bind, and — critically — how VRAM is rationed
# between the inference engine and background ML consumers (Immich face/CLIP
# indexing) so the two never race each other into an OOM.
#
# OUTPUTS (atomic, idempotent, safe to re-run on every boot):
#   ${SB_ETC}/profile.env         KEY=VALUE, consumed directly as a compose env_file
#   ${SB_LIB}/hardware.json       machine-readable inventory for the dashboard/API
#
# CONTRACT: this script must NEVER fail the boot. Every probe degrades to a
# documented fallback. An unknown value is reported as `unknown` and downgrades
# the tier — it is never silently guessed.
#
# Re-run points:
#   1. first boot, before the GPU runtime exists  -> vendor known, VRAM unknown
#   2. immediately after 30-gpu-runtime.sh        -> VRAM now readable, tier settles
#   3. every subsequent boot (systemd oneshot)    -> hardware changes are picked up
#
set -uo pipefail

SB_TAG="hardware-detect"
_SB_SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=engine/lib/common.sh
source "${_SB_SELF_DIR}/lib/common.sh"
sb_trap_err

# ---------------------------------------------------------------------------
# Tunables. Every threshold is here, named, and overridable — no magic numbers
# buried in the logic.
# ---------------------------------------------------------------------------
# VRAM tier boundaries, in MiB.
: "${TIER1_VRAM_MB:=24000}"          # >= 24 GB  -> 32B-70B class
: "${TIER2_VRAM_MB:=11500}"          # >= ~12 GB -> 8B-14B class
# Tier 3 needs no GPU but does need a real CPU to be worth 7B-8B inference.
: "${TIER3_CPU_CORES:=8}"
: "${TIER3_RAM_MB:=24000}"
# Fraction of raw VRAM we allow a model to occupy. The remainder covers the KV
# cache, the compositor, and any second CUDA/ROCm consumer.
: "${VRAM_BUDGET_FRACTION:=85}"      # percent
# VRAM below which Immich's ML worker is forced onto the CPU regardless of tier.
: "${IMMICH_GPU_MIN_VRAM_MB:=20000}"

# VRAM below which the image plane cannot run at all, even streaming weights
# from system RAM. Below this the plane is disabled rather than shipped broken.
: "${IMAGE_MIN_VRAM_MB:=6000}"

# The floor below which this is not a slower appliance, it is a broken one.
#
# Discovered the way these things always are: someone proposed installing onto
# a Pi Zero 2 W (512 MiB). Nextcloud alone wants ~2 GiB, the photo library
# realistically 4, and the smallest chat model 2.5. The honest answer is not a
# tier 5 — it is that the machine cannot host this, said clearly, before an
# hour is spent finding out.
: "${MIN_RAM_MB:=3500}"
# Storage headroom multiplier applied to the estimated model-set size.
: "${MODEL_DISK_HEADROOM_PCT:=130}"

OUTPUT_DIR="$SB_ETC"
JSON_PATH=""
PRINT_ONLY=0
EMIT_JSON=0
FORCE_TIER=""
USE_LOCK=1

usage() {
    cat <<'USAGE'
sambuca hardware-detect — profile the machine and emit its resource tier.

Usage: hardware-detect.sh [options]

  --print              Write nothing; print the resolved profile to stdout.
  --json               Print the hardware inventory as JSON to stdout.
  --force-tier N       Override tier detection (1-4). Recorded as an override.
  --output-dir DIR     Where profile.env is written (default: /etc/sambuca).
  --json-path PATH     Where hardware.json is written (default: /var/lib/sambuca).
  --dry-run            Probe and report, but do not write any file.
  --no-lock            Skip single-instance enforcement (for tests only).
  --quiet              Suppress INFO logging.
  -h, --help           This text.

Exit codes: 0 always, unless an argument is invalid (2) or a write fails (1).
USAGE
}

while (($# > 0)); do
    case "$1" in
        --print)       PRINT_ONLY=1 ;;
        --json)        EMIT_JSON=1 ;;
        --force-tier)  FORCE_TIER="${2:-}"; shift ;;
        --output-dir)  OUTPUT_DIR="${2:-}"; shift ;;
        --json-path)   JSON_PATH="${2:-}"; shift ;;
        --dry-run)     SB_DRY_RUN=1 ;;
        --no-lock)     USE_LOCK=0 ;;
        --quiet)       SB_QUIET=1 ;;
        -h|--help)     usage; exit 0 ;;
        *)             usage >&2; die "unknown argument: $1" ;;
    esac
    shift
done
: "${JSON_PATH:=$SB_LIB/hardware.json}"

if [[ -n $FORCE_TIER && ! $FORCE_TIER =~ ^[1-4]$ ]]; then
    SB_EXIT_CODE=2 die "--force-tier must be 1, 2, 3 or 4 (got '${FORCE_TIER}')"
fi

# Concurrent runs would race on profile.env. Kill any predecessor first.
((USE_LOCK)) && sb_single_instance "hardware-detect"

# ===========================================================================
# PROBE: CPU
# ===========================================================================
# 0 is the "not yet determined" sentinel. Seeding these to 1 would make the
# validity checks below pass on the default and silently skip every fallback.
CPU_MODEL="unknown"; CPU_CORES=0; CPU_THREADS=0
CPU_AVX2=0; CPU_AVX512=0; CPU_VIRT=0

probe_cpu() {
    if [[ -r /proc/cpuinfo ]]; then
        CPU_MODEL="$(awk -F': ' '/^model name/{print $2; exit}' /proc/cpuinfo 2>/dev/null || true)"
        [[ -z $CPU_MODEL ]] && CPU_MODEL="$(awk -F': ' '/^Model/{print $2; exit}' /proc/cpuinfo 2>/dev/null || true)"
        grep -qm1 '\bavx2\b'    /proc/cpuinfo 2>/dev/null && CPU_AVX2=1
        grep -qm1 'avx512'      /proc/cpuinfo 2>/dev/null && CPU_AVX512=1
        grep -qm1 -E '\b(vmx|svm)\b' /proc/cpuinfo 2>/dev/null && CPU_VIRT=1
    fi
    CPU_MODEL="${CPU_MODEL:-unknown}"
    CPU_MODEL="$(printf '%s' "$CPU_MODEL" | tr -s ' ' | sed 's/^ *//;s/ *$//')"

    # Logical CPUs. Take the LARGEST credible signal rather than the first that
    # answers. `nproc` reports scheduler affinity, which is not the machine's
    # CPU count — observed returning 1 on a 4-core/8-thread host. A first-wins
    # chain never reaches the other sources because nproc "succeeded", and
    # undercounting here silently downgrades the tier and starves every thread
    # limit derived from it. This is a HOST profiler, so the physical count is
    # the truthful answer.
    local n
    for n in "$(nproc 2>/dev/null || echo 0)" \
             "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 0)" \
             "$(grep -c '^processor' /proc/cpuinfo 2>/dev/null || echo 0)"; do
        [[ $n =~ ^[0-9]+$ ]] && ((n > CPU_THREADS)) && CPU_THREADS="$n"
    done
    ((CPU_THREADS > 0)) || CPU_THREADS=1

    # Physical cores matter more than SMT threads for llama.cpp throughput.
    if sb_have lscpu; then
        local sockets per_socket
        sockets="$(lscpu 2>/dev/null | awk -F': *' '/^Socket\(s\)/{print $2; exit}')"
        per_socket="$(lscpu 2>/dev/null | awk -F': *' '/^Core\(s\) per socket/{print $2; exit}')"
        if [[ $sockets =~ ^[0-9]+$ && $per_socket =~ ^[0-9]+$ ]] \
           && ((sockets > 0)) && ((per_socket > 0)); then
            CPU_CORES=$((sockets * per_socket))
        fi
    fi

    # lscpu is absent on minimal images and inside the installer. /proc/cpuinfo
    # carries the same facts.
    if ((CPU_CORES == 0)) && [[ -r /proc/cpuinfo ]]; then
        local per_socket sockets
        per_socket="$(awk -F': *' '/^cpu cores/{print $2; exit}' /proc/cpuinfo 2>/dev/null || echo 0)"
        sockets="$(awk -F': *' '/^physical id/{print $2}' /proc/cpuinfo 2>/dev/null | sort -u | wc -l)"
        if [[ $per_socket =~ ^[0-9]+$ ]] && ((per_socket > 0)) && ((sockets > 0)); then
            CPU_CORES=$((per_socket * sockets))
        fi
    fi

    # Last resort: SMT threads. Overstates capability slightly, which is the
    # right direction to err for a floor check — unlike understating it to 1.
    ((CPU_CORES > 0)) || CPU_CORES="$CPU_THREADS"
    return 0
}

# ===========================================================================
# PROBE: RAM
# ===========================================================================
RAM_TOTAL_MB=0
probe_ram() {
    if [[ -r /proc/meminfo ]]; then
        local kb; kb="$(awk '/^MemTotal:/{print $2; exit}' /proc/meminfo 2>/dev/null || echo 0)"
        [[ $kb =~ ^[0-9]+$ ]] && RAM_TOTAL_MB=$((kb / 1024))
    fi
    return 0
}

# ===========================================================================
# PROBE: GPU
#
# Three-stage strategy, because on first boot the driver is not installed yet:
#   1. vendor-specific tooling (nvidia-smi / amdgpu sysfs) -> exact VRAM
#   2. DRM sysfs                                           -> exact VRAM, no driver tooling
#   3. lspci device class                                  -> vendor only, VRAM unknown
# Stage 3 deliberately yields VRAM_UNKNOWN=1 so the tier falls back to CPU
# classification rather than optimistically provisioning a 70B model.
# ===========================================================================
GPU_VENDOR="none"; GPU_COUNT=0; GPU_NAMES=""
VRAM_TOTAL_MB=0; VRAM_LARGEST_MB=0; VRAM_UNKNOWN=0
GPU_HOMOGENEOUS=1

_gpu_record() {
    local name="$1" vram_mb="$2"
    GPU_NAMES="${GPU_NAMES:+$GPU_NAMES; }${name}"
    ((GPU_COUNT++))
    if [[ $vram_mb =~ ^[0-9]+$ ]] && ((vram_mb > 0)); then
        VRAM_TOTAL_MB=$((VRAM_TOTAL_MB + vram_mb))
        ((vram_mb > VRAM_LARGEST_MB)) && VRAM_LARGEST_MB="$vram_mb"
    else
        VRAM_UNKNOWN=1
    fi
}

probe_gpu_nvidia() {
    sb_have nvidia-smi || return 1
    local out
    out="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null || true)"
    [[ -n $out ]] || return 1
    GPU_VENDOR="nvidia"
    local name mib
    while IFS=',' read -r name mib; do
        [[ -z ${name// /} ]] && continue
        name="$(printf '%s' "$name" | sed 's/^ *//;s/ *$//')"
        mib="$(printf '%s' "$mib" | tr -cd '0-9')"
        _gpu_record "$name" "${mib:-0}"
    done <<<"$out"
    ((GPU_COUNT > 0)) || return 1
    log "nvidia-smi: ${GPU_COUNT} GPU(s), ${VRAM_TOTAL_MB} MiB total VRAM"
    return 0
}

probe_gpu_amd() {
    # amdgpu exposes VRAM in sysfs without needing the ROCm stack installed.
    local f found=0 bytes name card
    for f in /sys/class/drm/card*/device/mem_info_vram_total; do
        [[ -r $f ]] || continue
        bytes="$(cat -- "$f" 2>/dev/null || echo 0)"
        [[ $bytes =~ ^[0-9]+$ ]] || continue
        ((bytes > 0)) || continue
        card="${f#/sys/class/drm/}"; card="${card%%/*}"
        name="amdgpu ${card}"
        if [[ -r "/sys/class/drm/${card}/device/product_name" ]]; then
            name="$(cat -- "/sys/class/drm/${card}/device/product_name" 2>/dev/null || echo "$name")"
        fi
        GPU_VENDOR="amd"
        _gpu_record "$name" "$((bytes / 1024 / 1024))"
        found=1
    done
    ((found)) || return 1
    log "amdgpu sysfs: ${GPU_COUNT} GPU(s), ${VRAM_TOTAL_MB} MiB total VRAM"
    return 0
}

probe_gpu_pci() {
    # Last resort: we know a GPU is physically present but not how big it is.
    sb_have lspci || return 1
    local line ids
    ids="$(lspci -nn 2>/dev/null | grep -E '\[(0300|0302|0380)\]' || true)"
    [[ -n $ids ]] || return 1

    local vendors=""
    while IFS= read -r line; do
        [[ -z $line ]] && continue
        case "$line" in
            *'[10de:'*) vendors+=" nvidia"; _gpu_record "NVIDIA (driver not loaded)" 0 ;;
            *'[1002:'*|*'[1022:'*) vendors+=" amd"; _gpu_record "AMD (driver not loaded)" 0 ;;
            *'[8086:'*) vendors+=" intel"; _gpu_record "Intel graphics" 0 ;;
            *) vendors+=" other"; _gpu_record "unknown display controller" 0 ;;
        esac
    done <<<"$ids"

    # shellcheck disable=SC2086
    set -- $vendors
    GPU_VENDOR="$1"
    local v; for v in "$@"; do [[ $v == "$GPU_VENDOR" ]] || GPU_HOMOGENEOUS=0; done
    VRAM_UNKNOWN=1
    warn "GPU present (${GPU_VENDOR}) but VRAM is unreadable — no vendor driver yet."
    warn "Tier will be computed from CPU/RAM. Re-run after 30-gpu-runtime.sh."
    return 0
}

probe_gpu() {
    probe_gpu_nvidia && return 0
    probe_gpu_amd    && return 0
    probe_gpu_pci    && return 0
    GPU_VENDOR="none"
    log "no discrete GPU detected — CPU inference path"
    return 0
}

# ===========================================================================
# PROBE: model storage
# ===========================================================================
MODEL_DIR="${SAMBUCA_MODEL_DIR:-${SB_LIB}/ollama}"
DISK_FREE_MB=0
probe_disk() {
    local target="$MODEL_DIR"
    # Walk up to the nearest existing ancestor so df works pre-provisioning.
    while [[ ! -d $target && $target != "/" ]]; do target="$(dirname -- "$target")"; done
    local kb; kb="$(df -Pk -- "$target" 2>/dev/null | awk 'NR==2{print $4}' || echo 0)"
    [[ $kb =~ ^[0-9]+$ ]] && DISK_FREE_MB=$((kb / 1024))
    return 0
}

# ===========================================================================
# CLASSIFY
# ===========================================================================
TIER=4; TIER_NAME="low-resource"; TIER_REASON=""; TIER_OVERRIDDEN=0
TIER_UNSUPPORTED=0
VRAM_BUDGET_MB=0

classify() {
    VRAM_BUDGET_MB=$((VRAM_TOTAL_MB * VRAM_BUDGET_FRACTION / 100))

    if [[ -n $FORCE_TIER ]]; then
        TIER="$FORCE_TIER"; TIER_OVERRIDDEN=1
        TIER_REASON="operator override via --force-tier"
    elif ((VRAM_UNKNOWN == 0)) && ((VRAM_TOTAL_MB >= TIER1_VRAM_MB)); then
        TIER=1; TIER_REASON="${VRAM_TOTAL_MB} MiB VRAM >= ${TIER1_VRAM_MB} MiB"
    elif ((VRAM_UNKNOWN == 0)) && ((VRAM_TOTAL_MB >= TIER2_VRAM_MB)); then
        TIER=2; TIER_REASON="${VRAM_TOTAL_MB} MiB VRAM >= ${TIER2_VRAM_MB} MiB"
    elif ((CPU_CORES >= TIER3_CPU_CORES)) && ((RAM_TOTAL_MB >= TIER3_RAM_MB)); then
        TIER=3; TIER_REASON="${CPU_CORES} cores / ${RAM_TOTAL_MB} MiB RAM, GPU insufficient"
    else
        TIER=4; TIER_REASON="${CPU_CORES} cores / ${RAM_TOTAL_MB} MiB RAM below tier-3 floor"
    fi

    if ((RAM_TOTAL_MB > 0)) && ((RAM_TOTAL_MB < MIN_RAM_MB)); then
        warn "this machine has ${RAM_TOTAL_MB} MiB of RAM; sambuca needs at least ${MIN_RAM_MB} MiB"
        warn "  that is not a slow install, it is one that will not come up:"
        warn "    the file server alone wants ~2000 MiB"
        warn "    the photo library wants ~4000 MiB with its database"
        warn "    the smallest chat model wants ~2500 MiB"
        warn "  a Pi Zero, a thin client or a 2 GiB VM is below this floor. A"
        warn "  second-hand office desktop with 8 GiB is not, and costs very little."
        TIER_UNSUPPORTED=1
    fi

    case "$TIER" in
        1) TIER_NAME="heavy-gpu" ;;
        2) TIER_NAME="mid-gpu" ;;
        3) TIER_NAME="cpu-capable" ;;
        4) TIER_NAME="low-resource" ;;
    esac

    # A tier is a claim about GPU capacity. If the GPU is unusable, the compose
    # GPU overlay must not be selected even when the tier was forced.
    if ((VRAM_UNKNOWN == 1)) || [[ $GPU_VENDOR == none || $GPU_VENDOR == intel || $GPU_VENDOR == other ]]; then
        GPU_PROFILE="cpu"
    else
        GPU_PROFILE="$GPU_VENDOR"
    fi

    if [[ $GPU_VENDOR == intel ]]; then
        warn "Intel graphics detected: treated as CPU-tier. Ollama ships no supported"
        warn "SYCL/oneAPI backend, so claiming GPU acceleration here would be a lie."
    fi
    return 0
}

# ===========================================================================
# MODEL SELECTION
#
# Sourced from engine/profiles/tierN.env so the catalogue is data, not code.
# Selection is then trimmed against the real VRAM budget and free disk.
# ===========================================================================
MODEL_CHAT=""; MODEL_CODE=""; MODEL_VISION=""; MODEL_EMBED=""
MODEL_SET_EST_MB=0; MODEL_DOWNGRADED=0
IMAGE_ENABLED=0; IMAGE_AVAILABLE_OPT_IN=0; IMAGE_SET_EST_MB=0
IMAGE_MODEL_NAME=""; IMAGE_MODEL_LICENCE=""; IMAGE_WORKFLOW=""; IMAGE_STEPS=4
IMAGE_CHECKPOINT_URL=""; IMAGE_CHECKPOINT_FILE=""; IMAGE_VRAM_ARGS="--normalvram"
IMAGE_VRAM_MB=0; IMAGE_CORESIDENT_BUDGET_MB=40000
IMAGE_HANDOFF="none"; IMAGE_DROPPED=0

select_models() {
    local profile="${_SB_SELF_DIR}/profiles/tier${TIER}.env"
    if [[ -r $profile ]]; then
        # shellcheck disable=SC1090
        source "$profile"
    else
        warn "profile ${profile} missing — falling back to the tier-4 model set"
        MODEL_CHAT="llama3.2:3b-instruct-q4_K_M"
        MODEL_EMBED="nomic-embed-text"
        MODEL_SET_EST_MB=2600
    fi

    # Tier 1 spans 24 GB to 96 GB+. Split it on the real budget rather than
    # inventing more tiers.
    if ((TIER == 1)); then
        if ((VRAM_BUDGET_MB >= ${TIER1_XL_BUDGET_MB:-40000})); then
            MODEL_CHAT="${MODEL_CHAT_XL:-$MODEL_CHAT}"
            MODEL_SET_EST_MB="${MODEL_SET_EST_XL_MB:-$MODEL_SET_EST_MB}"
        fi
    fi

    # An owner may force the image plane on where the tier only offers it.
    if [[ ${SAMBUCA_IMAGE_ENABLED:-} == 1 ]] && ((IMAGE_AVAILABLE_OPT_IN == 1)); then
        IMAGE_ENABLED=1
        log "image plane enabled by owner opt-in"
        if ((RAM_TOTAL_MB < ${IMAGE_OPT_IN_MIN_RAM_MB:-0})); then
            warn "image opt-in wants >= ${IMAGE_OPT_IN_MIN_RAM_MB} MiB RAM, this machine has ${RAM_TOTAL_MB} MiB"
            warn "  generation will be slow and may be killed under memory pressure"
        fi
    fi

    # Disk guard: never queue a pull that cannot land. The image checkpoint is
    # counted here — it is the single largest download on the machine, and
    # leaving it out of the sum is how a 16 GiB fetch fills the root volume at
    # 80% through a walk-away install.
    #
    # DROP ORDER IS FIXED AND STATED: image plane, then vision, then code. Not
    # because that ranking is universally right, but because a predictable rule
    # an owner can read and override beats a clever one they cannot anticipate.
    local needed=$(( (MODEL_SET_EST_MB + IMAGE_SET_EST_MB) * MODEL_DISK_HEADROOM_PCT / 100 ))
    if ((DISK_FREE_MB > 0)) && ((DISK_FREE_MB < needed)); then
        warn "model set needs ~${needed} MiB but only ${DISK_FREE_MB} MiB free at ${MODEL_DIR}"

        if ((IMAGE_ENABLED == 1)); then
            warn "dropping the image plane (${IMAGE_SET_EST_MB} MiB) to fit"
            IMAGE_ENABLED=0; IMAGE_DROPPED=1; IMAGE_SET_EST_MB=0
            needed=$(( MODEL_SET_EST_MB * MODEL_DISK_HEADROOM_PCT / 100 ))
        fi

        if ((DISK_FREE_MB < needed)); then
            warn "dropping the optional code/vision models to fit"
            MODEL_CODE=""; MODEL_VISION=""
            MODEL_DOWNGRADED=1
            MODEL_SET_EST_MB="${MODEL_SET_EST_CHAT_ONLY_MB:-$MODEL_SET_EST_MB}"
        fi
    fi
    return 0
}

# ===========================================================================
# RESOURCE ARBITRATION
#
# The OOM this prevents: Ollama holds a 14B model resident (~9 GB) while Immich
# starts a CLIP + face-recognition batch on the same device. Both allocators
# believe they own the card. The arbitration rule is explicit and one-directional:
#
#   THE INFERENCE ENGINE OWNS THE GPU. Background ML is a guest.
#
#   - Below IMMICH_GPU_MIN_VRAM_MB of total VRAM, Immich ML is pinned to the CPU.
#     There is no negotiation and no "try GPU first" path.
#   - Above it, Immich may use the GPU, but Ollama's keep-alive is bounded so
#     idle VRAM is returned instead of being camped on indefinitely.
#   - Concurrency and container memory ceilings are set from real RAM, so a
#     runaway worker hits its cgroup limit instead of the kernel OOM killer.
# ===========================================================================
OLLAMA_MAX_LOADED=1; OLLAMA_PARALLEL=1; OLLAMA_KEEP_ALIVE="5m"
OLLAMA_FLASH_ATTN=0; OLLAMA_MEM_LIMIT="4g"; OLLAMA_CTX=4096
IMMICH_ML_DEVICE="cpu"; IMMICH_ML_IMAGE_SUFFIX=""; IMMICH_ML_MEM_LIMIT="2g"
COMFYUI_MEM_LIMIT="12g"; COMFYUI_OUTPUT_TMPFS="2g"; COMFYUI_RESERVE_VRAM_GB=1
IMMICH_ML_WORKERS=1; IMMICH_ML_THREADS=2

arbitrate() {
    # --- inference engine ---------------------------------------------------
    case "$TIER" in
        1) OLLAMA_MAX_LOADED=2; OLLAMA_PARALLEL=4; OLLAMA_KEEP_ALIVE="30m"; OLLAMA_CTX=16384 ;;
        2) OLLAMA_MAX_LOADED=1; OLLAMA_PARALLEL=2; OLLAMA_KEEP_ALIVE="10m"; OLLAMA_CTX=8192  ;;
        3) OLLAMA_MAX_LOADED=1; OLLAMA_PARALLEL=1; OLLAMA_KEEP_ALIVE="5m";  OLLAMA_CTX=8192  ;;
        4) OLLAMA_MAX_LOADED=1; OLLAMA_PARALLEL=1; OLLAMA_KEEP_ALIVE="2m";  OLLAMA_CTX=4096  ;;
    esac
    # Flash attention materially cuts KV-cache VRAM, but only on CUDA.
    [[ $GPU_PROFILE == nvidia ]] && OLLAMA_FLASH_ATTN=1

    # CPU inference lives in host RAM: cap it at half, floor 2 GiB.
    if [[ $GPU_PROFILE == cpu ]]; then
        local half=$((RAM_TOTAL_MB / 2))
        ((half < 2048)) && half=2048
        OLLAMA_MEM_LIMIT="$((half))m"
    else
        OLLAMA_MEM_LIMIT="$(( RAM_TOTAL_MB / 4 < 4096 ? 4096 : RAM_TOTAL_MB / 4 ))m"
    fi

    # --- image plane (ComfyUI) ---------------------------------------------
    # The arbitration rule for BACKGROUND ML is "the inference engine owns the
    # GPU, the guest yields". That rule does not transfer here, because image
    # generation is not background work — a human is sitting there watching a
    # progress bar. So the rule is a HANDOFF instead: whoever the owner is
    # actively waiting on gets the card, and the other one is unloaded first.
    #
    #   coresident  the card fits both. Nobody yields, nothing is unloaded.
    #   handoff     the card fits one at a time. The chat model is evicted
    #               before a generation starts and reloads on the next message.
    #   none        no contention (CPU generation, or no image plane at all).
    #
    # Without this, the two allocators each see "free VRAM" at the moment they
    # ask, and the failure lands mid-generation as an out-of-memory abort
    # rather than at the point where the decision was actually made.
    # A tier is a claim about the CLASS of machine; it is not a measurement of
    # the card in this one. A forced tier, or a card at the bottom of tier 2,
    # can select an image model the GPU cannot hold. Check the measured budget
    # against the model's real footprint before promising anything.
    if ((IMAGE_ENABLED == 1)) && [[ $GPU_PROFILE != cpu ]] && ((IMAGE_VRAM_MB > 0)); then
        if ((VRAM_BUDGET_MB < IMAGE_MIN_VRAM_MB)); then
            warn "image plane disabled: ${VRAM_BUDGET_MB} MiB VRAM budget is below the ${IMAGE_MIN_VRAM_MB} MiB floor"
            warn "  ${IMAGE_MODEL_NAME} needs ~${IMAGE_VRAM_MB} MiB resident, and cannot stream into this little"
            IMAGE_ENABLED=0; IMAGE_DROPPED=1; IMAGE_SET_EST_MB=0
        elif ((VRAM_BUDGET_MB < IMAGE_VRAM_MB)); then
            # It fits only by streaming blocks from system RAM. That is slower,
            # and it is the difference between a picture and an OOM abort.
            IMAGE_VRAM_ARGS="--lowvram"
            log "image plane: ${VRAM_BUDGET_MB} MiB < ${IMAGE_VRAM_MB} MiB needed — forcing --lowvram"
        fi
    fi

    if ((IMAGE_ENABLED == 0)); then
        IMAGE_HANDOFF="none"
        # Blank the ComfyUI knobs so a reader of profile.env is not misled into
        # thinking a disabled plane has a memory mode.
        IMAGE_VRAM_ARGS=""; COMFYUI_MEM_LIMIT=""; COMFYUI_OUTPUT_TMPFS=""
    elif [[ $GPU_PROFILE == cpu ]]; then
        IMAGE_HANDOFF="none"
        log "image plane: CPU generation — no VRAM contention, expect minutes per picture"
    elif ((VRAM_BUDGET_MB >= IMAGE_CORESIDENT_BUDGET_MB)); then
        IMAGE_HANDOFF="coresident"
        log "image plane: ${VRAM_BUDGET_MB} MiB budget holds both models — no handoff needed"
    else
        IMAGE_HANDOFF="handoff"
        # A 30-minute keep-alive means the first picture waits half an hour for
        # the chat model to time out, or races it. Bound it so the card can
        # actually change hands.
        case "$OLLAMA_KEEP_ALIVE" in
            30m|10m) OLLAMA_KEEP_ALIVE="5m" ;;
        esac
        log "image plane: ${VRAM_BUDGET_MB} MiB budget holds one model at a time — handoff mode"
        log "  the chat model is unloaded before a picture and reloads after it"
    fi

    # ComfyUI's own ceilings. The tmpfs holds generated pictures in RAM and
    # never on disk, so it is sized as a fraction of real memory rather than
    # from a round number that happens to fit the developer's machine.
    if ((IMAGE_ENABLED == 1)); then
        local cmem=$(( RAM_TOTAL_MB / 2 ))
        ((cmem < 8192)) && cmem=8192
        COMFYUI_MEM_LIMIT="${cmem}m"
        local tmpfs_mb=$(( RAM_TOTAL_MB / 16 ))
        ((tmpfs_mb < 512))  && tmpfs_mb=512
        ((tmpfs_mb > 4096)) && tmpfs_mb=4096
        COMFYUI_OUTPUT_TMPFS="${tmpfs_mb}m"
        COMFYUI_RESERVE_VRAM_GB="${COMFYUI_RESERVE_VRAM_GB:-1}"
    fi

    # --- background ML (Immich) --------------------------------------------
    if ((VRAM_UNKNOWN == 0)) && ((VRAM_TOTAL_MB >= IMMICH_GPU_MIN_VRAM_MB)) \
       && [[ $GPU_PROFILE != cpu ]]; then
        IMMICH_ML_DEVICE="$GPU_PROFILE"
        case "$GPU_PROFILE" in
            nvidia) IMMICH_ML_IMAGE_SUFFIX="-cuda" ;;
            amd)    IMMICH_ML_IMAGE_SUFFIX="-rocm" ;;
        esac
        log "VRAM arbitration: ${VRAM_TOTAL_MB} MiB is enough to share — Immich ML on ${GPU_PROFILE}"
    else
        IMMICH_ML_DEVICE="cpu"; IMMICH_ML_IMAGE_SUFFIX=""
        if [[ $GPU_PROFILE != cpu ]]; then
            log "VRAM arbitration: ${VRAM_TOTAL_MB} MiB < ${IMMICH_GPU_MIN_VRAM_MB} MiB — Immich ML pinned to CPU"
            log "  rationale: the inference engine owns the GPU; background indexing yields."
        fi
    fi

    # Leave at least 2 cores and 2 GiB for everything that is not photo indexing.
    IMMICH_ML_THREADS=$(( CPU_CORES > 3 ? CPU_CORES - 2 : 1 ))
    ((IMMICH_ML_THREADS > 8)) && IMMICH_ML_THREADS=8
    IMMICH_ML_WORKERS=$(( TIER <= 2 ? 2 : 1 ))
    local ml_mb=$(( RAM_TOTAL_MB / 8 ))
    ((ml_mb < 2048)) && ml_mb=2048
    ((ml_mb > 8192)) && ml_mb=8192
    IMMICH_ML_MEM_LIMIT="${ml_mb}m"
    return 0
}

# ===========================================================================
# EMIT
# ===========================================================================
render_env() {
    cat <<EOF
# ---------------------------------------------------------------------------
# GENERATED BY sambuca hardware-detect — DO NOT EDIT.
# Overrides belong in ${SB_ETC}/profile.local.env, which is sourced after this.
# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)
#
# MOST OF WHAT FOLLOWS IS A MEASUREMENT, NOT A CONTROL, and the difference
# matters if you are tempted to edit something here. Values like CPU_AVX2,
# GPU_COUNT and VRAM_LARGEST_MB record what this machine IS: they are written
# for the completion report and for a human diagnosing a tier decision, and
# changing them changes nothing.
#
# The ones that genuinely drive behaviour are read by the provisioning phases:
# TIER, TIER_UNSUPPORTED, GPU_PROFILE, IMAGE_ENABLED, the MODEL_* choices and
# the container limits. To change one of those, set it in profile.local.env —
# which is sourced after this file and therefore wins.
# ---------------------------------------------------------------------------

# --- classification ---
SAMBUCA_TIER=${TIER}
SAMBUCA_TIER_NAME=${TIER_NAME}
SAMBUCA_TIER_REASON="${TIER_REASON}"
SAMBUCA_TIER_OVERRIDDEN=${TIER_OVERRIDDEN}
SAMBUCA_TIER_UNSUPPORTED=${TIER_UNSUPPORTED}

# --- cpu / memory ---
SAMBUCA_CPU_MODEL="${CPU_MODEL}"
SAMBUCA_CPU_CORES=${CPU_CORES}
SAMBUCA_CPU_THREADS=${CPU_THREADS}
SAMBUCA_CPU_AVX2=${CPU_AVX2}
SAMBUCA_CPU_AVX512=${CPU_AVX512}
SAMBUCA_CPU_VIRT=${CPU_VIRT}
SAMBUCA_RAM_TOTAL_MB=${RAM_TOTAL_MB}

# --- gpu ---
SAMBUCA_GPU_VENDOR=${GPU_VENDOR}
SAMBUCA_GPU_PROFILE=${GPU_PROFILE}
SAMBUCA_GPU_COUNT=${GPU_COUNT}
SAMBUCA_GPU_NAMES="${GPU_NAMES}"
SAMBUCA_GPU_HOMOGENEOUS=${GPU_HOMOGENEOUS}
SAMBUCA_VRAM_TOTAL_MB=${VRAM_TOTAL_MB}
SAMBUCA_VRAM_LARGEST_MB=${VRAM_LARGEST_MB}
SAMBUCA_VRAM_BUDGET_MB=${VRAM_BUDGET_MB}
SAMBUCA_VRAM_UNKNOWN=${VRAM_UNKNOWN}

# --- storage ---
SAMBUCA_MODEL_DIR=${MODEL_DIR}
SAMBUCA_DISK_FREE_MB=${DISK_FREE_MB}
SAMBUCA_MODEL_SET_EST_MB=${MODEL_SET_EST_MB}
SAMBUCA_MODEL_DOWNGRADED=${MODEL_DOWNGRADED}

# --- model set (consumed by 70-models.sh) ---
SAMBUCA_MODEL_CHAT=${MODEL_CHAT}
SAMBUCA_MODEL_CODE=${MODEL_CODE}
SAMBUCA_MODEL_VISION=${MODEL_VISION}
SAMBUCA_MODEL_EMBED=${MODEL_EMBED}

# --- image plane ---
SAMBUCA_IMAGE_ENABLED=${IMAGE_ENABLED}
SAMBUCA_IMAGE_AVAILABLE_OPT_IN=${IMAGE_AVAILABLE_OPT_IN}
SAMBUCA_IMAGE_DROPPED=${IMAGE_DROPPED}
SAMBUCA_IMAGE_MODEL_NAME="${IMAGE_MODEL_NAME}"
SAMBUCA_IMAGE_MODEL_LICENCE="${IMAGE_MODEL_LICENCE}"
SAMBUCA_IMAGE_CHECKPOINT_URL="${IMAGE_CHECKPOINT_URL}"
SAMBUCA_IMAGE_CHECKPOINT_FILE="${IMAGE_CHECKPOINT_FILE}"
SAMBUCA_IMAGE_WORKFLOW=${IMAGE_WORKFLOW}
SAMBUCA_IMAGE_STEPS=${IMAGE_STEPS}
SAMBUCA_IMAGE_SET_EST_MB=${IMAGE_SET_EST_MB}
SAMBUCA_IMAGE_HANDOFF=${IMAGE_HANDOFF}
COMFYUI_VRAM_ARGS="${IMAGE_VRAM_ARGS}"
COMFYUI_MEM_LIMIT=${COMFYUI_MEM_LIMIT}
COMFYUI_OUTPUT_TMPFS=${COMFYUI_OUTPUT_TMPFS}
COMFYUI_RESERVE_VRAM_GB=${COMFYUI_RESERVE_VRAM_GB}

# --- compose wiring ---
# Phase 60-stack appends compose/gpu.<profile>.<bundle>.yml for each ENABLED
# bundle. It is not one file: an overlay that names a service an unselected
# bundle does not define invalidates the whole compose project.
#
# THE VARIABLE THAT USED TO SIT HERE IS GONE. SAMBUCA_COMPOSE_GPU_PROFILE held
# exactly the same value as SAMBUCA_GPU_PROFILE above, and nothing read it —
# 60-stack uses SAMBUCA_GPU_PROFILE. Two names for one fact, one of which does
# nothing, in a file an owner might reasonably edit: setting the dead one to
# force CPU would have changed nothing, with no way to tell why.

# --- inference engine limits ---
OLLAMA_MAX_LOADED_MODELS=${OLLAMA_MAX_LOADED}
OLLAMA_NUM_PARALLEL=${OLLAMA_PARALLEL}
OLLAMA_KEEP_ALIVE=${OLLAMA_KEEP_ALIVE}
OLLAMA_FLASH_ATTENTION=${OLLAMA_FLASH_ATTN}
OLLAMA_CONTEXT_LENGTH=${OLLAMA_CTX}
OLLAMA_MEM_LIMIT=${OLLAMA_MEM_LIMIT}

# --- background ML limits (VRAM arbitration) ---
IMMICH_ML_DEVICE=${IMMICH_ML_DEVICE}
IMMICH_ML_IMAGE_SUFFIX=${IMMICH_ML_IMAGE_SUFFIX}
IMMICH_ML_MEM_LIMIT=${IMMICH_ML_MEM_LIMIT}
MACHINE_LEARNING_WORKERS=${IMMICH_ML_WORKERS}
MACHINE_LEARNING_WORKER_TIMEOUT=120
IMMICH_ML_THREADS=${IMMICH_ML_THREADS}
EOF
}

render_json() {
    cat <<EOF
{
  "schema": 1,
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "host": "$(sb_json_escape "$(hostname 2>/dev/null || echo unknown)")",
  "tier": { "id": ${TIER}, "name": "${TIER_NAME}", "reason": "$(sb_json_escape "$TIER_REASON")", "overridden": $([[ $TIER_OVERRIDDEN == 1 ]] && echo true || echo false) },
  "cpu": { "model": "$(sb_json_escape "$CPU_MODEL")", "cores": ${CPU_CORES}, "threads": ${CPU_THREADS}, "avx2": $([[ $CPU_AVX2 == 1 ]] && echo true || echo false), "avx512": $([[ $CPU_AVX512 == 1 ]] && echo true || echo false) },
  "memory": { "total_mb": ${RAM_TOTAL_MB} },
  "gpu": { "vendor": "${GPU_VENDOR}", "runtime_profile": "${GPU_PROFILE}", "count": ${GPU_COUNT}, "names": "$(sb_json_escape "$GPU_NAMES")", "vram_total_mb": ${VRAM_TOTAL_MB}, "vram_largest_mb": ${VRAM_LARGEST_MB}, "vram_budget_mb": ${VRAM_BUDGET_MB}, "vram_unknown": $([[ $VRAM_UNKNOWN == 1 ]] && echo true || echo false) },
  "storage": { "model_dir": "${MODEL_DIR}", "free_mb": ${DISK_FREE_MB}, "model_set_est_mb": ${MODEL_SET_EST_MB}, "downgraded": $([[ $MODEL_DOWNGRADED == 1 ]] && echo true || echo false) },
  "models": { "chat": "${MODEL_CHAT}", "code": "${MODEL_CODE}", "vision": "${MODEL_VISION}", "embed": "${MODEL_EMBED}" },
  "arbitration": { "ollama_max_loaded": ${OLLAMA_MAX_LOADED}, "ollama_parallel": ${OLLAMA_PARALLEL}, "ollama_keep_alive": "${OLLAMA_KEEP_ALIVE}", "immich_ml_device": "${IMMICH_ML_DEVICE}", "immich_ml_mem_limit": "${IMMICH_ML_MEM_LIMIT}" }
}
EOF
}

# ===========================================================================
# MAIN
# ===========================================================================
main() {
    probe_cpu
    probe_ram
    probe_gpu
    probe_disk
    classify
    select_models
    arbitrate

    log "tier ${TIER} (${TIER_NAME}) — ${TIER_REASON}"
    log "chat model: ${MODEL_CHAT:-none}  |  runtime: ${GPU_PROFILE}  |  immich-ml: ${IMMICH_ML_DEVICE}"

    if ((EMIT_JSON)); then render_json; fi
    if ((PRINT_ONLY)); then render_env; return 0; fi
    if [[ $SB_DRY_RUN == 1 ]]; then log "dry-run: not writing profile.env / hardware.json"; return 0; fi

    render_env  | sb_atomic_write "${OUTPUT_DIR}/profile.env" 0644 \
        || { err "failed writing ${OUTPUT_DIR}/profile.env"; return 1; }
    render_json | sb_atomic_write "$JSON_PATH" 0644 \
        || warn "failed writing ${JSON_PATH} (non-fatal)"

    # A local override file always exists so operators have an obvious seam.
    if [[ ! -f "${OUTPUT_DIR}/profile.local.env" ]]; then
        printf '%s\n' \
            '# sambuca local overrides. Sourced AFTER profile.env; survives regeneration.' \
            '# Example: pin a tier and a model regardless of what was detected.' \
            '#   SAMBUCA_TIER=2' \
            '#   SAMBUCA_MODEL_CHAT=qwen2.5:14b-instruct-q4_K_M' \
            | sb_atomic_write "${OUTPUT_DIR}/profile.local.env" 0644 || true
    fi

    ok "profile written: ${OUTPUT_DIR}/profile.env"
    return 0
}

main "$@"
