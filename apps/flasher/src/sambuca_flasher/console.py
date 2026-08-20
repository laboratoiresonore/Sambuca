"""
sambuca :: safe console output.

The flasher runs on Windows, whose default console codepage renders an em-dash
as a replacement glyph. Mojibake on the screens a stuck novice is reading — the
boot guide, the hardware estimate — is not cosmetic; it is the difference
between instructions that look authoritative and instructions that look broken.

Sanitising at the RENDER layer rather than in the data means a contributor
typing a typographic quote cannot reintroduce it. Lives here rather than in one
command's module because two commands needed it, and a second copy is a copy
that drifts.
"""

from __future__ import annotations

_ASCII_MAP = {
    "—": "-",      # em dash
    "–": "-",      # en dash
    "‘": "'",      # left single quote
    "’": "'",      # right single quote
    "“": '"',      # left double quote
    "”": '"',      # right double quote
    "…": "...",    # ellipsis
    "→": "->",     # right arrow
    "⌥": "Option", # mac option key
    "·": "-",      # middle dot
    " ": " ",      # non-breaking space
    "×": "x",      # multiplication sign
    "✓": "ok",     # check mark
    "£": "GBP ",   # pound sign
    "€": "EUR ",   # euro sign
}


def ascii_safe(text: str) -> str:
    """Reduce text to plain ASCII, mapping the typography we actually use.

    Anything still outside ASCII is replaced rather than raising — a
    UnicodeEncodeError would abort the whole guide, and a guide with one odd
    character in it is far better than no guide at all.
    """
    for bad, good in _ASCII_MAP.items():
        text = text.replace(bad, good)
    return text.encode("ascii", "replace").decode("ascii")
