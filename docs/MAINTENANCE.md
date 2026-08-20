# Maintenance register — every coupling to something we do not control

**This is the list of things that will break by themselves.** Not because of a
bug in this repository, but because something upstream changed underneath it: a
protocol, an API, a package repository, a container tag, a vendor's terms of
service.

Every entry states **what breaks**, **how it fails** (loudly or silently — the
silent ones are the dangerous ones), **how fast it moves**, and **what watches
it**. If a coupling is not in this table, it is not being watched, and that is
the bug.

> **Rule for contributors:** adding a dependency on anything outside this
> repository — a registry, an API, an install script, a protocol, a file format
> owned by someone else — means adding a row here in the same pull request. See
> [CONTRIBUTING.md](../CONTRIBUTING.md).

Automated drift detection lives in `tools/check-upstreams.py`, run daily by the
`upstream-drift` CI workflow and available locally as `make check-upstreams`.

---

## Tier 1 — BRIDGES: constant monitoring required

A bridge speaks a protocol owned by a company that does not want it spoken by
us. These break the most often, break silently, and are the only components in
the appliance that carry account-level risk to the owner.

**All three are excluded from unattended updates, live in their own bundle, and
must have an Uptime Kuma monitor before they are enabled.** A bridge that stops
working looks exactly like "nobody messaged me today", which is why a health
check is not optional here.

| Bridge | Speaks | Fails | Moves | Risk to the owner |
|---|---|---|---|---|
| `mautrix-whatsapp` | WhatsApp multi-device web protocol | **silently** — messages simply stop arriving | protocol changes with no notice; breakage measured in weeks | **Account suspension.** Unofficial clients breach Meta's terms. Real, if uncommon. |
| `mautrix-signal` | Signal, as a linked secondary device | usually loudly (link drops), sometimes silently | tracks upstream `libsignal`; breaks on major protocol bumps | Device unlink; re-pairing needed |
| `heisenbridge` | IRC | loudly | slow; IRC is a stable protocol | none |

**Not yet built.** These are designed in [design/NEXT-STAGE.md](design/NEXT-STAGE.md)
and must not ship until each has:

1. A health monitor that alerts on *no traffic AND no heartbeat*, not just on a
   dead container — a bridge can be up and disconnected.
2. The ToS warning shown to the owner in plain words before pairing.
3. Exclusion from Watchtower and from the GitOps auto-apply path.
4. A named person watching upstream releases. **This is the part that cannot be
   automated**, and it is the reason bridges are Tier 1: everything else on this
   page fails in a way a script can notice.

**Bridges terminate end-to-end encryption.** They decrypt to re-encrypt into
Matrix. On a hosted bridge that means a stranger reads your messages; here the
bridge is the owner's own hardware, on their own tailnet, on an encrypted disk.
That is a genuine advantage and still a change to the threat model.

---

## Tier 2 — remote code executed during installation

These fetch and run code from the internet, as root, on a machine that will hold
the owner's documents. They are the highest-value supply-chain targets in the
project.

| What | Where | Verified? | Notes |
|---|---|---|---|
| **CasaOS installer** | `provision/50-network.sh` | ❌ **NO** — `curl \| bash`, unpinned | **Known weakness.** See below. |
| Docker apt repo | `provision/20-docker.sh` | ✅ GPG key pinned to keyring, `signed-by=` | key fetched at runtime over TLS |
| NVIDIA Container Toolkit repo | `provision/30-gpu-runtime.sh` | ✅ `signed-by=` | key fetched at runtime |
| Tailscale apt repo | `provision/50-network.sh` | ✅ `signed-by=` | key fetched at runtime |
| Debian security updates | `provision/10-system.sh` | ✅ Debian archive keys | security-only by policy |

### The CasaOS installer is the weakest link in the project

`curl -fsSL https://get.casaos.io | bash` runs unreviewed, unpinned, unsigned
code as root. Whoever controls that URL controls every sambuca appliance at
install time. It is the only remote-execution point in the repository that is
not signature-verified, and it is called out here rather than buried because it
is a real hole, not a theoretical one.

It is tolerated today because CasaOS has no packaged distribution and the
dashboard is optional. **The fix, in preference order:**

1. Pin a specific release tarball plus a checksum recorded in this repository.
2. Vendor the install steps so nothing is fetched-and-executed at all.
3. Drop CasaOS. Caddy already serves every service; CasaOS provides a tile view.

Until one of those lands, an operator who wants no unverified code on their
appliance should remove `casaos` from provisioning. Everything else works
without it.

---

## Tier 3 — container images

19 images from 4 registries. **Tags are mutable**: a publisher can retag or
delete one, and two machines flashed a month apart then run different software.

- **Watched by:** `tools/verify-images.py`, daily in CI, and `make verify-images`.
- **Policy and digests:** [IMAGES.md](IMAGES.md).
- **Fails:** loudly at pull time, or *silently* if a tag is repointed to a newer
  incompatible version — which is why pinning to digests before a release tag is
  mandatory rather than tidy.

Three need attention on every bump:

