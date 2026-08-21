#!/usr/bin/env bash
#
# sambuca :: tests/test-ml-variant.sh
#
# Which Immich machine-learning image gets pulled.
#
# This selection was a concatenation in cloud.yml — `${IMMICH_ML_IMAGE}` with
# `${IMMICH_ML_IMAGE_SUFFIX}` glued on at pull time. Every tool verified
# IMMICH_ML_IMAGE, which resolves perfectly well on its own; nothing verified
# base+suffix, which is what compose actually pulled. On AMD the suffix was
# "-rocm", a variant upstream publishes on NO release tag, so the container
# never started: no face recognition and no photo search, on every AMD box, for
# as long as that path existed.
#
# It also could not be tested, because it only existed inside a provisioning
# script that wants root and a Docker daemon. So these drive the extracted
# function directly, against a fixture — the real one, not a copy of it.

set -uo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1

pass=0; fail=0
ok_()  { printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad_() { printf '  FAIL  %s\n' "$1"; fail=$((fail+1)); }

# shellcheck source=engine/lib/common.sh
SB_QUIET=1 source engine/lib/common.sh

FIX="$(mktemp -d)"; trap 'rm -rf -- "$FIX"' EXIT
cat >"${FIX}/.env.example" <<'ENVEOF'
IMMICH_ML_IMAGE=ghcr.io/immich-app/immich-machine-learning:v1.128.0@sha256:aaa
IMMICH_ML_CUDA_IMAGE=ghcr.io/immich-app/immich-machine-learning:v1.128.0-cuda@sha256:bbb
IMMICH_ML_OPENVINO_IMAGE=ghcr.io/immich-app/immich-machine-learning:v1.128.0-openvino@sha256:ccc
ENVEOF

echo
echo "each variant resolves to its own pin"

got="$(sb_ml_image_ref "${FIX}/.env.example" "")"
[[ $got == *":v1.128.0@sha256:aaa" ]] \
    && ok_ "no suffix selects the CPU pin" || bad_ "cpu -> ${got}"

got="$(sb_ml_image_ref "${FIX}/.env.example" "-cuda")"
[[ $got == *"-cuda@sha256:bbb" ]] \
    && ok_ "-cuda selects its own pin, not base+suffix" || bad_ "cuda -> ${got}"

got="$(sb_ml_image_ref "${FIX}/.env.example" "-openvino")"
[[ $got == *"-openvino@sha256:ccc" ]] \
    && ok_ "-openvino selects its own pin" || bad_ "openvino -> ${got}"

echo
echo "nothing is ever assembled"

# THE ORIGINAL BUG, stated as a property: no output may be a base with a suffix
# stuck to the end of it. That string is unpullable once a digest is present,
# and unverified even when it is not.
for suf in "" "-cuda" "-openvino" "-rocm" "-nonsense"; do
    got="$(sb_ml_image_ref "${FIX}/.env.example" "$suf" || true)"
    if [[ $got == *"@sha256:"?*"-"* ]]; then
        bad_ "suffix '${suf}' produced something appended after the digest: ${got}"
    else
        ok_ "suffix '${suf}' yields a clean reference"
    fi
done

echo
echo "an unknown variant degrades to something that runs"

got="$(sb_ml_image_ref "${FIX}/.env.example" "-rocm")"; rc=$?
((rc == 1)) && ok_ "-rocm reports failure so the caller can say why" \
             || bad_ "-rocm returned ${rc}; the caller cannot warn"
[[ $got == *":v1.128.0@sha256:aaa" ]] \
    && ok_ "and still yields the CPU image, not a 404" || bad_ "-rocm -> ${got}"

got="$(sb_ml_image_ref "${FIX}/.env.example" "-cuda-typo" || true)"
[[ $got == *":v1.128.0@sha256:aaa" ]] \
    && ok_ "a near-miss typo does not silently become a broken pull" \
    || bad_ "typo -> ${got}"

echo
echo "the real .env.example backs every variant this engine can choose"

# Ties the fixture back to reality: hardware-detect may only assign suffixes
# that the shipped env file actually pins.
for suf in $(grep -ohE 'IMMICH_ML_IMAGE_SUFFIX="[^"]*"' engine/hardware-detect.sh \
             | sed 's/.*="//; s/"//' | sort -u); do
    if sb_ml_image_ref compose/.env.example "$suf" >/dev/null 2>&1; then
        ok_ "hardware-detect may select '${suf}' — it is pinned"
    else
        bad_ "hardware-detect can select '${suf}', which .env.example does not pin"
    fi
done

echo
echo "the variant knob is not offered where it does nothing"

# A REGRESSION INTRODUCED BY THE FIX ABOVE, caught by asking what the change
# broke downhill. While compose still concatenated, setting
# IMMICH_ML_IMAGE_SUFFIX in .env worked. Once the reference is resolved once,
# during .env rendering, a copy of the suffix inside .env is consulted by
# nothing — a knob that turns and changes nothing, in the file an owner is most
# likely to open. The real one lives in profile.local.env.
if grep -qE '^IMMICH_ML_IMAGE_SUFFIX=' compose/.env.example; then
    bad_ ".env.example offers IMMICH_ML_IMAGE_SUFFIX, where setting it does nothing"
else
    ok_ ".env.example does not offer a knob that changes nothing"
fi

if grep -q "grep -v '\^IMMICH_ML_IMAGE_SUFFIX='" engine/provision/60-stack.sh; then
    ok_ "60-stack keeps the inert copy out of the generated .env"
else
    bad_ "60-stack copies IMMICH_ML_IMAGE_SUFFIX into .env, where nothing reads it"
fi

# And the documented place to set it must still be the one that works.
if grep -q 'IMMICH_ML_IMAGE_SUFFIX' engine/provision/30-gpu-runtime.sh \
   && grep -q 'profile.local.env' engine/provision/30-gpu-runtime.sh; then
    ok_ "the documented location is profile.local.env, which 60-stack reads"
else
    bad_ "nothing tells an owner where the working knob lives"
fi

echo
printf '  %d passed, %d failed\n\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
