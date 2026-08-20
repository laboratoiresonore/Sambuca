#!/usr/bin/env bash
#
# sambuca :: engine/maintenance/recovery-key.sh
#
# Inspect and repair the disk RECOVERY KEYSLOT — the second, independent way
# into the encrypted disk, derived from the 24-word seed phrase.
#
# Verbs:
#   status   Report whether a recovery keyslot exists, and say plainly what it
#            means if one does not.
#   enrol    Add one. Needed after an --interactive install, or when the
#            installer could not enrol during setup.
#   verify   Prove a recovery key actually opens the disk WITHOUT unlocking
#            anything or changing a single byte.
#
# `verify` is the important one and the one people skip. A keyslot that exists
# is not a keyslot that works: a stray newline in the key file, a transcription
# error on the printed sheet, or a mis-typed enrolment all produce a slot that
# looks perfect in `luksDump` and opens nothing. The only honest way to know is
# to test the key you actually have against the disk you actually own — before
# you need it, not during the emergency.
#
set -uo pipefail

SB_TAG="recovery-key"
_SB_SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=engine/lib/common.sh
source "${_SB_SELF_DIR}/../lib/common.sh"
sb_trap_err

MARKER="${SB_LIB}/recovery-keyslot"

usage() {
    cat <<'USAGE'
sambuca recovery-key — manage the disk recovery keyslot.

Usage: recovery-key.sh {status|enrol|verify} [--device DEV]

  status            Is there a second way into this disk?
  enrol             Add a recovery keyslot (asks for the current passphrase).
  verify            Test a recovery key against the disk. Changes nothing.
  --device DEV      LUKS device (default: autodetected).

The recovery key is derived from your 24-word seed phrase. Recompute it on any
computer, offline, with:   sambuca-flasher derive-recovery-key
USAGE
}

