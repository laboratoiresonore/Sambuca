# The whole workflow, start to finish

Every phase, every fork, and what happens when the answer is "I don't have
that". Written 2026-08-20.

**The rule this is built on** (`CLAUDE.md`, axis 1):

> **1. Do it for the user. 2. If you cannot, guide them through every step.**

So every step below answers three questions: *can this be automatic? if not,
what exactly do we say? and what happens if they say no?*

**Status is marked honestly.** ✅ built and verified · 🟡 partly built · ⬜ not built.

---

## The shape of the thing

Two machines, and confusing them is the root of several past mistakes.

```
   MACHINE A                      MACHINE B
   your everyday computer         the spare box that becomes the appliance
   runs the installer             headless, no screen, no keyboard
   has your keys, your browser    profiles ITSELF on first boot
        │                                    ▲
        └──────── card / USB ────────────────┘
                       │
                  and afterwards:
              ssh (LAN) + tailnet (anywhere)
```

**Machine A cannot see machine B's hardware.** That is why the free-text
"estimator" was deleted: it asked someone to type the specifications of a
computer that was not in front of them. B measures itself and reports back.

---

## Phase 0 — Before anything is written ⬜

| Question | Automatic? | If not |
|---|---|---|
| Which machine am I installing onto? | **No** | Show the tier table. Do not ask them to type specs — B will measure itself. |
| Is it good enough? | **No, not from here** | State the floor plainly: below ~4 GB of memory it will not come up. B refuses on first boot with the specific reasons. |
| Do I have a spare card or stick? | No | Say the size needed and that it will be erased. |

**Not built:** a first-run screen that asks nothing and just orients someone.
Today the app opens on a menu.

---

## Phase 1 — Reachability 🟡 *(the fork that was missing)*

**This is step 1 for a reason.** The appliance is headless. If reachability is
not settled before the card is written, the failure appears at the worst
possible moment: a finished machine that cannot be found.

### The fork, in full

| Where they are | What Sambuca does | Status |
|---|---|---|
| **Tailscale here, signed in** | Detect it, name the tailnet back to them, open the key page, accept and validate the key. | ✅ |
| **Tailscale here, NOT signed in** | Today: tells them to sign in and re-run. **Should:** run `tailscale up` and let their browser handle it, then continue in the same session. | 🟡 |
| **Tailscale not installed** | Offer to install via winget/brew/apt, then fall through to the case above. | ✅ |
| **No Tailscale ACCOUNT** | **Not handled.** Should say it is free for personal use, that signing in uses an account they already have (Google, Microsoft, GitHub) with no new password, and open the signup page. | ⬜ |
| **Declines Tailscale** | Continue LAN-only, and say what that costs: you must find the address yourself, and it can change. | ✅ |
| **Tailscale blocked** (corporate, school, some ISPs) | **Not handled.** Should detect the failure at first boot and fall back rather than appearing broken. | ⬜ |
| **Offline entirely** | **Not handled.** The card can still be written; enrolment must be deferrable to later. | ⬜ |

### The LAN-only fallback needs to be real ⬜

If someone declines or cannot use a tailnet, "find the address yourself" is not
good enough. What is needed:

- **mDNS**: the appliance answers to `sambuca.local`, so there is a name even
  without a tailnet.
- **The install beacon** (already designed in `docs/design/INSTALLER.md`, not
  built): machine A watches for machine B appearing, so the address is found
  *for* them.

Without these, declining Tailscale means the guidance quietly stops — which is
the failure this whole document exists to prevent.

---

## Phase 2 — Identity and access ✅

| Step | Automatic? | Note |
|---|---|---|
| Authorise machine A on the appliance | **Yes** | Existing ssh key preferred; a dedicated one minted if there is none. |
| Put a PUBLIC key on the card | **Yes** | Never a private key. Guarded twice, and verified by scanning every file written. |
| The appliance's own user password | **No** | Collected by the Imager's own screen. Sambuca never stores it. |
| Wi-fi key | **No, deliberately** | Same. A PSK on a card that travels between machines is disclosed when the card is lost. |

---

## Phase 3 — Writing the card ✅ 🟡

Raspberry Pi Imager does the writing. Sambuca supplies the image list and the
guidance around it.

| Step | Automatic? | Status |
|---|---|---|
| Install the Imager if missing | **Yes** | ✅ |
| Pre-select device and OS | **Should be** — the catalogue supports `default: true` | ⬜ G2 |
| Pre-fill Customisation | **Yes** — hostname, timezone, keyboard, ssh | ✅ |
| **Choose the storage** | **NO, deliberately human** | ✅ guided |
| Warn before the permission prompt | **Yes** | ✅ |
| Write, verify | **Yes** — theirs | ✅ |
| Provision afterwards | **Yes**, automatically | ✅ |

