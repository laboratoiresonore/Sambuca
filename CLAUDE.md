# Sambuca — the three axes, and how they are enforced

Every change to this repository is measured against three axes. They are not
aspirations, they are acceptance criteria. **If a change does not advance one
and hold the other two, it is not finished.**

> **1. USER-FRIENDLINESS** — a complete novice, taken by the hand, start to finish.
> **2. SECURITY** — a fortress that can also be unbricked.
> **3. PERFECTED SETUP** — hardened variants never stock, ephemeral by construction, lean.

The rest of this file is what those actually mean when you are about to write a
line of code, and the failures that put each rule here.

---

## The rule above the three axes: VERIFY, DON'T ASSERT

Every serious bug in this project was found by **running the thing**, and every
one of them looked correct on the page first.

- `CPU_CORES=1` as a sentinel — invisible in review, found by executing the profiler.
- CRLF from Python's default text mode — `#!/bin/sh\r` is not an interpreter.
- `grep -qE "-----BEGIN..."` — the pattern parsed as options and silently
  disabled the private-key check. The test suite caught it; reading did not.
- A GPU overlay naming a service only one bundle defined — invalidated the
  entire compose project, not just that service.
- `> [!TIP]` inside `<details>` — rendered the literal text `[!TIP]` at readers.
  The rendered page disproved it; the API was inconclusive.
- FLUX.1-schnell's official repo is **gated** and 401s anonymously. The licence
  was right; the download would have stalled every walk-away install.

**So:** run the script, fetch the URL, render the page, execute the binary,
`docker compose config` the chain. An exit code of 0 means bytes moved, not
that the right bytes did. A tool that reports success is a claim, not a result.

**Corollary — check before you "fix".** During the image-plane work the
profiler reported `handoff` on what looked like a GPU-less box. The instinct
was to fix it. Checking first showed the machine has a GTX 1080 and the output
was correct — but it exposed a real gap next to it, that `IMAGE_VRAM_MB` was
declared and never compared against the card actually present.

---

## Axis 1 — user-friendliness

**The audience is someone who has never installed an operating system.** Not a
developer in a hurry. Write for the person who got the "storage full" email and
has an old desktop under the stairs.

- **Never ship a control that does nothing.** The FLUX negative-prompt box is
  the canonical example: at `cfg 1.0` it has no effect, so exposing it would be
  worse than exposing nothing. A dead knob teaches people the machine is
  arbitrary.
- **Explain the failure they will actually hit**, at the moment they hit it.
  The unsigned-binary warning is in the README *above* the download, because
  the alternative is a novice concluding the file is malware.
- **Plain English before the jargon, and in a different colour.** The `> [!TIP]`
  callouts above each folded section exist so a novice never has to expand
  anything to understand what a section is about. Alerts do **not** render
  inside `<details>` — put them above the fold.
- **Say the real number.** "Minutes per picture" beats "slower on this
  hardware". Tier 3 image generation is offered with the cost attached, not as
  a checkbox.
- **Refuse clearly instead of degrading silently.** A 512 MiB machine is told
  which specific things will not fit — the file server wants ~2 GiB, the photo
  library ~4, the smallest model ~2.5 — rather than being handed a stack that
  thrashes.

## Axis 2 — security

**A fortress that can also be unbricked.** Both halves are load-bearing; a
fortress nobody can recover is a brick, and a recoverable box that anyone can
walk into is a filing cabinet.

- **Fail closed.** oauth2-proxy with no client ID stays unhealthy and gated
  routes return 503. An auth gate that passes traffic when its backend is down
  is not a gate.
- **Secrets are files, never environment variables.** `docker inspect`,
  `/proc/<pid>/environ`, child processes, crash dumps and
  `docker compose config` all read the environment. Only convert where upstream
  documents `*_FILE`; where it is unsupported, leave it **with a comment saying
  so** rather than letting it look intentional.
- **Never commit personal data.** No IPs, no `C:\Users\...` paths, no emails,
  no tokens. Never `git add -A` or `git add .` — add named files.
