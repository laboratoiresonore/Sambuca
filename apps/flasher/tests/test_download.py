"""The downloader, driven against a real HTTP server on every path.

WHY A REAL SERVER. The interesting failures here are all protocol behaviour —
a server that ignores a Range header, a truncated body, a resume that silently
restarts. A mocked urlopen would confirm the happy path and none of the things
that actually corrupt a 755 MB file, which is the only reason this module has
any complexity at all.
"""

from __future__ import annotations

import hashlib
import http.server
import pathlib
import sys
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from sambuca_flasher import download  # noqa: E402

BODY = bytes(range(256)) * 4000          # ~1 MB, deterministic
DIGEST = hashlib.sha256(BODY).hexdigest()


@pytest.fixture(scope="module")
def server():
    state = {"ignore_range": False, "serve": BODY, "bytes_sent": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = state["serve"]
            rng = self.headers.get("Range")
            if rng and not state["ignore_range"]:
                start = int(rng.split("=")[1].split("-")[0])
                chunk = body[start:]
                state["bytes_sent"] += len(chunk)
                self.send_response(206)
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                self.wfile.write(chunk)
                return
            state["bytes_sent"] += len(body)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/x.iso", state
    finally:
        srv.shutdown()


class TestVerification:
    def test_a_clean_download_lands_atomically(self, server, tmp_path):
        """The real name must appear only once the digest matches, so an
        interrupted download can never be mistaken for a finished one."""
        url, _ = server
        out = download.fetch(url, tmp_path / "a.iso",
                             sha256=DIGEST, expected_size=len(BODY))
        assert out.read_bytes() == BODY
        assert not (tmp_path / "a.iso.part").exists()

    def test_a_damaged_download_is_deleted_not_left_lying_around(
            self, server, tmp_path):
        """A corrupt installer that reaches a disk writer is a bricked machine.

        Leaving it on disk under its real name is worse than failing, because
        the next run would find it, believe it, and use it.
        """
        url, state = server
        state["serve"] = BODY[:-1] + b"\x00"
        try:
            with pytest.raises(download.DownloadError):
                download.fetch(url, tmp_path / "b.iso",
                               sha256=DIGEST, expected_size=len(BODY))
        finally:
            state["serve"] = BODY
        assert not (tmp_path / "b.iso").exists()
        assert not (tmp_path / "b.iso.part").exists()

    def test_an_existing_correct_file_is_reused_without_a_download(
            self, tmp_path):
        dest = tmp_path / "c.iso"
        dest.write_bytes(BODY)
        # An unroutable URL: if it tried to fetch, this would fail.
        out = download.fetch("http://127.0.0.1:1/nope", dest, sha256=DIGEST)
        assert out == dest

    def test_an_existing_wrong_file_is_refused_never_overwritten(
            self, server, tmp_path):
        """A file with the right NAME is not evidence of anything. It may be
        half-copied, or last year's release."""
        url, _ = server
        dest = tmp_path / "d.iso"
        dest.write_bytes(b"not the iso")
        with pytest.raises(download.DownloadError):
            download.fetch(url, dest, sha256=DIGEST)
        assert dest.read_bytes() == b"not the iso"


class TestResume:
    def test_a_resume_transfers_only_the_remainder(self, server, tmp_path):
        """The headline property, and one a correct output does NOT prove:
        restarting from scratch also produces a correct file. The only
        evidence that resume works is how much crossed the wire."""
        url, state = server
        (tmp_path / "e.iso.part").write_bytes(BODY[:800_000])
        state["bytes_sent"] = 0
        out = download.fetch(url, tmp_path / "e.iso",
                             sha256=DIGEST, expected_size=len(BODY))
        assert out.read_bytes() == BODY
        assert state["bytes_sent"] < len(BODY), "it restarted instead of resuming"

    def test_a_server_that_ignores_range_does_not_corrupt_the_file(
            self, server, tmp_path):
        """Some servers answer 200 with the WHOLE body regardless of Range.

        Appending that to what is already on disk produces a file of exactly
        plausible length and entirely wrong content — the digest would catch
        it, but only after wasting the whole transfer.
        """
        url, state = server
        state["ignore_range"] = True
        try:
            (tmp_path / "f.iso.part").write_bytes(BODY[:400_000])
            out = download.fetch(url, tmp_path / "f.iso",
                                 sha256=DIGEST, expected_size=len(BODY))
        finally:
            state["ignore_range"] = False
        assert out.read_bytes() == BODY

    def test_a_part_bigger_than_the_target_is_discarded(self, server, tmp_path):
        """That is not a resumable download, it is a different file wearing
        the same name."""
        url, _ = server
        (tmp_path / "g.iso.part").write_bytes(BODY + b"extra")
        out = download.fetch(url, tmp_path / "g.iso",
                             sha256=DIGEST, expected_size=len(BODY))
        assert out.read_bytes() == BODY


class TestRefusal:
    def test_it_will_not_start_what_cannot_finish(self, server, tmp_path):
        """Running out of disk at 700 MB wastes the download and leaves a
        confusing mess behind."""
        url, _ = server
        with pytest.raises(download.DownloadError, match="free space"):
            download.fetch(url, tmp_path / "h.iso",
                           sha256=DIGEST, expected_size=10 ** 15)

    def test_a_dead_host_fails_with_a_sentence_not_a_traceback(self, tmp_path):
        with pytest.raises(download.DownloadError) as exc:
            download.fetch("http://127.0.0.1:1/nope", tmp_path / "i.iso",
                           sha256=DIGEST, timeout=3.0)
        assert "carry on" in str(exc.value), "it must say the retry resumes"


class TestProgress:
    def test_the_line_reads_like_something_a_person_can_use(self):
        """Megabytes, not mebibytes: the number should match what their
        browser and file manager say, not what is technically tidier."""
        p = download.Progress(done=331_000_000, total=791_674_880, rate=4_100_000)
        line = p.human()
        assert "%" in line and "MB" in line and "left" in line
        assert "41" in line

    def test_it_does_not_divide_by_zero_before_anything_has_moved(self):
        p = download.Progress(done=0, total=0, rate=0.0)
        assert p.percent == 0.0
        assert p.eta_seconds == 0.0
        p.human()
