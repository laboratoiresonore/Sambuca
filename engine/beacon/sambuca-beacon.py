#!/usr/bin/env python3
"""
sambuca :: the install beacon.

WHY THIS EXISTS. The setup page is served by Caddy, and Caddy starts at
60-stack — the last provisioning phase. Everything it exists to narrate (disk,
base system, Docker, GPU, storage, network) has already happened by the time it
can be reached. That leaves the first several minutes with nothing to watch,
which is exactly the window where an anxious owner power-cycles a machine
mid-partition and turns a slow install into a broken one.

So this runs FIRST, from the standard library, before Docker exists. It serves
the same progress.json the setup page reads, and it is torn down the moment the
real stack can take over.

═══════════════════════════════════════════════════════════════════════════
IT RUNS IN THE LEAST-DEFENDED WINDOW THIS MACHINE WILL EVER HAVE, and every
decision below follows from that. There is no firewall yet, no identity
provider, no reverse proxy, no fail2ban. A mistake here is a mistake made on a
machine with none of its defences up.

  NO FILE SERVING, AT ALL. Two fixed routes that emit computed JSON. Not a
  document root, not SimpleHTTPRequestHandler, not a path that is ever joined
  to user input — so path traversal is not mitigated here, it is absent. The
  stdlib's own file server was the obvious choice and is the wrong one.

  NO CONTROL SURFACE. GET only. It cannot start, stop, retry or configure
  anything. A provisioning-time endpoint that accepts commands is a remote
  execution hole at the worst possible moment.

  THE KEY NEVER APPEARS IN argv. `ps` is world-readable; a key passed as a
  command-line argument is visible to every user on the box and to anything
  that scrapes process lists. It is read from a root-owned file instead.

  COMPARED IN CONSTANT TIME. hmac.compare_digest, because == on a secret leaks
  its prefix through timing, and a beacon answering thousands of requests on a
  quiet LAN is a fine place to measure that.

  IT LEAKS NOTHING EVEN WHEN IT ANSWERS. Only the progress fields are emitted,
  which are plain-language stage text by construction. No hostnames, no paths,
  no log lines — a log line can carry a token or a directory layout. The file
  is re-read and re-serialised field by field rather than passed through, so a
  field added to progress.json later cannot silently start being published.

  IT DIES. Provisioning stops it and disables the unit. A thing that needs to
  exist for an hour must not survive the hour.
═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import hmac
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("SAMBUCA_BEACON_PORT", "8765"))
PROGRESS = os.environ.get("SAMBUCA_PROGRESS", "/var/lib/sambuca/progress.json")
KEYFILE = os.environ.get("SAMBUCA_BEACON_KEY", "/var/lib/sambuca/beacon.key")

# The ONLY fields ever published. An allowlist rather than a blocklist: if a
# future stage writes something sensitive into progress.json, it stays here
# rather than being served because nobody remembered to exclude it.
PUBLIC_FIELDS = (
    "schema", "updated", "state", "step", "steps_total",
    "title", "what", "how_long", "your_move", "next",
)

MAX_HEADER = 4096


def _load_key() -> str:
    """The pairing key, from a root-owned file.

    Never from argv (ps is world-readable) and never from the environment,
    which /proc/<pid>/environ exposes to anything that can read the process.
    """
    try:
        with open(KEYFILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _progress() -> dict:
    """Read progress, and publish only what is on the allowlist.

    Rebuilt field by field rather than forwarded. Passing the parsed document
    straight through would mean any field a later stage adds is published the
    moment it is written, which is how these things leak.
    """
    try:
        with open(PROGRESS, encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            raise ValueError("progress is not an object")
    except (OSError, ValueError):
        # NOT AN ERROR. Before the first stage writes, there is genuinely
        # nothing to report, and "starting" is the honest answer. A 500 here
        # would tell an owner something is wrong when nothing is.
        return {
            "schema": 1,
            "state": "starting",
            "step": 0,
            "title": "Starting up",
            "what": "The machine is booting and about to begin.",
            "your_move": "Nothing.",
        }
    return {k: raw[k] for k in PUBLIC_FIELDS if k in raw}


class Handler(BaseHTTPRequestHandler):
    server_version = "sambuca-beacon"
    sys_version = ""                      # do not advertise the Python version
    protocol_version = "HTTP/1.1"

    key = ""

    def log_message(self, *args):
        """Silence. Request logging on a provisioning box writes attacker-
        controlled strings into a file somebody later reads with a terminal."""

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # It is not a website. Nothing here should ever be framed, sniffed,
        # cached, or reachable from a page the owner happens to have open.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        """Constant-time comparison, and no key means no service.

        Failing closed matters more here than anywhere else in the project: an
        unauthenticated beacon announces to every device on the network —
        including a guest phone — that this machine is mid-install and
        therefore in its least-defended state.
        """
        if not self.key:
            return False
        supplied = self.headers.get("X-Sambuca-Key", "")
        if len(supplied) > 256:
            return False
        return hmac.compare_digest(supplied, self.key)

    def do_GET(self):                                    # noqa: N802
        # Query strings and fragments are discarded rather than parsed. There
        # is nothing to parameterise, so accepting parameters would only create
        # somewhere for input to go.
        path = self.path.split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"

        if path == "/health":
            # Deliberately unauthenticated and deliberately EMPTY of detail.
            # It answers "something is listening" and nothing else — enough for
            # the flasher to know it found the right port, useless to anyone
            # else. It does not confirm the hostname, the stage, or that this
            # is even mid-install.
            self._json(200, {"ok": True})
            return

        if path != "/progress":
            self._json(404, {"error": "not found"})
            return

        if not self._authorised():
            self._json(403, {"error": "not authorised"})
            return

        self._json(200, _progress())

    # Every other verb, including HEAD, POST, PUT and DELETE, falls through to
    # BaseHTTPRequestHandler's 501. Read-only is enforced by not implementing
    # anything else, rather than by checking a method name and hoping.


def _lan_address() -> str:
    """The address on the local network, so the socket is not bound wider.

    Binding 0.0.0.0 would also expose this on the tailnet once phase 50 joins
    it, which is outside what this was reviewed for. The UDP connect() never
    sends a packet; it just asks the routing table which source address would
    be used to reach off-link.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))       # TEST-NET-1, RFC 5737: never routed
        return s.getsockname()[0]
    except OSError:
        return "0.0.0.0"
    finally:
        s.close()


