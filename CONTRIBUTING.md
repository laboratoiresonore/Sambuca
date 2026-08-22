# Contributing

## Before you open a PR

```bash
make check
```

That is `tools/preflight.sh`: ruff, **both test trees**, shellcheck over every
engine script, `dash -n` over the installer scripts, every `tests/test-*.sh`
suite, the steward catalogue lint, the compose YAML parse and the hardware
profiler. It names the three checks it cannot run locally rather than implying
full coverage, and it exits non-zero if a tool is merely MISSING — a partial
preflight that returns 0 reads as a pass.

If you have no `make`, the same thing:

```bash
bash tools/preflight.sh
```

**Then check CI, because local green is not CI green.** Three commits once went
out reporting "89 tests pass" while the build was failing, and the failing step —
ruff — was the one finding real bugs. `gh run list --limit 3` costs a second.
Read the per-JOB results, not the run conclusion: a Windows job was red for hours
while runs were reported green, and the macOS jobs were cancelled 17 times out of
18 without ever completing while every report said "green except macOS still
running".

CI additionally validates the Caddyfile, runs `docker compose config` across
every GPU profile × bundle subset, and builds the frozen binary on Windows and
macOS. `make lint-compose` only renders the CPU overlays — the full matrix needs
a Docker runner, and it is the check that caught `gpu.amd.image.yml` shipping
broken.

> `make check` did not exist until 2026-08-22. It was the first instruction in
> this file and it failed with "No rule to make target". `make test` ran only the
> flasher tree, so every appliance test passed locally without being executed by
> it. Both are fixed, and `check` now delegates to preflight rather than keeping
> a second list to drift against it.

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

## Adding a dependency on anything you do not control

**Register it in [docs/MAINTENANCE.md](docs/MAINTENANCE.md), in the same pull
request.** A registry, an API, an install script, a protocol, someone else's
file format, an undocumented path inside another project's container — all of
it. If a coupling is not in that table, nothing is watching it, and it will
break on a user's machine before it breaks on ours.

The row must answer four questions:

1. **What breaks** when the upstream changes.
2. **How it fails** — loudly, or *silently*. Silent failures are the dangerous
   ones and get a monitor, not a note.
3. **How fast it moves.** A protocol owned by a hostile company is not the same
   risk as the Debian archive.
4. **What watches it.** If a machine can check it, add it to
   `tools/check-upstreams.py`. If only a human can, say who and how often — and
   expect to justify why a machine cannot.

Two rules that came out of real defects and are not negotiable:

- **No container is ever given the CA private key.** Services that cannot get a
  certificate from Caddy get a dedicated leaf from `issue-service-cert.sh`.
- **No unverified remote code.** Exactly one exception exists (the CasaOS
  installer), it is documented as a known weakness, and adding a second will be
  rejected.

## Every stage must tell the owner what is happening

Anything the owner sees during installation or recovery states what is
happening, how long it takes, what they should do, and what comes next — and on
failure, what it means and the exact command to resume. Use `sb_stage`,
`sb_stage_ok` and `sb_stage_failed` from `engine/lib/common.sh`.

Write the wording in the owner's language, not ours. "Preparing your disks" is
correct; "partman-auto" is not. The audience is someone leaving Google, not
someone who reads shell scripts.

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
