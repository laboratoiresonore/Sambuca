"""The install beacon, driven as an owner and as an attacker.

IT RUNS WITH NONE OF THE MACHINE'S DEFENCES UP — no firewall, no identity
provider, no reverse proxy, on a box that is mid-install. So these are not unit
tests of a JSON endpoint; they are the review of the one service exposed before
anything exists to protect it.

Every test drives a REAL process on a REAL socket. The interesting failures here
(a method that should not be implemented, a header compared the wrong way, a
field that leaks because it was forwarded rather than rebuilt) are all things a
mocked handler would happily pretend to get right.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

BEACON = (pathlib.Path(__file__).resolve().parents[1]
          / "engine" / "beacon" / "sambuca-beacon.py")
KEY = "test-pairing-key-0123456789abcdef"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _request(port: int, path: str, key: str | None = None,
             method: str = "GET", timeout: float = 5.0):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    if key is not None:
        req.add_header("X-Sambuca-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def _start(tmp_path, *, key_contents: str | None = KEY, progress: dict | None = None):
    """Launch a real beacon. Returns (proc, port, progress_path)."""
    progress_path = tmp_path / "progress.json"
    if progress is not None:
        progress_path.write_text(json.dumps(progress), encoding="utf-8")

    env = {**os.environ,
           "SAMBUCA_PROGRESS": str(progress_path),
           "SAMBUCA_BEACON_BIND": "127.0.0.1"}

    keyfile = tmp_path / "beacon.key"
    if key_contents is not None:
        keyfile.write_text(key_contents, encoding="utf-8")
    env["SAMBUCA_BEACON_KEY"] = str(keyfile)

    port = _free_port()
    env["SAMBUCA_BEACON_PORT"] = str(port)

    proc = subprocess.Popen([sys.executable, str(BEACON)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc, port, progress_path


def _wait_up(port: int, proc, deadline: float = 10.0) -> bool:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if proc.poll() is not None:
            return False
        try:
            _request(port, "/health", timeout=0.5)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    return False


@pytest.fixture
def beacon(tmp_path):
    proc, port, progress_path = _start(tmp_path, progress={
        "schema": 1, "state": "running", "step": 3, "steps_total": 10,
        "title": "Installing the container engine",
        "what": "Downloading and configuring Docker.",
        "how_long": "2 to 5 minutes", "your_move": "Nothing.",
        "next": "Connecting to the network",
    })
    if not _wait_up(port, proc):
        proc.kill()
        pytest.fail(f"beacon never came up: {proc.stderr.read()[:400]!r}")
    try:
        yield port, progress_path
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


class TestItFailsClosed:
    def test_no_pairing_key_means_it_refuses_to_start(self, tmp_path):
        """The easy failure here is serving progress to whoever asks.

        An unauthenticated beacon announces to every device on the network —
        a guest phone included — that this machine is mid-install and therefore
        in its least-defended state. Refusing to start is the only safe answer
        to a missing key.
        """
        proc, port, _ = _start(tmp_path, key_contents=None, progress={"schema": 1})
        try:
            assert proc.wait(timeout=10) != 0, "it started with no key"
            assert b"refusing to start" in proc.stderr.read()
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_an_empty_key_file_is_not_a_key(self, tmp_path):
        """A truncated or half-written key file must not become "any request
        with an empty header is authorised"."""
        proc, port, _ = _start(tmp_path, key_contents="   \n", progress={"schema": 1})
        try:
            assert proc.wait(timeout=10) != 0, "an empty key was accepted"
        finally:
            if proc.poll() is None:
                proc.kill()


class TestAuthorisation:
    def test_progress_without_a_key_is_refused(self, beacon):
        port, _ = beacon
        status, _ = _request(port, "/progress")
        assert status == 403

    def test_a_wrong_key_is_refused(self, beacon):
        port, _ = beacon
        status, _ = _request(port, "/progress", key="not-the-key")
        assert status == 403

    def test_a_key_that_is_a_prefix_of_the_real_one_is_refused(self, beacon):
        """The shape a timing attack or a truncated copy-paste produces."""
        port, _ = beacon
        status, _ = _request(port, "/progress", key=KEY[:-1])
        assert status == 403

    def test_the_right_key_is_accepted(self, beacon):
        port, _ = beacon
        status, body = _request(port, "/progress", key=KEY)
        assert status == 200
        assert body["title"] == "Installing the container engine"

    def test_an_absurdly_long_key_is_refused_not_processed(self, beacon):
        port, _ = beacon
        status, _ = _request(port, "/progress", key="x" * 10_000)
        assert status == 403


