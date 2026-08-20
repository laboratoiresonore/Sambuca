# Container image policy

> Images are **Tier 3** in the [maintenance register](MAINTENANCE.md), which
> lists every coupling to something outside this repository — bridges, install
> scripts, apt repos, the model registry — and what watches each one. Image
> drift is checked daily by the `upstream-drift` workflow.

## Verification status

**Last verified 2026-08-20 against the live registries: 18 of 19 references
resolve.** The one exception is `ODYSSEUS_IMAGE`, which is first-party and not
yet published — see below. Reproduce the check with:

```bash
python tools/verify-images.py compose/.env.example
```

It speaks the OCI distribution API directly, so it needs no Docker daemon. Exit
codes are deliberately split: **1** means a third-party reference is broken and
an appliance would fail to pull it; **2** means only first-party images are
unpublished, which is a known pre-release state. Collapsing those two into
"failed" is how a check becomes something people click past.

## Current status: TAGS, NOT DIGESTS

Every image reference in `compose/.env.example` is currently a **mutable tag**.
Tags can be reassigned by their publisher at any time, which means:

- two machines flashed a month apart from the same USB can end up running
  different software;
- the GitOps sync has no stable artefact to validate against;
- "zero-configuration, reproducible appliance" is not yet literally true.

**Before tagging a sambuca release, pin every image to a digest.**

```bash
make verify-images
```

```bash
make pin-images
```

`verify-images` resolves every `*_IMAGE` reference and prints its digest,
failing if any reference is unreachable. `pin-images` rewrites `.env.example`
with `@sha256:` digests, keeping the human-readable tag in the reference so the
diff is reviewable.

## References that need checking on every bump

Most images are stable, well-known upstream publications. Three need attention:

**`ODYSSEUS_IMAGE`** — `ghcr.io/laboratoiresonore/odysseus`. This organisation's
own build, and **the one reference that does not currently resolve.** Verified
2026-08-20: it is not anonymously pullable, meaning an appliance cannot fetch it.
Until it is pushed to GHCR *and marked public*, `docker compose pull` fails for
this one service and the `ai` bundle comes up with Ollama but no frontend.

A private GHCR package is not a partial solution — an appliance pulls
anonymously and will get a 401 exactly as an outsider does. Publishing it:

```bash
docker build -t ghcr.io/laboratoiresonore/odysseus:0.1.0 .
docker push ghcr.io/laboratoiresonore/odysseus:0.1.0
gh api -X PATCH /user/packages/container/odysseus -f visibility=public
```

If you are not laboratoiresonore: build Odysseus yourself and override the
reference in `compose/local.yml`, or drop `ai` from your bundle list.

**`IMMICH_DB_IMAGE`** — Immich's database image changes with its vector
extension, and has moved between `pgvecto-rs` and VectorChord across releases.
**Immich's own `docker-compose.yml` is the authority.** Check it whenever you
bump `IMMICH_SERVER_IMAGE`; a mismatched pair fails at schema migration, not at
startup, so the container looks healthy for a while first.

**`BENTOPDF_IMAGE`** — BentoPDF publishes two images. **Corrected 2026-08-20
after checking upstream:** an earlier revision of this file claimed the
non-`simple` build was the "commercial" one requiring a licence for public
deployments. That was wrong. Both images carry the same dual licence —
AGPL-3.0, or a paid commercial licence that removes the AGPL obligations — and
the difference between them is cosmetic: `-simple` omits the marketing site.
We use `-simple` under AGPL-3.0 because it is leaner, not because the other is
disallowed.

## Update policy

Two separate mechanisms, on purpose:

**Watchtower** — label-enabled, so a container is updated only if it explicitly
opts in via `com.centurylinklabs.watchtower.enable=true`. **Nothing with a
database schema opts in.** An unattended major-version bump of Immich or
Nextcloud at 05:00 with nobody watching is how self-hosted setups lose data.
`WATCHTOWER_REMOVE_VOLUMES` is `false` and will stay `false`.

**GitOps sync** — pulls version *decisions* from the repository, which is where
a human reviewed the upgrade. It verifies a signed tag, refuses to apply changes
under `engine/autoinstall/`, `engine/provision/40-`, `engine/provision/50-` or
`engine/maintenance/backup` without human review, and rolls back if the stack
does not come back healthy.

The stateful services — Vaultwarden, Nextcloud, Immich, Synapse — are updated
only through the second mechanism.

## Adding a service

1. Add the image to `.env.example` as `<NAME>_IMAGE=` with a specific tag.
2. Add the service to the appropriate bundle file, not to `docker-compose.yml`,
   unless it is genuinely core.
3. Give it a healthcheck. A service with no healthcheck is invisible to the
   health gate in `60-stack.sh` and to the rollback check in `gitops-sync.sh`.
