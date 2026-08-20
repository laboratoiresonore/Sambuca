"""
sambuca :: recovery document generation.

Produces `liberator-recovery.pdf` — the single piece of paper that can rebuild
this appliance from nothing.

DESIGN CONSTRAINTS, all learned from documents that failed when they were
actually needed:

  * The seed words are printed in a NUMBERED GRID, not a paragraph. Word order
    is the whole secret; a reflowed paragraph loses it.
  * Monospace for anything transcribed, with the ambiguous glyph pairs already
    excluded upstream in keys.py.
  * The fingerprint appears on every page, so a document found loose can be
    matched to a machine without reading the secret.
  * Recovery INSTRUCTIONS are on the same sheet as the secrets. A recovery
    procedure that lives on a wiki hosted by the machine you are recovering is
    not a recovery procedure.
  * No QR code for the seed. A camera-readable secret is a secret that leaks to
    every photo backup and every shoulder in the room.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .keys import KeyMaterial
from .payload import ApplianceConfig

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
except ImportError as exc:  # pragma: no cover - dependency is declared
    raise SystemExit(
        "sambuca-flasher requires 'reportlab' to produce the recovery document.\n"
        "Install it with:  pip install reportlab"
    ) from exc


_MARGIN = 18 * mm
_MONO = "Courier-Bold"
_BODY = "Helvetica"
_HEAD = "Helvetica-Bold"


def write_recovery_pdf(
    path: Path,
    keys: KeyMaterial,
    config: ApplianceConfig,
    *,
    tailnet_hint: str = "",
) -> Path:
    """Render the recovery document. Returns the path written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"sambuca recovery — {config.hostname}")
    c.setAuthor("sambuca")
    c.setSubject("Appliance recovery credentials — treat as cash")
    # Do not embed the hostname in keywords: PDF metadata is indexed by every
    # document manager and cloud sync, which is precisely what this appliance
    # exists to avoid.

    _page_one(c, keys, config)
    c.showPage()
    _page_two(c, keys, config, tailnet_hint)
    c.showPage()
    c.save()
    return path


# ---------------------------------------------------------------------------


def _header(c: canvas.Canvas, keys: KeyMaterial, subtitle: str) -> float:
    w, h = A4
    y = h - _MARGIN

    c.setFont(_HEAD, 20)
    c.drawString(_MARGIN, y, "SAMBUCA")
    c.setFont(_BODY, 10)
    c.drawRightString(w - _MARGIN, y + 2, f"key {keys.fingerprint}")
    y -= 7 * mm

    c.setFont(_BODY, 11)
    c.drawString(_MARGIN, y, subtitle)
    y -= 4 * mm

    c.setLineWidth(1.2)
    c.line(_MARGIN, y, w - _MARGIN, y)
    return y - 8 * mm


def _warning_box(c: canvas.Canvas, y: float, lines: list[str]) -> float:
    w, _ = A4
    box_h = 6 * mm + len(lines) * 5 * mm
    c.setLineWidth(1.5)
    c.rect(_MARGIN, y - box_h, w - 2 * _MARGIN, box_h)
    ty = y - 8 * mm
    for i, line in enumerate(lines):
        c.setFont(_HEAD if i == 0 else _BODY, 11 if i == 0 else 9.5)
        c.drawString(_MARGIN + 4 * mm, ty, line)
        ty -= 5 * mm
    return y - box_h - 6 * mm


