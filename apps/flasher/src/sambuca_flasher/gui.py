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

from dataclasses import dataclass, field


def available() -> tuple[bool, str]:
    """Can a window be opened here? Returns (yes, reason-if-not).

    Checked by IMPORTING, not by guessing from the platform. tkinter is
    stdlib-on-paper and genuinely absent in practice: Debian and Ubuntu split
    it into python3-tk, and a frozen binary only has it if the build bundled
    the Tcl/Tk shared libraries.
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


def plan(*, has_tailscale: bool, has_imager: bool) -> list[Step]:
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
        Step(
            key="recovery",
            title="Your way back in",
            body=("This is the only copy of the words that unlock the machine "
                  "if the password is ever lost.\n\n"
                  "Print it now. Nobody can recover it for you — that is what "
                  "makes it yours."),
            continue_label="Print",
        ),
        Step(
            key="done",
            title="Put the card in and switch it on",
            body=("First boot takes 10 to 20 minutes while it downloads and "
                  "starts everything.\n\n"
                  "Sambuca will watch and tell you when it is ready."),
            continue_label="Watch it start",
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
