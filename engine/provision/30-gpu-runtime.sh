# shellcheck shell=bash
# sambuca :: phase 30-gpu-runtime — vendor driver + container runtime, then RE-PROFILE.
#
# This phase is where the hardware profile becomes trustworthy. On first boot
# hardware-detect.sh could only see "an NVIDIA card exists" via lspci; after the
# driver lands, VRAM is readable and the tier settles. The re-run at the end of
# this phase is load-bearing, not cosmetic.

# shellcheck source=/dev/null
source "${SB_ETC}/profile.env" 2>/dev/null || true
: "${SAMBUCA_GPU_VENDOR:=none}"

export DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------------
# A GPU IS AN ACCELERATOR, NOT THE APPLIANCE — and this phase used to `die` when
# one could not be set up, which threw the whole machine away over it.
#
# first-boot.sh stops the run on a failing phase, and this is phase 30. So an
# NVIDIA card whose driver would not install — no non-free mirror reachable, an
# unsupported card, Secure Boot refusing an unsigned DKMS module, which is common
# and not the owner's fault — meant no file server, no photo library, no password
# manager and no completion report. Because a graphics driver failed.
#
# The AMD branch in this same file already had it right: missing firmware warns
# and continues, a missing /dev/kfd warns and continues. Only the NVIDIA path
# treated an accelerator as life-or-death.
#
# DEMOTING RATHER THAN MERELY WARNING IS THE LOAD-BEARING PART. 60-stack picks
# its compose overlay from SAMBUCA_GPU_PROFILE, and an overlay naming a runtime
# that was never registered invalidates the entire compose project — so a bare
# warning would move the death two phases later and make it harder to read.
# ---------------------------------------------------------------------------
demote_to_cpu() {
    warn "$1"
    warn "  Continuing on CPU. Everything else installs normally, and the AI"
    warn "  half still works — slower, and only for the models that fit."
    warn "  To retry after fixing it:  sambuca-first-boot --only 30-gpu-runtime --force"

    # Rewrite the profile IN PLACE. Overwriting the file would discard the tier
    # and the model catalogue that 70-models reads out of it.
    if [[ -f "${SB_ETC}/profile.env" ]]; then
        sed -i 's/^SAMBUCA_GPU_PROFILE=.*/SAMBUCA_GPU_PROFILE=cpu/' \
            "${SB_ETC}/profile.env" 2>/dev/null || true
        grep -q '^SAMBUCA_GPU_PROFILE=' "${SB_ETC}/profile.env" 2>/dev/null \
            || printf 'SAMBUCA_GPU_PROFILE=cpu\n' >>"${SB_ETC}/profile.env"
    fi
    SAMBUCA_GPU_PROFILE=cpu
    export SAMBUCA_GPU_PROFILE

    # Recorded so the completion report can say WHY a machine with a graphics
    # card in it is running on CPU. Without this the owner reads "runtime cpu"
    # and reasonably concludes the profiler cannot see their GPU.
    printf '%s\n' "$1" > "${SB_LIB}/gpu-degraded" 2>/dev/null || true
}

