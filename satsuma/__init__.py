try:  # written by scripts/build.ps1
    from ._version import UPDATE_REPO, __version__
except ImportError:  # running from source without a build
    __version__ = "dev"
    UPDATE_REPO = ""
