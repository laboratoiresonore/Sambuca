"""The last five minutes of an install, which decide what it was worth.

Handing somebody ten addresses and letting them find out that two are broken
wastes the trust the previous hour earned. These pin the two things most likely
to go quietly wrong: mistaking a broken service for a working one, and writing
a bookmark file no browser will import.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sambuca_flasher import handover
from sambuca_flasher.handover import Link


class TestStatusClassification:
    """Not all HTTP answers mean the same thing.

    REGRESSION. Every HTTPError was first treated as "behind sign-in —
    expected", so a 404 from a misconfigured route reported as a WORKING
    service. Found by probing a real 404 rather than by reading the branch.
    """

    def _fake(self, code):
        """A checker that raises the HTTPError we want, without a network."""
        import urllib.error

        def raiser(*_a, **_k):
            raise urllib.error.HTTPError("u", code, "m", {}, None)

        return raiser

    @pytest.mark.parametrize(("code", "reachable", "because"), [
        (401, True, "a service behind the auth gate is WORKING, by design"),
        (403, True, "same — oauth2-proxy answers this way on purpose"),
        (404, False, "reachable, but nothing is served there — a real fault"),
        (500, False, "the service answered with an error"),
        (503, False, "same"),
    ])
    def test_codes(self, monkeypatch, code, reachable, because):
        monkeypatch.setattr(handover.urllib.request, "urlopen", self._fake(code))
        result = handover.check(Link("x", "https://example.invalid", ""))
        assert result.reachable is reachable, because

    def test_a_name_that_does_not_resolve_says_so(self, monkeypatch):
        import socket
        import urllib.error

        def raiser(*_a, **_k):
            raise urllib.error.URLError(socket.gaierror("nope"))

        monkeypatch.setattr(handover.urllib.request, "urlopen", raiser)
        r = handover.check(Link("x", "https://nothing.invalid", ""))
        assert r.reachable is False
        assert "resolve" in r.detail, (
            "a name that does not resolve is a different problem from a service "
            "that is down, and the owner fixes them in different places"
        )


class TestBookmarkExport:
    """Netscape format, because every browser imports it and nothing else is
    universal. A file no browser accepts is worse than no file."""

    def _links(self):
        return [
            Link("Photos", "https://photos.sambuca.local", "instead of Google Photos",
                 reachable=True),
            Link("Notes", "https://notes.sambuca.local", "instead of Notion",
                 reachable=False, detail="no answer"),
        ]

    def test_structure_is_what_browsers_expect(self, tmp_path: Path):
        p = handover.write_bookmarks(self._links(), tmp_path / "b.html")
        t = p.read_text(encoding="utf-8")
        assert t.startswith("<!DOCTYPE NETSCAPE-Bookmark-file-1>")
        assert t.count("<DL><p>") == t.count("</DL><p>"), "unbalanced lists"
        assert t.count("<DT><A HREF=") == 2

    def test_unreachable_links_are_kept_but_marked(self, tmp_path: Path):
        """Dropping them would decide for the owner that a service they care
        about does not exist — and a service merely slow to start would vanish
        from their bookmarks permanently."""
        p = handover.write_bookmarks(self._links(), tmp_path / "b.html")
        t = p.read_text(encoding="utf-8")
        assert "photos.sambuca.local" in t
        assert "notes.sambuca.local" in t, "an unreachable link must still be saved"
        assert t.count("[not reachable yet]") == 1

    def test_html_is_escaped(self, tmp_path: Path):
        """A service name is not trusted input just because we generated it."""
        evil = [Link('A<script>x</script>', "https://x.local/?a=1&b=2", "a & b")]
        p = handover.write_bookmarks(evil, tmp_path / "b.html")
        t = p.read_text(encoding="utf-8")
        assert "<script>" not in t
        assert "&amp;" in t


class TestLinkList:
    def test_lan_and_tailnet_are_distinguished(self):
        """People assume the first address they are given is THE address, then
        find it dead in a cafe. The tailnet entry has to say it differs."""
        links = handover.appliance_links("sambuca.local", tailnet_name="s.tail1.ts.net")
        away = [x for x in links if "tail1" in x.url]
        assert away, "the tailnet address must be offered when there is one"
        assert "anywhere" in away[0].name.lower() or "anywhere" in away[0].what.lower()

    def test_no_tailnet_means_no_false_promise(self):
        links = handover.appliance_links("sambuca.local")
        assert all("ts.net" not in x.url for x in links)

    def test_the_address_people_are_told_to_start_at_is_checked(self):
        """The handover ends by saying "start here" and naming the dashboard.

        For a while it named an address that was not in this list at all, so
        the one page every owner opens first was the one page nothing had
        verified. An unchecked recommendation is how somebody's first contact
        with their new appliance becomes a browser error.
        """
        links = handover.appliance_links("sambuca.local")
        apex = [x for x in links if x.url == "https://sambuca.local"]
        assert apex, "the dashboard the owner is sent to must be checked too"
        assert apex[0] is links[0], "the front door belongs first"

    def test_what_it_replaces_is_in_plain_words(self):
        """The audience does not know what Immich is."""
        links = handover.appliance_links("sambuca.local")
        for x in links:
            assert x.what, f"{x.name} does not say what it is for"
            assert "immich" not in x.what.lower()
            assert "vaultwarden" not in x.what.lower()
