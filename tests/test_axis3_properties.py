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


# ── the per-service hardening posture ───────────────────────────────────────
#
# "Read-only wherever the service does not need to write" and "hardened
# variants, never stock" are axis-3 claims, and nothing counted them per
# service. Measured today: 20 services, no-new-privileges on 19, cap_drop on 0,
# read_only on 1.
#
# These do NOT assert the hardening this project eventually wants — adding
# cap_drop or read_only to a service that needs the capability turns a running
# appliance into a stopped one, and that cannot be verified from here. They
# assert the posture that EXISTS, so it can only be added to, never quietly
# lost. Rule 7 of the update guard does the same job for nightly updates; this
# does it for development, which is where these were going to disappear from.


def _all_services() -> dict[str, dict]:
    """Every service definition, merged the way a later file overrides an
    earlier one for the keys we care about."""
    out: dict[str, dict] = {}
    for path in sorted(COMPOSE.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for name, svc in (doc.get("services") or {}).items():
            if isinstance(svc, dict):
                out.setdefault(name, {}).update(svc)
    return out


def _has_nnp(svc: dict) -> bool:
    return any("no-new-privileges" in str(x) for x in (svc.get("security_opt") or []))


# Named, with the reason, because an unexplained absence reads as an oversight
# and gets "fixed" blind by whoever notices it next. The reason lives in full in
# compose/cloud.yml above the service.
NO_NNP_EXCEPTIONS = {
    "nextcloud-aio": "holds the docker socket and spawns the Nextcloud "
                     "deployment; unverified, and upstream sets no security_opt "
                     "at all. Needs hardware to close.",
}


def test_every_service_refuses_privilege_escalation() -> None:
    missing = sorted(n for n, s in _all_services().items()
                     if not _has_nnp(s) and n not in NO_NNP_EXCEPTIONS)
    assert not missing, (
        "these services allow setuid privilege escalation and are not listed as "
        f"known exceptions: {missing}. Add no-new-privileges, or add it to "
        f"NO_NNP_EXCEPTIONS with the reason.")


def test_the_exception_list_has_not_gone_stale() -> None:
    """An exception for a service that no longer exists is a hole waiting for a
    name collision — and it hides the fact that the gap was closed."""
    services = _all_services()
    gone = sorted(n for n in NO_NNP_EXCEPTIONS if n not in services)
    assert not gone, f"NO_NNP_EXCEPTIONS names services that do not exist: {gone}"

    closed = sorted(n for n in NO_NNP_EXCEPTIONS
                    if n in services and _has_nnp(services[n]))
    assert not closed, (
        f"{closed} now sets no-new-privileges — remove it from the exception "
        f"list and delete the explanation in compose/, so the next reader is "
        f"not told about a gap that is closed")


# A RATCHET, not a target. Whatever is hardened today must still be hardened
# tomorrow; adding to these sets is how hardening lands, and a name disappearing
# from a compose file is what this catches.
READ_ONLY_TODAY = {"bentopdf"}
CAP_DROP_TODAY: set[str] = set()      # none yet — see task #1


def test_read_only_rootfs_is_never_quietly_removed() -> None:
    services = _all_services()
    lost = sorted(n for n in READ_ONLY_TODAY
                  if not (services.get(n) or {}).get("read_only"))
    assert not lost, (
        f"{lost} lost read_only. If a service genuinely needs to write, say so "
        f"in the compose file and remove it from READ_ONLY_TODAY deliberately.")


def test_dropped_capabilities_are_never_quietly_removed() -> None:
    services = _all_services()
    lost = sorted(n for n in CAP_DROP_TODAY if not (services.get(n) or {}).get("cap_drop"))
    assert not lost, f"{lost} lost cap_drop"


def test_this_posture_check_can_still_see_the_services() -> None:
    """The failure this repository keeps rediscovering: a moved directory or a
    changed key turns the whole check into zero checks, and it reports green."""
    services = _all_services()
    assert len(services) >= 15, f"found almost no services: {sorted(services)}"
    hardened = sum(1 for s in services.values() if _has_nnp(s))
    assert hardened >= 15, (
        f"only {hardened} services carry no-new-privileges — either a real "
        f"regression, or this check has stopped reading security_opt")
