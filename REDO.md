# What has to be redone, end to end

> **Progress:** items struck through are done and verified. Updated as the loop works through the list — see the git log for what each one actually took.

Written 2026-08-20, after the flasher was built the wrong way and the README
was written to describe it.

This is not a wishlist. Every item below is either **code that exists and
should not**, **a claim the project makes that is not true**, or **a thing that
is broken right now**. Where something has been verified, it says so and how.

---

## The mistake this list corrects

Sambuca implemented its own image writer: device enumeration, raw device
access, volume locking, elevation, download, checksum verification, readback
verification, progress. That is **855 lines** reimplementing, on one platform,
what Raspberry Pi Imager already does on three.

The evidence was in the commit history before anyone said anything: it took
**five attempts** to complete one Windows raw write.

1. The C runtime cannot open a physical device — `open(path, "wb")` raises `Errno 22`.
2. `open_osfhandle` + `fdopen` died with `EBADF` two chunks into a 2.77 GiB copy.
3. Volume locking matched **zero** volumes, because a fresh card's partition has no drive letter.
4. The lock was released before a single byte was written — the handle was closed in a `finally`.
5. Then the boot partition could not be found, for the same letterless reason.

Every one of those is a bug rpi-imager fixed years ago. The repository's own
rules said not to do this: *"NEVER build something new before you VERIFY it
isn't already solved. Wrap, don't rewrite."*

A second, related mistake: **the flasher runs on machine A and installs onto
machine B**, so a "hardware estimator" that asks a novice to type the specs of
a different computer was wrong in concept, not just in its regexes. Machine B
already profiles itself correctly at first boot.

---

## 0. THE RULE — do it for them, or guide every step

**Reported after the first end-to-end attempt: "I ran Pi Imager 100%
unassisted."** That is a failure, and a worse one than the code it replaced.

The rule, in the owner's words:

> **1. Do it for the user.**
> **2. If you cannot do it for the user, GUIDE THEM THROUGH EVERY STEP.**

There is no third option. Silence is not an option, and neither is a printed
command. Not writing the image writer was right; leaving someone alone inside
someone else's tool was not. **"Wrap, don't rewrite" does not mean "delegate and
abandon" — the duty of care does not transfer with the work.**

### Every step of the flow, against the rule

| Step | Can it be done FOR them? | What must happen |
|---|---|---|
| **Install rpi-imager** | **Yes** | Detect it; if absent, install it via the manifest's command. Never print "go install this". |
| **Device** | **Yes** | The OS list already carries one tested device, and the catalogue supports `default: true`. It should arrive selected. |
| **OS** | **Yes** | One entry, ours, pre-selected. |
| **Storage** | **No — and deliberately not** | This is the destructive choice. **GUIDE:** say how many removable drives were seen, name the likely one by size and label, and say plainly that everything on it will be erased. |
| **Customisation** | **Mostly** | VERIFIED pre-fillable at `HKCU\Software\Raspberry Pi\Raspberry Pi Imager\imagecustomization`: hostname, timezone, keyboard, `sshEnabled`, `sshUserName`. **GUIDE** for the rest. |
| **The owner's secrets** | **No, and must not be** | That key also holds a password hash and a wifi PSK. Sambuca never writes them; rpi-imager's own UI collects them. Matches the existing rule in `pi.py` that no wifi key is ever written to a card. |
| **Writing** | **Yes** | rpi-imager's own progress. Sambuca stays on screen saying what is happening and how long it takes. |
| **Provisioning after** | **Yes** | Must follow automatically. `provision-pi` as a separate command a novice cannot know about is exactly the failure being fixed. |
| **UAC prompt** | **No** | **GUIDE:** warn before it appears. An elevation prompt from an app they did not knowingly start looks like malware. |
| **What now?** | **Yes** | Say what to do with the card, what will happen on first boot, and where the result appears. |

### Items

