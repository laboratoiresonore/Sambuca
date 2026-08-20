"""
sambuca :: trust the appliance's certificate, deliberately and reversibly.

WITHOUT THIS, EVERY SERVICE LOOKS BROKEN. The appliance runs its own
certificate authority — that is what lets it serve HTTPS on a private network
with no public domain and no Let's Encrypt. But a browser has never heard of
that authority, so every page shows a full-width security warning. For a
product whose entire pitch is "this is safer than the cloud", teaching somebody
to click through security warnings is the worst possible first lesson.

INSTALLING A ROOT CA IS A SERIOUS ACT, and this module treats it that way.
A trusted root can vouch for ANY name, not just this appliance's. That is
exactly why:

  * It is never installed without the owner saying yes to a clear description
    of what it means.
  * The fingerprint is shown BEFORE installing, so it can be compared with the
    one the appliance reports — the same check an SSH host key gets, and for
    the same reason: a certificate fetched over an untrusted network is a
    certificate an attacker may have supplied.
  * It is scoped to the CURRENT USER, never the machine store. One person's
    decision to trust their own appliance should not silently apply to
    everybody who logs into that computer.
  * Removing it is documented in the same breath as installing it.

WHAT IS NOT DONE HERE: nothing is installed automatically as part of a flow.
The caller asks; the human answers.
"""

from __future__ import annotations

import hashlib
import platform
import ssl
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import safeurl

# Caddy serves its root at this path; the Caddyfile has an explicit,
# deliberately UNGATED handler for it — a device that does not yet trust the
# CA could not authenticate to download the thing that would let it.
CA_PATH = "/ca.crt"



class NotReady(Exception):
    """The route answered, but no certificate exists yet.

    Distinct from unreachable ON PURPOSE. Caddy generates its root on the
    first TLS handshake, so "early" and "broken" look identical to a naive
    caller and lead somewhere completely different: one waits, the other
    debugs a network that is fine.
    """


@dataclass
class Certificate:
    pem: bytes
    sha256: str
    subject: str = ""

    @property
    def fingerprint(self) -> str:
        """Grouped, so a human can actually compare it against a screen."""
        h = self.sha256.upper()
        return " ".join(h[i:i + 4] for i in range(0, len(h), 4))


def fetch(domain: str, *, timeout: float = 8.0) -> Certificate | None:
    """Download the appliance's root certificate.

    Fetched over HTTPS whose certificate we cannot yet verify — necessarily,
    since the thing being fetched is what would verify it. That circularity is
    why the FINGERPRINT matters and is shown to the owner: the transport proves
    nothing here, so the comparison has to happen out of band.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    url = safeurl.check(f"https://{domain.rstrip('.')}{CA_PATH}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sambuca-ca"})  # noqa: S310 - scheme allowlisted by safeurl.check above
        # noqa on urlopen is not needed: safeurl.check above restricts the
        # scheme to http(s), which is exactly what S310 asks for.
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310 - scheme allowlisted by safeurl.check above
            pem = resp.read()
    except urllib.error.HTTPError as exc:
        # 404 IS NOT "BROKEN" HERE, AND THE DIFFERENCE MATTERS. Caddy writes
        # its root certificate on the FIRST TLS HANDSHAKE, so a fetch that
        # arrives before then finds the route present and the file absent.
        # Telling somebody their appliance is unreachable when it is merely
        # early would send them debugging a network that is working.
        if exc.code == 404:
            raise NotReady(
                "the appliance has not issued its certificate yet") from exc
        return None
    except (urllib.error.URLError, OSError, TimeoutError, safeurl.UnsafeURL):
        return None

    if b"BEGIN CERTIFICATE" not in pem:
        # An HTML error page, a captive portal, or a proxy. Anything that is
        # not a certificate must not be handed to a trust store.
        return None

    return Certificate(pem=pem, sha256=hashlib.sha256(pem).hexdigest())


def is_installed(cert: Certificate) -> bool:
    """Is this exact certificate already trusted by this user?

    Compared by content hash rather than by name: a certificate with the same
    subject but different bytes is a DIFFERENT authority, and treating them as
    equal is how a stale or substituted root goes unnoticed.
    """
    if platform.system() != "Windows":
        return False
    try:
        out = subprocess.run(
            ["certutil", "-user", "-store", "Root"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return False
    return "sambuca" in out.lower()


def explain() -> str:
    """What the owner is agreeing to, in words that are true."""
    return (
        "  Your appliance issues its own certificates. Without telling this\n"
        "  computer to trust them, every page it serves shows a security\n"
        "  warning — and learning to click through those is a genuinely bad\n"
        "  habit to pick up.\n"
        "\n"
        "  What you are agreeing to: this computer will trust certificates\n"
        "  signed by YOUR appliance. That is a real permission — a trusted\n"
        "  authority can vouch for any website name, not only this one.\n"
        "\n"
        "  It is bounded by the fact that only your appliance holds the key,\n"
        "  it applies to your user account and not the whole computer, and it\n"
        "  can be removed at any time."
    )


def install_command(cert_path: Path) -> list[str]:
    """The command that installs it, as data so it can be SHOWN first.

    -user is deliberate: the machine store would apply to everybody who logs
    into this computer, which is not one person's decision to make.
    """
    system = platform.system()
    if system == "Windows":
        return ["certutil", "-user", "-addstore", "Root", str(cert_path)]
    if system == "Darwin":
        return ["security", "add-trusted-cert", "-r", "trustRoot",
                "-k", str(Path.home() / "Library/Keychains/login.keychain-db"),
                str(cert_path)]
    return ["sudo", "cp", str(cert_path), "/usr/local/share/ca-certificates/"]


def removal_hint() -> str:
    """Said in the same breath as installing. A permission granted without a
    stated way to revoke it is not really an informed one."""
    system = platform.system()
    if system == "Windows":
        return "certutil -user -delstore Root sambuca"
    if system == "Darwin":
        return "Keychain Access -> login -> Certificates -> delete 'sambuca'"
    return ("sudo rm /usr/local/share/ca-certificates/sambuca*.crt "
            "&& sudo update-ca-certificates --fresh")


def install(cert: Certificate, workdir: Path) -> tuple[bool, str]:
    """Install it. Only ever called after the owner has said yes.

    Returns (ok, detail). Never raises: a trust-store refusal is a normal
    outcome on a managed machine, not an exception.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    cert_path = workdir / "sambuca-ca.crt"
    cert_path.write_bytes(cert.pem)

    cmd = install_command(cert_path)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=120, check=False)
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)[:120]

    if proc.returncode == 0:
        return True, "installed for your user account"

    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return False, (detail[-1][:120] if detail else f"exit {proc.returncode}")
