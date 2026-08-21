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

### The Pocket ID one-time admin link was world-readable

**Fixed 2026-08-20.** Pocket ID prints an initial-admin onboarding token in its
logs; provisioning scrapes it and shows it to the owner. That much is right —
the alternative is inventing a parallel bootstrap path with weaker properties.

What was wrong is where it went. Whoever opens that link becomes the FIRST ADMIN
of the identity provider gating every other service, and it was landing in four
places at once:

| where | mode |
|---|---|
| `identity.json` | 0644 |
| `completion-report.txt` | 0644 |
| `/var/log/sambuca/` | 0755 directory |
| the MOTD | printed to **every user at every login** |

It now goes to one root-only file (`/etc/sambuca/secrets/pocket_id_setup_url`,
0600) and is read with `sambuca-identity setup-link`. `identity.json` carries a
boolean instead of the credential; the report and the MOTD point at the command.

Found alongside it, in the same block:

- The provisioning warning printed the **first eight characters of the
  oauth2-proxy client secret** into that 0755 log. Eight characters an attacker
  no longer has to guess, bought nothing: the owner opens the file either way.
- Step 4 of the one attended step said `sambuca identity set-client` — and
  there is no `sambuca` command, only hyphenated binaries. The single manual
  step needed to arm the gate named something that does not exist.
  `tests/test_engine_promises.py` now fails the build on that.
- `/var/log/sambuca` is 0750 rather than 0755.

---

### The CasaOS installer — was the weakest link, now pinned

**Fixed 2026-08-20 (option 1 below).** It is no longer piped into a shell. The
installer is downloaded to a file, checked against a SHA-256 pinned in
`engine/provision/50-network.sh`, and only then run — by `sb_verify_and_run`,
which returns a distinct code for a checksum mismatch so a caller can tell
"upstream changed or someone tampered" apart from "it errored".

Piping was worse than it looked, separately from the trust problem: `bash`
executes what has arrived while the rest is still downloading, so a connection
cut mid-transfer can run half a script.

**What this fixes and what it does not.** It pins the INSTALLER. The component
tarballs that installer fetches are versioned GitHub release URLs baked into
it — so pinning the script does fix those versions, but they are still fetched
over TLS without checksums of our own. "Whoever controls that URL controls every
appliance" becomes "whoever controls it can serve the exact bytes we reviewed,
or nothing". A real improvement, not a complete one.

**When upstream updates it, provisioning will decline and say so**, leaving the
appliance working without a dashboard. Adopting a new version means REVIEWING it
and updating the constant. `tools/check-upstreams.py` already reports the hash,
so the drift shows up in the daily run rather than as a failed install.

The original entry read:

> `curl -fsSL https://get.casaos.io | bash` runs unreviewed, unpinned, unsigned
> code as root. Whoever controls that URL controls every sambuca appliance at
> install time. It is the only remote-execution point in the repository that is
> not signature-verified, and it is called out here rather than buried because
> it is a real hole, not a theoretical one.

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
- **Scanned by:** the `image-scan` workflow daily, and `make scan-images` /
  `tools/scan-images.sh` on the appliance itself.
