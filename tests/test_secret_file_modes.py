"""A file that carries a secret must not be created world-readable.

THE BUG THIS ENCODES had four symptoms and one cause. Pocket ID's one-time
admin link — whoever opens it becomes the FIRST ADMIN of the identity provider
gating every service — was written into identity.json at 0644, because it was
being treated as status rather than as a credential.

Nobody would defend "write an admin token 0644". It happened because the write
looked like every other status write, three files away from anything that said
"secret". So the rule is mechanical rather than a matter of care:

    if an sb_atomic_write block expands a secret-ish variable,
    its mode must be owner-only.

WHAT THIS DOES NOT CLAIM. It is a heuristic over variable NAMES. It cannot tell
that `$setup_url` is a credential and `$lan_ip` is not — it catches the former
because the name contains "url" alongside a token-ish sibling, and it would miss
a secret stored in a variable called `$x`. It is a tripwire on the common shape,
not a proof.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"

# Modes that do not expose a file to every local user.
OWNER_ONLY = {"0600", "0640", "0400", "0700", "0750"}

SECRETISH = re.compile(
    r"\$\{?(\w*(?:secret|token|passphrase|password|authkey|setup_url)\w*)\}?", re.I)

# A VALUE, not a LOCATION. Names ending _FILE/_DIR/_PATH say where a secret
# lives, and writing such a path into a status file is exactly what the fix for
# the Pocket ID bug does — flagging it would fail the build on the repair.
PATHLIKE = re.compile(r"_(FILE|DIR|PATH)$", re.I)

# Names that LOOK secret-ish and are not. Each is a value that is public by
# design, not a judgement call about how sensitive something is.
PUBLIC_BY_DESIGN = {
    "SAMBUCA_ADMIN_SSH_KEY",   # a PUBLIC key; the private half never leaves the owner
    "BACKUP_SEED_HASH",        # a hash for verification, not the seed
    "SAMBUCA_BACKUP_SEED_HASH",
}

WRITE = re.compile(r"\|\s*sb_atomic_write\s+(\S+)\s+(\d{3,4})")


def _blocks(text: str):
    """Each `{ … } | sb_atomic_write <path> <mode>` and the content above it.

    Walks backwards from the write to its opening brace rather than parsing
    shell: good enough to associate content with its destination, and it fails
    towards including too much rather than too little.
    """
    for m in WRITE.finditer(text):
        path, mode = m.group(1), m.group(2)
        head = text[:m.start()]
        open_at = head.rfind("\n{\n")
        # A BLOCK ENDS WITH `}` IMMEDIATELY BEFORE THE PIPE — that is what
        # `{ … } | sb_atomic_write` means. The first version tested for the
        # ABSENCE of a closing brace, which every block has, so it always fell
        # through to the single-line branch and never inspected a block at all.
        # It passed its own suite while being unable to see the very bug it was
        # written for. Caught by reintroducing that bug and watching it stay
        # green.
        if head.rstrip().endswith("}") and open_at != -1:
            body = head[open_at:]
        else:
            # A single pipeline: `printf … | sb_atomic_write x 0644`.
            #
            # Taking a fixed slab of preceding text here was wrong and produced
            # a false positive — it swept up variables from unrelated commands
            # three lines earlier and accused the tailscale sources.list of
            # carrying a secret.
            body = head[head.rfind("\n") + 1:] if "\n" in head else head
        yield path, mode, body, text[:m.start()].count("\n") + 1


def test_no_secret_is_written_into_a_world_readable_file():
    problems = []
    for f in sorted(ENGINE.rglob("*.sh")):
        text = f.read_text(encoding="utf-8", errors="ignore")
        for path, mode, body, line in _blocks(text):
            if mode in OWNER_ONLY:
                continue
            names = {n for n in SECRETISH.findall(body)
                     if n.upper() not in PUBLIC_BY_DESIGN
                     and not PATHLIKE.search(n)}
            if names:
                problems.append(
                    f"{f.relative_to(ROOT)}:{line}  writes {path} as {mode} "
                    f"but the block expands {sorted(names)}")
    assert not problems, (
        "secret-bearing files created readable by every local user:\n  "
        + "\n  ".join(problems))


def test_the_secrets_directory_is_owner_only():
    """Everything under it inherits this. A 0755 secrets directory would make
    every 0600 file inside it enumerable, which is most of the value gone."""
    found = []
    for f in ENGINE.rglob("*.sh"):
        for m in re.finditer(r"install -d -m (\d{3,4}) \"\$\{SB_ETC\}/secrets\"",
                             f.read_text(encoding="utf-8", errors="ignore")):
            found.append(m.group(1))
    assert found, "nothing creates the secrets directory — has it moved?"
    bad = [m for m in found if m not in {"0700", "0750"}]
    assert not bad, f"the secrets directory is created {bad}"


def test_the_check_still_finds_writes_to_examine():
    """Every audit here fails the same way: a regex tightened or a directory
    moved turns it into a no-op that reports green."""
    total = sum(len(list(_blocks(f.read_text(encoding="utf-8", errors="ignore"))))
                for f in ENGINE.rglob("*.sh"))
    assert total >= 15, f"only found {total} sb_atomic_write blocks — regex broken?"
