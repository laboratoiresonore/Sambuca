#!/usr/bin/env bash
#
# sambuca :: tests/test-optional-phases.sh
#
# AN OPTIONAL COMPONENT MUST NOT BE ABLE TO KILL THE INSTALL.
#
# `first-boot.sh` runs the phases in order and stops on the first failure
# (`run_phase … || { rc=1; break; }`). That is correct — provisioning half a
# machine and calling it done is how you get a file server with no certificates.
# The consequence is that **every `die` in a phase abandons the whole
# appliance**, which is a far higher bar than "this step did not work".
#
# Three phases failed that test, and each one threw away a working machine:
#
#   50-network   obtaining Tailscale had four `die`s, so a network that blocks
#                pkgs.tailscale.com meant the stack, the certificates and the
#                setup page never provisioned at all.
#   30-gpu       an NVIDIA driver that would not install ended the run at phase
#                30 — no file server, no photos, no passwords, no report,
#                because of a graphics driver. The AMD branch in the same file
#                already warned and continued.
#   70-models    the chat model is pulled AFTER the entire stack is up and
#                BEFORE the completion report. A failed download — a registry
#                blip, a full disk — discarded a finished appliance at the
#                reporting step, leaving nine working services unmentioned.
#
# The test each phase must pass: **is the appliance worth having without it?**
# A missing disk is fatal. A missing Docker is fatal. A missing accelerator, a
# missing remote-access convenience and a missing chat model are warnings that
# name what was lost, what still works, and how to add it later.

set -uo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1

pass=0; fail=0
ok_()  { printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad_() { printf '  FAIL  %s\n' "$1"; fail=$((fail+1)); }

# A `die` in a USE position, never a comment quoting one — the phases carry
# comments explaining what they used to do, and matching those would flag the
# explanation as the offence.
dies_in() {
    grep -vE '^\s*#' "$1" | grep -cE '\|\|[[:space:]]*die[[:space:]]|^[[:space:]]*die[[:space:]]'
}

echo
echo "optional components warn; they do not abandon the machine"

for phase in 30-gpu-runtime 50-network 70-models 90-report; do
    f="engine/provision/${phase}.sh"
    n="$(dies_in "$f")"
    if [[ $n -eq 0 ]]; then
        ok_ "${phase} has no fatal path"
    else
        bad_ "${phase} can abandon the install (${n} die): $(grep -vE '^\s*#' "$f" | grep -nE '\|\|[[:space:]]*die|^[[:space:]]*die ' | head -2 | tr '\n' ' ')"
    fi
done

echo
echo "and the genuinely fatal phases are still fatal"

# WITHOUT THIS THE CHECK ABOVE IS HALF AN ARGUMENT. If `die` were removed
# everywhere, provisioning would stagger on past a missing Docker and produce a
# machine that reports success with nothing on it. The classification is the
# point, not the absence.
for phase in 10-system 20-docker 40-storage-pool 60-stack; do
    f="engine/provision/${phase}.sh"
    [[ "$(dies_in "$f")" -gt 0 ]] \
        && ok_ "${phase} still refuses to continue when it cannot do its job" \
        || bad_ "${phase} no longer fails on anything — the appliance would report success with nothing on it"
done

echo
echo "the GPU fallback demotes the profile, not just the mood"

# 60-stack picks its compose overlay from SAMBUCA_GPU_PROFILE. Warning without
# demoting would leave an overlay naming a runtime that was never registered,
# which invalidates the ENTIRE compose project — moving the death to phase 60
# and making it much harder to read.
GPU=engine/provision/30-gpu-runtime.sh

# MATCH THE SED ITSELF, NOT THE STRING ANYWHERE IN THE FILE. The first version
# of this check grepped for "SAMBUCA_GPU_PROFILE=cpu", which also appears in the
# bare assignment and the append-fallback two lines below — so pointing the sed
# at the wrong value passed cleanly. Caught by mutating it. That is the
# "appears anywhere" fault for the seventh time in this repository, committed
# inside a test written to enforce precision.
grep -qE "sed -i 's/\^SAMBUCA_GPU_PROFILE=\.\*/SAMBUCA_GPU_PROFILE=cpu/'" "$GPU" \
    && ok_ "the in-place rewrite actually sets cpu" \
    || bad_ "the profile rewrite does not set cpu — 60-stack would select an overlay for a runtime that is not there"

grep -q "sed -i 's/\^SAMBUCA_GPU_PROFILE=" "$GPU" \
    && ok_ "in place, so the tier and model catalogue survive" \
    || bad_ "the profile is rewritten wholesale, which would discard the tier"

grep -qE '^\s*SAMBUCA_GPU_PROFILE=cpu\s*$' "$GPU" \
    && ok_ "and the running shell sees the demotion too" \
    || bad_ "the variable is not reassigned in this process, so later checks in this phase still think there is a GPU"

grep -q "rm -f /etc/apt/sources.list.d/nvidia-container-toolkit.list" "$GPU" \
    && ok_ "a failed toolkit install removes its own apt source" \
    || bad_ "an unreachable repo is left behind, breaking every later apt-get update"

echo
echo "a missing chat model does not take the picture plane with it"

MODELS=engine/provision/70-models.sh
# The first version of the skip-guard was `exit 0`, which would also have
# skipped the image-model block below it — and ComfyUI never speaks to Ollama.
img_line="$(grep -n 'SAMBUCA_IMAGE_ENABLED:=0' "$MODELS" | cut -d: -f1)"
last_guard="$(grep -n 'OLLAMA_OK == 1' "$MODELS" | tail -1 | cut -d: -f1)"
if [[ -n $img_line && -n $last_guard && $img_line -gt $last_guard ]]; then
    ok_ "the image plane is configured outside every Ollama guard"
else
    bad_ "the image block sits inside an Ollama guard (image ${img_line:-?}, last guard ${last_guard:-?})"
fi

grep -q "OLLAMA_OK=0" "$MODELS" \
    && ok_ "an unreachable engine sets a flag rather than exiting the phase" \
    || bad_ "nothing records that the inference engine was unreachable"

echo
echo "what was recorded is what gets read"

# THE SESSION'S OWN LESSON, APPLIED HERE: wherever two programs must agree about
# a string and only one is tested, the agreement is an assumption. One phase
# writes these markers and a different one reads them; a rename on either side
# silently loses the message, and the owner is told nothing.
REPORT=engine/provision/90-report.sh
for marker in gpu-degraded chat-model-missing; do
    written=0; read_=0
    # Written: a redirection INTO the marker, not a mention of its name.
    grep -rqE "> *\"\\\$\{SB_LIB\}/${marker}\"" engine/provision/[0-9]*.sh && written=1
    # Read: the report must GATE on the file existing. The first version of this
    # check grepped the report for the name anywhere, and the name also appears
    # inside the heredoc body that prints the reason — so renaming the `[[ -f ]]`
    # test passed cleanly while the message became unreachable. Mutating it is
    # the only reason that was caught.
    grep -qE '\[\[ +-f +"\$\{SB_LIB\}/'"${marker}"'" +\]\]' "$REPORT" && read_=1
    if [[ $written -eq 1 && $read_ -eq 1 ]]; then
        ok_ "${marker} is both written and gated on by the report"
    else
        bad_ "${marker}: written=${written} gated_by_report=${read_} — a marker nobody reads is no better than the die it replaced"
    fi
done

echo
printf '  %d passed, %d failed\n\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
