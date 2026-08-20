"""
sambuca :: getting the target machine to boot from the USB.

THE STEP THAT ACTUALLY DEFEATS PEOPLE. The install is automatic; what stops a
novice is the ten minutes before it — a machine that boots straight past the
stick, a boot-menu key that differs per vendor, and three traps that produce
"the USB doesn't work" with no error message anywhere.

Everything here is offline data. Boot keys are stable, publicly documented
facts, and a machine being set up as an air-gapped appliance may well have no
working internet on the desk beside it. The one network action — a search — is
composed here but opened by the user, in their own browser, after they have seen
the URL.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field

from .console import ascii_safe


@dataclass(frozen=True)
class Vendor:
    key: str
    name: str
    boot_menu: str          # one-off "boot from what?" menu
    firmware: str           # full firmware setup
    gotcha: str = ""        # the vendor-specific trap
    aliases: tuple[str, ...] = field(default_factory=tuple)


# Ordered: most common first, so the picker reads sensibly.
VENDORS: tuple[Vendor, ...] = (
    Vendor("dell", "Dell", "F12", "F2",
           "Some business models block USB booting until you turn off "
           "'UEFI Boot Path Security' in the firmware.",
           ("alienware",)),
    Vendor("hp", "HP", "F9 (or Esc, then F9)", "F10 (or Esc, then F10)",
           "On business lines USB booting is often off until you enable "
           "'Legacy Support' or add the stick under Boot Options.",
           ("hewlett", "compaq", "hewlett-packard")),
    Vendor("lenovo-thinkpad", "Lenovo ThinkPad", "F12 (or Enter, then F12)", "F1",
           "Press Enter during the logo for the interrupt menu if F12 alone "
           "does nothing.",
           ("thinkpad", "thinkcentre")),
    Vendor("lenovo-ideapad", "Lenovo IdeaPad / Yoga", "F12", "F2 (or Fn+F2)",
           "Many have a tiny NOVO pinhole beside the power button — press it "
           "with the machine OFF, using a paperclip, and pick Boot Menu.",
           ("ideapad", "yoga", "legion", "lenovo")),
    Vendor("asus", "ASUS", "Esc (or F8)", "F2 (or Del)",
           "Fast Boot must be off, or the firmware never looks at the USB.",
           ("rog", "zenbook", "vivobook")),
    Vendor("acer", "Acer", "F12", "F2",
           "The F12 boot menu is DISABLED by default. Enter setup with F2, set "
           "'F12 Boot Menu' to Enabled, save, then reboot.",
           ("predator", "aspire", "nitro", "swift")),
    Vendor("msi", "MSI", "F11", "Del", "", ()),
    Vendor("gigabyte", "Gigabyte / AORUS", "F12", "Del", "", ("aorus",)),
    Vendor("asrock", "ASRock", "F11", "F2 (or Del)", "", ()),
    Vendor("samsung", "Samsung", "Esc", "F2",
           "Hold Esc BEFORE you power on and keep holding — tapping it after "
           "the logo is too late.",
           ()),
    Vendor("toshiba", "Toshiba / Dynabook", "F12", "F2", "", ("dynabook",)),
    Vendor("surface", "Microsoft Surface",
           "Hold VOLUME DOWN while powering on",
           "Hold VOLUME UP while powering on",
           "There is no keyboard shortcut. Use the volume rocker, and keep "
           "holding until the logo appears.",
           ("microsoft",)),
    Vendor("apple-intel", "Apple (Intel Mac)", "Hold Option/Alt at the chime", "n/a",
           "APPLE SILICON (M1/M2/M3/M4) IS NOT SUPPORTED — sambuca is an x86-64 "
           "appliance. Only Intel Macs can run it.",
           ("mac", "macbook", "imac", "apple")),
    Vendor("custom", "Self-built / other", "F11, F12, Esc or Del", "Del or F2",
           "It depends on the MOTHERBOARD, not the case. Look for the brand "
           "printed on the board itself — ASUS, Gigabyte, MSI, ASRock.",
           ("selfbuilt", "diy", "other", "unknown")),
)

_BY_KEY = {v.key: v for v in VENDORS}


def find_vendor(text: str) -> Vendor | None:
    """Best-effort match of free text ('my dell xps 15') to a vendor."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if t in _BY_KEY:
        return _BY_KEY[t]
    for v in VENDORS:
        if v.key in t or v.name.lower().split(" (")[0] in t:
            return v
        for a in v.aliases:
            if a in t:
                return v
    return None