| # | What |
|---|---|
| ~~**G1**~~ **DONE** | Auto-install rpi-imager when missing, using the manifest's install command. |
| **G2** | Pre-select device and OS so those two screens are already answered. |
| ~~**G3**~~ **DONE** | Pre-fill the non-secret Customisation fields via the verified registry key. |
| ~~**G4**~~ **DONE** | Never write the owner's password or wifi key. Ever. |
| ~~**G5**~~ **DONE** | Guide the Storage step explicitly — the one choice that must stay human, so it gets the most words, not the fewest. |
| ~~**G6**~~ **DONE** | Warn before the UAC prompt appears. |
| ~~**G7**~~ **DONE** | Detect completion and provision automatically; no second command. |
| **G10** **DONE** | Settle reachability FIRST: detect the tailnet, offer to install Tailscale, open the key page, accept the key — before anything is written. |
| **G11** **DONE** | Authorise the installing machine on the appliance by default. An installer must not build something it cannot reach. |
| ~~**G8**~~ **DONE** | Close the loop: what the card does next, and where to read the result. |
| ~~**G9**~~ **DONE** | Record the rule in `CLAUDE.md` under axis 1, so the next wrapper does not repeat this. |

**Acceptance:** someone who has never seen Raspberry Pi Imager goes from opening
Sambuca to a provisioned card without reading documentation, without typing a
command, and without making a single choice they were not told how to make.

---

## A. Delete — code that exists and should not

| # | What | Lines | Why |
|---|---|---|---|
| ~~A1~~ **DONE** | `winraw.py` | 379 | Windows raw device access. rpi-imager does this. |
| ~~A2~~ **DONE** | `writer.py` — the write and verify path | ~200 of 280 | Same. **Keep `inject_payload`**, which is ours. |
| A3 | `devices.py` — the target-picker half | ~150 of 196 | rpi-imager picks the device. **Keep boot-partition lookup**, needed after writing. |
| ~~A4~~ **DONE** | `pi.py` — `write_raspios`, `_restore_disk` | ~120 of 495 | Same. **Keep `render_firstrun` and `provision_boot_partition`.** |
| ~~A5~~ **DONE** | `estimate.py` | 319 | Conceptually wrong: guesses about a different machine from a typed sentence. |
| ~~A6~~ **DONE** | `tests/test_winraw.py` | 90 | Tests A1. |
| ~~A7~~ **DONE** | `tests/test_estimate_parsing.py` | 110 | Tests A5. |
| A8 | `cli.py` — `write`/`write-pi` device handling, the `_interactive` menu's dead options | ~200 of 891 | Superseded by launching rpi-imager. |

**Roughly 1,500 of 4,217 lines go.** Deleting rather than archiving, per the
repository's DEAD-CODE-GETS-DELETED rule; git history is the safety net.

**Acceptance:** the package imports with no reference to `winraw`, the test
suite passes without those two files, and `sambuca-flasher write-pi` is either
gone or is a thin call into `imager.launch()`.

---

## B. Fix — broken right now

| # | What | Evidence |
|---|---|---|
| ~~B1~~ **DONE** | **The Windows release build fails.** `$PWD` in git-bash gives PyInstaller `\d\a\Sambuca\Sambuca\engine`. | `v0.1.0-preview2` built linux + macOS, failed windows, skipped the release job. |
| ~~B2~~ **DONE** | **The only published release cannot flash anything.** preview1's binaries have no engine bundled, so `write` and `write-pi` both fail. | Verified by building and running the .exe. |
| ~~B3~~ **DONE** | **The README's download links point at those broken binaries.** | `README.md:62-74`. |
| ~~B4~~ **DONE** | `estimate` is still referenced in three places in the README and in the app's menu. | Being deleted in A5; references must go with it. |
| B5 | The `_interactive` menu's options 4 and 5 print a command instead of doing anything. | *"HOW THE FUCK IS THAT HELPING A NOVICE"* — correct. |

**Acceptance for B1:** a tagged build produces three binaries and a release with
`SHA256SUMS.txt`, and each binary passes the engine-bundle check that already
exists in CI.

---

## C. Build — missing

| # | What | Note |
|---|---|---|
| C1 | **A real GUI**, or an honest admission there isn't one. | rpi-imager is now the GUI for writing. What remains ours — provisioning the card afterwards, showing the result — still has no window. |
| C2 | **Wire provisioning to run after rpi-imager finishes.** | Today it is a separate `provision-pi` command the user must know to run. It should be automatic, or offered. |
| C3 | **The arm64 engine port.** | `engine/` is x86-only: preseed, Debian netinst, docker on amd64. `hardware-detect.sh` is portable enough to run, which is what the Zero 2 W will test. |
| C4 | **The Steward runtime.** | The verb catalogue and linter exist and are tested. The thing that selects and executes verbs does not. |
| C5 | **Odysseus integration** for chat, pictures and the Steward. | Blocked on publishing Odysseus. |
| C6 | **The GPU handoff protocol.** | The decision is computed and emitted; the unload/reload it describes is not implemented. |