class TestItLeaksNothing:
    def test_a_field_added_later_is_not_published(self, beacon):
        """THE LEAK THIS DESIGN IS BUILT AGAINST.

        progress.json is written by shell in another file entirely. If a future
        stage adds a hostname, a path, or a log excerpt, forwarding the parsed
        document would publish it the moment it was written. The allowlist means
        a new field stays private until somebody deliberately adds it here.
        """
        port, progress_path = beacon
        doc = json.loads(progress_path.read_text(encoding="utf-8"))
        doc["admin_password"] = "hunter2"
        doc["log_tail"] = "/var/lib/sambuca/secret-path"
        progress_path.write_text(json.dumps(doc), encoding="utf-8")

        status, body = _request(port, "/progress", key=KEY)
        assert status == 200
        assert "admin_password" not in body
        assert "log_tail" not in body
        assert "hunter2" not in json.dumps(body)

    def test_health_confirms_only_that_something_listens(self, beacon):
        """Unauthenticated on purpose, so it must give away nothing: not the
        hostname, not the stage, not even that this is mid-install."""
        port, _ = beacon
        status, body = _request(port, "/health")
        assert status == 200
        assert body == {"ok": True}

    def test_it_does_not_advertise_its_python_version(self, beacon):
        """Free reconnaissance otherwise: the exact interpreter build on a
        machine with no firewall up yet."""
        port, _ = beacon
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=5) as r:
            server = r.headers.get("Server", "")
        assert "Python" not in server


class TestItHasNoControlSurface:
    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
    def test_write_methods_are_not_implemented(self, beacon, method):
        """A provisioning-time endpoint that accepts commands is a remote
        execution hole at the worst possible moment. Read-only is enforced by
        not implementing anything else, not by checking a method name."""
        port, _ = beacon
        status, _ = _request(port, "/progress", key=KEY, method=method)
        assert status in (405, 501)

    @pytest.mark.parametrize("path", [
        "/../../etc/passwd",
        "/progress/../../../etc/shadow",
        "/setup/index.html",
        "/var/lib/sambuca/beacon.key",
    ])
    def test_there_is_nothing_to_traverse_to(self, beacon, path):
        """Not a mitigated traversal — an ABSENT one. Two fixed routes emit
        computed JSON; no path is ever joined to user input."""
        port, _ = beacon
        status, _ = _request(port, path, key=KEY)
        assert status == 404

    def test_query_strings_are_discarded_not_parsed(self, beacon):
        port, _ = beacon
        status, body = _request(port, "/progress?file=/etc/passwd", key=KEY)
        assert status == 200
        assert "passwd" not in json.dumps(body)


class TestBeforeAnythingHasHappened:
    def test_a_missing_progress_file_reports_starting_not_an_error(self, tmp_path):
        """Before the first stage writes, there is genuinely nothing to report.

        A 500 would tell an owner something is wrong at the exact moment
        nothing is — and this whole service exists to stop them concluding
        that and pulling the power.
        """
        proc, port, _ = _start(tmp_path, progress=None)
        try:
            assert _wait_up(port, proc), proc.stderr.read()[:400]
            status, body = _request(port, "/progress", key=KEY)
            assert status == 200
            assert body["state"] == "starting"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def test_a_corrupt_progress_file_does_not_crash_it(self, tmp_path):
        """It is written by shell, mid-boot, and could be caught half-written."""
        proc, port, progress_path = _start(tmp_path, progress={"schema": 1})
        try:
            assert _wait_up(port, proc), proc.stderr.read()[:400]
            progress_path.write_text("{ this is not json", encoding="utf-8")
            status, body = _request(port, "/progress", key=KEY)
            assert status == 200
            assert body["state"] == "starting"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
