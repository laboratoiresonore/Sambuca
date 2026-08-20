# Container image policy

## Verification status

**Last verified 2026-08-19 against the live registries: 18 of 19 references
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
2026-08-19: it is not anonymously pullable, meaning an appliance cannot fetch it.
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

**`BENTOPDF_IMAGE`** — BentoPDF publishes **two** images, and the difference is
a licensing one, not a technical one:

| image | build | use |
|---|---|---|
| `ghcr.io/alam00000/bentopdf-simple` | self-hosted | **what sambuca uses.** Internal / team / single-owner deployments. |
| `ghcr.io/alam00000/bentopdf` | commercial | powers bentopdf.com; requires a licence for public-facing deployments. |

An appliance serving its own owner is unambiguously the self-hosted case, so
`-simple` is both correct and the lawful default. If you ever expose a sambuca
box publicly, that choice is the one to revisit.

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

## Verified digests (2026-08-19)

Generated by `tools/verify-images.py`. These are what `make pin-images`
would write; they are recorded here so a drifted tag is detectable without
having already pinned.

| variable | reference | manifest digest |
|---|---|---|
| `CADDY_IMAGE` | `caddy:2.8-alpine` | `sha256:af32e97399febea808609119bb21544d0265c58a02836576e32a2d082c262c17` |
| `POCKET_ID_IMAGE` | `ghcr.io/pocket-id/pocket-id:v0.53` | `sha256:7224174546de6a378fb705f763d11b604e3031b62efdc707cf1757b1b09705f5` |
| `OAUTH2_PROXY_IMAGE` | `quay.io/oauth2-proxy/oauth2-proxy:v7.6.0` | `sha256:dcb6ff8dd21bf3058f6a22c6fa385fa5b897a9cd3914c88a2cc2bb0a85f8065d` |
| `UPTIME_KUMA_IMAGE` | `louislam/uptime-kuma:1.23.16` | `sha256:431fee3be822b04861cf0e35daf4beef6b7cb37391c5f26c3ad6e12ce280fe18` |
| `WATCHTOWER_IMAGE` | `containrrr/watchtower:1.7.1` | `sha256:6dd50763bbd632a83cb154d5451700530d1e44200b268a4e9488fefdfcf2b038` |
| `OLLAMA_IMAGE` | `ollama/ollama:0.5.7` | `sha256:7e672211886f8bd4448a98ed577e26c816b9e8b052112860564afaa2c105800e` |
| `OLLAMA_ROCM_IMAGE` | `ollama/ollama:0.5.7-rocm` | `sha256:d05b7eefc8d8309b47377a6ac301a7f2c9e468ba13f3ef1c64dd4f51cd5151e8` |
| `ODYSSEUS_IMAGE` | `ghcr.io/laboratoiresonore/odysseus:latest` | **UNPUBLISHED** — HTTP 401, not anonymously pullable |
| `VAULTWARDEN_IMAGE` | `vaultwarden/server:1.32.7-alpine` | `sha256:f2da5d437e0c25f0a6f3a5283db74ed06dfcf4136f8db7cb17277506b1d30a5c` |
| `NEXTCLOUD_AIO_IMAGE` | `nextcloud/all-in-one:latest` | `sha256:428550e6266183cbac89340e6c9ae19d0cfcc590596f116baa9dbd5c087cb1aa` |
| `IMMICH_SERVER_IMAGE` | `ghcr.io/immich-app/immich-server:v1.119.1` | `sha256:d63feeee7a41095b3c1b18607d86a1264bcac53728a19f538a77a6e66043f492` |
| `IMMICH_ML_IMAGE` | `ghcr.io/immich-app/immich-machine-learning:v1.119.1` | `sha256:e8c416445db60c0ec94394c1e0e672b78409664f17de78787c62f6d13d3f6d92` |
| `IMMICH_DB_IMAGE` | `tensorchord/pgvecto-rs:pg14-v0.2.0` | `sha256:739cdd626151ff1f796dc95a6591b55a714f341c737e27f045019ceabf8e8c52` |
| `REDIS_IMAGE` | `redis:7-alpine` | `sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2` |
| `POSTGRES_IMAGE` | `postgres:16-alpine` | `sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685` |
| `BLINKO_IMAGE` | `blinkospace/blinko:1.4.2` | `sha256:0758270bffc5f728a4e20965c9b3686dab92c1afd5b50f7f934ec30a69b07b62` |
| `BENTOPDF_IMAGE` | `ghcr.io/alam00000/bentopdf-simple:v2.8.7` | `sha256:ba67d44f07ec0d2d636c945eef1c186f71bceda3d11b1e9809d7dbfbbaaacfa4` |
| `ERGO_IMAGE` | `ghcr.io/ergochat/ergo:v2.13.1` | `sha256:b14e45079fecf90ffeb9ff4a941212f0602bc6630b881a7aa40015965d500789` |
| `SYNAPSE_IMAGE` | `ghcr.io/element-hq/synapse:v1.119.0` | `sha256:a18c25d7c80a226905943483840d143c7d1f7fc6c95d5911d41a5e6567971e7c` |