- **Pin, verify, and check the digest.** Dated tags, not `:latest`. The 16 GiB
  checkpoint is SHA-256 pinned, resumable, and atomic — it lands on `.part` and
  is renamed only once the digest matches.
- **Verify redaction by SHAPE, not by delimiter.** Splitting on `:` once leaked
  a key whose value sat on the next line. Match the secret's pattern.
- **An AI with privileges picks a lever; it never has hands.** The Steward
  chooses a verb from a closed catalogue and fills typed parameters. It never
  emits shell, SQL, or an API call. Injected text can at worst cause an
  existing verb to be *proposed*, never invent one.
- **Nothing may edit its own guard.** The Steward cannot change its catalogue,
  its privileges, or the audit log. Enforced by `tools/steward-lint.py` in CI,
  not by good intentions.
- **Free software means the weights too.** FLUX.1-dev is `licence: other` and
  was rejected for a product a lawyer will use commercially. The audit passes
  on models, not only containers.

## Axis 3 — perfected setup

**Hardened variants, never stock.** If the upstream default is what everyone
ships, that is a reason to look harder, not a reason to copy it.

- **Disable the arbitrary-code path, ship a curated set.** ComfyUI's
  custom-node installer is unreachable; "just paste this node pack" is the
  likeliest route to an owner being compromised.
- **Enforce ephemerality with the mount, not a cron job.** Generated images
  live on a tmpfs. A cleanup timer can silently stop running; a tmpfs cannot
  silently start persisting.
- **Strip metadata by default.** ComfyUI embeds the full prompt and workflow in
  saved PNGs, and it travels with the picture when it is shared.
- **Read-only wherever the service does not need to write.** Models are mounted
  `:ro` — a generation request cannot rewrite weights.
- **Arbitrate shared resources explicitly.** Two allocators each seeing "free
  VRAM" at the moment they ask is how a box dies at 3am mid-import. The
  inference engine owns the GPU and background ML yields; *foreground* work
  (image generation) takes turns via an explicit handoff instead.
- **Prefer one code path with three settings** over three code paths. One FLUX
  checkpoint with `--normalvram` / `--lowvram` / `--cpu` beat a second GGUF
  path that would have saved 5 GiB and added an unmaintained third-party node.
- **Reject unmaintained dependencies**, even good ones. No tagged releases and
  no commits in seven months disqualifies a component from an appliance meant
  to run untouched for years.

---

## Mechanical checks — run these before claiming done

```bash
bash -n engine/hardware-detect.sh && bash -n engine/provision/*.sh
python tools/steward-lint.py
python -c "import yaml,glob; [yaml.safe_load(open(f,encoding='utf-8')) for f in glob.glob('compose/*.yml')]"
bash engine/hardware-detect.sh --print --force-tier 1 --no-lock --quiet
bash tests/test-update-guard.sh
```

- **A conditionally-present service needs its own bundle.** A GPU overlay
  naming a service the selected bundles do not define invalidates the *entire*
  compose project. This is why `image.yml` is separate from `ai.yml`. CI
  validates every bundle subset against every GPU profile — keep that matrix
  updated when adding a bundle.
- **Caddyfile:** a closing brace on the same line is a parse error, and a
  global `email` with an empty value is a hard parse error.
- **Shell:** `read -t` is not POSIX and will block forever in `dash`. `printf`
  a leading `-----` through `printf '%s\n'`. `--` before a `grep` pattern that
  begins with a dash.
- **Windows:** the console is cp1252. `✗` in a lint tool crashes the tool that
  was supposed to report the finding. Use ASCII markers.
- **Heredocs mangle `\\n`.** For Python-in-heredoc edits, use the Edit tool or
  write the script to a file first.

## Status honesty

The README carries a status table, and it is the most important table in it.
**"Written and renders in CI" is not "works".** Say which. The project's
credibility rests on an owner being able to trust the status section when they
are deciding whether to put their client files on this.

Never mark something verified because it looks right. `docs/design/AI-PLANE.md`
says in plain words that no picture has yet come out of the FLUX workflow —
keep that discipline.