install_nvidia() {
    if sb_have nvidia-smi && nvidia-smi >/dev/null 2>&1; then
        log "NVIDIA driver already functional: $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1)"
    else
        log "installing the NVIDIA driver from Debian non-free-firmware"
        # contrib/non-free-firmware must be enabled or the driver package is invisible.
        if ! grep -qE 'non-free-firmware' /etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null; then
            sed -i 's/^\(deb .*debian\.org\/debian .* main\)$/\1 contrib non-free non-free-firmware/' /etc/apt/sources.list || true
            sb_retry 3 5 apt-get update -qq || warn "apt update after enabling non-free failed"
        fi
        sb_retry 2 10 apt-get install -y -qq nvidia-driver firmware-misc-nonfree \
            || { demote_to_cpu "the NVIDIA driver would not install"; return 0; }
        warn "a REBOOT is required before the NVIDIA kernel module loads"
        touch "${SB_LIB}/reboot-required"
    fi

    # --- container toolkit ---
    if ! sb_have nvidia-ctk; then
        log "installing the NVIDIA Container Toolkit"
        install -d -m 0755 /etc/apt/keyrings
        sb_retry 3 5 curl -fsSL --proto '=https' \
            https://nvidia.github.io/libnvidia-container/gpgkey \
            -o /tmp/nvidia-container.gpg \
            || { demote_to_cpu "could not fetch the NVIDIA container-toolkit key"; return 0; }
        gpg --dearmor </tmp/nvidia-container.gpg >/etc/apt/keyrings/nvidia-container-toolkit.gpg
        chmod 0644 /etc/apt/keyrings/nvidia-container-toolkit.gpg
        rm -f /tmp/nvidia-container.gpg

        curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
            | sed 's#deb https://#deb [signed-by=/etc/apt/keyrings/nvidia-container-toolkit.gpg] https://#g' \
            | sb_atomic_write /etc/apt/sources.list.d/nvidia-container-toolkit.list 0644

        # Remove the source again on the way out. An unreachable repository left
        # in sources.list.d makes every later apt-get update fail, which would
        # turn "no GPU acceleration" into "this machine can no longer install
        # security updates" — the same trap the Tailscale fallback had to avoid.
        if ! sb_retry 3 5 apt-get update -qq; then
            rm -f /etc/apt/sources.list.d/nvidia-container-toolkit.list
            apt-get update -qq >/dev/null 2>&1 || true
            demote_to_cpu "the NVIDIA container-toolkit repository is unreachable"
            return 0
        fi
        sb_retry 2 10 apt-get install -y -qq nvidia-container-toolkit \
            || { demote_to_cpu "nvidia-container-toolkit would not install"; return 0; }
    fi

    sb_run nvidia-ctk runtime configure --runtime=docker --set-as-default=false \
        || { demote_to_cpu "nvidia-ctk could not register the Docker runtime"; return 0; }
    sb_run systemctl restart docker \
        || { demote_to_cpu "Docker would not restart with the NVIDIA runtime"; return 0; }

    # Prove it, do not assume it.
    if docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi >/dev/null 2>&1; then
        ok "GPU passthrough into containers VERIFIED"
    else
        warn "GPU passthrough smoke test FAILED."
        warn "  Most often this means the driver needs the pending reboot."
        warn "  After rebooting: sambuca-first-boot --only 30-gpu-runtime --force"
    fi
}

install_amd() {
    log "configuring the AMD ROCm path"
    # amdgpu is in-tree; ROCm userspace is only needed for compute.
    sb_retry 2 10 apt-get install -y -qq firmware-amd-graphics libdrm-amdgpu1 \
        || warn "AMD firmware packages unavailable — continuing"

    # Containers reach the GPU through device nodes, not a custom runtime.
    if [[ -e /dev/kfd && -e /dev/dri ]]; then
        ok "ROCm device nodes present (/dev/kfd, /dev/dri)"
    else
        warn "/dev/kfd missing — ROCm compute will not work."
        warn "  Install the full ROCm stack from AMD's repository, then re-run this phase."
        touch "${SB_LIB}/reboot-required"
    fi

    # The render/video groups gate access to those nodes from inside a container.
    for grp in render video; do
        getent group "$grp" >/dev/null || sb_run groupadd -r "$grp" || true
    done
    {
        printf 'AMD_RENDER_GID=%s\n' "$(getent group render | cut -d: -f3)"
        printf 'AMD_VIDEO_GID=%s\n'  "$(getent group video  | cut -d: -f3)"
    } | sb_atomic_write "${SB_ETC}/gpu-amd.env" 0644
}

case "$SAMBUCA_GPU_VENDOR" in
    nvidia) install_nvidia ;;
    amd)    install_amd ;;
    intel)
        log "Intel graphics: no compute runtime installed."
        log "  Ollama has no supported SYCL backend, so this box runs CPU inference."
        log "  Immich, however, can use OpenVINO — enable it in profile.local.env with:"
        log "    IMMICH_ML_IMAGE_SUFFIX=-openvino"
        ;;
    none|*)
        log "no GPU to configure — CPU inference path"
        ;;
esac

# --- re-profile -------------------------------------------------------------
# VRAM may only now be readable. Everything downstream (model set, container
# limits, the compose GPU overlay) reads profile.env, so it must be re-derived
# before phase 60 renders the stack.
log "re-profiling hardware now that vendor tooling is present"
"${_SB_SELF_DIR:-/opt/sambuca/engine}/hardware-detect.sh" ${SB_QUIET:+--quiet} \
    || warn "re-profiling failed; the first-boot profile stands"

# shellcheck source=/dev/null
source "${SB_ETC}/profile.env" 2>/dev/null || true
ok "gpu phase done — tier ${SAMBUCA_TIER:-?} (${SAMBUCA_TIER_NAME:-?}), runtime ${SAMBUCA_GPU_PROFILE:-cpu}"

if [[ -f "${SB_LIB}/reboot-required" ]]; then
    warn "REBOOT REQUIRED before GPU acceleration is available."
    warn "Provisioning continues; the stack will start on the CPU path and pick up"
    warn "the GPU automatically after: reboot && sambuca-first-boot --from 30-gpu-runtime --force"
fi
