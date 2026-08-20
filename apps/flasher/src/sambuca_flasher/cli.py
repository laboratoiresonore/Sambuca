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
import sys
from pathlib import Path

from . import __version__
from .devices import DeviceError, RemovableDevice, list_removable_devices
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
    p_write.add_argument("--iso", type=Path, required=True, help="Debian 12 netinst ISO")
    p_write.add_argument("--device", help="target device path (from `list`)")
    p_write.add_argument("--config", type=Path, help="JSON appliance configuration")
    p_write.add_argument("--output-dir", type=Path, default=Path.cwd(),
                         help="where the recovery PDF is written (default: cwd)")
    p_write.add_argument(
        "--interactive", action="store_true",
        help="keep the disk passphrase OFF the USB; the installer prompts once")
    p_write.add_argument("--no-verify", action="store_true",
                         help="skip the readback verification pass (not recommended)")
    p_write.add_argument("--dry-run", action="store_true",
                         help="generate keys, payload and PDF; do not touch any device")

    p_pi = sub.add_parser(
        "write-pi",
        help="write a Raspberry Pi OS card with sambuca first-boot provisioning")
    p_pi.add_argument("--image", type=Path, required=True,
                      help="Raspberry Pi OS image (.img or .img.xz)")
    p_pi.add_argument("--device", help="target device path (from `list`)")
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
    p_pi.add_argument("--no-verify", action="store_true",
                      help="skip the readback pass (not recommended)")
    p_pi.add_argument("--dry-run", action="store_true",
                      help="stage and report; do not touch any device")

    p_prov = sub.add_parser(
        "provision-pi",
        help="add sambuca first-boot provisioning to an already-written Pi card")
    p_prov.add_argument("--device", help="target device path (from `list`)")
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

    p_cfg = sub.add_parser("example-config", help="print a commented example configuration")
    p_cfg.add_argument("--output", type=Path)

    # No arguments AND nobody typed a command: this was double-clicked.
    # argparse would print a usage line and exit 2, and the console window
    # created for us would close on that exit — which looks exactly like a
    # crash. Offer a menu instead.
    if argv is None and len(sys.argv) == 1:
        from .console import launched_by_double_click

        if launched_by_double_click():
            from .console import pause_before_exit

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

    for script in ("abort-countdown.sh", "disk-select.sh", "late-command.sh",
                   "enroll-recovery-key.sh"):
        src = repo_root / "engine" / "autoinstall" / script
        if src.is_file():
            (staging / script).write_bytes(src.read_bytes())
    _stage_engine(repo_root, staging)
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


def _stage_engine(repo_root: Path, staging: Path) -> None:
    """Copy the engine and compose trees onto the payload."""
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


