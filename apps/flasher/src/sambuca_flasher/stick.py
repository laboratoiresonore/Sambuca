"""Neutralise the installer USB once the appliance is up.

THE STICK IS A KEY AND NOTHING EVER TOOK IT BACK.

An unattended install has to put the disk passphrase on the medium — d-i reads
it from `preseed.cfg` — and the recovery key and backup password ride along in
their own files. `first-boot.sh` shreds `/boot/sambuca/provision.json`, which is
the copy on the INSTALLED machine. Nothing in the engine writes to /cdrom at
all. So the stick keeps every secret for as long as it exists, and until this
module the flasher told owners the opposite.

Warning them is the second-best answer. The rule is: do it for them, and only
guide them if you cannot. This can be done for them — but only at a moment when
it is provably safe, and only to a volume that is provably the right one.

WHEN. Not at write time (the install has not happened), and not from `watch`
(progress is not completion). `handover` runs after real services answered on
the network, which is the first moment the stick is certainly spent.

WHICH. Never "the removable drives" — that is how somebody's photo backup gets
erased. A volume qualifies only if it carries the payload marker, and if the
fingerprint inside that payload matches the appliance being handed over. A
second Sambuca stick for a different machine sitting in another port is left
alone.

WHAT. The secrets, not the stick. Overwrite and unlink the four files that
carry them; leave the medium mountable and reusable. Reformatting would need
elevation, would destroy a stick somebody may want to reuse, and would still
not be more thorough on flash storage — where wear levelling means neither
approach can promise the old blocks are gone. That limit is stated to the owner
rather than papered over.
"""

from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass, field

# The payload directory as `write` lays it out and as the installer reads it
# (`SRC=/cdrom/sambuca` in late-command.sh, `/cdrom/sambuca/preseed.cfg` in the
# kernel arguments).
PAYLOAD_DIR = "sambuca"
MARKER = "provision.json"

# Every staged file that carries a secret, and what is in it. The text is shown
# to the owner: "four files" means nothing, naming them means something.
SECRET_FILES: dict[str, str] = {
    "provision.json": "the provisioning payload",
    "preseed.cfg": "the disk passphrase, for the unattended installer",
    "luks-recovery.key": "the second-keyslot recovery key",
    "restic-password.key": "the backup repository password",
}


@dataclass
class Stick:
    """A mounted volume carrying this appliance's installer payload."""

    root: pathlib.Path
    fingerprint: str = ""
    present: list[str] = field(default_factory=list)

    @property
    def payload(self) -> pathlib.Path:
        return self.root / PAYLOAD_DIR


@dataclass
class Result:
    removed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


def _read_fingerprint(payload: pathlib.Path) -> str:
    try:
        data = json.loads((payload / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    value = data.get("fingerprint", "")
    return value if isinstance(value, str) else ""


def inspect(root: pathlib.Path) -> Stick | None:
    """Is this mount point a Sambuca installer medium? None if not.

    Identified by the marker file, never by being removable. The marker is the
    same path the installer itself reads, so a volume that would install is a
    volume that qualifies — and one that would not, does not.
    """
    payload = pathlib.Path(root) / PAYLOAD_DIR
    if not (payload / MARKER).is_file():
        return None
    return Stick(
        root=pathlib.Path(root),
        fingerprint=_read_fingerprint(payload),
        present=[name for name in SECRET_FILES if (payload / name).is_file()],
    )


def find(roots, fingerprint: str = "") -> list[Stick]:
    """Sticks among `roots`, optionally only those matching one appliance.

    THE FILTER IS THE SAFETY. With a fingerprint, a stick built for a different
    machine is skipped rather than erased — and a stick whose payload cannot be
    read is skipped too, because an unreadable marker is not a match, it is an
    unknown.
    """
    found = []
    for root in roots:
        try:
            stick = inspect(root)
        except OSError:
            continue                      # unreadable mount: not ours to touch
        if stick is None:
            continue
        if fingerprint and stick.fingerprint != fingerprint:
            continue
        found.append(stick)
    return found


def _overwrite(path: pathlib.Path) -> None:
    """Best-effort overwrite before unlinking.

    NOT A GUARANTEE, and the caller says so out loud. On flash media the
    controller may write elsewhere entirely; on any medium a copy may survive
    in a journal. It is still worth doing: it defeats undelete, which is the
    realistic threat for a stick in a drawer, and costs milliseconds on files
    this size.
    """
    size = path.stat().st_size
    with open(path, "r+b", buffering=0) as fh:
        for _ in range(3):
            fh.seek(0)
            fh.write(os.urandom(size))
            fh.flush()
            os.fsync(fh.fileno())


def neutralise(stick: Stick) -> Result:
    """Remove the secrets, leave the stick usable."""
    result = Result()
    for name in SECRET_FILES:
        path = stick.payload / name
        if not path.is_file():
            continue
        try:
            _overwrite(path)
        except OSError:
            # Overwriting is the bonus; removal is the point. A read-only or
            # exotic filesystem must not stop the unlink.
            pass
        try:
            path.unlink()
            result.removed.append(name)
        except OSError as exc:
            result.failed.append((name, str(exc)))
    return result


def verify(stick: Stick) -> list[str]:
    """Which secret files are STILL there. Empty means clean.

    Called after `neutralise` because a delete that reports success and leaves
    the file is exactly the class of silent-success failure this project keeps
    finding — and here it would leave the owner believing a live key is dead.
    """
    return [name for name in SECRET_FILES if (stick.payload / name).is_file()]
