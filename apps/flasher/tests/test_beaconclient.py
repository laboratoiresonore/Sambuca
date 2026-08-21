"""The watch client, driven against a real beacon process.

WHY A REAL BEACON RATHER THAN A MOCK. This client exists to talk to one
specific server, and the things that go wrong between them are protocol
details: a 403 that means "wrong install" versus a timeout that means "not
booted yet", a header that has to arrive spelled exactly right. A mocked
urlopen would confirm the code I wrote matches the code I wrote.

THE DISTINCTION THIS FILE IS REALLY ABOUT. Almost every failure here is normal
and temporary — the appliance is mid-boot, its name does not resolve yet, the
network is not settled. Exactly one is not: a beacon that ANSWERS and refuses
the key. Treating that as "keep waiting" would leave somebody watching a
progress bar that will never move, for an appliance that is running perfectly,
because their watch file belongs to a different install.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from sambuca_flasher import beaconclient  # noqa: E402

BEACON = (pathlib.Path(__file__).resolve().parents[3]
          / "engine" / "beacon" / "sambuca-beacon.py")
KEY = "pair-0123456789abcdef0123"

PROGRESS = {
    "schema": 1, "state": "running", "step": 3, "steps_total": 10,
    "title": "Installing the container engine",
    "what": "Downloading and configuring Docker.",
    "how_long": "2 to 5 minutes", "your_move": "Nothing.",
    "next": "Connecting to the network",
}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def beacon(tmp_path, monkeypatch):
    """A real beacon on a free port, with the client pointed at it.

    A FREE PORT, NOT THE REAL ONE, and that is not fussiness. Four orphaned
    http.server processes from an earlier session were still holding the
    beacon's default port, and because ThreadingHTTPServer sets
    allow_reuse_address the second bind SUCCEEDED on Windows — connections went
    to whichever the kernel picked. The test failed against a twelve-hour-old
    stranger while looking like a bug in this code.
    """
    if not BEACON.is_file():
        pytest.skip("beacon script not present")

    progress = tmp_path / "progress.json"
    progress.write_text(json.dumps(PROGRESS), encoding="utf-8")
    keyfile = tmp_path / "beacon.key"
    keyfile.write_text(KEY, encoding="utf-8")

    port = _free_port()
    monkeypatch.setattr(beaconclient, "PORT", port)

    env = {**os.environ,
           "SAMBUCA_PROGRESS": str(progress),
           "SAMBUCA_BEACON_KEY": str(keyfile),
           "SAMBUCA_BEACON_BIND": "127.0.0.1",
           "SAMBUCA_BEACON_PORT": str(port)}
    proc = subprocess.Popen([sys.executable, str(BEACON)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"beacon died: {proc.stderr.read()[:300]!r}")
        if beaconclient.probe("127.0.0.1", KEY, timeout=0.5) is not None:
            break
        time.sleep(0.2)
    else:
        # KILL FIRST, THEN READ. `proc.stderr.read()` blocks until EOF, so on a
        # live process it hangs instead of reporting. This branch used to kill
        # the beacon and then say only "never answered", throwing away the one
        # thing that could explain why — which on macOS, where this fires every
        # single run, is the whole diagnosis.
        proc.kill()
        try:
            err = proc.communicate(timeout=5)[1] or b""
        except subprocess.TimeoutExpired:       # pragma: no cover
            proc.kill()
            err = proc.communicate()[1] or b""
        pytest.fail(f"beacon never answered; it said: {err[:400]!r}")

    try:
        yield port, progress
    finally:
        # KILL WHAT YOU SPAWN. The orphans above are what this line prevents.
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


class TestProbe:
    def test_the_right_key_returns_the_stage(self, beacon):
        doc = beaconclient.probe("127.0.0.1", KEY)
        assert doc["title"] == "Installing the container engine"
        assert doc["step"] == 3

    def test_a_refused_key_raises_rather_than_looking_like_silence(self, beacon):
        """THE ONE FAILURE THAT MUST NOT BE PATIENT.

        Something IS listening and does not accept this key: a different
        appliance, or a watch file from a previous install. Retrying quietly
        would be indistinguishable from "not booted yet" and would leave
        somebody waiting on a machine that is running perfectly.
        """
        with pytest.raises(PermissionError) as exc:
            beaconclient.probe("127.0.0.1", "not-the-right-key")
        assert "different install" in str(exc.value)

    def test_nothing_listening_is_not_an_error(self):
        """The ordinary case for the first minute or two of a boot."""
        assert beaconclient.probe("127.0.0.1", KEY, timeout=1.0) is None


class TestCandidates:
    def test_a_bare_hostname_also_tries_mdns(self):
        """mDNS is how a home network finds a machine with no DNS entry."""
        assert "sambuca.local" in beaconclient._candidates("sambuca")

    def test_a_local_name_also_tries_it_bare(self):
        """mDNS is unreliable on exactly the networks that need it most — some
        routers filter multicast outright — so the bare name is tried too."""
        assert "sambuca" in beaconclient._candidates("sambuca.local")

    def test_a_trailing_dot_does_not_produce_a_double_dot(self):
        for c in beaconclient._candidates("sambuca.local."):
            assert ".." not in c
            assert not c.endswith(".")


class TestFollow:
    def test_once_prints_the_stage_and_exits_clean(self, beacon):
        lines = []
        rc = beaconclient.follow("127.0.0.1", KEY, say=lines.append, once=True)
        assert rc == 0
        joined = "\n".join(str(x) for x in lines)
        assert "Installing the container engine" in joined
        assert "3 of 10" in joined

    def test_a_finished_install_points_at_the_next_command(self, beacon):
        """The flow must not end by stopping. Provisioning finishing is exactly
        the moment somebody needs to be told what to run next."""
        port, progress = beacon
        progress.write_text(json.dumps({**PROGRESS, "state": "done"}), encoding="utf-8")
        lines = []
        rc = beaconclient.follow("127.0.0.1", KEY, say=lines.append, poll=0.1)
        assert rc == 0
        assert "handover" in "\n".join(str(x) for x in lines)

    def test_a_failed_stage_says_where_the_detail_lives(self, beacon):
        """A headless machine cannot explain itself. The card can."""
        port, progress = beacon
        progress.write_text(json.dumps({**PROGRESS, "state": "failed"}), encoding="utf-8")
        lines = []
        rc = beaconclient.follow("127.0.0.1", KEY, say=lines.append, poll=0.1)
        assert rc == 1
        assert "sambuca-firstboot.log" in "\n".join(str(x) for x in lines)

    def test_a_refused_key_stops_immediately_with_the_reason(self, beacon):
        lines = []
        rc = beaconclient.follow("127.0.0.1", "wrong", say=lines.append, poll=0.1)
        assert rc == 1
        assert "different install" in "\n".join(str(x) for x in lines)

    def test_it_gives_up_honestly_rather_than_waiting_forever(self):
        """Thirty minutes of nothing means it never booted or cannot reach this
        network, and the answer is on the card. Saying so beats a spinner."""
        lines = []
        rc = beaconclient.follow("127.0.0.1", KEY, say=lines.append,
                                 poll=0.01, patience=0.05)
        assert rc == 1
        assert "sambuca-firstboot.log" in "\n".join(str(x) for x in lines)