---

## D. Documentation — claims that are not true

| # | What | Reality |
|---|---|---|
| ~~D1~~ **DONE** | **README does not mention rpi-imager or the manifest at all.** | It describes an architecture that no longer exists. |
| ~~D2~~ **DONE** | *"Plug in a blank USB stick and hit 'Flash.'"* (`README:274`) | There is no Flash button. There is a console app. |
| D3 | `docs/design/INSTALLER.md` describes a desktop app throughout. | It does not exist. Either build it (C1) or mark the document as a design not yet built. |
| D4 | The README's Pi note says a Pi "is not installable yet". | Still true, but now a card has been written and the OS list offers it. Needs reconciling. |
| D5 | `docs/MAINTENANCE.md` coupling register has no entry for rpi-imager, the manifest, jsDelivr, or the OS-list schema. | Four new external couplings, none registered. |
| ~~D6~~ **DONE** | The status table does not mention that the shipped binaries never worked. | The most important thing a reader could know. |

---

## E. The hosting facts, so they are not rediscovered

Measured 2026-08-20, not assumed:

| Host | Content-Type | Cache |
|---|---|---|
| `downloads.raspberrypi.org` | `application/json` | — |
| `raw.githubusercontent.com` | **`text/plain`** | `max-age=300` |
| `cdn.jsdelivr.net` | `application/json` | hours, on a branch ref |

**rpi-imager silently ignores a list served as `text/plain`** — empty device
picker, no error. The OS list must go through jsDelivr. Neither host is
uncached; an earlier note in this repo claimed raw was, and that was wrong.

---

## F. Verify on hardware — nothing below is proven

| # | What | State |
|---|---|---|
| F1 | Boot the Zero 2 W from the written card. | Card written and verified byte-for-byte; **never booted**. |
| F2 | Confirm `hardware-detect.sh` runs under bash on real arm64. | Expected `SAMBUCA_TIER_UNSUPPORTED=1` — 512 MB against a 3.5 GB floor. That refusal firing *is* the pass. |
| F3 | A full install on a real x86 machine, end to end. | Never done. This is the headline gap in the whole project. |
| F4 | Any image generated by the FLUX workflow. | Written against documented node interfaces; no picture has come out of it. |

---

## What actually works today

So the list above is not read as "nothing works":

- **rpi-imager launches against the live Sambuca OS list and shows one tested
  device.** Verified on screen: title `Using data from cdn.jsdelivr.net`,
  Raspberry Pi Zero 2 W listed.
- **The manifest is fetched live from GitHub**, with a bundled fallback, and
  reports which one it used. Verified both paths.
- **The OS list is generated from the manifest**, refuses an empty device
  picker, and `--check` fails on drift. Verified by mutation.
- **A card was written and readback-verified** — 2.77 GiB, `e235fd24…c33a9` —
  and provisioned with the engine and first-boot script.
- **The verb catalogue and its linter** are in CI and mutation-tested.
- **83 tests pass.**

---

## Order

1. **B1** — nobody can get a working binary until the build works.
2. **A1–A4, A6** — delete the writer while the reason is fresh.
3. **G1–G8** — do it for them, or guide every step. Without this the project
   does not do its job, whoever writes the bytes. Ahead of everything that is
   not simply broken.
4. **A5, A7, B4** — remove the estimator and its references together.
5. **D1, D2, D6** — make the README describe what exists.
6. **B2, B3** — cut a release that works, then point the README at it.
7. **F1, F2** — boot the Pi.
8. **D3, D5** — reconcile the design docs and the coupling register.
9. **C1, C3–C6** — the genuinely new work.

Item 1 first because every other fix is invisible while the build is broken. Item 3 immediately after, because a flasher that leaves a novice alone in someone else's tool has not replaced the 855 lines — it has just moved the failure somewhere less fixable.