| Image | Why |
|---|---|
| `IMMICH_*` | Immich moves fast and changes its vector extension across releases. **The server image and DB image must be bumped together**; a mismatch fails at schema migration, not at startup, so the container looks healthy for a while first. |
| `NEXTCLOUD_AIO_IMAGE` | `:latest` by design — AIO is a mastercontainer that manages its own children and expects to self-update. We do not pin it, and that is a deliberate exception. |
| `ODYSSEUS_IMAGE` | First-party, **not yet published**. Reported as UNPUBLISHED, not BROKEN. |

---

## Tier 4 — model registry

Ten model references in `engine/profiles/tier*.env`. Models are renamed, retagged
and withdrawn from the Ollama library with no deprecation period.

**Fails loudly** — the pull fails and `70-models.sh` reports it — but on a *fresh
install only*. An existing appliance keeps running a model that no longer exists
upstream, so drift here is invisible until someone reinstalls.

Watched by `tools/check-upstreams.py`, which resolves every tier's model set
against the registry.

---

## Tier 5 — other projects' internals we reach into

Undocumented paths and fixed names inside other people's containers. These have
no compatibility guarantee at all and break at the maintainer's discretion.

| Coupling | Where | Risk |
|---|---|---|
| Caddy's internal CA at `/data/caddy/pki/authorities/local/root.crt` | `60-stack.sh` CA export, `issue-service-cert.sh` | path is a Caddy implementation detail |
| `nextcloud_aio_mastercontainer` fixed volume name | `cloud.yml` | AIO refuses to start otherwise; not our choice |
| Immich upload path `/usr/src/app/upload` | `cloud.yml` | changed historically |
| Pocket ID one-time setup token, scraped from container logs | `80-identity.sh` | **most fragile thing in the repo** — a log format change breaks it silently, and the log line is not an API |

**Secrets that remain in the environment.** Most are now file-backed via the
`*_FILE` conventions their upstreams document, mounted through compose secrets.
Three are not, because their images support no such convention — and an
environment variable is readable by `docker inspect`, by anything that can read
`/proc/<pid>/environ`, and by every child process the service spawns:

| Variable | Service | Why not file-backed |
|---|---|---|
| `ENCRYPTION_KEY` | Pocket ID | no `*_FILE` support documented |
| `SYNAPSE_REGISTRATION_SHARED_SECRET` | Synapse | config generated from env on first run; can be removed after init |
| `NEXTAUTH_SECRET`, `DATABASE_URL` | Blinko | no `*_FILE` support; the URL embeds the password by construction |

Re-check these on every image bump — if upstream adds `*_FILE`, convert.

**Fixed 2026-08-20:** Ergo was mounted Caddy's CA *directory* and told to serve
`root.crt`/`root.key` as its TLS certificate. That handed a chat container the
CA **private key** — anything compromising Ergo could mint certificates trusted
by every device the owner had set up, for every service on the appliance. It
also could not have worked: a CA root has no hostname SAN and asserts
`CA:TRUE`. Ergo now receives a dedicated leaf certificate from
`issue-service-cert.sh`, and the CA key never leaves the host. **The rule this
establishes: no container is ever given the CA private key, for any reason.**

---

## Tier 6 — the update channel itself

| Thing | Fails | Watched by |
|---|---|---|
| GitOps signing key | loudly — unsigned tags are refused | `gitops-sync.sh` |
| `laboratoiresonore/Sambuca` availability | loudly | nightly timer, `gitops-state.json` |
| GitHub Actions versions | loudly, with deprecation warnings first | CI (`actions/checkout@v4` and `setup-python@v5` already warn about Node 20) |

The planned update-control hardening — diff size limits, secret scanning,
egress review, rollback exercised in CI — is specified in
[design/NEXT-STAGE.md](design/NEXT-STAGE.md) and is the most urgent unbuilt
security work, because the appliance is *already* pulling updates unattended.

---

## Tier 7 — third-party flows the companion will drive

Not code we run, but processes we hand a novice through. They break when a
vendor redesigns a settings page, and they fail *confusingly* rather than
loudly: the user is simply lost.

| Flow | Breaks when | Consequence |
|---|---|---|
| Gmail App Password | Google redesigns account security pages (roughly annually) | screenshots stop matching; user gets stuck |
| Google Takeout | export layout and sidecar metadata format change | importer produces wrong or missing metadata |
| iOS/Android certificate install | OS release changes the trust flow | the first step of onboarding fails |
| Nextcloud / Immich / Bitwarden mobile onboarding | app redesigns | QR flow diverges from instructions |

Every companion step therefore **verifies its outcome against the appliance**
rather than trusting a checkbox. A screenshot that no longer matches is annoying;
a checklist that reports success without checking is a lie.

---

## Cadence

| When | What |
|---|---|
| **Daily, automated** | `tools/check-upstreams.py` — images, registries, apt repos, models |
| **Weekly, human** | Bridge upstream releases and breakage reports (Tier 1) |
| **On every image bump** | Immich server/DB pairing; re-run `make verify-images` |
| **Before every release tag** | `make pin-images`; full `make check`; register reviewed |
| **On every Debian point release** | Preseed keys and `d-i` behaviour |
| **When a companion flow breaks** | Screenshots and steps re-verified against the live vendor UI |
