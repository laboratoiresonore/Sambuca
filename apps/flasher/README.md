# sambuca-flasher

Builds a Sambuca installer USB and the recovery document that goes with it.

This runs on **your everyday computer**, not on the appliance. It generates the
key material offline, writes the recovery PDF, stages the payload and hands the
actual writing to a mature tool — `rpi-imager` for a Raspberry Pi card, a raw
image write for x86. It then walks you through booting the target machine and
tells you where the result appears.

`pyproject.toml` declared `readme = "README.md"` and this file did not exist.
setuptools tolerates that silently — `pip install ./apps/flasher` and a wheel
build both succeed today — so it was latent rather than broken, which is exactly
how it survived. PEP 621 says the file should exist; now it does.

## Install

```bash
pip install ./apps/flasher
```

This is the supported path on **Intel Macs**, which have no prebuilt binary:
GitHub retired its last Intel build machine. Everyone else should take a release
binary from the [project README](../../README.md) — it needs no Python.

## Use

```bash
sambuca-flasher window               # the graphical flow (Pi)
sambuca-flasher boot-guide "Dell OptiPlex 7060"
sambuca-flasher watch                # install progress, before the appliance serves its own page
sambuca-flasher handover             # what is running, trust the certificate, save the addresses
```

**Double-clicking the binary opens the window** — a console menu is the fallback
where there is no toolkit or no display, which is the only route on a headless
box. From a terminal, a bare `sambuca-flasher` prints the usage line and exits,
like any other command; the menu is for people who did not open a terminal.

*(This paragraph was written the other way round first — "a bare invocation shows
the menu" — and running it said otherwise. The fork is on
`launched_by_double_click()`, and it is deliberate: somebody who opened an
application expects a window, somebody who typed a command expects a command.)*

Recovery, all of it offline and on this machine:

```bash
sambuca-flasher verify-sheet         # is the printed sheet readable and is it THIS machine's?
sambuca-flasher derive-recovery-key  # the disk recovery key, from the 24 words
sambuca-flasher derive-backup-key    # the backup repository password, from the 24 words
sambuca-flasher open-vault           # the three-question vault, if you made one
```

## What it deliberately does not do

**It does not write images itself.** `rpi-imager` does that, on three platforms,
with a decade of edge cases already handled. Wrapping a mature tool is right;
walking away and leaving a novice alone inside it is not — so the flasher
pre-fills what it can, explains each screen it cannot fill, and picks the flow
back up afterwards.

**It makes no network requests during a write**, other than fetching the Debian
netinst image when you ask it to. Key material is generated here and stays here.

**It never automates away a secret.** The same registry key that lets the Pi
Imager's settings be pre-filled also stores a password hash and a wifi PSK; the
harmless fields are filled in and the tool's own UI collects the rest.

## Dependencies

Four, and the list is short on purpose — every dependency is one more thing that
could exfiltrate a seed phrase from the machine that generates it. `mnemonic`
(BIP-39; do not hand-roll a wordlist), `reportlab` (the PDF), `passlib` (SHA-512
crypt on Windows/macOS and Python ≥ 3.13), `cryptography` (the vault's AEAD).

`cryptography` is declared explicitly because it had been importable on one dev
machine only as a transitive dependency of paramiko — nothing to do with this
project. A dependency that works by accident works until it doesn't.

## Tests

```bash
cd apps/flasher && python -m pytest -q      # 202 tests
```

From the repository root, `bash tools/preflight.sh` (or `make check`) runs these
**and** the appliance suite. Both trees matter and both are named there: the
beacon's tests were written, passing, and invisible to CI for a while because a
workflow named only one of them.