4. Join the minimum networks: `edge` only if Caddy must reach it, `data` only if
   it needs a database, `ai` only if it needs the model server.
5. Add a Caddy site block **and** a `:84xx` listener if it should be reachable
   remotely — the tailnet port map is not automatic.
6. Add the watchtower label only if it is stateless.
7. Add its data path to the backup target list in `engine/maintenance/backup.sh`,
   and a `pg_dump` call if it has a database. A service whose data is not in the
   backup set is a service that will be lost.

## Verified digests (2026-08-20)

Generated by `tools/verify-images.py`. These are what `make pin-images` would
write; they are recorded here so a drifted tag is detectable without having
already pinned.

| variable | reference | manifest digest |
|---|---|---|
| `CADDY_IMAGE` | `caddy:2.11.4-alpine` | `sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648` |
| `POCKET_ID_IMAGE` | `ghcr.io/pocket-id/pocket-id:v2.5.0` | `sha256:1549f31f76a6c158af0056a4f2c62a590627886a84e9da9bda76eb1e142a449a` |
| `OAUTH2_PROXY_IMAGE` | `quay.io/oauth2-proxy/oauth2-proxy:v7.14.2` | `sha256:121cdc6520a02d7a2ddd181af6dbdc0f11f7d0c0d9353a999a69c3998cbfe37e` |
| `UPTIME_KUMA_IMAGE` | `louislam/uptime-kuma:1.23.17` | `sha256:3d632903e6af34139a37f18055c4f1bfd9b7205ae1138f1e5e8940ddc1d176f9` |
| `WATCHTOWER_IMAGE` | `containrrr/watchtower:1.7.1` | `sha256:6dd50763bbd632a83cb154d5451700530d1e44200b268a4e9488fefdfcf2b038` |
| `OLLAMA_IMAGE` | `ollama/ollama:0.32.15` | `sha256:57d60e686821ea81a7748a3ec8141308c8b8f95b27105713954abf7a6529e700` |
| `OLLAMA_ROCM_IMAGE` | `ollama/ollama:0.32.15-rocm` | `sha256:b1495fb615be87fb43fc2321be80d49069e03175a4516730f102f9b6e8727a87` |
| `ODYSSEUS_IMAGE` | `ghcr.io/laboratoiresonore/odysseus:latest` | **UNPUBLISHED** — not anonymously pullable |
| `VAULTWARDEN_IMAGE` | `vaultwarden/server:1.37.1-alpine` | `sha256:b094afed4ed5ea353821c6efcedca446f30c6654ba2bc441db6089b0c2b94ac8` |
| `NEXTCLOUD_AIO_IMAGE` | `nextcloud/all-in-one:latest` | `sha256:428550e6266183cbac89340e6c9ae19d0cfcc590596f116baa9dbd5c087cb1aa` |
| `IMMICH_SERVER_IMAGE` | `ghcr.io/immich-app/immich-server:v1.128.0` | `sha256:3306cbb62e5ac5fd1449b0a92990686b6795afa7bed7fd9aec8fb81c978dec91` |
| `IMMICH_ML_IMAGE` | `ghcr.io/immich-app/immich-machine-learning:v1.128.0` | `sha256:8011358f5bd474d72b08a9dc1ad38f4c763ef0e4ebbc6012fd6141801268f141` |
| `IMMICH_DB_IMAGE` | `tensorchord/pgvecto-rs:pg14-v0.2.0` | `sha256:739cdd626151ff1f796dc95a6591b55a714f341c737e27f045019ceabf8e8c52` |
| `REDIS_IMAGE` | `valkey/valkey:9.1.1-alpine` | `sha256:de31910896150d5e754a07d57d227cfdde4e258ddd0d1aa4607f2d2f95843715` |
| `POSTGRES_IMAGE` | `postgres:16.15-alpine` | `sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685` |
| `BLINKO_IMAGE` | `blinkospace/blinko:1.8.8` | `sha256:31c3bc9f4fc00c82328c098c0c1120fe7fe43152a9f3cbc04b0303c32a2e60d3` |
| `BENTOPDF_IMAGE` | `ghcr.io/alam00000/bentopdf-simple:v2.8.7` | `sha256:ba67d44f07ec0d2d636c945eef1c186f71bceda3d11b1e9809d7dbfbbaaacfa4` |
| `ERGO_IMAGE` | `ghcr.io/ergochat/ergo:v2.19.1` | `sha256:ef885e44f7fa19101bbbc41baef11dc280dc8107465dccaf6f0860f41b48a682` |
| `SYNAPSE_IMAGE` | `ghcr.io/element-hq/synapse:v1.122.0` | `sha256:925534da6deefc83d3b82bdb08acd7bedfd34ca29f8d47a27c386c0ece9a0515` |
