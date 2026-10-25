"""The apps Corefold Cloud signs in as, registered with Google, Microsoft and Dropbox. These are public ids
for a desktop app (not secrets: a desktop app can't keep a secret, so sign-in uses PKCE instead).
Google also gives a desktop app a "client secret", and asks for it when signing in, but says itself that for an
app installed on a PC it isn't treated as a secret: anyone can read it in the app, and it opens nothing on its own
(each person still signs in on Google's own page).
Empty: signing in to that service online isn't offered in this copy. Corefold Cloud still works without them,
with the OneDrive, Dropbox, Google Drive and iCloud folders their own apps keep on the PC (cloudlocal.py)."""
import os

GOOGLE_CLIENT_ID = os.environ.get("COREFOLD_GOOGLE_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("COREFOLD_GOOGLE_SECRET", "")
ONEDRIVE_CLIENT_ID = os.environ.get("COREFOLD_ONEDRIVE_ID", "")
DROPBOX_APP_KEY = os.environ.get("COREFOLD_DROPBOX_KEY", "")


def any_set() -> bool:
    return bool(GOOGLE_CLIENT_ID or ONEDRIVE_CLIENT_ID or DROPBOX_APP_KEY)
