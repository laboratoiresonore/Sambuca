"""
sambuca :: flasher command line.

The whole write flow, in the order it must happen:

    1.  generate key material            (offline, on this machine)
    2.  build + validate the payload     (refuses if a secret leaked in)
    3.  WRITE THE RECOVERY DOCUMENT      <- before touching the USB
    4.  confirm the target device        (typed confirmation, no default yes)
    5.  write the ISO, verify by readback
    6.  inject the payload

Step 3 is before step 5 on purpose. If the write fails, the operator still has
the document; if the document generation fails, no half-configured stick exists
whose credentials nobody has. The expensive, destructive step goes last.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from . import __version__
from .devices import DeviceError, list_removable_devices

# NOT imported at module level. keys.py raises SystemExit at IMPORT time when
# the BIP-39 library is absent, which meant `list`, `boot-guide`,
# `example-config` and even `--version` all died demanding a package they do
# not use — every command in the CLI died demanding a seed-phrase library.
# Found by running `list` against a real card reader.
from .payload import ApplianceConfig, build_provision_payload, config_from_dict, render_preseed

# Imported lazily inside cmd_write, for the same reason as .keys above:
# recovery_pdf raises SystemExit at import time when reportlab is missing, and
# its module-level constants genuinely need reportlab's units, so the guard
# cannot simply move to the point of use inside that module. Keeping the import
# here would mean `list` and `boot-guide` still died — just demanding a PDF
# library instead of a seed-phrase one.
from .writer import inject_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sambuca-flasher",
        description="Write a sambuca installer USB and its recovery document.",
    )
    parser.add_argument("--version", action="version", version=f"sambuca-flasher {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list writable removable devices")
    p_list.add_argument(
        "--allow-large", action="store_true",
        help="include devices over 512 GB (hidden by default — usually backup drives)")

    p_write = sub.add_parser("write", help="build and write an installer USB")
    p_write.add_argument("--iso", type=Path, required=True,
                         help="Debian netinst ISO (the menu can download it for you)")
    p_write.add_argument("--config", type=Path, help="JSON appliance configuration")
    p_write.add_argument("--output-dir", type=Path, default=Path.cwd(),
                         help="where the recovery PDF is written (default: cwd)")
    p_write.add_argument(
        "--interactive", action="store_true",
        help="keep the disk passphrase OFF the USB; the installer prompts once")
    p_write.add_argument("--dry-run", action="store_true",
                         help="generate keys, payload and PDF; do not touch any device")

    p_pi = sub.add_parser(
        "write-pi",
        help="write a Raspberry Pi OS card with sambuca first-boot provisioning")
    # NOT REQUIRED. rpi-imager downloads the image itself, from the OS list
    # Sambuca publishes. Demanding a file here made the first command anybody
    # runs refuse to start, asking for something they neither have nor need.
    # rpi-imager selects the device and verifies its own write. Arguments for
    # both used to sit here doing nothing, which is the same fault as a
    # negative-prompt box: a control that changes no behaviour is worse than
    # no control, because someone will reasonably assume it works.
    p_pi.add_argument("--image", type=Path,
                      help="a local image to write instead of the one rpi-imager "
                           "downloads (rarely needed)")
    p_pi.add_argument("--hostname", default="sambuca")
    p_pi.add_argument("--engine", type=Path,
                      help="engine directory to stage onto the card "
                           "(default: the engine/ beside this repo)")
    p_pi.add_argument("--wifi-ssid",
                      help="note the network name on the card. NO KEY IS WRITTEN.")
    p_pi.add_argument("--no-ssh", action="store_true")
    p_pi.add_argument(
        "--tailscale-key",
        help="pre-auth key so the appliance joins your tailnet on first boot "
             "and is reachable by name from anywhere. Shredded off the card "
             "once used.")
    p_pi.add_argument(
        "--no-authorise", action="store_true",
        help="do NOT authorise this computer on the appliance (you will need "
             "another way in)")
    p_pi.add_argument("--no-probe", action="store_true",
                      help="do not run hardware-detect.sh on first boot")
    p_pi.add_argument("--dry-run", action="store_true",
                      help="stage and report; do not touch any device")

    p_prov = sub.add_parser(
        "provision-pi",
        help="add sambuca first-boot provisioning to an already-written Pi card")
    p_prov.add_argument("--boot", type=Path,
                        help="path to the mounted FAT32 boot partition, if you "
                             "already know it")
    p_prov.add_argument("--hostname", default="sambuca")
    p_prov.add_argument("--engine", type=Path)
    p_prov.add_argument("--wifi-ssid")
    p_prov.add_argument("--no-ssh", action="store_true")
    p_prov.add_argument("--tailscale-key")
    p_prov.add_argument("--no-authorise", action="store_true")
    p_prov.add_argument("--no-probe", action="store_true")

    sub.add_parser("derive-backup-key",
                   help="recover the backup repository password from a seed phrase")

    sub.add_parser("derive-recovery-key",
                   help="recover the DISK recovery key from a seed phrase "
                        "(use when the root passphrase is lost)")

    p_boot = sub.add_parser("boot-guide",
                            help="how to boot the target machine from the USB "
                                 "(the step that defeats most people)")
    p_boot.add_argument("model", nargs="?", default="",
                        help='the target machine, e.g. "Dell XPS 15 9520"')
    p_boot.add_argument("--engine", default="google",
                        choices=["google", "duckduckgo", "bing", "startpage"])
    p_boot.add_argument("--open", action="store_true",
                        help="also open the search in your browser")
    p_boot.add_argument("--list-vendors", action="store_true")

    p_win = sub.add_parser(
        "window",
        help="open the graphical flow (falls back to the console flow)")
    p_win.add_argument("--hostname", default="sambuca")
    p_win.add_argument("--engine", type=Path,
                       help="engine directory to stage onto the card")

    p_vault = sub.add_parser(
        "open-vault",
        help="recover your secrets by answering your three questions")
    p_vault.add_argument("--file", type=Path,
                         help="a vault file, if it is not in the usual place")

    p_watch = sub.add_parser(
        "watch",
        help="watch an appliance build itself, before its own setup page exists")
    p_watch.add_argument("--file", type=Path,
                         help="the sambuca-watch-*.json saved by `write`")
    p_watch.add_argument("--host", default="",
                         help="override the address to look for")
    p_watch.add_argument("--once", action="store_true",
                         help="print the current stage and exit")

    p_hand = sub.add_parser(
        "handover",
        help="check what your appliance is running, trust it, and bookmark it")
    p_hand.add_argument("--domain", default="sambuca.local",
                        help="the appliance's name (default: sambuca.local)")
    p_hand.add_argument("--tailnet", default="",
                        help="its full tailnet name, if you use Tailscale")

    sub.add_parser(
        "verify-sheet",
        help="prove a printed recovery sheet is readable and is this machine's")

    p_cfg = sub.add_parser("example-config", help="print a commented example configuration")
    p_cfg.add_argument("--output", type=Path)

    # No arguments AND nobody typed a command: this was double-clicked.
    # argparse would print a usage line and exit 2, and the console window
    # created for us would close on that exit — which looks exactly like a
    # crash. Offer a menu instead.
    if argv is None and len(sys.argv) == 1:
        from .console import launched_by_double_click

        if launched_by_double_click():
            # A WINDOW IS WHAT SOMEBODY WHO DOUBLE-CLICKS EXPECTED. They opened
            # an application; a text menu in a black rectangle is not what they
            # asked for, and the verdict on that was "that is NOT a GUI like I
            # asked".
            #
            # The console menu stays as the fallback rather than being deleted:
            # it is complete, it works where there is no toolkit or no display,
            # and it is the only route on a headless box.
            from . import gui
            from .console import pause_before_exit

            can_window, _why = gui.available()
            if can_window:
                rc = _cmd_window(argparse.Namespace(hostname="sambuca",
                                                    engine=None))
                pause_before_exit()
                return rc

            rc = _interactive()
            pause_before_exit()
            return rc

    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "write":
            return _cmd_write(args)
        if args.command == "write-pi":
            return _cmd_write_pi(args)
        if args.command == "provision-pi":
            return _cmd_provision_pi(args)
        if args.command == "window":
            return _cmd_window(args)
        if args.command == "open-vault":
            return _cmd_open_vault(args)
        if args.command == "watch":
            return _cmd_watch(args)
        if args.command == "handover":
            return _cmd_handover(args)
        if args.command == "verify-sheet":
            return _cmd_verify_sheet(args)
        if args.command == "derive-backup-key":
            return _cmd_derive()
        if args.command == "derive-recovery-key":
            return _cmd_derive_recovery()
        if args.command == "boot-guide":
            return _cmd_boot_guide(args)
        if args.command == "example-config":
            return _cmd_example(args)
    except DeviceError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    except PermissionError as exc:
        # A NOVICE MUST NEVER SEE A TRACEBACK. The frozen binary printed a raw
        # Python stack for a plain permissions problem — eight lines of
        # shutil.py internals that tell the reader nothing they can act on,
        # ending in "Failed to execute script". Observed while provisioning a
        # real card.
        print(f"\nerror: permission denied: {exc.filename or exc}", file=sys.stderr)
        print("       Writing to a device or its partitions needs "
              "Administrator on Windows, or sudo elsewhere.", file=sys.stderr)
        print("       Close any window showing the card, then re-run from an "
              "elevated terminal.", file=sys.stderr)
        return 1
    except OSError as exc:
        # Everything else the filesystem or a device can throw. The message is
        # the operating system's own, which is more useful than ours would be,
        # but the traceback is not.
        print(f"\nerror: {exc.strerror or exc}", file=sys.stderr)
        if getattr(exc, "filename", None):
            print(f"       while working on: {exc.filename}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\naborted — nothing was written.", file=sys.stderr)
        return 130
    return 2


# ---------------------------------------------------------------------------


def _cmd_list(args) -> int:
    devices = list_removable_devices(allow_large=args.allow_large)
    if not devices:
        print("No writable removable devices found.")
        print("\nInternal disks are never listed. If your stick is missing:")
        print("  - re-insert it and wait a few seconds")
        print("  - if it is over 512 GB, pass --allow-large")
        return 1

    print(f"{len(devices)} device(s):\n")
    for d in devices:
        print(f"  {d.describe()}")
    print("\nInternal and system disks are excluded from this list by design.")
    return 0


def _cmd_write(args) -> int:
    # --- configuration ---
    if args.config:
        config = config_from_dict(json.loads(args.config.read_text(encoding="utf-8")))
    else:
        config = ApplianceConfig()
        print("note: no --config given; using defaults "
              "(hostname=sambuca, domain=sambuca.local, all bundles)")
    config.unattended = not args.interactive

    problems = config.validate()
    if problems:
        print("configuration problems:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    # The recovery PDF and the payload both land in --output-dir, which defaults
    # to the current directory. If that happens to be a git working tree, a
    # careless `git add -A` publishes a seed phrase. This repository ignores
    # those names; a stranger's does not.
    _warn_if_git_worktree(args.output_dir)

    # --- 1. keys ---
    print("\n[1/6] generating key material (offline, on this machine)")
    from .keys import generate_key_material

    keys = generate_key_material()
    print(f"      key fingerprint: {keys.fingerprint}")

    # --- 2. payload ---
    print("[2/6] building the provisioning payload")
    payload = build_provision_payload(config, keys)

    # Same bundle-aware lookup as the Pi path. This used to walk up from
    # __file__, which in a frozen one-file binary points inside a temporary
    # extraction directory — so the shipped .exe could not find the preseed
    # template and the primary command of the application failed. CI never
    # caught it because the smoke tests only exercise --version, boot-guide,
    # boot-guide and example-config, none of which touch the engine.
    engine_root = _find_engine(None)
    if engine_root is None:
        raise DeviceError(
            "could not find the sambuca engine. This build should carry it "
            "bundled; from a source checkout, run from the repository."
        )
    preseed_template = engine_root / "autoinstall" / "preseed.cfg"
    if not preseed_template.is_file():
        raise DeviceError(f"preseed template not found at {preseed_template}")
    preseed = render_preseed(preseed_template, config, keys)

    staging = args.output_dir / f"sambuca-payload-{keys.fingerprint}"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "provision.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (staging / "preseed.cfg").write_text(preseed, encoding="utf-8")

    # KEEP THIS SIDE OF THE PAIRING KEY, or the beacon is unreachable by the
    # only thing meant to reach it. provision.json is shredded off the boot
    # partition during install; the appliance holds its copy root-only. Without
    # a copy here, `watch` would have nothing to authenticate with — a beacon
    # nobody can talk to is the same as no beacon.
    #
    # Written beside the recovery document rather than into the payload, which
    # is a staging tree that gets copied onto a stick and then deleted.
    _write_watch_file(args.output_dir, payload, config)

    # The disk recovery key, for the installer to enrol as a second LUKS
    # keyslot. Written with NO trailing newline and no encoding surprises:
    # cryptsetup would otherwise enrol a passphrase containing the newline,
    # which nobody could ever type. The installer normalises it again as a
    # belt-and-braces check — see enroll-recovery-key.sh.
    if config.unattended:
        (staging / "luks-recovery.key").write_bytes(
            keys.luks_recovery_key.encode("ascii"))
    else:
        # Interactive mode puts no secret on the stick, so there is nothing for
        # the installer to enrol. The owner enrols it from the running system.
        print("      interactive mode: recovery keyslot must be enrolled after "
              "install\n                        (sambuca-recovery enrol)")

    # repo_root WAS NEVER DEFINED. `write` — the whole x86 installer path —
    # raised NameError here at step 2 of 6, on every single run. Found by ruff
    # (F821), not by the test suite, because 110 passing tests never once
    # executed this command end to end.
    #
    # Resolved through _find_engine so it works frozen as well as from source:
    # a one-file binary unpacks to a temporary directory, so walking up from
    # __file__ gives nonsense. The engine's parent is the tree that holds both
    # engine/ and compose/, in the bundle and in the repository alike.
    engine_dir = _find_engine(getattr(args, "engine", None))
    if engine_dir is None:
        print("\nerror: could not find the sambuca engine.", file=sys.stderr)
        print("       This build should carry it; pass --engine <path> to "
              "override.", file=sys.stderr)
        return 1
    repo_root = engine_dir.parent

    for script in ("abort-countdown.sh", "disk-select.sh", "late-command.sh",
                   "enroll-recovery-key.sh"):
        src = engine_dir / "autoinstall" / script
        if src.is_file():
            (staging / script).write_bytes(src.read_bytes())
    _stage_full_tree(repo_root, staging)
    print(f"      staged: {staging}")

    # --- 3. recovery document, BEFORE anything is written ---
    print("[3/6] writing the recovery document")
    pdf_path = args.output_dir / f"liberator-recovery-{config.hostname}-{keys.fingerprint}.pdf"
    from .recovery_pdf import write_recovery_pdf

    write_recovery_pdf(pdf_path, keys, config,
                       tailnet_hint=f"https://{config.hostname}.<your-tailnet>.ts.net/")
    print(f"      {pdf_path}")
    print("      PRINT THIS NOW. It is the only copy of the seed phrase and passphrase.")

    if args.dry_run:
        print("\ndry run: no device was touched.")
        print(json.dumps(keys.redacted(), indent=2))
        return 0

    # --- 4 & 5. the write, done by Raspberry Pi Imager ---
    #
    # Sambuca no longer writes images. Everything above this point IS ours —
    # the keys, the recovery document, the payload — and all of it is already
    # on disk before anything touches a device, which was always the design.
    from . import imager

    print("[4/6] handing the write to Raspberry Pi Imager")
    if imager.find_imager() is None:
        print("\n" + imager.install_hint(), file=sys.stderr)
        print("\nYour recovery document and payload are already written and safe:",
              file=sys.stderr)
        print(f"  {args.output_dir}", file=sys.stderr)
        return 1

    print("      In the Imager window:")
    print("        - choose 'Use custom' and select:")
    print(f"            {args.iso}")
    print("        - then choose your USB stick. ONLY that one is erased.")
    print("      Windows will ask permission — that prompt is expected.\n")

    try:
        imager.launch(wait=True)
    except (imager.ImagerNotFound, RuntimeError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    # --- 6. inject ---
    print("[6/6] injecting the sambuca payload")
    from . import pi

    boot = pi.find_boot_partition()
    if boot is None:
        print("\nerror: the stick was written, but its boot partition did not "
              "appear.", file=sys.stderr)
        print("       Re-insert it and run: sambuca-flasher provision-pi",
              file=sys.stderr)
        return 1
    dest = inject_payload(boot, staging)
    print(f"      {dest}")

    print("\n" + "=" * 70)
    print(f"  READY — key {keys.fingerprint}")
    print("=" * 70)
    if config.unattended:
        print("  This USB now carries the disk passphrase. Treat it as a key until")
        print("  installation finishes; the appliance erases it on first boot.")
    else:
        print("  Interactive mode: no secret is on this USB. The installer will")
        print("  prompt once for the root passphrase from the recovery document.")
    print(f"\n  Recovery document: {pdf_path}")
    print("  Print it, then delete the file. Boot the target machine from this USB.")
    print("  You get 30 seconds at the console to abort before any disk is touched.")

    # NAME THE NEXT COMMAND. The install takes 20-40 minutes on a machine with
    # no screen, and its own setup page is served by Caddy — which does not
    # start until the LAST phase. Without this line the owner has nothing to
    # watch for most of the wait, which is precisely when somebody decides it
    # has hung and pulls the power mid-partition.
    print("\n  Once it is booting, come back here and run:")
    print("\n      sambuca-flasher watch")
    print("\n  That shows each step as it happens - it reads a small file this")
    print("  command just saved next to the recovery document.")

    # VERIFY THE SHEET WHILE IT IS STILL IN THEIR HAND.
    #
    # An untested recovery key is a hypothesis, and it gets tested for the
    # first time on the day the disk will not unlock — months later, under
    # stress, when being wrong costs everything the appliance was built to
    # protect. This is the only moment anybody is well placed to test it.
    #
    # It checks the PAPER, not the PDF. That the file rendered proves nothing
    # about a smudged word or a line the printer ate, and those failures leave
    # a perfect-looking file beside a useless sheet.
    # KeyMaterial is frozen, deliberately — the secrets in it must not be
    # mutable after generation. So the path is passed, not attached.
    # PRINT FIRST. Asking somebody to read words off a sheet that has not
    # been printed is nonsense, and the previous order did exactly that.
    if not _offer_to_print(pdf_path):
        _say()
        _say("  The sheet is not printed, so it cannot be checked yet.")
        _say("  Once you have printed it, run:  sambuca-flasher verify-sheet")
        return 0

    if not _verify_recovery_sheet(keys, pdf_path):
        print()
        print("  The sheet is UNVERIFIED. Everything else is done, but please")
        print("  check it against the document before you rely on it.")

    # OFFERED LAST, AND ONLY AFTER THE SHEET. The paper is the primary path and
    # this is a second one — offering it earlier would invite somebody to take
    # it INSTEAD of printing, swapping a durable artefact for a file that dies
    # with this laptop. That is the precise failure it exists to prevent.
    _offer_vault(keys, args.output_dir)
    return 0


def _cmd_verify_sheet(args) -> int:
    """Prove a printed recovery sheet is readable and belongs to this machine.

    NEEDS NOTHING THAT WAS STORED, which is what makes it possible at all.
    Sambuca keeps no copy of the seed phrase by design. This leans on two
    things that travel with the sheet itself:

      BIP-39 CHECKSUM - a mistyped or misprinted word fails it. That catches a
      smudged glyph, a line the printer ate, a zero read as an O.

      FINGERPRINT - derived from the phrase and printed on the sheet. Matching
      it proves this is THIS machine's sheet, not a previous install's. It is
      explicitly non-secret and exists for exactly this comparison.

    Runnable any time, on any machine, years later. A recovery document you
    cannot test is one you are only guessing about.
    """
    from .keys import _require_bip39, seed_fingerprint

    _require_bip39()
    from mnemonic import Mnemonic

    _say()
    _say("=" * 68)
    _say("  CHECK A RECOVERY SHEET")
    _say("=" * 68)
    _say()
    _say("  Type the 24 words from the sheet, separated by spaces.")
    _say("  Nothing is sent anywhere and nothing is stored.")
    _say()

    try:
        phrase = input("  words: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return 1

    words = phrase.split()
    if len(words) != 24:
        _say()
        _say(f"  That is {len(words)} words, not 24.")
        return 1

    if not Mnemonic("english").check(" ".join(words)):
        _say()
        _say("  THOSE WORDS DO NOT FORM A VALID PHRASE.")
        _say()
        _say("  Seed phrases carry their own checksum, so at least one word is")
        _say("  wrong - misread, mistyped, or badly printed. Look for a smudge,")
        _say("  a 0 read as an O, or a line the printer cut off.")
        _say()
        _say("  DO NOT rely on this sheet until it checks out.")
        return 1

    _say()
    _say("  The phrase is valid.")
    _say()
    _say(f"  Fingerprint from what you typed:  {seed_fingerprint(' '.join(words))}")
    _say()
    _say("  Compare that with the fingerprint printed on the sheet.")
    _say("    MATCHES     readable, and it is that machine's sheet.")
    _say("    DIFFERENT   valid words, but a DIFFERENT machine's sheet.")
    return 0


def _cmd_derive() -> int:
    # Check the dependency BEFORE asking for the secret. Prompting someone to
    # type their 24-word seed phrase and only then announcing that the tool
    # cannot use it is a poor thing to do with the most sensitive string they
    # own — and this is the "I have lost the master password" path, so whoever
    # is running it is already having a bad day.
    from .keys import _require_bip39

    _require_bip39()

    print("Recover the backup repository password from a 24-word seed phrase.")
    print("Nothing is transmitted; this runs entirely on this machine.\n")
    phrase = input("seed phrase (24 words): ").strip()
    try:
        from .keys import derive_backup_password

        password = derive_backup_password(phrase)
    except ValueError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        print("Check the word order and spelling — BIP-39 has a checksum, so a "
              "single wrong word is detected.", file=sys.stderr)
        return 1

    print("\nrestic repository password:\n")
    print(f"  {password}\n")
    print("Use it with:  restic -r <repository> snapshots")
    return 0


def _cmd_derive_recovery() -> int:
    """Recompute the disk recovery key from the seed phrase.

    This is the "I forgot the master password" path. It runs entirely offline,
    on this machine, and needs nothing from the appliance.
    """
    # Check the dependency BEFORE asking for the secret. Prompting someone to
    # type their 24-word seed phrase and only then announcing that the tool
    # cannot use it is a poor thing to do with the most sensitive string they
    # own — and this is the "I have lost the master password" path, so whoever
    # is running it is already having a bad day.
    from .keys import _require_bip39

    _require_bip39()

    print("Recover the DISK RECOVERY KEY from your 24-word seed phrase.")
    print("Use this when the root passphrase is lost and the machine will not unlock.")
    print("Nothing is transmitted; this runs entirely on this machine.\n")
    phrase = input("seed phrase (24 words): ").strip()
    try:
        from .keys import derive_luks_recovery_key

        key = derive_luks_recovery_key(phrase)
    except ValueError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        print("BIP-39 has a checksum, so a single wrong or transposed word is "
              "detected. Check the word order against the numbers on the sheet.",
              file=sys.stderr)
        return 1

    print("\ndisk recovery key:\n")
    print(f"  {key}\n")
    print("At the machine's passphrase prompt, type it EXACTLY as shown, dashes")
    print("included. It is case-sensitive and unlocks the disk on its own.")
    print("\nOnce you are in, set a new root passphrase you will remember:")
    print("  sudo cryptsetup luksChangeKey <device>   # e.g. /dev/nvme0n1p3")
    print("\nIf it is rejected, this machine may predate recovery keyslots, or the")
    print("installer could not enrol one. Check: sudo cryptsetup luksDump <device>")
    return 0


def _warn_if_git_worktree(out_dir: Path) -> None:
    """Refuse to be quiet about writing a seed phrase into a git repository.

    Found by running the compiled binary from inside this repo: the recovery
    PDF and the staged payload go to the current directory by default, and one
    `git add -A` away from a public commit. This repository ignores those
    filenames; somebody else's will not.
    """
    d = out_dir.resolve()
    for parent in [d, *d.parents]:
        if (parent / ".git").exists():
            print()
            print("!" * 70)
            print("  THIS IS A GIT REPOSITORY.")
            print(f"    {parent}")
            print()
            print("  Your recovery document and provisioning payload are about to be")
            print("  written here. They contain your seed phrase, your disk passphrase")
            print("  and your disk recovery key. One `git add -A` publishes them.")
            print()
            print("  Use --output-dir to put them somewhere else, or make sure both")
            print("  are ignored:  liberator-recovery*.pdf  and  sambuca-payload-*/")
            print("!" * 70)
            print()
            return


def _offer_certificate(domain: str) -> None:
    """Teach this computer to trust the appliance, or explain why not.

    WITHOUT THIS EVERY PAGE LOOKS BROKEN. The appliance issues its own
    certificates - that is what lets it serve HTTPS on a home network with no
    public domain. But no browser has heard of that authority, so every service
    opens behind a full-width security warning. Teaching a novice to click
    through those is the worst habit this project could possibly install.

    IT IS STILL A REAL PERMISSION, so it is asked for, never assumed, and the
    fingerprint goes on screen first: a certificate fetched over a network we
    cannot yet verify is a certificate somebody else may have supplied.
    """
    from . import ca

    _say()
    _say("-" * 68)
    _say("  TRUST YOUR APPLIANCE'S CERTIFICATE")
    _say("-" * 68)
    _say()

    try:
        cert = ca.fetch(domain)
    except ca.NotReady:
        # EARLY IS NOT BROKEN. Caddy writes its root on the first TLS
        # handshake, so this means wait - not debug a network that is fine.
        _say("  The appliance is still setting up its certificate.")
        _say("  Give it a few minutes, then run:  sambuca-flasher handover")
        return

    if cert is None:
        _say("  Could not download the certificate from the appliance.")
        _say("  Services will still work; the browser will warn you each time.")
        _say("  You can do this later with:  sambuca-flasher handover")
        return

    if ca.is_installed(cert):
        _say("  Already trusted by this computer. Nothing to do.")
        return

    _say(ca.explain())
    _say()
    _say("  Certificate fingerprint:")
    _say(f"    {cert.fingerprint[:39]}")
    _say(f"    {cert.fingerprint[39:]}")
    _say()

    if not _ask_yes("  Tell this computer to trust it?"):
        _say()
        _say("  Left alone. Your browser will warn you on every page - the")
        _say("  warning is expected, but you will have to click past it.")
        return

    _say()
    _say("  Running:")
    _say(f"    {' '.join(ca.install_command(Path('sambuca-ca.crt')))}")
    _say()

    workdir = Path(tempfile.gettempdir()) / "sambuca-ca"
    ok, detail = ca.install(cert, workdir)
    if ok:
        _say(f"  Done - {detail}.")
        _say()
        _say("  Close and reopen your browser for it to take effect.")
        _say(f"  To undo this later:  {ca.removal_hint()}")
    else:
        # A managed or locked-down machine refusing is NORMAL, not an error
        # worth alarming somebody over.
        _say(f"  Could not install it: {detail}")
        _say("  This is common on work computers, which often forbid it.")
        _say("  Everything still works; the browser will warn you each time.")


def _clear_progress_line(live: bool) -> None:
    """Wipe the in-place progress line, but only where one was drawn.

    Off a terminal nothing was ever overwritten, so emitting 78 spaces just
    adds a line of blanks to whatever is reading the output — which is exactly
    what it did until somebody looked at piped output instead of assuming it
    matched the console.
    """
    if live:
        sys.stdout.write("\r" + " " * 78 + "\r")
        sys.stdout.flush()


def _obtain_iso():
    """Get Debian's installer file, downloading it rather than asking for it.

    THE RULE'S FIRST CLAUSE APPLIES HERE AFTER ALL. This step looked like a
    clause-two case - "we cannot fetch 755 MB for them, so guide them to
    debian.org" - and that reading was wrong. Of course the app can download a
    file. The Pi path already gets its image downloaded by rpi-imager; the x86
    path asked a human to go and find one purely because no wrapped tool
    happened to do it. That is an accident of tooling, not a decision, and it
    was costing a novice a trip to a mirror index.

    The manifest pins the exact release and its digest, so what lands is
    verified before anything is allowed to use it.

    Returns a Path, or None if the owner backed out or it could not be had.
    """
    from . import download, manifest

    spec = manifest.installer_iso()
    if not spec.get("url") or not spec.get("sha256"):
        # NO PIN, NO DOWNLOAD. Fetching 755 MB with nothing to check it
        # against is worse than asking, because it looks trustworthy.
        return _ask_for_iso("Sambuca does not have a verified download for "
                            "your version.")

    size_mb = int(spec.get("size", 0)) / 1_000_000
    _say()
    _say("  This writes an installer USB for a normal PC or laptop.")
    _say()
    _say(f"  It needs {spec.get('name', 'the Debian installer')} -")
    _say(f"  free, official, and about {size_mb:.0f} MB.")
    _say()

    dest = Path.home() / "Downloads" / Path(spec["url"]).name
    if dest.is_file():
        _say(f"  Found one already downloaded:\n    {dest}")
        _say("  Checking it is intact...")
    elif not _ask_yes("  Download it now?"):
        return _ask_for_iso("")

    _say()

    # \r ONLY WORKS ON A TERMINAL. Piped to a file or a log it is just a
    # character, so 3,000 progress updates become 3,000 lines of noise with
    # the useful output buried somewhere inside. Redrawing in place is a
    # terminal affordance, not a universal one.
    live = sys.stdout.isatty()
    state = {"last": -1}

    def show(p):
        if live:
            sys.stdout.write("\r  " + p.human() + "   ")
            sys.stdout.flush()
            return
        # Not a terminal: one line every 10%, so a log stays readable and
        # still shows the download is moving.
        step = int(p.percent // 10)
        if step != state["last"]:
            state["last"] = step
            print(f"  {p.human()}")

    try:
        path = download.fetch(spec["url"], dest, sha256=spec["sha256"],
                              expected_size=int(spec.get("size", 0)),
                              on_progress=show)
    except download.DownloadError as exc:
        _clear_progress_line(live)
        _say(f"  {exc}")
        _say()
        return _ask_for_iso("You can also download it yourself:")
    except KeyboardInterrupt:
        # THE PART FILE SURVIVES ON PURPOSE. Stopping a 755 MB download should
        # not throw away what has already arrived.
        _clear_progress_line(live)
        _say("  Stopped. Choosing this again will carry on where it got to.")
        return None

    _clear_progress_line(live)
    _say(f"  Ready, and verified:\n    {path}")
    return path


def _ask_for_iso(reason: str):
    """The fallback: guide them to fetch it themselves, step by step.

    Reached when there is no verified download, when one fails, or when the
    owner would rather do it their own way. Still guidance, never an
    instruction thrown over a wall.
    """
    _say()
    if reason:
        _say(f"  {reason}")
    _say("  Debian's installer is free and comes straight from debian.org:")
    _say()
    _say("    https://www.debian.org/download")
    _say()
    _say("  Save it somewhere you can find, then come back here.")
    _say()
    try:
        raw = input("  Drag the file here, or paste its path: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    # A dragged file arrives wrapped in quotes; a novice will not strip them.
    iso = raw.strip('"').strip("'").strip()
    if not iso:
        _say("\n  Nothing entered. Nothing done.")
        return None
    if not Path(iso).is_file():
        _say(f"\n  There is no file at:\n    {iso}")
        _say("  Check it finished downloading, then try again.")
        return None
    return Path(iso)


def _write_watch_file(output_dir: Path, payload: dict, config) -> Path:
    """Save what `watch` needs: where the appliance is, and its pairing key.

    NOT A SECRET WORTH CEREMONY, and not nothing either. It authorises reading
    install progress - stage names and timings - and nothing else: the beacon
    has no control surface and publishes only an allowlist of plain-language
    fields. Still 0600 where the platform honours it, because it is a
    credential and habits matter more than this one file does.
    """
    watch = output_dir / f"sambuca-watch-{payload.get('fingerprint', 'unknown')}.json"
    watch.write_text(json.dumps({
        "schema": 1,
        "domain": config.domain,
        "hostname": config.hostname,
        "beacon_key": payload.get("beacon_key", ""),
        "note": ("Run: sambuca-flasher watch   -- shows install progress while "
                 "the appliance is building itself. `handover` deletes this "
                 "for you once the appliance is answering."),
    }, indent=2), encoding="utf-8")
    try:
        os.chmod(watch, 0o600)
    except OSError:
        # Windows and some filesystems do not honour this. Not fatal, and not
        # worth failing a write over.
        pass
    return watch


def _cmd_watch(args) -> int:
    """Watch an appliance build itself, before it can serve its own setup page.

    THE GAP THIS FILLS. Caddy serves /setup, and Caddy starts in the LAST
    provisioning phase. Everything before it - disk, base system, Docker, GPU,
    storage, network - happens with nothing to watch, and that is precisely the
    window where somebody decides it has hung and pulls the power mid-partition.
    """
    from . import beaconclient

    src = _find_watch_file(getattr(args, "file", None))
    if src is None:
        _say()
        _say("  No watch file found.")
        _say()
        _say("  `write` saves one next to the recovery document, named")
        _say("  sambuca-watch-<fingerprint>.json. Point at it directly with:")
        _say("    sambuca-flasher watch --file <path>")
        return 1

    try:
        cfg = json.loads(Path(src).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _say(f"\n  Could not read {src}: {exc}")
        return 1

    host = (getattr(args, "host", None) or cfg.get("domain") or "sambuca.local")
    key = cfg.get("beacon_key", "")
    if not key:
        _say("\n  That watch file has no pairing key in it.")
        return 1

    _say()
    _say("=" * 68)
    _say("  WATCHING YOUR APPLIANCE BUILD ITSELF")
    _say("=" * 68)
    _say()
    _say(f"  Looking for {host} on this network...")
    _say()

    rc = beaconclient.follow(host, key, say=_say,
                             once=bool(getattr(args, "once", False)))
    return rc


def _find_watch_file(explicit):
    """The most recent watch file, or the one asked for.

    Most recent rather than "the only one": somebody who builds a second
    appliance should watch THAT one without having to name a file, and picking
    an older one silently would be worse than asking.
    """
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None

    seen = []
    for d in (Path.cwd(), Path.home() / "Desktop", Path.home()):
        if d.is_dir():
            seen.extend(d.glob("sambuca-watch-*.json"))
    if not seen:
        return None
    return max(seen, key=lambda p: p.stat().st_mtime)


def _offer_vault(keys, output_dir) -> None:
    """Offer a second way back in, for when the paper is gone.

    THE SHEET STAYS PRIMARY. This is offered AFTER it has been printed and
    checked, never instead — an owner who accepts this and skips the printing
    has swapped a durable artefact for one that dies with a laptop.

    Opt-in, because it is a genuine trade and not a free upgrade: the vault is
    a second complete copy of every secret on the machine.
    """
    from . import vault

    _say()
    _say("-" * 68)
    _say("  A SECOND WAY BACK IN  (optional)")
    _say("-" * 68)
    _say()
    _say("  The printed sheet is your way back in if the password is ever")
    _say("  lost. Paper gets lost too.")
    _say()
    _say("  Sambuca can keep an encrypted copy on this computer, locked behind")
    _say("  three questions only you can answer. No account, nothing uploaded,")
    _say("  no company involved - just a file you can copy anywhere.")
    _say()
    _say("  BE CLEAR ABOUT THE TRADE:")
    _say("    This is a SECOND COMPLETE COPY of every secret on the appliance.")
    _say("    Someone who steals this computer AND guesses your three answers")
    _say("    has your disk. It is deliberately slow to try answers against -")
    _say("    seconds each, not thousandths - but the answers still have to be")
    _say("    things a stranger could not look up.")
    _say()
    _say("  Not 'mother's maiden name'. That is a public record.")
    _say()

    if not _ask_yes("  Set one up?"):
        _say()
        _say("  Skipped. The printed sheet remains your way back in - which is")
        _say("  the design, not a consolation.")
        return

    questions, answers = [], []
    _say()
    _say("  Three questions, in your own words. Good ones are specific, will")
    _say("  never change, and are not on anybody's social media.")
    _say()
    _say("  For example: 'what did we call the car that broke down in France?'")
    _say()

    for i in range(1, vault.QUESTION_COUNT + 1):
        try:
            q = input(f"  Question {i}: ").strip()
            a = input(f"  Answer {i}:   ").strip()
        except (EOFError, KeyboardInterrupt):
            _say("\n  Cancelled. Nothing was written.")
            return
        if not q or not a:
            _say("\n  Both are needed. Nothing was written.")
            return
        questions.append(q)
        answers.append(a)

    verdict = vault.check_answers(questions, answers)
    if not verdict.ok:
        _say()
        _say(f"  {verdict.reason}")
        _say("  Nothing was written. Run this again when you have three you")
        _say("  are happy with.")
        return

    _say()
    _say("  Encrypting. This deliberately takes a few seconds - that slowness")
    _say("  is what makes guessing the answers expensive.")

    path = vault.default_path(keys.fingerprint)
    try:
        vault.create(path, {
            "seed_phrase": keys.seed_phrase,
            "root_passphrase": keys.root_passphrase,
            "luks_recovery_key": keys.luks_recovery_key,
            "fingerprint": keys.fingerprint,
        }, questions, answers, fingerprint=keys.fingerprint)
    except vault.VaultError as exc:
        _say()
        _say(f"  Could not write it: {exc}")
        _say("  The printed sheet is unaffected.")
        return

    # PROVE IT OPENS, NOW, WHILE THE ANSWERS ARE STILL IN THEIR HEAD. An
    # untested vault is a hypothesis, and it gets tested for the first time on
    # the day everything else has already failed.
    try:
        vault.open_vault(path, answers)
    except vault.VaultError:
        _say()
        _say("  WARNING: the vault was written but did not open with those")
        _say("  answers. Do not rely on it. The printed sheet is your way in.")
        return

    _say()
    _say(f"  Done, and checked:  {path}")
    _say()
    _say("  COPY THAT FILE SOMEWHERE ELSE. On this computer alone it dies with")
    _say("  this computer, which is the failure it exists to prevent. A USB")
    _say("  stick in a drawer is enough.")
    _say()
    _say("  To use it:  sambuca-flasher open-vault")


def _cmd_window(args) -> int:
    """Open the graphical flow, or explain plainly why it cannot.

    THE FALLBACK IS THE POINT. tkinter is stdlib on paper and absent in
    practice — Debian splits it into python3-tk, and a frozen binary only has
    it if the build bundled Tcl/Tk. Rather than crash, this says what is
    missing and names the console flow that already works.
    """
    from . import gui

    ok, why = gui.available()
    if not ok:
        _say()
        _say("  A window cannot be opened here.")
        _say(f"    {why}")
        _say()
        _say("  On Debian or Ubuntu:  sudo apt install python3-tk")
        _say()
        _say("  Everything works without it. To write a Raspberry Pi card:")
        _say("      sambuca-flasher write-pi")
        return 1

    # LOOK BEFORE DECIDING WHAT TO SHOW. Offering to install something already
    # installed reads as a program that has not bothered to check.
    from . import imager, tailnet

    steps = gui.plan(
        has_tailscale=tailnet.status().installed,
        has_imager=imager.find_imager() is not None,
        # The Pi flow produces no recovery document — no keys, no PDF, no
        # vault, because that appliance has no encrypted root. Showing "print
        # your way back in" here would promise paper that never appears.
        makes_recovery_document=False,
    )

    _say()
    _say("  Opening the Sambuca window.")
    _say("  Close it at any time; nothing is written until you confirm.")

    wizard = gui.Wizard(
        steps,
        actions=gui.build_actions(hostname=getattr(args, "hostname", "sambuca"),
                                  engine=getattr(args, "engine", None)),
    )
    wizard.run()
    return 0


def _cmd_open_vault(args) -> int:
    """Recover the secrets from a vault, by answering its three questions."""
    from . import vault

    path = getattr(args, "file", None)
    if path:
        path = Path(path)
    else:
        d = Path.home() / ".sambuca" / "vault"
        found = sorted(d.glob("recovery-*.json")) if d.is_dir() else []
        if not found:
            _say()
            _say("  No vault found on this computer.")
            _say(f"  Looked in: {d}")
            _say()
            _say("  If you copied one to a USB stick, point at it:")
            _say("    sambuca-flasher open-vault --file <path>")
            return 1
        # Newest, so somebody with two appliances gets the recent one rather
        # than a silent guess at the older.
        path = max(found, key=lambda p: p.stat().st_mtime)

    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _say(f"\n  Cannot read a vault at {path}")
        return 1

    questions = doc.get("questions", [])
    _say()
    _say("=" * 68)
    _say("  OPEN THE RECOVERY VAULT")
    _say("=" * 68)
    _say()
    _say(f"  {path}")
    _say()
    _say("  Answer the three questions you chose. Capitals, accents and")
    _say("  punctuation do not matter.")
    _say()

    answers = []
    for i, q in enumerate(questions, 1):
        try:
            answers.append(input(f"  {i}. {q}\n     ").strip())
        except (EOFError, KeyboardInterrupt):
            return 1

    _say()
    _say("  Checking. This takes a few seconds on purpose.")

    try:
        payload = vault.open_vault(path, answers)
    except vault.WrongAnswers as exc:
        _say()
        _say(f"  {exc}")
        return 1
    except vault.VaultError as exc:
        _say(f"\n  {exc}")
        return 1

    _say()
    _say("-" * 68)
    _say("  YOUR RECOVERY DETAILS")
    _say("-" * 68)
    _say()
    _say(f"  Machine fingerprint:  {payload.get('fingerprint', '?')}")
    _say()
    _say("  Seed phrase (24 words):")
    words = str(payload.get("seed_phrase", "")).split()
    for row in range(0, len(words), 4):
        _say("      " + "  ".join(f"{row + j + 1:2d}. {w:<10}"
                                   for j, w in enumerate(words[row:row + 4])))
    _say()
    _say(f"  Root passphrase:      {payload.get('root_passphrase', '')}")
    _say(f"  Disk recovery key:    {payload.get('luks_recovery_key', '')}")
    _say()
    _say("  ON SCREEN AND NOWHERE ELSE. Nothing here was written to a file.")
    _say("  Write it down now, then clear this window.")
    return 0


def _cmd_handover(args) -> int:
    """Hand the finished appliance over: what works, trusted, bookmarked.

    THE FLOW ENDED BY STOPPING. Provisioning said "put the card in the Pi and
    power it on" and that was the last word - leaving somebody holding a
    machine with nine services on it and no idea of a single address. The
    project had built a private cloud and handed over silence.

    Three things, in the only order that works: find out what is actually
    answering, trust the certificate so the addresses open cleanly, then write
    the addresses somewhere their browser can keep.
    """
    from . import handover

    domain = (getattr(args, "domain", None) or "sambuca.local").strip()
    tailnet_name = (getattr(args, "tailnet", None) or "").strip()

    _say()
    _say("=" * 68)
    _say("  YOUR APPLIANCE")
    _say("=" * 68)
    _say()
    _say(f"  Looking for {domain} ...")
    _say()

    links = handover.check_all(handover.appliance_links(
        domain, tailnet_name=tailnet_name))

    working = [x for x in links if x.reachable]
    for link in links:
        mark = "up  " if link.reachable else "--  "
        _say(f"    {mark}{link.name:<28} {link.url}")

    _say()
    if not working:
        # DO NOT DRESS THIS UP. Nothing answered, and pretending otherwise
        # sends somebody off to bookmark addresses that go nowhere.
        _say("  Nothing answered yet.")
        _say()
        _say("  If you have only just powered it on, first boot takes 10-20")
        _say("  minutes - it is downloading and starting everything.")
        _say()
        _say("  If it has been longer than that, put the card in a reader and")
        _say("  read  sambuca-firstboot.log  - it records what happened.")
        return 1

    _say(f"  {len(working)} of {len(links)} services are answering.")

    # CERTIFICATE BEFORE BOOKMARKS. Bookmarks that all open behind a security
    # warning teach exactly the wrong lesson on first use.
    _offer_certificate(domain)

    dest = Path.home() / "Desktop"
    if not dest.is_dir():
        dest = Path.home()
    path = handover.write_bookmarks(working, dest / "sambuca-bookmarks.html")

    _say()
    _say("-" * 68)
    _say("  YOUR ADDRESSES")
    _say("-" * 68)
    _say()
    _say(f"  Saved to:  {path}")
    _say()
    _say("  Import it into your browser so the addresses are always there:")
    _say("    Chrome / Edge   Bookmarks -> Import bookmarks and settings")
    _say("    Firefox         Bookmarks -> Manage -> Import -> From HTML")
    _say()
    _say("  Start here:")
    _say(f"    https://{domain}")

    # CLEAN UP THE CREDENTIAL WE LEFT LYING AROUND. The watch file holds a
    # pairing key for a beacon that provisioning has already killed, so it is
    # now a secret with nothing to open — the worst kind to leave in a Downloads
    # folder, because nobody will ever have a reason to think about it again.
    #
    # The file itself said "delete this once it is up", which is THE RULE's
    # named failure written in miniature: telling somebody to do a thing you
    # could simply do. It gets done here, and only after the appliance has
    # answered — deleting it while the install is still running would throw
    # away the only way to watch the rest of it.
    _retire_watch_file()
    return 0


def _retire_watch_file() -> None:
    """Remove the watch file once the appliance is up and answering.

    Silent about the ordinary cases. A missing file is normal (somebody may
    have moved it, or provisioned from another machine), and a failed delete is
    not worth alarming anyone over at the end of a successful install.
    """
    path = _find_watch_file(None)
    if path is None:
        return
    try:
        Path(path).unlink()
    except OSError:
        _say()
        _say(f"  One thing left: delete {path}")
        _say("  It holds a key for the install-progress service, which has now")
        _say("  shut down. It is no use to you and no use to anyone else.")
        return
    _say()
    _say("  Tidied up: the install-progress key is no longer needed and has")
    _say("  been deleted.")


def _cmd_boot_guide(args) -> int:
    """Print the boot guide, and optionally open the search."""
    from . import bootguide

    if args.list_vendors:
        print("Known vendors (free text also works — 'my old thinkpad'):\n")
        for v in bootguide.VENDORS:
            print(f"  {v.key:<18} {v.name:<26} boot: {v.boot_menu}")
        return 0

    model = args.model
    if not model:
        print("Which machine are you installing sambuca ONTO?")
        print("The brand is enough; the exact model gets you a better search.")
        print('Examples:  Dell XPS 15 9520   ·   HP EliteDesk 800   ·   my old thinkpad\n')
        model = input("machine: ").strip()

    print(bootguide.guide(model, engine=args.engine))

    if args.open:
        _, url = bootguide.search_url(model, args.engine)
        # Opened in the user's OWN browser, after they have seen the URL above.
        # This is the flasher's only outbound action, and it carries no
        # identifier of any kind — an installer that quietly phones home while
        # building a sovereign appliance has lost the argument before it starts.
        import webbrowser

        print("Opening that search in your browser…\n")
        webbrowser.open(url)
    else:
        print("Add --open to launch that search in your browser.\n")
    return 0


def _cmd_example(args) -> int:
    example = {
        "hostname": "sambuca",
        "timezone": "America/Vancouver",
        "locale": "en_CA.UTF-8",
        "domain": "sambuca.local",
        "admin_user": "sambuca",
        "admin_ssh_key": "ssh-ed25519 AAAA... you@yourlaptop",
        "acme_email": "",
        "tailscale_authkey": "tskey-auth-... (single-use, tagged, expires)",
        "tailscale_tags": "tag:sambuca",
        "bundles": ["ai", "cloud", "office", "comms"],
        "target_disk": "/dev/disk/by-id/nvme-Samsung_SSD_990_PRO_1TB_XXXXXXX",
        "target_disk_hint": "",
        "data_disks": [],
        "parity_disks": [],
        "tier_override": "",
    }
    text = json.dumps(example, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)
    print("\n# target_disk must be a /dev/disk/by-id/ path — kernel names reorder", file=sys.stderr)
    print("# between boots, and an unattended installer that guesses erases the", file=sys.stderr)
    print("# wrong disk. Leave it empty only if the machine has exactly one disk.", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------


def _stage_full_tree(repo_root: Path, staging: Path) -> None:
    """Copy the whole engine and compose trees onto the x86 payload.

    RENAMED BECAUSE IT WAS SHADOWED. Two functions were called _stage_engine —
    this one, and the Pi-card variant defined 300 lines later that copies only
    the subset a FAT partition can hold. The later definition simply replaced
    this one at import time, so every call resolved to the Pi version with
    entirely different semantics and a different return type.

    Python does not warn about a redefinition; the name just quietly changes
    meaning. Distinct jobs need distinct names.
    """
    import shutil

    for tree in ("engine", "compose"):
        src = repo_root / tree
        if not src.is_dir():
            continue
        dst = staging / tree
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(
            src, dst,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", "*.log"),
        )


def _say(text: str = "") -> None:
    """print(), but safe on a Windows console.

    The default codepage renders typographic characters as replacement glyphs.
    Sanitising at the RENDER layer means a contributor typing an em-dash cannot
    reintroduce the problem — which is exactly what happened to the guided
    output the first time it was written.
    """
    from .console import ascii_safe

    print(ascii_safe(text) if text else "")


def _settle_reachability(args) -> str:
    """STEP 1: how will you reach the machine you are about to build?

    Runs BEFORE the engine is staged, before the Imager is touched, before
    anything is written. Reachability is a prerequisite, not a footnote — an
    appliance nobody can find is not an appliance, and discovering that after
    the card is finished is the worst possible moment.

    Returns a tailnet pre-auth key, or "" if the owner chose to go without.
    """
    from . import tailnet

    supplied = (getattr(args, "tailscale_key", None) or "").strip()
    if supplied:
        if not tailnet.valid_key(supplied):
            print("\nThat does not look like a Tailscale pre-auth key.", file=sys.stderr)
            print("They begin with 'tskey-'. Create one at:", file=sys.stderr)
            print(f"  {tailnet.KEY_PAGE}", file=sys.stderr)
            raise SystemExit(1)
        return supplied

    _say()
    _say("=" * 68)
    _say("  STEP 1 of 2 — how you will reach the machine you are building")
    _say("=" * 68)
    _say()
    _say("  Once this card is written, the appliance runs headless: no screen,")
    _say("  no keyboard. You reach it over the network — so that has to be")
    _say("  settled BEFORE it is built, not after.")
    _say()

    st = tailnet.status()

    if not st.installed:
        _say("  Tailscale is not installed on this computer. It is what lets")
        _say("  you reach the appliance by name from anywhere, without opening")
        _say("  any ports on your router.")
        _say()
        _say("  It is free for personal use, and you sign in with an account")
        _say("  you already have — Google, Microsoft or GitHub. No new")
        _say("  password, no card.")
        _say()
        if _ask_yes("  Install it now?"):
            if tailnet.install_here():
                _say("  installed.")
                st = tailnet.status()
                # Freshly installed means almost certainly not signed in, and
                # quite likely no account either. Offer both in one step
                # rather than falling through to a second question.
                if not st.running and _ask_yes("  Sign in now? (opens your browser)"):
                    _say("  Waiting for you to finish in the browser...")
                    if tailnet.sign_in():
                        st = tailnet.status()
                        _say(f"  Signed in. This computer is on: {st.tailnet}")
            else:
                _say("  Could not install it automatically. Get it from:")
                _say("    https://tailscale.com/download")
        _say()

    # FORK: installed but signed out — and, indistinguishable from here, "has
    # never had an account at all". Both are answered by the same action, so
    # both are offered the same way. The no-account case is named out loud,
    # because "you need an account" reads like a wall to someone who assumes
    # it means a form and a credit card.
    if st.installed and not st.running:
        _say("  Tailscale is installed here but not signed in.")
        _say()
        _say("  If you have never used it: it is free for personal use, and")
        _say("  you sign in with an account you already have — Google,")
        _say("  Microsoft or GitHub. There is no new password to invent.")
        _say()
        if _ask_yes("  Sign in now? (this opens your browser)"):
            _say("  Waiting for you to finish in the browser...")
            if tailnet.sign_in():
                st = tailnet.status()
                _say(f"  Signed in. This computer is on: {st.tailnet}")
            else:
                _say("  Sign-in did not finish. You can do it later — the")
                _say("  appliance still works on this network in the meantime.")
        _say()

    if st.ready:
        _say(f"  This computer is on the tailnet:  {st.tailnet}")
        _say("  The appliance will join the same one, and you will reach it")
        _say("  by name — no address to remember, and it survives your")
        _say("  router handing out a different one.")
        _say()
        _say("  It needs a one-time key to join. Creating one takes about")
        _say("  fifteen seconds and I can open the page for you.")
        _say()
        if _ask_yes("  Open the key page in your browser?"):
            if tailnet.open_key_page():
                _say("  opened. Choose 'Generate auth key', leave the defaults,")
                _say("  and copy the key it shows you.")
            else:
                _say(f"  Could not open a browser. The page is:\n    {tailnet.KEY_PAGE}")
        else:
            _say(f"  The page is:  {tailnet.KEY_PAGE}")
        _say()

        try:
            key = input("  Paste the key (or press Enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            key = ""

        if key and tailnet.valid_key(key):
            _say("  Key accepted. The appliance will join your tailnet on first")
            _say("  boot, and the key is shredded off the card afterwards.")
            return key
        if key:
            _say("  That does not look like a pre-auth key — they begin with")
            _say("  'tskey-'. Continuing without one.")

    _say()
    _say("  Continuing WITHOUT a tailnet key.")
    _say("  The appliance will still work, and will still be reachable on")
    _say("  this network by its address — but you will have to find that")
    _say("  address, and it can change.")
    _say()
    return ""


def _ask_yes(question: str, *, default: bool = True) -> bool:
    """A yes/no that never blocks a non-interactive run."""
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        from .console import ascii_safe

        answer = input(ascii_safe(question + suffix)).strip().lower()
    except (EOFError, KeyboardInterrupt, OSError):
        return default
    if not answer:
        return default
    return answer.startswith("y")



def _offer_to_print(pdf_path) -> bool:
    """Get the recovery sheet onto paper before anyone is asked to read it.

    A PDF in a folder is not a recovery document. It lives on the machine whose
    disk it exists to unlock, under a name nobody will recognise in eight
    months. If the APPLIANCE fails, the file is fine; if this computer fails,
    the only copy of the seed phrase goes with it.

    The job is never sent silently. The file holds a disk passphrase, and an
    unprompted print could land on a shared office device or pass through a
    print-to-PDF driver that writes a second copy nobody is tracking. The owner
    chooses the printer in their own dialogue.

    Returns whether paper exists, according to the only authority on that
    question: the person standing next to the printer.
    """
    from . import printing

    _say()
    _say("=" * 68)
    _say("  PRINT THE RECOVERY SHEET")
    _say("=" * 68)
    _say()
    _say(f"  {pdf_path}")
    _say()
    _say("  This is the only way back in if the password is lost. Right now it")
    _say("  is a file on THIS computer - the one it cannot help you with if")
    _say("  this computer is what breaks.")
    _say()

    if not printing.can_print():
        _say("  No way to print was found. Copy the file to a USB stick or")
        _say("  another machine, print it there, then delete both copies.")
        printing.open_folder(pdf_path)
        return False

    if not _ask_yes("  Open the print dialogue now?"):
        _say()
        _say("  Skipped. The file is still there, and still the only copy.")
        printing.open_folder(pdf_path)
        return False

    if not printing.open_print_dialog(pdf_path):
        _say("  Could not open a print dialogue. Opening the folder instead.")
        printing.open_folder(pdf_path)
        return False

    _say("  Opened. Choose your printer and print it.")
    _say()
    # NOTHING reliably reports whether a page came out - not on any platform.
    # A silently failed job (no paper, wrong printer, driver asleep) is the
    # likeliest outcome nobody checks, so ask the human.
    if _ask_yes("  Did a page actually come out of the printer?"):
        return True

    _say()
    _say("  Then it has not printed. Try again, or copy the file elsewhere.")
    printing.open_folder(pdf_path)
    return False


def _verify_recovery_sheet(keys, pdf_path=None, *, sample: int = 3) -> bool:
    """Ask the owner to read a few words off the printed sheet.

    AN UNTESTED RECOVERY KEY IS A HYPOTHESIS. This is the only moment anyone
    is well placed to test it: the sheet is in their hand, nothing has gone
    wrong, and there is no pressure on the answer. The alternative is finding
    out on the day the disk will not unlock.

    It checks the SHEET, not the PDF. That the file rendered proves nothing
    about whether a human can read a smudged word, or whether the printer ate
    a line — and those failures produce a perfect-looking file and a useless
    piece of paper.

    A few positions, not all twenty-four: a check people skip is worth nothing,
    and spot-checking still catches the failures that actually occur.
    """
    import random

    words = keys.seed_phrase.split()
    if len(words) != 24:
        return False

    _say()
    _say("=" * 68)
    _say("  CHECK THE SHEET BEFORE YOU PUT IT AWAY")
    _say("=" * 68)
    _say()
    _say("  That paper is the only way back into this machine if the password")
    _say("  is lost. It has never been tested, and the usual moment to")
    _say("  discover a problem with it is the worst one possible.")
    _say()
    _say("  Read a few words off it. Not to prove the file is correct — to")
    _say("  prove the PAPER can be read and typed.")
    _say()

    positions = sorted(random.sample(range(1, 25), sample))
    wrong = []
    for pos in positions:
        try:
            got = input(f"  Word {pos:>2} on the sheet: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            _say()
            _say("  Skipped. The sheet is UNVERIFIED — please check it yourself.")
            return False
        if got != words[pos - 1].lower():
            wrong.append(pos)

    _say()
    if wrong:
        _say(f"  MISMATCH at word{'s' if len(wrong) > 1 else ''} "
             f"{', '.join(str(w) for w in wrong)}.")
        _say()
        _say("  Either the sheet did not print correctly, or it is a sheet")
        _say("  from a different machine. Do not rely on it.")
        _say()
        _say("  Print it again from:")
        _say(f"    {pdf_path or 'the recovery document'}")
        return False

    _say("  Verified. Those words match, so the sheet is readable and it is")
    _say("  the right sheet for this machine.")
    _say()
    _say("  Put it somewhere you would keep a passport. Not in the same room")
    _say("  as the appliance — a fire that takes one should not take both.")
    return True


def _find_engine(explicit: Path | None) -> Path | None:
    """Locate the engine directory, working BOTH from source and frozen.

    A PyInstaller one-file binary unpacks itself into a temporary directory and
    points __file__ inside it, so walking up from __file__ resolves to nonsense.
    On the first run of an actual built .exe it produced
    `C:\\Users\\...\\AppData\\engine`, and the app could not provision a card at
    all — the source tree had been tested, the shipped artefact had not.

    Someone who downloads a single .exe has no repository to walk up into, so
    the engine is BUNDLED into the binary and found via sys._MEIPASS.

    Order: what the operator asked for, then the bundle, then the repository.
    """
    if explicit:
        return explicit if explicit.is_dir() else None

    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        candidate = Path(bundled) / "engine"
        if candidate.is_dir():
            return candidate

    candidate = Path(__file__).resolve().parents[4] / "engine"
    return candidate if candidate.is_dir() else None


def _stage_engine(engine_dir: Path, into: Path) -> int:
    """Copy the parts of the engine a Pi actually needs onto the card.

    Not the whole tree. The FAT partition is ~512 MiB and shared with the
    firmware, and the x86 provisioning phases are meaningless here — the Pi
    image is already an installed system. What matters on first boot is the
    profiler and the data it reads.
    """
    import shutil

    into.mkdir(parents=True, exist_ok=True)
    count = 0

    probe = engine_dir / "hardware-detect.sh"
    if not probe.is_file():
        raise DeviceError(f"no hardware-detect.sh under {engine_dir}")
    shutil.copy2(probe, into / probe.name)
    count += 1

    for sub in ("lib", "profiles"):
        src = engine_dir / sub
        if src.is_dir():
            dst = into / sub
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst)
            count += sum(1 for f in dst.rglob("*") if f.is_file())

    # Written with LF endings or the Pi's /bin/sh will not run them. This is the
    # same CRLF trap that made the x86 abort countdown unrunnable.
    for f in into.rglob("*"):
        if f.is_file() and f.suffix in (".sh", ".env"):
            data = f.read_bytes()
            if b"\r\n" in data:
                f.write_bytes(data.replace(b"\r\n", b"\n"))

    return count


def _cmd_write_pi(args) -> int:
    """Write a Raspberry Pi card — by launching Raspberry Pi Imager.

    Sambuca no longer writes images. It publishes an OS list and starts the
    tool that does, which handles device selection, download, checksum
    verification, writing, readback and elevation on all three platforms.

    THE RULE (CLAUDE.md, axis 1): do it for them, or guide them through every
    step. Here that means installing the Imager if it is missing, filling in its
    Customisation screen in advance, naming the drives that are attached so the
    destructive choice is an informed one, warning before the permission prompt,
    provisioning the card automatically afterwards, and saying what happens next.

    STILL DONE BY HAND: choosing the device and the OS entry inside the Imager
    (REDO G2).
    """
    import tempfile

    from . import imager

    # STEP 1, BEFORE ANYTHING ELSE. Reachability is a prerequisite: an
    # appliance nobody can find is not an appliance, and finding that out
    # after the card is written is the worst possible moment. This used to be
    # a tip printed halfway through, after the owner had already committed.
    # ASSIGNED BACK ONTO args, WHICH IT WAS NOT. The returned key was bound to
    # a local and then dropped: the owner was walked through installing
    # Tailscale, signing in, and minting a pre-auth key, and the key was
    # discarded before anything could write it to the card. Provisioning reads
    # args.tailscale_key, so the whole reachability step - the thing that runs
    # FIRST because an appliance nobody can find is not an appliance - ended in
    # an appliance that never joined the tailnet.
    #
    # Found by ruff (F841 "assigned but never used"), not by any test. The
    # symptom would have been an owner who did everything right and still could
    # not reach their machine, with nothing on screen suggesting why.
    if not args.dry_run:
        args.tailscale_key = _settle_reachability(args)

    # Stage the engine first, and do it even on a dry run. This is what proves
    # a BUILT BINARY carries its engine — CI runs exactly this path, because a
    # binary that starts but cannot find its payload is the failure that shipped
    # in v0.1.0-preview1 while every other check passed.
    engine_dir = _find_engine(args.engine)
    if engine_dir is None:
        print("\nerror: could not find the sambuca engine.", file=sys.stderr)
        _say("       This build should carry it; pass --engine <path> to override.",
              file=sys.stderr)
        return 1

    staging = Path(tempfile.mkdtemp(prefix="sambuca-pi-"))
    staged = _stage_engine(engine_dir, staging / "sambuca")
    _say(f"\nstaged {staged} engine file(s) from {engine_dir}")

    if args.dry_run:
        _say("\ndry run: no device was touched, and Raspberry Pi Imager was "
              "not started.")
        _say(f"  staging: {staging}")
        return 0

    # ---- G1: get the tool. Do not tell them to go and get it. -----------
    if imager.find_imager() is None:
        _say("\nRaspberry Pi Imager does the writing, and it is not installed.")
        _say("Installing it now. This takes a minute.\n")
        if not imager.try_install():
            print(imager.install_hint(), file=sys.stderr)
            return 1
        _say("  installed.\n")

    # ---- G3: answer the Customisation screen before they see it ---------
    from . import customisation as cust

    if cust.supported():
        tz, kb = cust.detect_locale()
        ok, changed = cust.apply(cust.Customisation(
            hostname=args.hostname,
            timezone=tz,
            keyboard=kb,
            ssh_enabled=not args.no_ssh,
            ssh_username="sambuca",
        ))
        if ok:
            _say("Filled in the Imager's Customisation screen for you:")
            for line in changed:
                _say(f"    {line}")
            _say()
            _say("  Your password and wi-fi key are NOT set here. The Imager")
            _say("  asks you for those itself, and Sambuca never stores them.")
            _say()

    # ---- the prerequisite nothing else checked --------------------------
    # A headless appliance with no network is a brick you cannot ask why. The
    # Pi Zero 2 W has NO ETHERNET, so wi-fi is not optional for it — and the
    # wi-fi key is a secret Sambuca must not hold, so this is guidance, not
    # automation. Nothing said it was required until now, and EVERYTHING
    # downhill depends on it: the tailnet join, ssh, and the address written
    # back onto the card.
    from . import customisation as _cust

    if _cust.supported() and not _cust.wifi_configured():
        _say("  IMPORTANT — set wi-fi in the Imager's Customisation screen.")
        _say()
        _say("  This machine will have no screen and no keyboard. If it cannot")
        _say("  reach a network, there is no way to reach IT, and no way for")
        _say("  it to tell you what went wrong.")
        _say()
        _say("  A Raspberry Pi Zero 2 W has no ethernet socket at all, so")
        _say("  wi-fi is the only way in.")
        _say()
        _say("  Sambuca does not set this for you on purpose: the wi-fi")
        _say("  password is yours, and it would have to be stored to be")
        _say("  filled in. The Imager asks you directly.")
        _say()

    # ---- G5: the one choice that stays yours ----------------------------
    # Storage is the destructive step, so it gets the MOST words, not the
    # fewest. Naming what is attached, by size and label, is the difference
    # between picking a card and picking a backup drive.
    _say("When the Imager asks for STORAGE, this is what is plugged in now:")
    _say()
    try:
        drives = list_removable_devices(allow_large=True)
    except DeviceError:
        drives = []

    if drives:
        for d in drives:
            _say(f"    {d.size_human:>10}   {d.label}")
        _say()
        if len(drives) == 1:
            _say("  That is the only removable drive attached, so it is almost")
            _say("  certainly your card. Check the size looks right anyway.")
        else:
            _say(f"  {len(drives)} removable drives are attached. Match the SIZE to")
            _say("  your card. EVERYTHING on the one you choose is erased.")
    else:
        _say("    (nothing detected — insert your card before continuing)")
    _say()

    # ---- G6: warn before the prompt, not after --------------------------
    # G2: PRESELECTING TURNS OUT NOT TO BE POSSIBLE, so this is the guide
    # half — and it can be exact rather than general. Checked rather than
    # assumed: nothing upstream ever sets `default: true` (it is false wherever
    # it appears, and absent on the Pi 5, which is the entry shown first), and
    # setting it true in our own list did not preselect on v2.0.10.
    #
    # Since the list carries ONE tested device and ONE image, "click the only
    # entry" is a complete instruction rather than a vague one.
    _say("The Imager will show you four screens. The first two have exactly")
    _say("one entry each, because this list offers only tested hardware:")
    _say()
    _say("    DEVICE          click the only entry")
    _say("    OS              click the only entry (the Sambuca image)")
    _say("    STORAGE         YOUR choice — see the drives listed above")
    _say("    CUSTOMISATION   already filled in, except wi-fi")
    _say()
    _say("Then press WRITE.")
    _say()
    _say("Your computer will ask permission to run the Imager first. That")
    _say("prompt is expected: writing to a card needs it.")
    _say("Sambuca waits here until you close the Imager window.")
    _say()

    try:
        imager.launch(wait=True)
    except (imager.ImagerNotFound, RuntimeError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    # ---- G7: provisioning follows automatically -------------------------
    _say()
    _say("Imager finished. Adding Sambuca's own configuration to the card...")
    rc = _cmd_provision_pi(args)
    if rc != 0:
        return rc

    # ---- G8: close the loop ---------------------------------------------
    _say()
    _say("=" * 68)
    _say("  DONE — what happens next")
    _say("=" * 68)
    _say("  1. Put the card in the Raspberry Pi and switch it on.")
    _say("  2. It configures itself and reboots once. Give it a few minutes.")
    _say("  3. It writes what it found BACK ONTO THE CARD.")
    _say()
    _say("  To read that: switch the Pi off, put the card back in this")
    _say("  computer, and open  sambuca-firstboot.log  on the drive that")
    _say("  appears.")
    _say()
    _say("  The Pi needs no screen, keyboard or network for you to find out")
    _say("  whether it worked.")
    _say("=" * 68)
    return 0


def _cmd_provision_pi(args) -> int:
    """Provision an already-written card, without rewriting 2.77 GiB.

    This exists because the write and the provisioning genuinely can fail
    independently: the image lands and verifies, and then Windows declines to
    surface the boot partition. Re-imaging the whole card to retry a handful of
    small file writes would be absurd, and an error message that names a
    command which does not exist is worse than no suggestion at all.
    """
    import tempfile

    from . import pi

    boot = args.boot
    if boot is None:
        # NO DEVICE NEEDED. rpi-imager chose and wrote the card; Sambuca never
        # held a handle to it. The card is found by what it CONTAINS.
        print("\nlooking for the card...")
        boot = pi.find_boot_partition()

        # THE EJECT SEAM. rpi-imager dismounts the card when it finishes — its
        # own last screen says "you can now remove the SD card". So the very
        # next step goes looking for a partition the operating system has let
        # go of, and no amount of rescanning brings it back. The card has to
        # be physically re-seated, and saying so is the whole job here.
        if boot is None:
            print()
            print("  The Imager finished and released the card, which is normal.")
            print("  Sambuca still needs to add its own configuration to it.")
            print()
            print("  Take the card out and put it straight back in.")
            print()
            for attempt in range(3):
                try:
                    input("  Press Enter once you have done that ")
                except (EOFError, KeyboardInterrupt):
                    break
                boot = pi.find_boot_partition()
                if boot is not None:
                    break
                if attempt < 2:
                    print("  Still cannot see it. Give it a few seconds and try again.")

    if boot is None or not Path(boot).is_dir():
        print("\nerror: could not find the FAT32 boot partition.", file=sys.stderr)
        print("       Re-insert the card, then pass it explicitly:", file=sys.stderr)
        print("         sambuca-flasher provision-pi --boot E:\\", file=sys.stderr)
        return 1

    boot = Path(boot)
    print(f"  {boot}")

    engine_dir = _find_engine(args.engine)
    if engine_dir is None:
        print("\nerror: could not find the sambuca engine.", file=sys.stderr)
        print("       This build should carry it; pass --engine <path> to override.",
              file=sys.stderr)
        return 1

    staging = Path(tempfile.mkdtemp(prefix="sambuca-pi-"))
    staged = _stage_engine(engine_dir, staging / "sambuca")
    print(f"staged {staged} engine file(s)")

    # ACCESS IS NOT AN AFTERTHOUGHT. An appliance with ssh enabled and nobody
    # authorised is unreachable from the machine that built it — which is what
    # the first real card produced, and it is an installer defect, not a chore
    # for its owner.
    authorized_key = ""
    if not args.no_ssh and not getattr(args, "no_authorise", False):
        from . import access

        key = access.operator_key()
        if key is None:
            print("\nwarning: no ssh key on this computer and one could not be",
                  file=sys.stderr)
            print("         generated. The appliance will be unreachable from here.",
                  file=sys.stderr)
        elif access.looks_like_private_key(key.public_key):
            # Cannot happen through operator_key(), which only returns lines
            # beginning with a public key type. Checked anyway: a private key
            # on a card that travels between machines is disclosed the moment
            # the card is lost.
            print("\nrefusing to write what looks like a PRIVATE key",
                  file=sys.stderr)
        else:
            authorized_key = key.public_key
            if key.generated:
                print(f"\nCreated an ssh key so this computer can reach the "
                      f"appliance:\n  {key.path}")
            else:
                print(f"\nUsing your existing ssh key so this computer can "
                      f"reach the appliance:\n  {key.path}")

    actions = pi.provision_boot_partition(
        boot,
        payload_dir=staging / "sambuca",
        hostname=args.hostname,
        enable_ssh=not args.no_ssh,
        run_probe=not args.no_probe,
        wifi_ssid=args.wifi_ssid,
        authorized_key=authorized_key,
        tailscale_key=getattr(args, "tailscale_key", "") or "",
    )
    for a in actions:
        print(f"  {a}")

    # WHERE THE FLOW USED TO END. It said "power it on" and stopped, leaving
    # somebody holding a machine with nine services on it and no address for
    # any of them. Naming the next command is the whole difference between a
    # handover and an abandonment.
    print("\nDone. Put the card in the Pi and power it on.")
    print()
    print("  FIRST BOOT TAKES 10-20 MINUTES. It downloads and starts")
    print("  everything. Leave it alone while it does.")
    print()
    print("  Then come back here and run:")
    print()
    print("      sambuca-flasher handover")
    print()
    print("  That checks what is running, sets this computer up to trust it,")
    print("  and saves every address as bookmarks you can import.")
    print()
    print("  If something went wrong, the first boot writes its results BACK")
    print("  ONTO THE CARD: put it in a reader and read sambuca-firstboot.log")
    return 0


_MENU = """
====================================================================
  SAMBUCA
  Turn a spare computer into your own private cloud and AI server.
