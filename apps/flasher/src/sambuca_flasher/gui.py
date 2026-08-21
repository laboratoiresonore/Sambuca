"""
sambuca :: a window for the half of the flow that is ours.

WHY THIS EXISTS. The owner asked for a graphical program and got a console
menu; the verdict was blunt and correct — "that is NOT a GUI like I asked". The
writing half is Raspberry Pi Imager's and stays that way. This is the half
Sambuca owns: settling reachability, guiding the Imager, provisioning the card,
and handing over.

═══════════════════════════════════════════════════════════════════════════
tkinter IS IMPORTED LAZILY, AND THAT IS A DELIBERATE SAFETY PROPERTY, NOT
TIDINESS.

If tkinter is missing — no python3-tk on a Linux box, a PyInstaller build that
did not bundle the Tcl/Tk libraries — a module-level import would raise at
STARTUP and take the whole application with it. Every command would die,
including `list`, `verify-sheet` and `open-vault`, for want of a window nobody
asked for.

That failure has already happened once in this project, with a different
library: keys.py raised SystemExit at import time when the BIP-39 package was
absent, so `--version` died demanding a seed-phrase library. The fix there was
the same as the rule here.

So: importing this module is always safe. `available()` answers honestly, and
the caller falls back to the console flow that already works.
═══════════════════════════════════════════════════════════════════════════

WHAT IT DOES NOT DO, on purpose:

  * It does not write images. rpi-imager does, and it does it on three
    platforms with years of edge cases behind it.
  * It does not collect the owner's wifi key or disk password. Those go into
    the Imager's own Customisation screen, where Sambuca never sees them.
  * It does not replace the console flow. Both reach the same functions; the
    window is a second front door, not a fork.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field


@functools.lru_cache(maxsize=1)
def available() -> tuple[bool, str]:
    """Can a window be opened here? Returns (yes, reason-if-not).

    Checked by IMPORTING, not by guessing from the platform. tkinter is
    stdlib-on-paper and genuinely absent in practice: Debian and Ubuntu split
    it into python3-tk, and a frozen binary only has it if the build bundled
    the Tcl/Tk shared libraries.

    PROBED ONCE PER PROCESS. The probe has a side effect — it creates a real Tk
    root and destroys it — and repeating that is not reliably idempotent: on a
    Windows CI runner with a partial Tcl install, the first probe succeeded and
    the next raised TclError, so the same process gave two different answers to
    the same question. Cached, because the answer cannot usefully change
    mid-run and asking twice is what broke it.

    Tests that fake the toolkit must call `available.cache_clear()`.
    """
    try:
        import tkinter
    except Exception as exc:                      # noqa: BLE001
        return False, f"no graphical toolkit available ({exc.__class__.__name__})"

    # Importing is not the same as being able to open a display. A headless
    # Linux box has the module and no X server, and Tk raises only when a root
    # window is actually created.
    try:
        root = tkinter.Tk()
        root.withdraw()
        root.destroy()
    except Exception as exc:                      # noqa: BLE001
        return False, f"no display to open a window on ({exc.__class__.__name__})"

    return True, ""


@dataclass
class Step:
    """One screen's worth of state, kept out of the widgets.

    SEPARATED SO IT CAN BE TESTED. Widget code needs a display and a running
    event loop; the decisions — which step comes next, whether the owner may
    continue, what the message says — are ordinary data and get ordinary tests.
    Every bug found in this project came from executing logic, and logic buried
    in a callback cannot be executed without a screen.
    """
    key: str
    title: str
    body: str
    can_continue: bool = True
    continue_label: str = "Continue"
    notes: list[str] = field(default_factory=list)


def plan(*, has_tailscale: bool, has_imager: bool,
         makes_recovery_document: bool = False,
         can_watch: bool = False) -> list[Step]:
    """The screens, decided from what is actually on this machine.

    Takes facts rather than discovering them, so the sequence can be tested
    against every combination without installing or uninstalling anything.
    """
    steps = [
        Step(
            key="welcome",
            title="Turn a spare machine into your own private cloud",
            body=(
                "This writes a memory card that turns a Raspberry Pi into a "
                "machine that holds your files, photos and passwords — in your "
                "home, on hardware you own.\n\n"
                "Nothing is erased until you choose a card and confirm it."),
            continue_label="Start",
        ),
    ]

    # REACHABILITY FIRST, before anything is written. An appliance nobody can
    # find is not an appliance, and discovering that after the card is finished
    # is the worst possible moment.
    if has_tailscale:
        steps.append(Step(
            key="reachability",
            title="How you will reach it",
            body=("Tailscale is already installed on this computer, so the "
                  "appliance can join your private network and be reachable by "
                  "name from anywhere.\n\n"
                  "Sambuca will ask Tailscale for a one-time key. You never "
                  "type it."),
        ))
    else:
        steps.append(Step(
            key="reachability",
            title="How you will reach it",
            body=("The machine you are building will have no screen and no "
                  "keyboard. You reach it over the network, so that has to be "
                  "settled BEFORE it is built.\n\n"
                  "Tailscale is free for personal use and you sign in with an "
                  "account you already have. No new password.\n\n"
                  "You can also skip this and reach it on your home network "
                  "only."),
            continue_label="Install Tailscale",
        ))

    if not has_imager:
        steps.append(Step(
            key="imager",
            title="One tool to install first",
            body=("Sambuca does not write memory cards itself. It uses "
                  "Raspberry Pi Imager, which is made by the people who make "
                  "the Pi and has been doing this for years.\n\n"
                  "It is free, and Sambuca can install it for you now."),
            continue_label="Install it",
        ))

    steps.extend([
        Step(
            key="write",
            title="Choose the card",
            body=("Raspberry Pi Imager will open in its own window.\n\n"
                  "Sambuca has already filled in the machine name and the "
                  "settings that are not secret. You choose the card, and you "
                  "type your own wi-fi key and password into the Imager — "
                  "Sambuca never sees them.\n\n"
                  "EVERYTHING ON THE CARD YOU CHOOSE WILL BE ERASED."),
            continue_label="Open the Imager",
        ),
        Step(
            key="provision",
            title="Adding Sambuca to the card",
            body=("The Imager has finished writing. Sambuca now adds its own "
                  "first-boot configuration.\n\n"
                  "If the card was ejected, put it back in — that is normal, "
                  "and the Imager does it on purpose."),
        ),
    ])

    # ONLY WHERE THE DOCUMENT ACTUALLY EXISTS. The first draft of these screens
    # included "Your way back in — print it now, nobody can recover it for
    # you", which is true of the x86 installer and a plain lie about a
    # Raspberry Pi card: that flow generates no key material, writes no PDF and
    # offers no vault, because the Pi appliance has no encrypted root to be
    # locked out of.
    #
    # Promising a novice a piece of paper that never appears is worse than
    # saying nothing, so the screen exists only when the document does.
    if makes_recovery_document:
        steps.append(Step(
            key="recovery",
            title="Your way back in",
            body=("This is the only copy of the words that unlock the machine "
                  "if the password is ever lost.\n\n"
                  "Print it now. Nobody can recover it for you — that is what "
                  "makes it yours."),
            continue_label="Print",
        ))

    steps.extend([
        Step(
            key="done",
            title="Put the card in and switch it on",
            # WHAT THIS SAYS DEPENDS ON WHETHER ANYTHING CAN ACTUALLY WATCH.
            #
            # It used to promise "Sambuca will watch and tell you when it is
            # ready", with a button reading "Watch it start" — and there was NO
            # action behind that button, so it simply closed the window. Worse,
            # on the Pi flow there is nothing to watch at all: the beacon runs
            # from first-boot.sh and the Pi's firstrun.sh never invokes it. The
            # promise could not have been kept even with the button wired.
            #
            # The card writing its results back into its own log IS the Pi's
            # feedback mechanism. Saying that is honest and actionable;
            # "Sambuca will tell you" was neither.
            body=("First boot takes 10 to 20 minutes while it downloads and "
                  "starts everything. The lights will blink; leave it alone.\n\n"
                  "It writes its results BACK ONTO THE CARD. If something goes "
                  "wrong, put the card in a reader and open "
                  "sambuca-firstboot.log — it says what happened, in plain "
                  "words.\n\n"
                  "Once it is up, come back and run:  sambuca-flasher handover")
            if not can_watch else
                 ("First boot takes 10 to 20 minutes while it downloads and "
                  "starts everything.\n\n"
                  "Sambuca can watch it and tell you when it is ready."),
            continue_label="Watch it start" if can_watch else "Close",
        ),
    ])
    return steps


def next_key(steps: list[Step], current: str) -> str | None:
    """The step after `current`, or None at the end."""
    keys = [s.key for s in steps]
    if current not in keys:
        return None
    i = keys.index(current)
    return keys[i + 1] if i + 1 < len(keys) else None


# ---------------------------------------------------------------------------
# The window itself
# ---------------------------------------------------------------------------
#
# EVERY ACTION IS INJECTED. The wizard knows how to show a screen and move to
# the next one; it does not know how to install Tailscale or start the Imager.
# That is not layering for its own sake — it is what lets the whole thing be
# driven in tests without launching a real installer or erasing a real card.


class Wizard:
    """A plain next/back wizard over `plan()`.

    Deliberately dull. A novice installing an operating system for the first
    time does not want a novel interface; they want to know where they are, how
    many steps are left, and which button goes forward.
    """

    def __init__(self, steps, actions=None, root=None):
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self.steps = list(steps)
        self.actions = dict(actions or {})
        self.index = 0
        self.busy = False

        self.root = root or tk.Tk()
        self.root.title("Sambuca")
        self.root.minsize(620, 420)

        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)

        self.progress = ttk.Label(outer, text="", foreground="#666")
        self.progress.pack(anchor="w")

        self.title = ttk.Label(outer, text="", font=("", 16, "bold"),
                               wraplength=560, justify="left")
        self.title.pack(anchor="w", pady=(8, 12))

        self.body = ttk.Label(outer, text="", wraplength=560, justify="left")
        self.body.pack(anchor="w", fill="x")

        # Its own row so a long message cannot push the buttons off-screen —
        # which on a 720p laptop would leave somebody with no way forward.
        buttons = ttk.Frame(outer)
        buttons.pack(side="bottom", fill="x", pady=(24, 0))

        self.back_btn = ttk.Button(buttons, text="Back", command=self.back)
        self.back_btn.pack(side="left")

        self.next_btn = ttk.Button(buttons, text="Continue", command=self.forward)
        self.next_btn.pack(side="right")

        self.status = ttk.Label(outer, text="", foreground="#666", wraplength=560)
        self.status.pack(side="bottom", anchor="w")

        self.render()

    # -- state -------------------------------------------------------------
    @property
    def current(self):
        return self.steps[self.index]

    def render(self):
        s = self.current
        self.progress.configure(
            text=f"Step {self.index + 1} of {len(self.steps)}")
        self.title.configure(text=s.title)
        self.body.configure(text=s.body)
        self.next_btn.configure(
            text=s.continue_label,
            state="normal" if (s.can_continue and not self.busy) else "disabled")
        # No Back on the first screen: a button that cannot do anything teaches
        # people the machine is arbitrary.
        self.back_btn.configure(
            state="disabled" if (self.index == 0 or self.busy) else "normal")

    def back(self):
        if self.index > 0 and not self.busy:
            self.index -= 1
            self.status.configure(text="")
            self.render()

    def forward(self):
        """Run this step's action, then advance only if it succeeded.

        ADVANCING ON FAILURE IS THE BUG THIS AVOIDS. If installing Tailscale or
        starting the Imager fails, moving on regardless would leave somebody on
        a screen that assumes work which never happened — and the next screen's
        instructions would be quietly wrong.
        """
        if self.busy:
            return
        step = self.current
        action = self.actions.get(step.key)

        if action is not None:
            self.busy = True
            self.render()
            try:
                ok, message = action()
            except Exception as exc:                      # noqa: BLE001
                # A traceback in a window is the failure mode this audience
                # cannot act on. Say what happened, stay put.
                ok, message = False, f"{exc.__class__.__name__}: {exc}"
            finally:
                self.busy = False
            self.status.configure(text=message or "")
            if not ok:
                self.render()
                return

        if self.index + 1 < len(self.steps):
            self.index += 1
            self.status.configure(text="")
            self.render()
        else:
            self.close()

    def close(self):
        # Swallowed deliberately and narrowly: Tk raises if the window is
        # already gone (the owner clicked the X, or a test destroyed it), and
        # there is nothing to report about closing something already closed.
        try:
            self.root.destroy()
        except Exception:                                 # noqa: BLE001, S110
            pass

    def run(self):                                        # pragma: no cover
        self.root.mainloop()


# ---------------------------------------------------------------------------
# What the buttons actually do
# ---------------------------------------------------------------------------


def build_actions(*, hostname="sambuca", engine=None):
    """Map step keys to callables, each returning (ok, message).

    THESE CALL THE SAME FUNCTIONS THE CONSOLE FLOW CALLS. Not a parallel
    implementation — `tailnet.install_here`, `imager.try_install`,
    `imager.launch` and `pi.provision_boot_partition` already exist as clean
    functions, and the console path is those functions wrapped in prompts.
    Two code paths for one job is how they drift until only one of them works.

    Every import is INSIDE a callable. Building the map must stay free of side
    effects and free of import cycles — cli imports gui, so gui importing cli
    at module scope would be a loop.
    """
    state: dict = {}

    def reachability():
        from . import tailnet
        st = tailnet.status()
        if st.installed:
            return True, "Tailscale is installed."
        if tailnet.install_here():
            return True, "Tailscale installed."
        # NOT FATAL. A LAN-only appliance is a supported outcome, not a
        # failure — saying otherwise would push somebody into abandoning a
        # perfectly good install.
        return True, ("Could not install Tailscale. Carrying on: the appliance "
                      "will be reachable on your home network only.")

    def imager_step():
        from . import imager
        if imager.find_imager() is not None:
            return True, "Raspberry Pi Imager is installed."
        if imager.try_install():
            return True, "Raspberry Pi Imager installed."
        return False, ("Could not install it automatically. Install Raspberry "
                       "Pi Imager yourself, then press this again.")

    def write():
        from . import imager
        if imager.find_imager() is None:
            return False, "Raspberry Pi Imager is not installed yet."
        if imager.already_running():
            return True, ("The Imager is already open — use that window, then "
                          "come back here.")
        try:
            imager.launch(wait=True)
        except Exception as exc:                          # noqa: BLE001
            return False, f"Could not start the Imager: {exc}"
        return True, "The Imager has closed."

    def provision():
        import tempfile
        from pathlib import Path

        from . import pi
        from .cli import _find_engine, _stage_engine

        boot = pi.find_boot_partition()
        if boot is None:
            # THE EJECT SEAM, and it is normal rather than an error. rpi-imager
            # dismounts the card when it finishes; no amount of rescanning
            # brings it back, so the card has to be physically re-seated.
            return False, ("Cannot see the card. The Imager ejects it when it "
                           "finishes, which is normal — take it out, put it "
                           "back in, then press this again.")

        engine_dir = _find_engine(engine)
        if engine_dir is None:
            return False, "This build cannot find its engine files."

        staging = Path(tempfile.mkdtemp(prefix="sambuca-pi-"))
        try:
            count = _stage_engine(engine_dir, staging / "sambuca")
            actions_done = pi.provision_boot_partition(
                boot,
                payload_dir=staging / "sambuca",
                hostname=hostname,
                tailscale_key=state.get("tailscale_key", ""),
            )
        except Exception as exc:                          # noqa: BLE001
            return False, f"Could not write to the card: {exc}"
        return True, f"Added {count} files to the card ({len(actions_done)} steps)."

    return {
        "reachability": reachability,
        "imager": imager_step,
        "write": write,
        "provision": provision,
    }
