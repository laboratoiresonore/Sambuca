"""
sambuca :: refuse to fetch anything that is not http(s).

NOT LINT APPEASEMENT — A REAL HOLE, given how this project is designed.
Sambuca deliberately fetches everything live: image URLs, checksums, the
installer ISO, dependency links, all of it from a manifest on GitHub. That is
the right design, and it means REMOTE DATA DECIDES WHICH URLS THIS PROGRAM
OPENS.

urllib.request.urlopen honours `file:`. So a manifest that was tampered with —
a compromised repository, a hostile mirror, a proxy rewriting a response —
could name `file:///etc/shadow` or `file://C:/Users/.../id_rsa` and the
downloader would treat it as a perfectly ordinary fetch: read it, checksum it,
and drop it somewhere as an .iso. It would also silently satisfy an
"is it reachable?" check against the local filesystem.

The fix is to state what is allowed instead of guessing what is dangerous.
Two schemes, refused loudly otherwise, at the one place every fetch passes
through.
"""

from __future__ import annotations

from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})


class UnsafeURL(ValueError):
    """The URL asked for something this program will not do."""


def check(url: str) -> str:
    """Return the URL if it is safe to open, else raise UnsafeURL.

    Deliberately an ALLOWLIST. A blocklist of "file, ftp, data, gopher..."
    would need updating every time urllib grows a handler, and the failure
    mode of forgetting one is silent.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURL(
            f"refusing to open a {parts.scheme or 'schemeless'!r} URL; "
            f"only http and https are allowed")
    if not parts.netloc:
        # "http:///etc/passwd" parses with an empty host and would otherwise
        # sail through a scheme-only check.
        raise UnsafeURL("refusing to open a URL with no host")
    return url