VERB="${1:-}"; shift || true
DEVICE=""
while (($# > 0)); do
    case "$1" in
        --device) DEVICE="${2:-}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; SB_EXIT_CODE=2 die "unknown argument: $1" ;;
    esac
    shift
done

find_luks() {
    if [[ -n $DEVICE ]]; then printf '%s' "$DEVICE"; return 0; fi
    local devs; devs="$(lsblk -rno NAME,FSTYPE | awk '$2=="crypto_LUKS"{print "/dev/"$1}')"
    local count; count="$(printf '%s\n' "$devs" | grep -c . || true)"
    if [[ $count -eq 0 ]]; then die "no LUKS container found (pass --device)"; fi
    if [[ $count -gt 1 ]]; then
        err "multiple LUKS containers found:"
        printf '%s\n' "$devs" | while read -r d; do [[ -n $d ]] && err "  $d"; done
        die "pass --device to choose one — guessing could consume a keyslot on the wrong disk"
    fi
    printf '%s' "$devs"
}

slot_count() {
    cryptsetup luksDump "$1" 2>/dev/null | grep -cE '^[[:space:]]+[0-9]+: luks2|^Key Slot [0-9]+: ENABLED' || echo 0
}

cmd_status() {
    sb_require cryptsetup
    local dev; dev="$(find_luks)"
    local slots; slots="$(slot_count "$dev")"

    log "device: ${dev}"
    log "enabled keyslots: ${slots}"

    if [[ -r $MARKER ]]; then
        local state detail
        state="$(head -n1 "$MARKER")"; detail="$(tail -n +2 "$MARKER")"
        log "installer reported: ${state} — ${detail}"
    fi

    if ((slots >= 2)); then
        ok "this disk has ${slots} keyslots — a lost root passphrase is recoverable"
        log "  Recompute the key offline:  sambuca-flasher derive-recovery-key"
        log "  Prove it actually works:    sambuca-recovery verify"
        return 0
    fi

    err "═══════════════════════════════════════════════════════════════"
    err " THIS DISK HAS ONLY ONE KEY."
    err ""
    err " If the root passphrase is lost, every file on this machine is"
    err " gone permanently. There is no reset, no support line, and no"
    err " way back — that is what full-disk encryption means."
    err ""
    err " Fix it now, it takes ten seconds:"
    err "   sambuca-recovery enrol"
    err "═══════════════════════════════════════════════════════════════"
    return 1
}

cmd_enrol() {
    sb_require_root
    sb_require cryptsetup
    local dev; dev="$(find_luks)"

    log "adding a recovery keyslot to ${dev}"
    log "Get the key with:  sambuca-flasher derive-recovery-key"
    log "It looks like:     XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX"
    printf '\n'

    local key key2
    read -rsp "recovery key: " key; printf '\n'
    read -rsp "confirm     : " key2; printf '\n'
    [[ $key == "$key2" ]] || die "the two entries differ — nothing was changed"
    [[ -n ${key// /} ]] || die "empty key — nothing was changed"

    # No trailing newline, ever: cryptsetup reads a key file byte-for-byte, and
    # a newline would enrol a passphrase that cannot be typed at a prompt.
    local tmp; tmp="$(mktemp)"
    chmod 0600 -- "$tmp"
    printf '%s' "$key" >"$tmp"
    # shellcheck disable=SC2064
    trap "shred -u '${tmp}' 2>/dev/null || rm -f '${tmp}'" EXIT

    log "you will now be asked for a passphrase that ALREADY opens this disk"
    if cryptsetup luksAddKey "$dev" "$tmp"; then
        ok "recovery keyslot added — this disk now has $(slot_count "$dev") keyslots"
        printf 'enrolled\nadded by sambuca-recovery enrol at %s\n' \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" | sb_atomic_write "$MARKER" 0644
        warn "VERIFY IT NOW, while you still have the sheet in front of you:"
        warn "  sambuca-recovery verify"
    else
        die "enrolment failed — the existing passphrase was wrong, or all keyslots are full"
    fi
}

cmd_verify() {
    sb_require cryptsetup
    local dev; dev="$(find_luks)"

    log "testing a recovery key against ${dev}"
    log "This unlocks nothing and changes nothing — it only asks the disk"
    log "whether the key would work."
    printf '\n'

    local key
    read -rsp "recovery key: " key; printf '\n'
    [[ -n ${key// /} ]] || die "empty key"

    local tmp; tmp="$(mktemp)"
    chmod 0600 -- "$tmp"
    printf '%s' "$key" >"$tmp"
    # shellcheck disable=SC2064
    trap "shred -u '${tmp}' 2>/dev/null || rm -f '${tmp}'" EXIT

    # --test-passphrase reports which slot matched without opening the device.
    local slot
    if slot="$(cryptsetup open --test-passphrase --key-file="$tmp" "$dev" 2>&1)"; then
        ok "THIS KEY OPENS THE DISK. Your recovery path is real and tested."
        [[ -n $slot ]] && log "  ${slot}"
        return 0
    fi

    err "═══════════════════════════════════════════════════════════════"
    err " THIS KEY DOES NOT OPEN THE DISK."
    err ""
    err " Check, in this order:"
    err "   1. Typed exactly as printed, dashes included, case-sensitive?"
    err "   2. Derived from the seed for THIS machine? Compare the key"
    err "      fingerprint on the sheet against:  $(cat "${SB_ETC}/fingerprint" 2>/dev/null || echo '(unrecorded)')"
    err "   3. Does a recovery slot exist at all?  sambuca-recovery status"
    err ""
    err " Do not wipe or reinstall anything while you still have a working"
    err " passphrase — enrol a fresh recovery key instead:"
    err "   sambuca-recovery enrol"
    err "═══════════════════════════════════════════════════════════════"
    return 1
}

case "$VERB" in
    status) cmd_status ;;
    enrol|enroll) cmd_enrol ;;
    verify) cmd_verify ;;
    ""|-h|--help) usage; exit 0 ;;
    *) usage >&2; SB_EXIT_CODE=2 die "unknown verb: ${VERB}" ;;
esac
