"""Entrypoint for `python -m sambuca_flasher`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
