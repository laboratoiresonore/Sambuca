# Shipped workflows

JSON has no comments, so the reasoning lives here.

These are ComfyUI **API-format** graphs — the shape ComfyUI accepts at
`POST /prompt`, not the editor's save format.

> [!IMPORTANT]
> **Nothing loads these yet.** The orchestrator that would substitute the
> `%PLACEHOLDER%` tokens and post the graph is **not built**, so no picture has
> come out of this workflow. The file below is the intended contract, written
> in advance — not a description of something running.
>
> This paragraph used to describe the orchestrator in the present tense, which
> read as though the path worked. The whole status discipline of this project
> rests on an owner being able to trust that distinction.

## Why the owner never edits this

A novice does not want a node graph; they want a picture of a bicycle. The
graph is an implementation detail that happens to be visible if you go looking
for it, in the same way `/etc/fstab` is.

## flux-schnell.json

FLUX.1-schnell, Apache-2.0, via the self-contained fp8 checkpoint. One file
covers every tier — only ComfyUI's memory mode changes between them.

Placeholders: `%PROMPT%` `%WIDTH%` `%HEIGHT%` `%SEED%` `%STEPS%`.

Three values are **not** placeholders, deliberately:

| Value | Why it is fixed |
|---|---|
| `cfg: 1.0` | schnell is guidance-distilled. Raising cfg does not make it follow the prompt harder, it makes the picture fall apart. This is the single most common way people "tune" FLUX into producing garbage. |
| `sampler euler` / `scheduler simple` | The pairing schnell was distilled against. |
| Empty negative prompt | At cfg 1.0 the negative branch has no effect. Exposing a negative-prompt box would be a control that does nothing — worse than no control. |

`EmptySD3LatentImage`, not `EmptyLatentImage`: FLUX uses a 16-channel latent,
and the SD-era node emits 4 channels. The mismatch surfaces as a shape error
deep in the sampler rather than anywhere near the node that caused it.

## Status

**Not yet executed on a real GPU.** The graph is written against the documented
node interfaces and the values above are the schnell defaults, but no picture
has come out of this file yet. See the status table in the README.
