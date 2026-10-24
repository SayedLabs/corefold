"""The apps Corefold Cloud signs in as, registered with Microsoft and Dropbox. These are public ids
for a desktop app (not secrets: a desktop app can't keep a secret, so sign-in uses PKCE instead).
Empty: signing in to that service online isn't offered in this copy. Corefold Cloud still works without them,
with the OneDrive, Dropbox, Google Drive and iCloud folders their own apps keep on the PC (cloudlocal.py)."""
import os

ONEDRIVE_CLIENT_ID = os.environ.get("COREFOLD_ONEDRIVE_ID", "")
DROPBOX_APP_KEY = os.environ.get("COREFOLD_DROPBOX_KEY", "")


def any_set() -> bool:
    return bool(ONEDRIVE_CLIENT_ID or DROPBOX_APP_KEY)