def main() -> int:
    key = _load_key()
    if not key:
        # FAIL CLOSED AND SAY SO. Serving progress to anyone who asks would be
        # the easy failure mode here, and it is the one that matters.
        print("sambuca-beacon: no pairing key; refusing to start", file=sys.stderr)
        return 1

    Handler.key = key
    bind = os.environ.get("SAMBUCA_BEACON_BIND") or _lan_address()

    # REFUSE IF SOMETHING IS ALREADY THERE, checked by connecting rather than
    # by trusting bind() to fail.
    #
    # This cost real time to learn. ThreadingHTTPServer sets allow_reuse_address,
    # which on Windows behaves like SO_REUSEPORT: a second process binds the
    # same port QUITE HAPPILY and connections go to whichever the kernel feels
    # like. A stale beacon from an earlier boot then answers with an old key and
    # an old progress file, and nothing anywhere reports a conflict — the new
    # one prints "listening" and is simply never spoken to.
    #
    # On Linux bind() would fail properly, but a beacon that behaves differently
    # on the platform it is developed on is a beacon whose failure mode is
    # discovered late.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.5)
    try:
        if probe.connect_ex(("127.0.0.1" if bind == "0.0.0.0" else bind, PORT)) == 0:
            print(f"sambuca-beacon: port {PORT} is already in use; refusing to "
                  f"start a second one", file=sys.stderr)
            return 1
    finally:
        probe.close()

    try:
        httpd = ThreadingHTTPServer((bind, PORT), Handler)
    except OSError as exc:
        print(f"sambuca-beacon: cannot listen on {bind}:{PORT}: {exc}",
              file=sys.stderr)
        return 1
    httpd.daemon_threads = True
    # A slow or dead client must not wedge the one thing an owner is watching.
    httpd.timeout = 10
    print(f"sambuca-beacon: listening on {bind}:{PORT}", flush=True)

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
