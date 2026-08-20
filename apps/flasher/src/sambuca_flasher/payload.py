"""
sambuca :: provisioning payload construction.

Builds the `provision.json` that first-boot.sh consumes, and patches the
preseed with the generated credentials.

══════════════════════════════════════════════════════════════════════════════
WHAT THE PAYLOAD CONTAINS, AND WHAT IT DELIBERATELY DOES NOT

  IN:   hostname, timezone, locale, domain, bundle selection, disk hints,
        the admin SSH public key, a SINGLE-USE Tailscale auth key, and the
        SHA-256 of the seed phrase.

  NOT IN:  the seed phrase, the backup password, or the root passphrase in any
        recoverable form. Those exist on the printed document and nowhere else.

  THE HONEST EXCEPTION: the LUKS passphrase must be present in the preseed, or
  the installation cannot be unattended. That is not a solvable problem — it is
  a choice between "unattended" and "no disk secret ever touches the USB", and
  you cannot have both.

  So the tradeoff is made explicit and mitigated rather than hidden:

    * UNATTENDED mode (default): the preseed carries the passphrase. THE USB
      STICK IS A KEY from the moment it is written until installation finishes.
      first-boot.sh shreds the payload from the boot partition on first boot,
      and the recovery document says this in plain language on page one.

    * INTERACTIVE mode (--interactive): the passphrase is NOT written. The
      installer stops once and asks. Nothing sensitive is ever on the stick,
      at the cost of one person standing at the machine for ten seconds.

  Anything that claims to give you both is encrypting the payload with a key
  that is also on the stick.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .keys import KeyMaterial

PAYLOAD_SCHEMA = 1

DEFAULT_BUNDLES = ("ai", "cloud", "office", "comms")
VALID_BUNDLES = frozenset({"ai", "cloud", "office", "comms"})
VALID_DISK_HINTS = frozenset({"nvme", "largest", "smallest-ssd"})


@dataclass
class ApplianceConfig:
    """Operator-supplied configuration for one appliance."""

    hostname: str = "sambuca"
    timezone: str = "UTC"
    locale: str = "en_US.UTF-8"
    domain: str = "sambuca.local"
    admin_user: str = "sambuca"
    admin_ssh_key: str = ""
    acme_email: str = ""
    tailscale_authkey: str = ""
    tailscale_tags: str = "tag:sambuca"
    bundles: tuple[str, ...] = DEFAULT_BUNDLES
    target_disk: str = ""
    target_disk_hint: str = ""
    data_disks: list[str] = field(default_factory=list)
    parity_disks: list[str] = field(default_factory=list)
    tier_override: str = ""
    unattended: bool = True

    def validate(self) -> list[str]:
        """Return a list of problems. Empty list means the config is usable."""
        problems: list[str] = []

        if not self.hostname or not self.hostname.replace("-", "").isalnum():
            problems.append(
                f"hostname {self.hostname!r} must be alphanumeric with hyphens only"
            )
        if len(self.hostname) > 63:
            problems.append("hostname exceeds the 63-character DNS label limit")

        unknown = set(self.bundles) - VALID_BUNDLES
        if unknown:
            problems.append(f"unknown bundle(s): {', '.join(sorted(unknown))}")

        if self.target_disk_hint and self.target_disk_hint not in VALID_DISK_HINTS:
            problems.append(
                f"target_disk_hint {self.target_disk_hint!r} is not one of "
                f"{', '.join(sorted(VALID_DISK_HINTS))}"
            )

        if self.target_disk and not self.target_disk.startswith("/dev/"):
            problems.append("target_disk must be an absolute device path")

        # A by-id path survives disk reordering; /dev/sda does not. This is a
        # warning-as-error because the consequence is erasing the wrong disk.
        if self.target_disk and "/by-id/" not in self.target_disk:
            problems.append(
                f"target_disk {self.target_disk!r} is not a /dev/disk/by-id/ path. "
                "Kernel device names reorder between boots — use a by-id path."
            )

        if self.admin_ssh_key and not self.admin_ssh_key.startswith(
            ("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")
        ):
            problems.append("admin_ssh_key does not look like an OpenSSH public key")

        if self.tailscale_authkey and not self.tailscale_authkey.startswith("tskey-"):
            problems.append("tailscale_authkey does not start with 'tskey-'")

        overlap = set(self.data_disks) & set(self.parity_disks)
        if overlap:
            problems.append(
                f"disk(s) listed as BOTH data and parity: {', '.join(sorted(overlap))}"
            )

        return problems


def build_provision_payload(
    config: ApplianceConfig,
    keys: KeyMaterial,
) -> dict:
    """Assemble provision.json.

    The seed phrase appears only as a SHA-256 hash, so the appliance can verify
    a seed the owner later types during recovery without the stick ever having
    carried the seed itself.
    """
    problems = config.validate()
    if problems:
        raise ValueError(
            "configuration is not usable:\n  - " + "\n  - ".join(problems)
        )

    payload = {
        "schema": PAYLOAD_SCHEMA,
        "generated_by": "sambuca-flasher",
        "fingerprint": keys.fingerprint,
        "hostname": config.hostname,
        "timezone": config.timezone,
        "locale": config.locale,
        "domain": config.domain,
        "admin_user": config.admin_user,
        "admin_ssh_key": config.admin_ssh_key,
        "acme_email": config.acme_email,
        "tailscale_authkey": config.tailscale_authkey,
        "tailscale_tags": config.tailscale_tags,
        "bundles": list(config.bundles),
        "tier_override": config.tier_override,
        "data_disks": config.data_disks,
        "parity_disks": config.parity_disks,
        # Verification material, not the secret itself.
        "backup_seed_hash": keys.backup_seed_hash,
        # first-boot.sh shreds this file from the unencrypted boot partition.
        "shred_after_install": True,
    }

    if config.target_disk:
        payload["target_disk"] = config.target_disk
    if config.target_disk_hint:
        payload["target_disk_hint"] = config.target_disk_hint

    _assert_no_secrets(payload, keys)
    return payload


def _assert_no_secrets(payload: dict, keys: KeyMaterial) -> None:
    """Fail loudly if a secret leaked into the payload.

    A guard rather than a comment, because "we're careful" is not a control.
    This runs on every build, including the ones nobody reviews.
    """
    blob = json.dumps(payload)
    leaks = []
    if keys.seed_phrase in blob:
        leaks.append("seed phrase")
    if keys.root_passphrase in blob:
        leaks.append("root passphrase")
    if keys.backup_password in blob:
        leaks.append("backup password")
    for word in keys.seed_phrase.split()[:4]:
        if f'"{word} ' in blob:
            leaks.append("seed phrase fragment")
            break
    if leaks:
        raise RuntimeError(
            "REFUSING TO WRITE: the payload contains "
            + ", ".join(sorted(set(leaks)))
            + ". This is a bug in payload construction, not a configuration error."
        )


def render_preseed(
    template: Path,
    config: ApplianceConfig,
    keys: KeyMaterial,
) -> str:
    """Patch the preseed template with the generated credentials.

    In interactive mode the passphrase placeholders are REMOVED rather than
    filled, which makes debian-installer stop and prompt.
    """
    text = template.read_text(encoding="utf-8")

    # SHA-512 crypt for the console account. Explicit random salt: the platform
    # default varies and a short salt weakens the hash.
    salt = "$6$" + "".join(
        secrets.choice("./abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        for _ in range(16)
    )
    user_hash = _sha512_crypt(keys.root_passphrase, salt)

    text = text.replace("$6$PLACEHOLDER_REPLACED_BY_FLASHER", user_hash)
    text = text.replace("d-i netcfg/get_hostname string sambuca",
                        f"d-i netcfg/get_hostname string {config.hostname}")
    text = text.replace("d-i netcfg/hostname string sambuca",
                        f"d-i netcfg/hostname string {config.hostname}")
    text = text.replace("d-i time/zone string UTC",
                        f"d-i time/zone string {config.timezone}")
    text = text.replace("d-i debian-installer/locale string en_US.UTF-8",
                        f"d-i debian-installer/locale string {config.locale}")

    if config.unattended:
        text = text.replace("PLACEHOLDER_REPLACED_BY_FLASHER", keys.root_passphrase)
    else:
        # Strip the crypto passphrase lines entirely; d-i then asks.
        text = "\n".join(
            line for line in text.splitlines()
            if "partman-crypto/passphrase" not in line
        )
        text += (
            "\n\n# INTERACTIVE MODE: no disk passphrase is present on this medium.\n"
            "# The installer will stop once and prompt for it.\n"
        )

    if "PLACEHOLDER_REPLACED_BY_FLASHER" in text and config.unattended:
        raise RuntimeError(
            "preseed still contains an unfilled placeholder — refusing to write "
            "a medium that would produce a machine with a known-literal passphrase"
        )

    return text


def _sha512_crypt(password: str, salt: str) -> str:
    """SHA-512 crypt, portably.

    `crypt` is Unix-only and was removed in Python 3.13, and the flasher's
    primary targets are Windows and macOS. passlib is therefore the portable
    path, with stdlib `crypt` as the fallback for older Unix installs. Failing
    here must be loud: a preseed with an unusable password hash produces a
    machine nobody can log into.
    """
    try:
        from passlib.hash import sha512_crypt  # type: ignore

        return sha512_crypt.using(salt=salt[3:], rounds=5000).hash(password)
    except ImportError:
        pass

    try:
        import crypt as _crypt  # type: ignore  # noqa: PLC0415  (Unix-only, ≤3.12)

        result = _crypt.crypt(password, salt)
        if result and result.startswith("$6$"):
            return result
    except (ImportError, OSError):
        pass

    raise RuntimeError(
        "cannot produce a SHA-512 crypt hash on this platform.\n"
        "Install passlib:  pip install passlib\n"
        "(the stdlib 'crypt' module is Unix-only and was removed in Python 3.13)"
    )


def config_from_dict(data: dict) -> ApplianceConfig:
    """Build a config from a JSON/dict, ignoring unknown keys with a warning."""
    known = {f for f in asdict(ApplianceConfig()).keys()}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown configuration key(s): {', '.join(sorted(unknown))}")
    if "bundles" in data:
        data = {**data, "bundles": tuple(data["bundles"])}
    return ApplianceConfig(**data)