====================================================================

  You opened this by double-clicking, so here is a menu. Everything
  below is also available as a command if you prefer typing, and as a
  window (option 8) if this computer can open one.

  Nothing here touches a disk until it asks you first, in capitals.

    1   Which machine can run this?      (opens the guide)
    2   Which USB sticks can I write to?
    3   How do I boot a machine from USB?
    4   Write an installer USB            (for a PC or laptop)
    5   Write a Raspberry Pi card
    6   Recover a lost password from my seed phrase
    7   My machine is running - set it up on this computer
    8   Open the window instead

    q   Quit

"""


def _interactive() -> int:
    """A menu, for someone who double-clicked the file.

    THE BUG THIS EXISTS FOR: with no arguments argparse printed a usage line
    and exited 2. Double-clicked from a file manager the console window is
    created for the process and destroyed the moment it exits, so the window
    appeared and vanished — reported, entirely fairly, as "it crashed as soon
    as I clicked it".

    That is the first thing a non-technical owner does with a downloaded
    program, and the README tells them to download it and run it. A usage
    string aimed at people who already know the commands is no answer.
    """
    from .console import ascii_safe

    while True:
        print(ascii_safe(_MENU))
        try:
            choice = input("  choose: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return 0

        if choice in ("q", "quit", "exit", ""):
            return 0

        try:
            if choice == "1":
                print()
                print("  Which machines can run Sambuca is a table in the README,")
                print("  not something to describe here:")
                print("    https://github.com/laboratoiresonore/Sambuca"
                      "#which-machine-should-i-use")
                print()
                print("  This app runs on YOUR computer and installs onto a")
                print("  DIFFERENT one, so it cannot measure the machine that")
                print("  matters. That machine profiles its own hardware on first")
                print("  boot and tells you what it found.")
                return 0
            if choice == "2":
                return main(["list"])
            if choice == "3":
                model = input('\n  Which machine? (e.g. "Dell XPS 15"): ').strip()
                return main(["boot-guide", model])
            if choice == "4":
                iso = _obtain_iso()
                if iso is None:
                    return 1
                return main(["write", "--iso", str(iso)])
            if choice == "5":
                # NO ARGUMENTS NEEDED ANY MORE. rpi-imager downloads the image
                # from the manifest, so the old instruction to pass --image was
                # both an abdication AND stale: that flag is now "rarely
                # needed" and demanding it turned people away at the door.
                return main(["write-pi"])
            if choice == "7":
                return main(["handover"])
            if choice == "8":
                return main(["window"])
            if choice == "6":
                return main(["derive-recovery-key"])
        except (EOFError, KeyboardInterrupt):
            return 0

        print("\n  Sorry, I did not understand that.\n")