def _resolve_device(requested: str | None) -> RemovableDevice | None:
    devices = list_removable_devices()
    if requested:
        for d in devices:
            if d.path == requested:
                return d
        print(f"error: {requested} is not in the removable-device list.", file=sys.stderr)
        print("Run `sambuca-flasher list`. Internal disks are excluded by design.",
              file=sys.stderr)
        return None

    if not devices:
        print("error: no removable devices found.", file=sys.stderr)
        return None
    if len(devices) == 1:
        print(f"      only one candidate: {devices[0].describe()}")
        return devices[0]

    print("\nmultiple devices found:\n")
    for i, d in enumerate(devices, 1):
        print(f"  {i}. {d.describe()}")
    choice = input("\nselect (number, or blank to abort): ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(devices)):
        return None
    return devices[int(choice) - 1]


def _confirm_destruction(device: RemovableDevice) -> bool:
    print("\n" + "!" * 70)
    print(f"  ALL DATA ON {device.path} WILL BE DESTROYED")
    print(f"  {device.label}  ({device.size_human})")
    print("!" * 70)
    # A typed word, not y/N. A default-yes prompt on a destructive operation is
    # how somebody's photo archive ends up as a Debian installer.
    answer = input("\nType ERASE to continue: ").strip()
    return answer == "ERASE"


def _progress(done: int, total: int) -> None:
    pct = done * 100 // max(total, 1)
    bar = "#" * (pct // 2) + "-" * (50 - pct // 2)
    print(f"\r      [{bar}] {pct:3d}%  {done / 1024**2:.0f}/{total / 1024**2:.0f} MB",
          end="", flush=True)


if __name__ == "__main__":
    sys.exit(main())


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

    # Stage the engine first, and do it even on a dry run. This is what proves
    # a BUILT BINARY carries its engine — CI runs exactly this path, because a
    # binary that starts but cannot find its payload is the failure that shipped
    # in v0.1.0-preview1 while every other check passed.
    engine_dir = _find_engine(args.engine)
    if engine_dir is None:
        print("\nerror: could not find the sambuca engine.", file=sys.stderr)
        print("       This build should carry it; pass --engine <path> to override.",
              file=sys.stderr)
        return 1

    staging = Path(tempfile.mkdtemp(prefix="sambuca-pi-"))
    staged = _stage_engine(engine_dir, staging / "sambuca")
    print(f"\nstaged {staged} engine file(s) from {engine_dir}")

    if args.dry_run:
        print("\ndry run: no device was touched, and Raspberry Pi Imager was "
              "not started.")
        print(f"  staging: {staging}")
        return 0

    # ---- G1: get the tool. Do not tell them to go and get it. -----------
    if imager.find_imager() is None:
        print("\nRaspberry Pi Imager does the writing, and it is not installed.")
        print("Installing it now. This takes a minute.\n")
        if not imager.try_install():
            print(imager.install_hint(), file=sys.stderr)
            return 1
        print("  installed.\n")

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
            print("Filled in the Imager's Customisation screen for you:")
            for line in changed:
                print(f"    {line}")
            print()
            print("  Your password and wi-fi key are NOT set here. The Imager")
            print("  asks you for those itself, and Sambuca never stores them.")
            print()

    # ---- G5: the one choice that stays yours ----------------------------
    # Storage is the destructive step, so it gets the MOST words, not the
    # fewest. Naming what is attached, by size and label, is the difference
    # between picking a card and picking a backup drive.
    print("When the Imager asks for STORAGE, this is what is plugged in now:")
    print()
    try:
        drives = list_removable_devices(allow_large=True)
    except DeviceError:
        drives = []

    if drives:
        for d in drives:
            print(f"    {d.size_human:>10}   {d.label}")
        print()
        if len(drives) == 1:
            print("  That is the only removable drive attached, so it is almost")
            print("  certainly your card. Check the size looks right anyway.")
        else:
            print(f"  {len(drives)} removable drives are attached. Match the SIZE to")
            print("  your card. EVERYTHING on the one you choose is erased.")
    else:
        print("    (nothing detected — insert your card before continuing)")
    print()

    # ---- G6: warn before the prompt, not after --------------------------
    # G: do it for them, or guide them. A tailnet pre-auth key cannot be
    # minted without the owner's own Tailscale account, so this is the guide
    # half — with the exact link, not a vague suggestion.
    if not getattr(args, "tailscale_key", None):
        print("Tip: the appliance will be reachable on this network by its")
        print("address. To reach it by NAME from anywhere — and to survive the")
        print("address changing — create a pre-auth key and pass it in:")
        print("    https://login.tailscale.com/admin/settings/keys")
        print("    sambuca-flasher write-pi --tailscale-key tskey-auth-...")
        print("  The key is used once on first boot and then shredded off the card.")
        print()

    print("Next, your computer will ask permission to run the Imager.")
    print("That prompt is expected: writing to a card needs it.")
    print("Sambuca waits here until you close the Imager window.")
    print()

    try:
        imager.launch(wait=True)
    except (imager.ImagerNotFound, RuntimeError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    # ---- G7: provisioning follows automatically -------------------------
    print()
    print("Imager finished. Adding Sambuca's own configuration to the card...")
    rc = _cmd_provision_pi(args)
    if rc != 0:
        return rc

    # ---- G8: close the loop ---------------------------------------------
    print()
    print("=" * 68)
    print("  DONE — what happens next")
    print("=" * 68)
    print("  1. Put the card in the Raspberry Pi and switch it on.")
    print("  2. It configures itself and reboots once. Give it a few minutes.")
    print("  3. It writes what it found BACK ONTO THE CARD.")
    print()
    print("  To read that: switch the Pi off, put the card back in this")
    print("  computer, and open  sambuca-firstboot.log  on the drive that")
    print("  appears.")
    print()
    print("  The Pi needs no screen, keyboard or network for you to find out")
    print("  whether it worked.")
    print("=" * 68)
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
        device = _resolve_device(args.device)
        if device is None:
            return 1
        print(f"\nlocating the boot partition on {device.path}")
        boot = pi.find_boot_partition(device)

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

    print("\nDone. Put the card in the Pi and power it on.")
    print("The first boot writes its results BACK ONTO THE CARD:")
    print("  put the card in a reader and read  sambuca-firstboot.log")
    return 0


_MENU = """
====================================================================
  SAMBUCA
  Turn a spare computer into your own private cloud and AI server.
====================================================================

  You opened this by double-clicking, so here is a menu. Everything
  below is also available as a command if you prefer typing.

  Nothing here touches a disk until it asks you first, in capitals.

    1   Which machine can run this?      (opens the guide)
    2   Which USB sticks can I write to?
    3   How do I boot a machine from USB?
    4   Write an installer USB
    5   Write a Raspberry Pi card
    6   Recover a lost password from my seed phrase

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
                print("\n  Writing an installer USB needs a Debian ISO and an "
                      "elevated terminal.")
                print("  Run:  sambuca-flasher write --iso <path-to.iso>")
                print("  See:  https://github.com/laboratoiresonore/Sambuca")
                return 0
            if choice == "5":
                print("\n  Writing a Raspberry Pi card needs an image and an "
                      "elevated terminal.")
                print("  Run:  sambuca-flasher write-pi --image <path-to.img.xz>")
                return 0
            if choice == "6":
                return main(["derive-recovery-key"])
        except (EOFError, KeyboardInterrupt):
            return 0

        print("\n  Sorry, I did not understand that.\n")
