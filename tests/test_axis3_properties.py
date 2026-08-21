"""Properties CLAUDE.md states as facts about the shipped appliance.

Each of these is written down as something the machine DOES, and until now each
was verified by nothing. That combination is what rule 7 of the update guard
exists to catch — a protection nobody counts is a protection that can leave
without anyone noticing.

  * "Strip metadata by default. ComfyUI embeds the full prompt and workflow in
     saved PNGs, and it travels with the picture when it is shared."
  * "Read-only wherever the service does not need to write. Models are mounted
     :ro — a generation request cannot rewrite weights."
  * "Enforce ephemerality with the mount, not a cron job. A cleanup timer can
     silently stop running; a tmpfs cannot silently start persisting."
  * "Fail closed. An auth gate that passes traffic when its backend is down is
     not a gate."

WHAT THIS COST TO GET RIGHT, recorded because the same mistake keeps recurring:
a grep counting `--disable-metadata` per file reported 0 for the AMD overlay and
I nearly filed it as an AMD-only privacy leak. The AMD overlay simply inherits
the base. A second attempt simulated the merge against `command`, where the flag
does not live, and reported every variant unprotected. Both were wrong in the
alarming direction. The flag is in `environment: CLI_ARGS`, and compose merges
environment PER KEY — so these tests resolve the value the way compose does
rather than counting occurrences in a file.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = REPO / "compose"


def _load(name: str) -> dict:
    return yaml.safe_load((COMPOSE / name).read_text(encoding="utf-8")) or {}


def _service(doc: dict, name: str) -> dict:
    return (doc.get("services") or {}).get(name) or {}


def _image_overlays() -> list[str]:
    """Every file that can define the comfyui service."""
    return [p.name for p in sorted(COMPOSE.glob("*.yml"))
            if "comfyui" in (p.read_text(encoding="utf-8"))]


def test_the_overlays_were_actually_found() -> None:
    """A glob matching nothing passes every parametrised test below it."""
    found = _image_overlays()
    assert len(found) >= 3, f"only found {found}"


@pytest.mark.parametrize("overlay", _image_overlays())
def test_comfyui_never_writes_the_prompt_into_saved_pictures(overlay: str) -> None:
    """The privacy property, resolved the way compose resolves it.

    A picture carrying the full prompt and workflow is a picture that says what
    its owner asked for, to everyone they send it to.
    """
    base = _service(_load("image.yml"), "comfyui").get("environment") or {}
    over = _service(_load(overlay), "comfyui").get("environment") or {}
    if not base and not over:
        pytest.skip(f"{overlay} does not define comfyui")
    # environment merges per key; an overlay key replaces the base value
    cli = str(over.get("CLI_ARGS", base.get("CLI_ARGS", "")))
    assert "--disable-metadata" in cli, (
        f"{overlay}: ComfyUI would embed the prompt and workflow in every saved "
        f"PNG. Resolved CLI_ARGS: {cli!r}"
    )


def test_the_model_store_is_mounted_read_only() -> None:
    """A generation request must not be able to rewrite the weights."""
    svc = _service(_load("image.yml"), "comfyui")
    mounts = [m for m in (svc.get("volumes") or []) if "models" in str(m)]
    assert mounts, "no model mount found at all"
    for m in mounts:
        assert str(m).endswith(":ro"), f"model mount is writable: {m}"


def test_generated_pictures_land_on_a_tmpfs() -> None:
    """Ephemerality enforced by the MOUNT, not by a timer that can stop."""
    svc = _service(_load("image.yml"), "comfyui")
    tmpfs = svc.get("tmpfs") or []
    assert tmpfs, "comfyui has no tmpfs — generated images would persist to disk"
    assert any("output" in str(t) for t in tmpfs), (
        f"a tmpfs exists but not for the output directory: {tmpfs}"
    )


def test_every_gated_route_goes_through_forward_auth() -> None:
    """Fail closed.

    The `gate` snippet uses forward_auth, so if oauth2-proxy is down Caddy gets
    a connection error and refuses the request rather than serving it. A route
    that reverse_proxies without importing the gate would bypass that entirely,
    and it would look identical in a browser while the gate was up.
    """
    caddy = (COMPOSE / "config/caddy/Caddyfile").read_text(encoding="utf-8")
    assert "(gate)" in caddy, "the gate snippet is gone"
    gate_body = caddy.split("(gate)", 1)[1].split("\n}", 1)[0]
    assert "forward_auth" in gate_body, (
        "the gate no longer uses forward_auth, so a dead auth backend would no "
        "longer refuse traffic"
    )
    assert "oauth2-proxy" in gate_body, "the gate does not name the auth backend"


def test_the_auth_backend_reports_its_own_health() -> None:
    """An unhealthy gate must be visible as unhealthy, not merely broken."""
    svc = _service(_load("docker-compose.yml"), "oauth2-proxy")
    assert svc, "oauth2-proxy is not defined"
    assert "healthcheck" in svc, (
        "oauth2-proxy has no healthcheck — a misconfigured gate would look "
        "identical to a working one until someone tried to log in"
    )
