import sys

# Pillow opens EPS files (whatever their name says, "photo.jpg" too) by running Ghostscript on them, if
# it's on the PC: a booby-trapped file could then run code. Corefold never needs EPS, so Pillow never
# gets that part (it skips a part that can't be loaded).
sys.modules.setdefault("PIL.EpsImagePlugin", None)

try:  # written by scripts/build.ps1
    from ._version import UPDATE_REPO, __version__
except ImportError:  # running from source without a build
    __version__ = "dev"
    UPDATE_REPO = ""
