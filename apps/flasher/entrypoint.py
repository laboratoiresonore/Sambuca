"""
PyInstaller entry point.

Freezing `__main__.py` directly does not work: it uses `from .cli import main`,
and a relative import has no parent package when the module IS the entry script.
The frozen binary builds cleanly and then dies with

    ImportError: attempted relative import with no known parent package

at the first thing a user does with it. This module exists solely to give
PyInstaller an absolute import to start from.

Caught by the build's "prove the binary actually runs" step, which is why that
step exists — a build that produces a file is not a build that produces a
working program.
"""

import sys

from sambuca_flasher.cli import main

if __name__ == "__main__":
    sys.exit(main())
