# Working on Corefold

How to run, build and test Corefold. For what the app does, see the
[README](README.md).

## Running it from the source
Double-click **`run-from-source.bat`** (needs Python 3.12). It installs what's in
`requirements.txt` and starts Corefold from the code as it is, without building anything.

## Building it on your own PC
Double-click **`build.bat`**. It downloads everything it needs (installing Python and Inno Setup
for you if they're missing), builds the installer, then asks **"Install Corefold on this PC now?"**.
Press **Y** to install it, or **N** to open the folder with the installer to share it.

## Releasing it
How to build a release and publish it is in [RELEASING.md](RELEASING.md). Every installed copy
updates itself from the releases of `SayedLabs/corefold` (see [Updates](README.md#updates)).

## Checking it on a real Windows PC
These run on one of GitHub's Windows PCs. Start them from the **Actions** tab → pick one → **Run workflow**.
- **Screenshots** takes a picture of every screen in English and Arabic, dark and light, clicks through
  the window like a person and tries the quick menu and wheel with real keys. The pictures are on the
  run's page and on the `screenshots` branch.
- **README pictures** makes the pictures in `docs/screenshots/` that the README and the website show
  (`scripts/readme_shots.py`). Run it after the look changes, and copy the new pictures in.
- **Bug hunt** runs every test on a clean Windows PC and lists what went wrong.
- **Measure memory and CPU**: see below.

## How light is it?
**Actions → Measure memory and CPU → Run workflow** builds Corefold on one of GitHub's Windows
PCs, starts it in the tray and writes down its memory, CPU use and start-up time on the run's
summary page. Nothing is published.

## Good to know before you share it
- **"Windows protected your PC" warning:** new apps without a code-signing certificate show this.
  People click *More info → Run anyway*. To remove it, sign the app (e.g. Microsoft
  *Trusted Signing* or a code-signing certificate from a certificate authority).
- **Antivirus:** apps packaged with PyInstaller are occasionally flagged by mistake. If that
  happens, submit the file as a false positive (Microsoft: https://www.microsoft.com/wdsi/filesubmission).
- **Licences:** Corefold is used under its licence agreement (`EULA.txt`, shown by the installer). It
  includes FFmpeg (as a separate program) and other third-party parts listed in
  `THIRD_PARTY_NOTICES.md`; keep that file included. Every release gets FFmpeg's source attached
  automatically; if the build warns that it couldn't get it, don't publish that build.
- **Privacy:** `docs/privacy.html` is the privacy policy (the same text is in `PRIVACY.md`), linked
  from the website, from *About* and from the Cloud page in the app. When something new goes online,
  update both files and the date.

## Updating from versions before 1.7.0
Version 1.7.0 gave the app its new name and look. Installing it over an older version upgrades it
(same installer ID), and the first start moves the old settings, history and uninstall backups into
the Corefold folders and replaces the old right-click entries. Nothing is deleted that couldn't be
moved. That code stays as long as releases also carry the copy for the old updater (see
[RELEASING.md](RELEASING.md)).

## Project layout
```
main.py                        entry point
corefold/app.py                tray icon, Shift-drag detection, jobs, startup
corefold/window.py             the Corefold window: top bar, sidebar, every page and the Settings list
corefold/tools.py              every tool in one list (sidebar, Home, Settings tabs and the Store are made from it)
corefold/store.py              the Store: add or remove tools, and Coming soon
corefold/navbar.py             the sidebar (drag to reorder, icons only)
corefold/atomic/               Atomic, the design system: colours, spacing, motion, shared widgets, Ctrl + K search
corefold/quick.py              the quick menu and quick wheel (quickmenu.py, quickwheel.py, quickpage.py)
corefold/viewer.py             Corefold Viewer (mailview.py opens .msg and .eml)
corefold/player.py             Corefold Player
corefold/capture.py            Corefold Capture (capview.py is the editor)
corefold/spacepage.py          Corefold Space (space.py does the scanning and cleaning)
corefold/clipboard.py          Corefold Clipboard (clipview.py is its window)
corefold/cloud.py              Corefold Cloud (cloudpage.py), waiting for its app keys
corefold/wheel.py              the format wheel and its preview
corefold/theme.py              light/dark themes and accent colours (from the Corefold design system)
corefold/icons.py              line icons in any colour (icondata.py: Fluent UI System Icons)
corefold/toast.py              progress / result popup
corefold/settings.py           saved settings, start with Windows, bringing over settings from older versions
corefold/converters.py         all the conversions (Pillow, FFmpeg, pypdf, PDFium, zxing, ONNX Runtime)
corefold/core.py               shared building blocks (app name, file types, jobs, output names)
corefold/archives.py           ZIP / RAR / 7z: extract, browse, test, create (7-Zip + Python fallback)
corefold/extras.py             make it fit, copy text (OCR), watermark, frames, slideshow, scan
corefold/browser.py            the archive window
corefold/panel.py              the Home page drop area with every option as buttons
corefold/sounds.py             sounds at the chosen volume
corefold/dialogs.py            resize / rotate / trim / fit / watermark / archive / password windows
corefold/history.py            recent conversions and stats
corefold/shellmenu.py          the right-click menu in File Explorer
corefold/ipc.py                hands right-click requests to the running app without loading it again
corefold/i18n.py               languages; lang_ar.py, lang_fr.py, lang_es.py, lang_tr.py have the translations
corefold/updater.py            self-updates from GitHub Releases (checksum-verified)
corefold/uninstall.py          the Apps page: uninstalling and leftovers, with backups
scripts/check_translations.py  checks that every text has a translation
scripts/screenshots.py         pictures of every screen; readme_shots.py makes the README's pictures
scripts/selftest.py            tests the conversions and archives (uitest.py, navtest.py, quicktest.py: the window)
assets/                        icon, installer pictures and sounds (scripts/make_icon.py, make_sounds.py)
scripts/build.ps1              builds the app, installer and zip
installer/corefold.iss         Inno Setup installer script
.github/workflows/             automatic builds on GitHub, and the memory/CPU measurement
docs/                          the download website and privacy policy (GitHub Pages)
run-from-source.bat            run your changes without building (needs Python)
```
Settings are stored in `%APPDATA%\Corefold`, logs in `%LOCALAPPDATA%\Corefold`.
