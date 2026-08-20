#!/bin/sh
#
# sambuca :: engine/autoinstall/enroll-recovery-key.sh
#
# Enrol the seed-derived RECOVERY KEY into a second LUKS keyslot.
#
# ══════════════════════════════════════════════════════════════════════════
# THE FAILURE THIS PREVENTS
#
# Without a second keyslot, the root passphrase is the only thing on earth that
# opens the disk. An owner who loses that one string has not been "locked out" —
# every photo, document and password on the machine is gone, permanently, with
# no support line to call. For an appliance aimed at people who are leaving
# Google precisely because they were tired of being locked out of their own
# data, that is the worst possible failure mode.
#
# After this script runs, the disk has TWO independent keys:
#
#   slot 0  the root passphrase   (typed daily, printed on the recovery sheet)
#   slot 1  the recovery key      (derived from the 24-word seed, also printed)
#
# Either opens the disk. Neither can be computed from the other. Losing the
# sheet loses both — which is why the sheet says to store it like cash.
# ══════════════════════════════════════════════════════════════════════════
#
# WHERE THIS RUNS: in the Debian installer, from preseed/late_command, NOT
# in-target. That is deliberate — this is the only moment at which the disk
# passphrase is legitimately available (in debconf) without ever writing it to
# the installed system. Doing it later would mean persisting the passphrase past
# installation just to have it available, which is exactly the exposure the
# whole design avoids.
#
# It is also NON-FATAL. A machine that installs with one keyslot is degraded,
# not broken; a machine whose installation aborts at 95% is broken. Failures are
# recorded for first-boot to surface loudly.

set -u

KEY_FILE=/cdrom/sambuca/luks-recovery.key
MARKER=/target/var/lib/sambuca/recovery-keyslot
LOGF=/var/log/sambuca-enroll-recovery.log

log() { printf '%s sambuca-enroll: %s\n' "$(date -u +%H:%M:%S)" "$*" >>"$LOGF" 2>/dev/null; }
say() { printf 'sambuca: %s\n' "$*"; log "$*"; }

mkdir -p /target/var/lib/sambuca 2>/dev/null || true

record() {
    # $1 = status, $2 = detail. first-boot reads this and tells the owner.
    printf '%s\n%s\n' "$1" "$2" > "$MARKER" 2>/dev/null || true
}

if [ ! -r "$KEY_FILE" ]; then
    say "no recovery key on the installation medium — skipping enrolment"
    say "  (expected in --interactive mode; enrol later with: sambuca-recovery enrol)"
    record "absent" "no key file was staged on the installation medium"
    exit 0
fi

if ! command -v cryptsetup >/dev/null 2>&1; then
    say "cryptsetup unavailable in the installer — cannot enrol"
    record "failed" "cryptsetup not present in the installer environment"
    exit 0
fi

# --- locate the LUKS container -------------------------------------------
# There is normally exactly one. If there are several we refuse rather than
# guess: enrolling into the wrong container would consume a keyslot on a disk
# the owner never intended to touch.
LUKS_DEV=""
COUNT=0
for dev in $(blkid -t TYPE=crypto_LUKS -o device 2>/dev/null); do
    LUKS_DEV="$dev"
    COUNT=$((COUNT + 1))
done

if [ "$COUNT" -eq 0 ]; then
    say "no LUKS container found — nothing to enrol into"
    record "absent" "no crypto_LUKS device was present at install time"
    exit 0
fi
if [ "$COUNT" -gt 1 ]; then
    say "found ${COUNT} LUKS containers — refusing to guess which one to modify"
    record "failed" "${COUNT} LUKS containers present; enrolment needs a single target"
    exit 0
fi
say "LUKS container: ${LUKS_DEV}"

# --- the existing passphrase ---------------------------------------------
# From debconf, where the partitioner put it. It is read into a variable in the
# installer's RAM, used once, and never written anywhere.
EXISTING="$(debconf-get partman-crypto/passphrase 2>/dev/null || true)"
if [ -z "$EXISTING" ]; then
    say "the install passphrase is not available from debconf"
    say "  (expected in --interactive mode, where the operator typed it)"
    record "deferred" "install passphrase not retrievable; enrol from the running system"
    exit 0
fi

# --- normalise the key file ----------------------------------------------
# LOAD-BEARING. `cryptsetup luksAddKey <dev> <keyfile>` uses the file's ENTIRE
# contents as the new passphrase, newlines included. A key file ending in "\n"
# therefore enrols the passphrase "KEY\n" — which nobody can ever type, because
# a terminal strips the newline that submits the line. The keyslot would exist,
# look correct in `luksDump`, and never open the disk.
#
# So the key is stripped to exactly the characters printed on the recovery
# document, in a file the installer creates itself, rather than trusting
# whatever the flasher wrote.
NORM_KEY=/tmp/sambuca-recovery.norm
tr -d '\r\n \t' < "$KEY_FILE" > "$NORM_KEY" 2>/dev/null || {
    say "could not normalise the recovery key file"
    record "failed" "unable to write the normalised key file"
    exit 0
}
if [ ! -s "$NORM_KEY" ]; then
    say "recovery key file is empty after normalisation"
    record "failed" "key file contained no usable characters"
    rm -f "$NORM_KEY"
    exit 0
fi
log "normalised key length: $(wc -c < "$NORM_KEY") bytes"

# --- enrol ----------------------------------------------------------------
# The EXISTING passphrase goes in on stdin so it never appears in the process
# table, which `ps` would happily show to anything running on the box.
if printf '%s' "$EXISTING" | cryptsetup luksAddKey \
        --key-file=- \
        --batch-mode \
        "$LUKS_DEV" "$NORM_KEY" >>"$LOGF" 2>&1; then
    say "recovery keyslot enrolled"
    record "enrolled" "seed-derived recovery key added as an additional LUKS keyslot"
else
    say "recovery keyslot enrolment FAILED — the install passphrase still works"
    say "  first boot will report this; enrol later with: sambuca-recovery enrol"
    record "failed" "cryptsetup luksAddKey returned non-zero; see ${LOGF}"
fi

rm -f "$NORM_KEY"

# The passphrase leaves scope here. The key file stays on the installation
# medium, which the recovery document already says to treat as a key until the
# install completes — this adds no new exposure class.
EXISTING=""

exit 0
