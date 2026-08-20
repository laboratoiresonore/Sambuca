# Contributing

## Before you open a PR

```bash
make check
```

That runs shellcheck over every engine script, validates the compose chain for
all three GPU overlays, lints the flasher and runs its tests. CI runs the same
thing plus a Caddyfile validation and the flasher suite on Windows and macOS.

## The bar for engine scripts

These run unattended, as root, on a machine holding somebody's irreplaceable
data, with nobody watching. The review standard is correspondingly unfriendly:

1. **Idempotent.** Re-running must be safe. In particular, never regenerate a
   secret that an initialised database is already using.
2. **Fails loudly, and specifically.** An error message must name what failed
   and give the command to resume. `|| true` needs a comment explaining why the
   failure is genuinely acceptable.
3. **Never destroys recovery state without a human.** Parity, backups, key slots
   and existing filesystems. If a change can lose data, it asks.
4. **Verifies rather than assumes.** `docker compose up` succeeding is not the
   stack working; check the healthchecks. A backup exit code is not a backup;
   restore a file.
5. **`sb_single_instance` first** in anything long-running, before it binds a
   port or writes a file. Two concurrent provisioning runs interleaving
   `compose up` with `tailscale up` is not a hypothetical.
6. **No hardcoded device paths.** `/dev/sda` reorders between boots.

## Adding a service

Follow the checklist in [docs/IMAGES.md](docs/IMAGES.md). The step people skip
is the last one: add its data to `engine/maintenance/backup.sh`. A service whose
data is not in the backup set is a service that will be lost, and nothing else
in the system will notice.

## Documentation

If a change makes a tradeoff, say what it costs. This project's documentation
names its compromises — the USB is a key during install, passkey enrolment is
attended, docker group membership is root — because a security model that reads
as flawless is one nobody can evaluate.
