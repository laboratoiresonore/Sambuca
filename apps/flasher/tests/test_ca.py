"""The certificate step, driven on every path it can take.

WHY THIS FILE IS CAREFUL. Installing a root certificate is the single most
privileged thing the flasher ever asks for on the owner's own computer, and the
failure modes are quiet ones: a captive portal returning a login page, a proxy
returning HTML, an appliance that has simply not issued its certificate yet.
Every one of those looks like "some bytes arrived" to a naive caller, and
handing any of them to a trust store would be the worst bug this project could
ship.
"""

from __future__ import annotations

import http.server
import pathlib
import ssl
import subprocess
import sys
import tempfile
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from sambuca_flasher import ca  # noqa: E402


@pytest.fixture(scope="module")
def appliance():
    """A real TLS server, because the point is the handshake.

    A mocked urlopen would prove the parsing and none of the behaviour that
    actually matters here — that fetching works against a certificate nothing
    trusts, which is the entire situation this code exists for.
    """
    d = pathlib.Path(tempfile.mkdtemp())
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048",
             "-keyout", str(d / "k.pem"), "-out", str(d / "c.pem"),
             "-days", "1", "-nodes", "-subj", "/CN=sambuca-test"],
            capture_output=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("openssl not available")

    mode = {"v": "cert"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path != "/ca.crt" or mode["v"] == "missing":
                self.send_error(404)
                return
            body = (b"<html>sign in</html>" if mode["v"] == "html"
                    else (d / "c.pem").read_bytes())
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(d / "c.pem", d / "k.pem")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"127.0.0.1:{srv.server_address[1]}", mode
    finally:
        srv.shutdown()


class TestFetch:
    def test_a_real_certificate_is_fetched_over_untrusted_tls(self, appliance):
        """The handshake CANNOT be verified here and that is not a bug.

        The thing being downloaded is the very thing that would verify it. That
        circularity is why the fingerprint is shown to the owner instead.
        """
        host, mode = appliance
        mode["v"] = "cert"
        cert = ca.fetch(host)
        assert cert is not None
        assert b"BEGIN CERTIFICATE" in cert.pem
        assert len(cert.sha256) == 64

    def test_not_yet_issued_is_distinct_from_unreachable(self, appliance):
        """Caddy writes its root on the FIRST TLS handshake.

        So a fetch that arrives before then finds the route present and the
        file absent. Collapsing that into "unreachable" would send somebody off
        to debug a network that is working perfectly.
        """
        host, mode = appliance
        mode["v"] = "missing"
        with pytest.raises(ca.NotReady):
            ca.fetch(host)

    def test_a_login_page_is_never_mistaken_for_a_certificate(self, appliance):
        """Captive portals and proxies answer 200 with HTML.

        Bytes arrived, the status was fine, and none of it is a certificate.
        Anything that is not one must never reach a trust store.
        """
        host, mode = appliance
        mode["v"] = "html"
        assert ca.fetch(host) is None

    def test_nothing_listening_is_refused_quietly(self):
        assert ca.fetch("127.0.0.1:1", timeout=2.0) is None


class TestConsent:
    def test_the_fingerprint_is_grouped_for_human_comparison(self):
        """It exists to be read aloud off one screen and checked against
        another. An unbroken 64-character string cannot be."""
        cert = ca.Certificate(pem=b"x", sha256="ab" * 32)
        assert " " in cert.fingerprint
        assert cert.fingerprint.replace(" ", "") == ("ab" * 32).upper()

    def test_the_install_is_scoped_to_this_user_not_the_machine(self):
        """One person deciding to trust their own appliance must not silently
        apply to everybody else who logs into that computer."""
        cmd = ca.install_command(pathlib.Path("x.crt"))
        if cmd[0] == "certutil":
            assert "-user" in cmd
            assert "-enterprise" not in cmd

    def test_removal_is_documented_alongside_installation(self):
        """A permission granted with no stated way to revoke it is not an
        informed one."""
        assert ca.removal_hint().strip()

    def test_the_explanation_admits_what_is_actually_being_granted(self):
        """It would be easy, and dishonest, to describe this as "trust your own
        appliance" and stop there. A trusted root can vouch for ANY name, and
        the person clicking yes is entitled to know that."""
        words = ca.explain().lower()
        assert "any" in words and "authority" in words
        assert "remove" in words