def search_url(model: str, engine: str = "google") -> tuple[str, str]:
    """Compose the search a novice could not phrase, and return (query, url).

    The phrasing IS the feature. Searching "bios" returns a decade of forum
    threads for the wrong model; quoting the exact model string and naming the
    task returns the vendor's own support page.

    The caller shows the query before opening it — partly so the user can sanity
    check it, and partly so they learn the shape of a good search instead of
    being handed a magic button.
    """
    model = (model or "").strip()
    query = f'"{model}" boot from USB drive BIOS boot menu key' if model \
        else "how to boot from a USB drive BIOS boot menu key"

    engines = {
        "google":     "https://www.google.com/search?q=",
        "duckduckgo": "https://duckduckgo.com/?q=",
        "bing":       "https://www.bing.com/search?q=",
        "startpage":  "https://www.startpage.com/sp/search?query=",
    }
    base = engines.get(engine, engines["google"])
    return query, base + urllib.parse.quote_plus(query)


# ---------------------------------------------------------------------------
# The three traps that are not the boot key. Each produces "the USB doesn't
# work" with no error message, and none of them is discoverable by trying
# harder.
# ---------------------------------------------------------------------------
TRAPS: tuple[tuple[str, str], ...] = (
    (
        "Find your BitLocker key FIRST (Windows machines)",
        "If the target machine currently runs Windows with BitLocker on, changing "
        "the boot order can make Windows demand a recovery key next time it starts. "
        "Get that key BEFORE touching the firmware - sign in at aka.ms/myrecoverykey "
        "and save it somewhere off the machine. Afterwards is too late, and this is "
        "the one trap that can lock you out of the computer you were replacing.",
    ),
    (
        "Use Restart, not Shut Down (Windows machines)",
        "Windows 'Shut Down' does not fully shut down - Fast Startup hibernates the "
        "system instead, and the firmware never re-runs boot selection, so your key "
        "press appears to do nothing. Choose RESTART instead, or turn off Fast "
        "Startup in Control Panel > Power Options.",
    ),
    (
        "Try Secure Boot ON first",
        "The Debian installer sambuca uses is signed, so Secure Boot normally works "
        "untouched. Only turn it off if the stick genuinely refuses to boot - and "
        "turn it back on afterwards. Disabling a security feature pre-emptively 'to "
        "be safe' is how machines end up permanently less safe.",
    ),
)


def guide(model: str = "", vendor: Vendor | None = None, engine: str = "google") -> str:
    """The full printable guide for one machine."""
    v = vendor or find_vendor(model) or _BY_KEY["custom"]
    query, url = search_url(model, engine)
    label = model.strip() or v.name

    out: list[str] = []
    add = out.append

    add("")
    add("=" * 68)
    add(f"  BOOTING {label.upper()} FROM THE SAMBUCA USB")
    add("=" * 68)
    add("")
    add("  This is the only fiddly part. The install itself is automatic.")
    add("")
    add(f"  Detected vendor : {v.name}")
    add(f"  Boot menu key   : {v.boot_menu}")
    add(f"  Firmware setup  : {v.firmware}")
    if v.gotcha:
        add("")
        add(f"  !! {v.name} note:")
        for line in _wrap(v.gotcha, 62):
            add(f"     {line}")

    add("")
    add("  BEFORE YOU START")
    add("  " + "-" * 64)
    for i, (title, body) in enumerate(TRAPS, 1):
        add("")
        add(f"  {i}. {title}")
        for line in _wrap(body, 62):
            add(f"     {line}")

    add("")
    add("  THE STEPS")
    add("  " + "-" * 64)
    add("")
    add("  1. Plug the sambuca USB into the target machine.")
    add("  2. Turn it off completely - use Restart if it runs Windows.")
    add(f"  3. As it powers on, tap {v.boot_menu} repeatedly, about twice a second.")
    add("     Start before anything appears on screen. Too early is fine;")
    add("     too late means starting over.")
    add("  4. A short list of drives appears. Choose the USB stick - it is")
    add("     usually its brand name (SanDisk, Kingston...) and may say 'UEFI'.")
    add("  5. Sambuca starts, shows you the disk it intends to erase, and")
    add("     gives you 30 seconds to stop it.")
    add("")
    add("  If the machine boots normally into its old system instead, the key")
    add(f"  press was too late, or {v.name} needs the firmware change above.")
    add("  Nothing was damaged. Try again.")
    add("")
    add("  IF YOU GET STUCK - the exact search worth running:")
    add("  " + "-" * 64)
    add("")
    add(f"    {query}")
    add("")
    add(f"    {url}")
    add("")
    add("  That phrasing matters. Searching 'bios' gets you ten years of forum")
    add("  threads about other people's machines; quoting your exact model and")
    add("  naming the task gets you your own manufacturer's page.")
    add("")
    add("=" * 68)
    add("")
    return ascii_safe("\n".join(out))


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines
