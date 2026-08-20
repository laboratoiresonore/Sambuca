"""
sambuca :: talk to the install beacon.

The other half of engine/beacon/sambuca-beacon.py. A beacon nothing can talk to
is the same as no beacon, and building only the listener is exactly the unwired
failure this project keeps finding in its own work.

WHAT IT IS FOR: the appliance serves its own setup page from Caddy, and Caddy
starts in the LAST provisioning phase. Everything before that — disk, base
system, Docker, GPU, storage, network — happens with nothing to watch. That is
the window where somebody decides the machine has hung and pulls the power in
the middle of partitioning it.

WHY IT IS PATIENT RATHER THAN CLEVER: the appliance is booting. It will not
answer for the first minute or two, its address may not resolve yet, and the
network it is on may not be the one it will settle on. Every one of those is
NORMAL here, and none of them should produce an error — a progress viewer that
gives up before the thing it watches has started is worse than no viewer.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from . import safeurl

PORT = 8765
_POLL = 3.0
_TIMEOUT = 4.0


def _candidates(host: str) -> list[str]:
    """Addresses worth trying, in the order most likely to answer.

    mDNS is how a home network finds a machine with no DNS entry, but it is
    unreliable on exactly the networks that need it most — some routers filter
    multicast, and some corporate wifi drops it entirely. So the bare hostname
    is tried too.
    """
    host = host.strip().rstrip(".")
    out = [host]
    if host.endswith(".local"):
        out.append(host[: -len(".local")])
    elif "." not in host:
        out.append(f"{host}.local")
    return out


def probe(host: str, key: str, *, timeout: float = _TIMEOUT) -> dict | None:
    """One request. Returns the progress fields, or None if it did not answer.

    None covers every ordinary reason — not booted yet, name does not resolve,
    wrong network — because the caller's job is to keep waiting, not to
    distinguish them. A key that is REFUSED is different and is raised.
    """
    for candidate in _candidates(host):
        url = f"http://{candidate}:{PORT}/progress"
        try:
            safeurl.check(url)
            req = urllib.request.Request(url, headers={"X-Sambuca-Key": key})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - scheme allowlisted by safeurl.check above
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                # NOT a "keep waiting" case. Something IS listening and it does
                # not accept this key — a different appliance, or a stale watch
                # file. Silently retrying would look identical to "not up yet"
                # and waste somebody's afternoon.
                raise PermissionError(
                    "the appliance refused this pairing key - this watch file "
                    "probably belongs to a different install") from exc
            continue
        except (urllib.error.URLError, OSError, TimeoutError, ValueError, safeurl.UnsafeURL):
            continue
    return None


def follow(host: str, key: str, *, say, once: bool = False,
           poll: float = _POLL, patience: float = 1800.0) -> int:
    """Print each stage as it changes, until provisioning ends.

    Only on CHANGE. A line every three seconds would bury the one thing that
    matters — that it moved — under a wall of identical text.
    """
    started = time.monotonic()
    last_step = None
    ever_answered = False

    while True:
        try:
            doc = probe(host, key)
        except PermissionError as exc:
            say(f"  {exc}")
            return 1

        if doc is None:
            if once:
                say("  No answer yet. The appliance is probably still booting.")
                return 1
            if not ever_answered and (time.monotonic() - started) > patience:
                # GIVE UP HONESTLY, and say where the real answer lives.
                say()
                say("  Still nothing after 30 minutes.")
                say("  Either it cannot reach this network, or it never booted.")
                say("  The card records what happened: put it in a reader and")
                say("  read sambuca-firstboot.log")
                return 1
            time.sleep(poll)
            continue

        ever_answered = True
        step = doc.get("step")
        state = str(doc.get("state", ""))

        if step != last_step:
            last_step = step
            total = doc.get("steps_total") or "?"
            say()
            say(f"  [{step} of {total}]  {doc.get('title', '')}")
            if doc.get("what"):
                say(f"      {doc['what']}")
            if doc.get("how_long"):
                say(f"      about {doc['how_long']}")
            if doc.get("your_move"):
                say(f"      you: {doc['your_move']}")

        if once:
            return 0

        if state in ("done", "complete", "finished"):
            say()
            say("  Finished. Now run:  sambuca-flasher handover")
            return 0
        if state == "failed":
            say()
            say("  That stage failed. The card records the detail:")
            say("  put it in a reader and read sambuca-firstboot.log")
            return 1

        time.sleep(poll)