- **Gated on REGRESSION, not absolute state.** "Fixable" means the *package*
  has a patch — not that the image *publisher* has rebuilt. Once we are on the
  newest published tag, a remaining fixable CVE is not actionable by us, and
  gating on it makes the job red every day. A job that is red every day is one
  everybody clicks past, which costs the attention a real regression needs. So
  counts are always reported and the build fails only when an image gets worse
  than `tools/vuln-baseline.json`. Improvements are reported but the floor is
  **never lowered automatically** — `make vuln-baseline` is deliberate, and the
  diff has to be reviewed, because a baseline that silently follows reality
  down can silently follow it up.
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
| `COMFYUI_IMAGE` / `_ROCM_` / `_CPU_` | **Third-party, and worth saying out loud: ComfyUI publishes no official container.** These are community builds on dated tags. They go through the same Trivy gate as everything else. Building our own from a pinned upstream git tag is the better answer and is not done. |

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
| **Raspberry Pi Imager `--repo`** | `imager.py`, `os-list/` | The whole write path is now theirs. `--repo` is documented, but a schema change to the OS list breaks the device picker SILENTLY — empty list, no error. Verified against v2.0.10. |
| **The OS-list schema** (`imager.devices`, `os_list`) | `tools/build-os-list.py` | Mirrors `downloads.raspberrypi.org/os_list_imagingutility_v4.json`. Upstream adding a required field would not fail loudly; it would just stop matching. |
| **jsDelivr as the host** | `manifest/sambuca-manifest.json` | NOT interchangeable with raw.githubusercontent, which serves `.json` as `text/plain` — and rpi-imager ignores a list served that way, with no error at all. If jsDelivr becomes unavailable the fallback must still send `application/json`. |
| **The manifest itself** | `manifest.py`, everything | Fetched live. A schema bump must raise `SUPPORTED_SCHEMA` in lockstep or every client silently falls back to its bundled copy and runs on stale checksums. |
| **`imagecustomization` registry key** | `customisation.py` | `HKCU\Software\Raspberry Pi\Raspberry Pi Imager`. Undocumented, and it also holds a password hash and a wifi PSK. If the field names change, pre-fill silently stops working — and the allowlist is what keeps the secrets out. |
| ComfyUI model directory layout (`models/checkpoints/…`) | `70-models.sh`, `image.yml` | a ComfyUI reorganisation moves the path; the checkpoint downloads fine and is then invisible to the loader |
| FLUX workflow node names (`EmptySD3LatentImage`, `CheckpointLoaderSimple`) | `config/comfyui/workflows/flux-schnell.json` | a node rename fails at generation time, not at start-up — the service looks healthy |
| `black-forest-labs/FLUX.1-schnell` is **gated**; we fetch the Comfy-Org repackage | `engine/profiles/tier*.env` | if that mirror disappears there is no ungated source for the weights, and the fallback is asking every owner for a Hugging Face account |

**Secrets that remain in the environment.** Most are now file-backed via the
`*_FILE` conventions their upstreams document, mounted through compose secrets.
Two are not, because their images support no such convention — and an
environment variable is readable by `docker inspect`, by anything that can read
`/proc/<pid>/environ`, and by every child process the service spawns:

| Variable | Service | Why not file-backed |
|---|---|---|
| `SYNAPSE_REGISTRATION_SHARED_SECRET` | Synapse | config generated from env on first run; can be removed after init |
| `NEXTAUTH_SECRET`, `DATABASE_URL` | Blinko | no `*_FILE` support; the URL embeds the password by construction |

Re-check these on every image bump — if upstream adds `*_FILE`, convert. That
has already paid off once: Pocket ID gained `ENCRYPTION_KEY_FILE` in v2, and the
bump from v0.53 closed that exception.

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

**Update control is now built and tested.** `engine/maintenance/update-guard.sh`
decides whether an incoming update may be applied unattended, and holds it for a
human if it: touches more than 200 files or adds more than 6000 lines; adds a
binary outside `assets/brand/`; introduces anything shaped like a private key or
an API token; **contacts a host this repository has never used** (the
highest-signal supply-chain check available); touches the installer, the storage
or firewall phases, the backup path, CI, or the guard itself; or changes a
pinned image digest.

It is a **separate, side-effect-free script on purpose**: `tests/test-update-guard.sh`
feeds it fourteen deliberately poisoned updates on every push and asserts it
refuses each one. A guard nobody has shown an attack to is an assumption, not a
control — and that test immediately earned its keep by catching a bug where
`grep -qE "-----BEGIN..."` parsed the pattern as command-line options, silently
disabling the private-key check.

`gitops-sync.sh` **fails closed** if the guard is missing: absent checks must
never read as "nothing objectionable found".

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
