"""
sambuca :: the last five minutes, which decide what the whole thing was worth.

An install that ends with a wall of addresses has not finished. The owner is
left to copy ten URLs onto a phone by hand, discover which two do not work, and
guess which one to use from a café. That is the moment the project is judged on,
and it was the emptiest phase in it.

THREE THINGS, in order of how much they matter:

  1. VERIFY EVERY LINK BEFORE SHOWING IT. Handing somebody ten addresses and
     letting them find out that two are broken wastes the trust the previous
     hour earned. A link that is not reachable is reported as not reachable,
     with what to do about it.

  2. EXPORT BOOKMARKS IN ONE CLICK. Netscape bookmark format — the one every
     browser on earth imports, including Safari and mobile. Nobody should type
     `https://photos.sambuca.local` into a phone.

  3. SAY WHICH ADDRESS IS FOR WHERE. The LAN name works at home and nowhere
     else; the tailnet name works everywhere including home. People reasonably
     assume the first one is "the address" and then find it dead in a café.

WHAT THIS DOES NOT DO. It does not pretend a service is up because a container
was started. Reachability is checked over the network the owner will actually
use, and anything unverified says so.
"""

from __future__ import annotations

import html
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Short: this runs while somebody watches, and a slow service is a finding in
# its own right rather than something to wait patiently for.
_TIMEOUT = 4.0


@dataclass
class Link:
    name: str
    url: str
    what: str                 # what it replaces, in plain words
    reachable: bool | None = None   # None = not yet checked
    detail: str = ""

    @property
    def status(self) -> str:
        if self.reachable is None:
            return "not checked"
        return "ok" if self.reachable else (self.detail or "unreachable")


def appliance_links(domain: str, *, tailnet_name: str = "") -> list[Link]:
    """Every address the owner is about to be given.

    Ordered by what people actually open first, not by what the stack considers
    important. Files and photos before identity providers.
    """
    base = domain.rstrip(".")
    links = [
        # THE FRONT DOOR GOES FIRST, and it belongs in this list at all because
        # the handover tells people to start here. An address we hand somebody
        # while never checking it is the one most likely to greet them with a
        # browser error, and it is the first thing they will ever open.
        Link("Dashboard", f"https://{base}", "the front door - start here"),
        Link("Files, calendar and contacts", f"https://cloud.{base}",
             "instead of Google Drive"),
        Link("Photos", f"https://photos.{base}", "instead of Google Photos"),
        Link("Passwords", f"https://vault.{base}", "instead of 1Password"),
        Link("Chat with the AI", f"https://chat.{base}", "your private assistant"),
        Link("Notes", f"https://notes.{base}", "instead of Notion"),
        Link("PDF tools", f"https://pdf.{base}", "instead of Acrobat online"),
        Link("Health of the machine", f"https://status.{base}",
             "what is running, and what is not"),
        Link("Sign-in", f"https://id.{base}", "one account for all of the above"),
    ]
    if tailnet_name:
        links.append(
            Link("Same machine, from anywhere", f"https://{tailnet_name}",
                 "works away from home; the addresses above do not")
        )
    return links


def check(link: Link, *, timeout: float = _TIMEOUT) -> Link:
    """Is it actually reachable?

    A self-signed certificate is EXPECTED here — the appliance runs its own
    certificate authority — so certificate errors are not treated as failure.
    What matters is whether something answered at all. Reporting "certificate
    error" to somebody who has not installed the CA yet would be technically
    true and completely unhelpful.
    """
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        req = urllib.request.Request(link.url, method="GET",
                                     headers={"User-Agent": "sambuca-handover"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            # Any HTTP answer means the service is up. A 401 or a redirect to
            # the sign-in page is a WORKING service behind an auth gate, which
            # is exactly how it is supposed to behave.
            link.reachable = True
            link.detail = f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        # NOT ALL HTTP ERRORS ARE EQUAL, and treating them alike hides real
        # faults. Found by probing a 404 and watching it reported as "behind
        # sign-in — expected", which would have quietly passed a service whose
        # route is misconfigured.
        if exc.code in (401, 403):
            # A working service behind the auth gate, behaving exactly as
            # designed. oauth2-proxy answers this way by intent.
            link.reachable = True
            link.detail = f"HTTP {exc.code} — behind sign-in, as expected"
        elif exc.code == 404:
            link.reachable = False
            link.detail = "HTTP 404 — reachable, but nothing is served there"
        elif 500 <= exc.code < 600:
            link.reachable = False
            link.detail = f"HTTP {exc.code} — the service answered with an error"
        else:
            link.reachable = True
            link.detail = f"HTTP {exc.code}"
    except (socket.timeout, TimeoutError):
        link.reachable = False
        link.detail = "no answer (still starting?)"
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.gaierror):
            link.reachable = False
            link.detail = "name does not resolve"
        else:
            link.reachable = False
            link.detail = str(reason)[:60]
    return link


def check_all(links: list[Link], *, timeout: float = _TIMEOUT) -> list[Link]:
    return [check(x, timeout=timeout) for x in links]


def write_bookmarks(links: list[Link], path: Path, *, title: str = "Sambuca") -> Path:
    """Netscape bookmark file — the format every browser imports.

    Chosen because it is universal and boring: Chrome, Firefox, Edge and Safari
    all take it, and it needs no extension, no account and no sync service.
    Somebody should not be typing `https://photos.sambuca.local` into a phone.

    Unreachable links are still included, marked. Leaving them out would be
    quietly deciding for the owner that a service they paid attention to does
    not exist; a service that is merely slow to start would vanish from their
    bookmarks forever.
    """
    path = Path(path)
    rows = []
    for x in links:
        mark = "" if x.reachable is not False else " [not reachable yet]"
        rows.append(
            f'        <DT><A HREF="{html.escape(x.url, quote=True)}">'
            f"{html.escape(x.name)}{mark}</A>\n"
            f"        <DD>{html.escape(x.what)}\n"
        )

    doc = (
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n"
        "<!-- Written by sambuca. Import this into any browser. -->\n"
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">\n'
        "<TITLE>Bookmarks</TITLE>\n"
        "<H1>Bookmarks</H1>\n"
        "<DL><p>\n"
        f"    <DT><H3>{html.escape(title)}</H3>\n"
        "    <DL><p>\n"
        + "".join(rows) +
        "    </DL><p>\n"
        "</DL><p>\n"
    )
    path.write_text(doc, encoding="utf-8", newline="\n")
    return path
