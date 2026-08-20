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
from .keys import derive_backup_password, derive_luks_recovery_key, generate_key_material
from .payload import ApplianceConfig, build_provision_payload, config_from_dict, render_preseed
from .recovery_pdf import write_recovery_pdf
from .writer import inject_payload, write_image


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

    sub.add_parser("derive-backup-key",
                   help="recover the backup repository password from a seed phrase")

    sub.add_parser("derive-recovery-key",
                   help="recover the DISK recovery key from a seed phrase "
                        "(use when the root passphrase is lost)")

    p_est = sub.add_parser("estimate",
                           help="what will this machine be able to do? "
                                "(check before you buy anything)")
    p_est.add_argument("machine", nargs="*", default=[],
                       help='e.g. "Dell OptiPlex 7060, 16GB, no graphics card"')

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

    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "write":
            return _cmd_write(args)
        if args.command == "derive-backup-key":
            return _cmd_derive()
        if args.command == "derive-recovery-key":
            return _cmd_derive_recovery()
        if args.command == "boot-guide":
            return _cmd_boot_guide(args)
        if args.command == "estimate":
            return _cmd_estimate(args)
        if args.command == "example-config":
            return _cmd_example(args)
    except DeviceError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
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
    keys = generate_key_material()
    print(f"      key fingerprint: {keys.fingerprint}")

    # --- 2. payload ---
    print("[2/6] building the provisioning payload")
    payload = build_provision_payload(config, keys)

    repo_root = Path(__file__).resolve().parents[4]
    preseed_template = repo_root / "engine" / "autoinstall" / "preseed.cfg"
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
    write_recovery_pdf(pdf_path, keys, config,
                       tailnet_hint=f"https://{config.hostname}.<your-tailnet>.ts.net/")
    print(f"      {pdf_path}")
    print("      PRINT THIS NOW. It is the only copy of the seed phrase and passphrase.")

    if args.dry_run:
        print("\ndry run: no device was touched.")
        print(json.dumps(keys.redacted(), indent=2))
        return 0

    # --- 4. target confirmation ---
    print("[4/6] selecting the target device")
    device = _resolve_device(args.device)
    if device is None:
        return 1
    if not _confirm_destruction(device):
        print("aborted — nothing was written.")
        return 1

    # --- 5. write ---
    print(f"[5/6] writing {args.iso.name} to {device.path}")
    write_image(args.iso, device, progress=_progress, verify=not args.no_verify)
    print("\n      write verified" if not args.no_verify else "\n      write complete (unverified)")

    # --- 6. inject ---
    print("[6/6] injecting the sambuca payload")
    dest = inject_payload(device, staging)
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
    print("Recover the backup repository password from a 24-word seed phrase.")
    print("Nothing is transmitted; this runs entirely on this machine.\n")
    phrase = input("seed phrase (24 words): ").strip()
    try:
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
    print("Recover the DISK RECOVERY KEY from your 24-word seed phrase.")
    print("Use this when the root passphrase is lost and the machine will not unlock.")
    print("Nothing is transmitted; this runs entirely on this machine.\n")
    phrase = input("seed phrase (24 words): ").strip()
    try:
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


def _cmd_estimate(args) -> int:
    """Tell someone what a machine can do before they commit it or buy it."""
    from . import estimate as est

    machine = " ".join(args.machine).strip()
    if not machine:
        print("Which machine are you thinking of using?")
        print("The brand and the rough specs are enough.")
        print()
        print("Examples:")
        print("  Dell OptiPlex 7060, 16GB RAM, no graphics card")
        print("  gaming PC with an RTX 3060 12GB")
        print("  Raspberry Pi 5 16GB")
        print("  old laptop")
        print()
        machine = input("machine: ").strip()

    print(est.report(machine))
    return 0


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