def _page_one(c: canvas.Canvas, keys: KeyMaterial, config: ApplianceConfig) -> None:
    w, _ = A4
    y = _header(c, keys, f"Recovery document — {config.hostname}")

    y = _warning_box(c, y, [
        "THIS SHEET IS THE KEY TO THE MACHINE.",
        "Anyone holding it can decrypt the disk and every backup.",
        "Store it the way you would store cash. Do not photograph it.",
        "Do not scan it. Do not type it into anything that syncs.",
    ])

    # --- seed phrase -------------------------------------------------------
    c.setFont(_HEAD, 13)
    c.drawString(_MARGIN, y, "1.  BACKUP SEED PHRASE  (24 words)")
    y -= 5 * mm
    c.setFont(_BODY, 9)
    c.drawString(_MARGIN, y,
                 "Reconstructs the encrypted backup repository. "
                 "Order matters — copy the numbers.")
    y -= 8 * mm

    words = keys.seed_phrase.split()
    cols, rows = 4, 6
    col_w = (w - 2 * _MARGIN) / cols
    top = y
    for idx, word in enumerate(words):
        col, row = idx % cols, idx // cols
        x = _MARGIN + col * col_w
        wy = top - row * 8 * mm
        c.setFont(_BODY, 8)
        c.setFillGray(0.45)
        c.drawString(x, wy, f"{idx + 1:>2}.")
        c.setFillGray(0)
        c.setFont(_MONO, 11)
        c.drawString(x + 7 * mm, wy, word)
    y = top - rows * 8 * mm - 6 * mm

    c.setLineWidth(0.5)
    c.setDash(2, 2)
    c.line(_MARGIN, y, w - _MARGIN, y)
    c.setDash()
    y -= 9 * mm

    # --- root passphrase ---------------------------------------------------
    c.setFont(_HEAD, 13)
    c.drawString(_MARGIN, y, "2.  ROOT PASSPHRASE  (32 characters)")
    y -= 5 * mm
    c.setFont(_BODY, 9)
    c.drawString(_MARGIN, y,
                 "Unlocks the encrypted disk at boot, and is the console account password.")
    y -= 9 * mm

    c.setLineWidth(0.8)
    c.rect(_MARGIN, y - 3 * mm, w - 2 * _MARGIN, 11 * mm)
    c.setFont(_MONO, 14)
    # Split into 8-character groups: 32 unbroken characters are transcribed
    # wrongly far more often than four visible groups of eight.
    pw = keys.root_passphrase
    grouped = "  ".join(pw[i : i + 8] for i in range(0, len(pw), 8))
    c.drawString(_MARGIN + 4 * mm, y + 1 * mm, grouped)
    y -= 14 * mm

    c.setFont(_BODY, 8.5)
    c.setFillGray(0.3)
    c.drawString(_MARGIN, y,
                 "Spaces above are for reading only — type the characters with no spaces.")
    c.setFillGray(0)
    y -= 9 * mm

    # --- disk recovery key -------------------------------------------------
    # The second way in. Without it, forgetting the passphrase above destroys
    # every file on the machine — so it is printed here, on the same sheet,
    # rather than being something the owner has to know to go and derive.
    c.setFont(_HEAD, 13)
    c.drawString(_MARGIN, y, "3.  DISK RECOVERY KEY")
    y -= 5 * mm
    c.setFont(_BODY, 9)
    c.drawString(_MARGIN, y,
                 "Also opens the disk, on its own, if the passphrase above is lost. "
                 "Type it exactly, dashes included.")
    y -= 9 * mm

    c.setLineWidth(0.8)
    c.rect(_MARGIN, y - 3 * mm, w - 2 * _MARGIN, 11 * mm)
    c.setFont(_MONO, 12)
    c.drawString(_MARGIN + 4 * mm, y + 1 * mm, keys.luks_recovery_key)
    y -= 15 * mm

    # --- the honest USB warning -------------------------------------------
    if config.unattended:
        _warning_box(c, y, [
            "THE INSTALLER USB IS ALSO A KEY, UNTIL INSTALLATION FINISHES.",
            "Unattended installation requires the disk passphrase to be on the stick.",
            "The appliance erases it on first boot. Until then, treat the USB as this",
            "sheet: do not leave it in the machine, do not lend it, do not post it.",
        ])
    else:
        _warning_box(c, y, [
            "INTERACTIVE MODE — no secret was written to the USB stick.",
            "The installer will pause once and ask for the root passphrase above.",
            "Have this sheet with you when you start the installation.",
        ])