**Storage stays manual on purpose.** It is the only irreversible choice, and
automating "which disk gets erased" is how someone loses a backup drive. So it
gets the most words: every attached drive named by size and label.

---

## Phase 4 — First boot 🟡

The card goes into machine B. Nobody is watching — there is no screen.

| Step | Status |
|---|---|
| Run once, then remove its own hook | ✅ |
| Log everything **back onto the card** | ✅ |
| Install the operator's key for every account | ✅ |
| Join the tailnet, then shred the key off the card | ✅ *(never run on hardware)* |
| Profile the hardware, refuse below the floor | ✅ *(never run on hardware)* |
| **Report progress to machine A while it happens** | ⬜ the beacon |
| Install Docker, the stack, the services | ⬜ x86 only today |

**The card is the channel home.** A headless Pi with no ethernet can still tell
you what happened: put the card in a reader and read one file. That works with
no network, no screen and no keyboard — and it is why it was built that way.

---

## Phase 5 — The appliance configures itself ⬜ *(x86 only, unverified)*

Files, calendar, photos, passwords, notes, PDF tools, chat, encrypted disk,
backups, health checks. **No machine has been installed end to end.** This is
the largest unverified claim in the project.

---

## Phase 6 — The AI plane 🟡

### Chat ✅ *(catalogue built, never run)*
Model chosen per tier from `engine/profiles/`, pulled and smoke-tested on first
boot.

### Pictures — ComfyUI 🟡

| Step | Status | Note |
|---|---|---|
| Decide whether this machine can | ✅ | VRAM floor, disk guard, fixed drop order |
| Fetch FLUX.1-schnell, digest-pinned | ✅ | 16 GiB, resumable, atomic — never run |
| Run headless behind a shipped workflow | ✅ written | No picture has come out of it |
| **The GPU handoff protocol** | ⬜ | The decision is computed; the unload/reload is not implemented |
| **The prompt-expansion step** | ⬜ | The chat model turning "a card for my mum" into a FLUX prompt |
| **The "describe a picture" surface** | ⬜ | Blocked on publishing Odysseus |

### ComfyUI fine settings — the polish ⬜

Not started, and this is where "it draws" becomes usable rather than technically
present:

- **Quality vs speed as one control.** Not steps, samplers and CFG — a person
  wants "quick" or "good". schnell is 4 steps; the honest knob is resolution
  and batch, not sampler soup.
- **Shape, not dimensions.** "Square / landscape / portrait / phone wallpaper",
  mapped to resolutions FLUX was trained near. Arbitrary sizes degrade it.
- **Where pictures go.** They live on a tmpfs and vanish. Saving one must put it
  in the owner's own photo library or drive, deliberately.
- **The negative-prompt trap.** At cfg 1.0 it does nothing, so it is not shown.
  A control that does nothing is worse than no control.
- **Sensible refusals.** When the card is busy generating, say so rather than
  queueing silently.
- **The graph, for the curious.** One click, gated, clearly "you do not need
  this".

### The Steward 🟡
Catalogue and linter built and mutation-tested. **The runtime that selects and
executes verbs is not built** — the single largest gap in the AI plane.

---

## Phase 7 — Handover ⬜

The moment the project is judged on, and almost none of it exists.

| Step | Status |
|---|---|
| Verify every link before showing it | ⬜ |
| Export browser bookmarks in one click | ⬜ |
| Install the CA certificate | ⬜ |
| Print the recovery sheet | 🟡 PDF generated; printing not prompted |
| **Verify the recovery key while they are holding the sheet** | ⬜ |
| Say which address is for home and which for away | ⬜ |

**Verifying the recovery key is the one that matters.** It is the only moment
someone is perfectly positioned to test it, and an untested recovery key is a
hypothesis.

---

## Phase 8 — Living with it ⬜

Updates, health alerts, adding a person, adding a device, backups proven by
restore, and the graduated response when something breaks. All designed, none
built.

---

## What to build next, in order

1. **Phase 1 forks** — no account, not signed in, blocked, offline. Step 1
   currently assumes a happy path that many people are not on.
2. **The LAN-only fallback** — mDNS plus the beacon, so declining a tailnet does
   not mean the guidance stops.
3. **G2** — pre-select device and OS, the last two screens still answered by hand.
4. **Phase 7 handover** — cheap to build, and it is what the whole thing is
   judged on.
5. **The Steward runtime** — the AI plane's headline claim.
6. **ComfyUI polish** — after a picture has come out of it once.
7. **Phase 5 on real hardware** — the largest unverified claim in the project.

Item 1 first because the fork is where people actually are. Everything after
the happy path is currently a cliff.
