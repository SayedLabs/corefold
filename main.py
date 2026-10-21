"""Entry point for Satsuma (used by PyInstaller and for running from source)."""
import sys

if sys.platform != "win32":
    sys.exit("Satsuma only runs on Windows.")

from satsuma.app import main

if __name__ == "__main__":
    sys.exit(main())