def _page_two(
    c: canvas.Canvas,
    keys: KeyMaterial,
    config: ApplianceConfig,
    tailnet_hint: str,
) -> None:
    w, _ = A4
    y = _header(c, keys, "How to reach it, and how to recover it")

    def section(title: str, ry: float) -> float:
        c.setFont(_HEAD, 12)
        c.drawString(_MARGIN, ry, title)
        return ry - 6 * mm

    def line(text: str, ry: float, *, mono: bool = False, indent: float = 0) -> float:
        c.setFont(_MONO if mono else _BODY, 9 if mono else 9.5)
        c.drawString(_MARGIN + indent, ry, text)
        return ry - 4.6 * mm

    y = section("REACHING THE APPLIANCE", y)
    y = line("On the same network, once installation completes:", y)
    y = line(f"https://{config.domain}/", y, mono=True, indent=6 * mm)
    y = line("If that name does not resolve, find the address from your router's", y)
    y = line(f"client list — the machine appears as \"{config.hostname}\".", y)
    y -= 2 * mm
    y = line("Your browser will warn about the certificate the first time. That is", y)
    y = line("expected: the appliance issues its own. Install its root certificate", y)
    y = line("from the link on the dashboard to stop the warning.", y)
    y -= 3 * mm

    if config.tailscale_authkey or tailnet_hint:
        y = line("From anywhere, over Tailscale (no port forwarding, no open ports):", y)
        y = line(tailnet_hint or f"https://{config.hostname}.<your-tailnet>.ts.net/", y,
                 mono=True, indent=6 * mm)
        y = line("Remote services are on ports 8443 (chat) 8444 (files) 8445 (photos)", y)
        y = line("8446 (passwords) 8447 (notes) — a tailnet host has only one name.", y)
        y -= 3 * mm

    y = section("IF THE MACHINE WILL NOT BOOT", y)
    y = line("1. At the passphrase prompt, type the ROOT PASSPHRASE from page 1.", y)
    y = line("2. If that is lost, type the DISK RECOVERY KEY instead. It opens the", y)
    y = line("   disk on its own. Then set a passphrase you will remember:", y)
    y = line("   sudo cryptsetup luksChangeKey <device>", y, mono=True, indent=6 * mm)
    y = line("3. Lost the sheet but still have the 24 words? Recompute the key on", y)
    y = line("   any computer, offline:", y)
    y = line("   sambuca-flasher derive-recovery-key", y, mono=True, indent=6 * mm)
    y = line("4. If the disk is intact but the system is broken, boot the installer", y)
    y = line("   USB in rescue mode and unlock with either secret.", y)
    y = line("5. Log in as:", y)
    y = line(f"   {config.admin_user}   (password = the root passphrase)", y, mono=True)
    y -= 3 * mm

    y = section("IF THE MACHINE IS GONE ENTIRELY", y)
    y = line("Your backups are encrypted with a key derived from the 24-word seed.", y)
    y = line("On any computer with restic installed:", y)
    y -= 1 * mm
    for cmd in (
        "pip install mnemonic",
        "sambuca-flasher derive-backup-key      # type the 24 words when asked",
        "restic -r <path-or-URL-to-repository> snapshots",
        "restic -r <repository> restore latest --target ./recovered",
    ):
        y = line(cmd, y, mono=True, indent=6 * mm)
    y -= 1 * mm
    y = line("The seed alone is sufficient. You do not need this machine, this USB,", y)
    y = line("or any account with any company, to read your own data again.", y)
    y -= 4 * mm

    y = section("WHAT IS ON THIS MACHINE", y)
    bundle_names = {
        "ai": "local AI chat and agents (no data leaves the machine)",
        "cloud": "files, calendar, contacts, photos, passwords",
        "office": "notes and PDF tools",
        "comms": "encrypted chat (IRC and Matrix)",
    }
    for b in config.bundles:
        y = line(f"  - {bundle_names.get(b, b)}", y)

    # --- footer -------------------------------------------------------------
    c.setFont(_BODY, 8)
    c.setFillGray(0.45)
    c.drawString(
        _MARGIN, _MARGIN,
        f"sambuca recovery document · key {keys.fingerprint} · "
        f"generated {datetime.now(timezone.utc).strftime('%Y-%m-%d')} · "
        "github.com/laboratoiresonore/Sambuca",
    )
    c.drawRightString(w - _MARGIN, _MARGIN, "page 2 of 2")
    c.setFillGray(0)
