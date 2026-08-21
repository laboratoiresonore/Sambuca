#!/usr/bin/env python3
"""
sambuca :: the image orchestrator.

WHAT WAS MISSING. The picture half of this appliance had every part except the
one that does anything: a pinned FLUX checkpoint, a hardened ComfyUI with its
arbitrary-code path disabled, a tmpfs for outputs, a curated API-format graph —
and nothing that loaded the graph, filled it in, or posted it. The workflow
README described this program in the present tense while it did not exist, and
docs/design/AI-PLANE.md says plainly that no picture has ever come out.

This is that missing piece. It is deliberately small and stdlib-only, like the
beacon: it runs on the appliance, and a dependency here is a dependency on the
machine that holds somebody's photographs.

═══════════════════════════════════════════════════════════════════════════
THE SUBSTITUTION IS STRUCTURAL, NOT TEXTUAL, and that is the whole security
argument of this file.

`%PROMPT%` sits INSIDE a JSON string in the graph. The obvious implementation —
read the file as text, str.replace the placeholder, parse the result — hands
the person typing the prompt a way to end the string and write their own JSON.
A prompt containing a double quote would at best corrupt the graph and at worst
add nodes to it, and ComfyUI executes whatever graph it is given.

So the graph is PARSED FIRST and the placeholders are replaced inside already-
parsed values. The prompt is then data by construction: it can contain quotes,
braces, backslashes, newlines, or the text of another workflow, and none of it
can become structure. There is no escaping to get right because nothing is ever
escaped.
═══════════════════════════════════════════════════════════════════════════

Numbers are bounded rather than trusted. FLUX.1-schnell is a four-step model;
asking for four thousand does not make a better picture, it occupies the GPU
for an hour on a machine that is also somebody's file server.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
import time
import urllib.error
import urllib.request

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8188
# THE PATH AS THE CONTAINER SEES IT. This program runs inside the comfyui
# container (see the sambuca-image wrapper: ComfyUI has no ports mapping on
# purpose, so the orchestrator goes to it rather than the other way round), and
# compose mounts the workflow directory there read-only. The host path
# /opt/sambuca/compose/config/comfyui/workflows does not exist in here.
DEFAULT_WORKFLOW = pathlib.Path(
    "/root/ComfyUI/user/default/workflows/flux-schnell.json"
)

# Bounds, not suggestions. Each one is a real limit of the shipped model or of
# the machine it runs on, and every rejection says which.
MAX_PROMPT = 2000
STEPS_MIN, STEPS_MAX = 1, 8            # schnell is a 4-step model
SIZE_MIN, SIZE_MAX = 256, 1536
SIZE_MULTIPLE = 16                     # the latent is downsampled by 8, twice


class ImageError(RuntimeError):
    """Something an owner needs to read, not a traceback."""


def _fill(node_tree: dict, values: dict[str, object]) -> dict:
    """Replace %PLACEHOLDER% inside an ALREADY-PARSED graph.

    Walks the parsed structure and swaps whole values. A string that is exactly
    a placeholder becomes the typed value — so %STEPS% becomes the integer 4,
    not the string "4", which ComfyUI's validator would reject.

    A placeholder embedded in a longer string (say "a photo of %PROMPT%") is
    substituted textually within that one string, which is safe because the
    result is assigned as a value and never re-parsed.
    """
    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            if node in values:                       # the whole value
                return values[node]
            for placeholder, value in values.items():
                if placeholder in node:              # embedded in a sentence
                    node = node.replace(placeholder, str(value))
            return node
        return node

    return walk(node_tree)


def build_graph(workflow: dict, *, prompt: str, width: int, height: int,
                steps: int, seed: int) -> dict:
    """A ready-to-post graph, with every input checked before it goes in."""
    if not prompt.strip():
        raise ImageError("an empty prompt would produce nothing; say what you want")
    if len(prompt) > MAX_PROMPT:
        raise ImageError(
            f"that prompt is {len(prompt)} characters; the limit is {MAX_PROMPT}")
    for name, value in (("width", width), ("height", height)):
        if not SIZE_MIN <= value <= SIZE_MAX:
            raise ImageError(
                f"{name} must be between {SIZE_MIN} and {SIZE_MAX}, not {value}")
        if value % SIZE_MULTIPLE:
            raise ImageError(
                f"{name} must be a multiple of {SIZE_MULTIPLE}, not {value}")
    if not STEPS_MIN <= steps <= STEPS_MAX:
        raise ImageError(
            f"steps must be between {STEPS_MIN} and {STEPS_MAX}, not {steps}. "
            f"This model is designed for four; more does not improve it and "
            f"occupies the graphics card for longer.")

    return _fill(workflow, {
        "%PROMPT%": prompt,
        "%WIDTH%": width,
        "%HEIGHT%": height,
        "%STEPS%": steps,
        "%SEED%": seed,
    })


def _post(base: str, path: str, payload: dict, timeout: float = 30.0) -> dict:
    body = json.dumps(payload).encode("utf-8")
    # noqa: S310 - base is built from a host and port this program owns,
    # never from the manifest or from anything the owner typed.
    req = urllib.request.Request(  # noqa: S310
        f"{base}{path}", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        # noqa justified: base is built from a host and port this program owns.
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = (exc.read() or b"")[:400].decode("utf-8", "replace")
        raise ImageError(
            f"the image service refused the request (HTTP {exc.code}): {detail}"
        ) from exc
    except OSError as exc:
        raise ImageError(
            f"cannot reach the image service at {base} ({exc.__class__.__name__}). "
            f"It may still be starting, or image generation may be switched off "
            f"on this machine."
        ) from exc


def _get(base: str, path: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(f"{base}{path}")  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise ImageError(f"the image service returned HTTP {exc.code}") from exc
    except OSError as exc:
        raise ImageError(
            f"lost contact with the image service ({exc.__class__.__name__})"
        ) from exc


def submit(base: str, graph: dict, client_id: str) -> str:
    reply = _post(base, "/prompt", {"prompt": graph, "client_id": client_id})
    prompt_id = reply.get("prompt_id")
    if not prompt_id:
        # ComfyUI reports a rejected graph in the body rather than the status.
        raise ImageError(f"the image service accepted nothing back: {reply}")
    return str(prompt_id)


def wait_for(base: str, prompt_id: str, *, timeout: float,
             poll: float = 1.0, sleep=time.sleep) -> list[str]:
    """Block until the picture exists, and return the filenames.

    The deadline is not optional. On a CPU-only machine a single picture takes
    minutes, and a hang here would be indistinguishable from slowness — the
    thing an owner cannot tell apart on their own.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        history = _get(base, f"/history/{prompt_id}")
        entry = history.get(prompt_id)
        if entry:
            status = (entry.get("status") or {})
            if status.get("status_str") == "error":
                raise ImageError(
                    "the image service could not run that graph; the machine is "
                    "fine, the request is not")
            names = [
                img["filename"]
                for out in (entry.get("outputs") or {}).values()
                for img in (out.get("images") or [])
                if img.get("filename")
            ]
            if names:
                return names
        sleep(poll)
    raise ImageError(
        f"no picture after {timeout:.0f} seconds. It may still be working — "
        f"check the image service before asking again, because a second request "
        f"queues behind the first rather than replacing it.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Ask this machine for a picture.",
        epilog="The result lands on a tmpfs and does NOT survive a reboot. "
               "Save anything you want to keep.")
    ap.add_argument("prompt", help="what the picture should be of")
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--steps", type=int, default=4)
    ap.add_argument("--seed", type=int, default=None,
                    help="reuse a seed to get the same picture again")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--workflow", type=pathlib.Path, default=DEFAULT_WORKFLOW)
    ap.add_argument("--timeout", type=float, default=600.0)
    args = ap.parse_args(argv)

    seed = args.seed if args.seed is not None else random.randrange(2**32)  # noqa: S311

    try:
        if not args.workflow.is_file():
            raise ImageError(f"no workflow at {args.workflow}")
        workflow = json.loads(args.workflow.read_text(encoding="utf-8"))
        graph = build_graph(workflow, prompt=args.prompt, width=args.width,
                            height=args.height, steps=args.steps, seed=seed)
        base = f"http://{args.host}:{args.port}"
        prompt_id = submit(base, graph, client_id="sambuca-image")
        print(f"asked for: {args.prompt}")
        print(f"seed {seed} — reuse it with --seed {seed} for the same picture")
        print("working. On a CPU-only machine this takes minutes, not seconds.")
        names = wait_for(base, prompt_id, timeout=args.timeout)
    except ImageError as exc:
        print(f"sambuca-image: {exc}", file=sys.stderr)
        return 1

    for n in names:
        print(f"done: {n}")
    print("Saved on a temporary disk — it will not survive a reboot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
