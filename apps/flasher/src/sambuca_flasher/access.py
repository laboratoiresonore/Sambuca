"""
sambuca :: make sure the machine that writes the card can reach what it built.

THE BUG THIS FIXES. Provisioning enabled SSH on the appliance and installed no
key. The result was a machine that came up with the door open and nobody's name
on the list — unreachable from the very computer that created it. The first
real card produced exactly that, and the response was to ask its owner to go
and fix it by hand, which is the failure twice over: an installer that builds
something it cannot talk to, and then asks a human to finish the job.

THE RULE: do it for the user. Access is not a thing to ask about afterwards. If
Sambuca writes a card, the machine that wrote it can reach the result.

WHAT GOES ON THE CARD: a PUBLIC key. Never a private one, not once, not to make
anything easier. The private half stays where it was generated, and the card is
carried between machines by hand.

PREFER AN EXISTING KEY. Most people already have one, and minting a second
identity for no reason is how key sprawl starts. A dedicated key is generated
ONLY when there is nothing to use, and then it is clearly named so it can be
recognised and revoked later.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Preference order. ed25519 first: shorter, faster, and what a modern sshd
# expects. rsa last because a 1024-bit one may be refused outright.
_CANDIDATES = ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub")

_GENERATED_NAME = "sambuca_appliance"


@dataclass(frozen=True)
class OperatorKey:
    public_key: str          # the single line that goes into authorized_keys
    path: Path               # where the public half lives
    generated: bool          # did we mint it just now?
    comment: str             # how a human recognises it later


def _ssh_dir() -> Path:
    return Path.home() / ".ssh"


def _read_pub(p: Path) -> str | None:
    try:
        line = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    # A public key is one line beginning with its type. Anything else here is
    # a private key or junk, and must not be shipped.
    if not line.startswith(("ssh-ed25519 ", "ecdsa-sha2-", "ssh-rsa ")):
        return None
    return line


def find_existing() -> OperatorKey | None:
    """An SSH key this operator already has."""
    d = _ssh_dir()
    if not d.is_dir():
        return None

    # A previously generated sambuca key wins: it is the one already trusted by
    # any appliance this machine has built.
    ordered = (f"{_GENERATED_NAME}.pub", *_CANDIDATES)
    for name in ordered:
        p = d / name
        if not p.is_file():
            continue
        pub = _read_pub(p)
        if pub:
            parts = pub.split(None, 2)
            return OperatorKey(
                public_key=pub,
                path=p,
                generated=False,
                comment=parts[2] if len(parts) > 2 else p.stem,
            )
    return None


def generate() -> OperatorKey | None:
    """Mint a dedicated key, named so it can be recognised and revoked.

    Only reached when the operator has no usable key at all. Passphrase-free
    on purpose: this exists so an unattended install can reach the machine it
    just built, and a passphrase would put a prompt in the middle of that.
    It is a key to one appliance, not an identity.
    """
    keygen = shutil.which("ssh-keygen")
    if not keygen:
        return None

    d = _ssh_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            d.chmod(0o700)
    except OSError:
        return None

    priv = d / _GENERATED_NAME
    pub = d / f"{_GENERATED_NAME}.pub"
    if pub.is_file():
        found = _read_pub(pub)
        if found:
            return OperatorKey(found, pub, False, f"sambuca@{socket.gethostname()}")

    comment = f"sambuca@{socket.gethostname()}"
    try:
        subprocess.run(
            [keygen, "-t", "ed25519", "-N", "", "-C", comment, "-f", str(priv)],
            capture_output=True, timeout=60, check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return None

    line = _read_pub(pub)
    if not line:
        return None
    return OperatorKey(line, pub, True, comment)


def operator_key(*, allow_generate: bool = True) -> OperatorKey | None:
    """The key the appliance should trust, existing or freshly minted."""
    found = find_existing()
    if found:
        return found
    return generate() if allow_generate else None


def looks_like_private_key(text: str) -> bool:
    """Guard for the one mistake that must never happen.

    Called before anything is written to a card. A private key on a FAT32
    partition that travels between machines is not recoverable from — it is
    disclosed the moment the card is lost.
    """
    markers = (
        "PRIVATE KEY",
        "-----BEGIN OPENSSH",
        "-----BEGIN RSA",
        "-----BEGIN EC",
        "-----BEGIN DSA",
    )
    return any(m in text for m in markers)
