# Working on Corefold

How to run, build, test and release Corefold. For what the app does, see the
[README](README.md).

## Running it from the source
Double-click **`run-from-source.bat`** (needs Python 3.12). It installs what's in
`requirements.txt` and starts Corefold from the code as it is, without building anything.

## Building it on your own PC
Double-click **`build.bat`**. It downloads everything it needs (installing Python and Inno Setup
for you if they're missing), builds the installer, then asks **"Install Corefold on this PC now?"**.
Press **Y** to install it, or **N** to open the folder with the installer to share it.

Copies built with `build.bat` find their updates through a file called `update-repo.txt` next to
`build.bat` containing the repository, e.g. `SayedLabs/corefold`. Copies built by GitHub know it
automatically.

## Releasing it
1. Go to **Releases → Draft a new release**, type a higher tag (e.g. `v1.8.0`) and click **Publish**.
2. Wait about 10 minutes (watch the **Actions** tab). GitHub builds it and the release page gets:
   - `Corefold-Setup-1.8.0.exe`: the installer (creates the desktop icon)
   - `Corefold-1.8.0-portable.zip`: runs without installing
   - `ffmpeg-source-…`: FFmpeg's source code and build scripts (its licence asks for these)
3. Every installed copy of Corefold picks it up and updates itself (see [Updates](README.md#updates)).

## Website
The `docs` folder is a ready-made download page. To put it online for free: on GitHub open
**Settings → Pages**, under *Branch* choose **main** and **/docs**, click **Save**. A minute later it's
at `https://sayedlabs.github.io/corefold/` with a big download button that always points at the
newest release, plus an "Is it safe?" section that explains the Windows warning.

## Checking it on a real Windows PC
These run on one of GitHub's Windows PCs. Start them from the **Actions** tab → pick one → **Run workflow**.
- **Screenshots** takes a picture of every screen in English and Arabic, dark and light, clicks through
  the window like a person and tries the quick menu and wheel with real keys. The pictures are on the
  run's page and on the `screenshots` branch.
- **README pictures** makes the pictures in `docs/screenshots/` that the README and the website show
  (`scripts/readme_shots.py`). Run it after the look changes, and copy the new pictures in.
- **Bug hunt** runs every test on a clean Windows PC and lists what went wrong.
- **Measure memory and CPU**: see below.

## Corefold's FFmpeg
Corefold ships its own smaller FFmpeg (about 55 MB instead of 190 MB): FFmpeg itself plus only the
libraries Corefold uses (`scripts/ffmpeg-lean.sh` lists them). **Actions → Build Corefold's FFmpeg →
Run workflow** makes it and keeps it on the `ffmpeg-lean` branch, where every build takes it from.
Run it again to move to a newer FFmpeg (change the version in `scripts/ffmpeg-lean.sh` and the
image in `.github/workflows/ffmpeg-lean.yml` together), then run the **Bug hunt**. If Corefold
starts writing a new kind of file with FFmpeg, add its library there first. Without that branch,
the build falls back to BtbN's full FFmpeg.

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
- **Licences:** Corefold's code is MIT. It includes FFmpeg (GPL, as a separate program) and other
  open-source parts listed in `THIRD_PARTY_NOTICES.md`; keep that file included and the source public. Every release gets FFmpeg's source attached
  automatically; if the build warns that it couldn't get it, don't publish that build.
- **Privacy:** `docs/privacy.html` is the privacy policy (the same text is in `PRIVACY.md`), linked
  from the website, from *About* and from the Cloud page in the app. When something new goes online,
  update both files and the date.

## Updating from versions before 1.7.0
Version 1.7.0 gave the app its new name and look. Installing it over an older version upgrades it
(same installer ID), and the first start moves the old settings, history and uninstall backups into
the Corefold folders and replaces the old right-click entries. Nothing is deleted that couldn't be
moved.

Versions 1.5.3 to 1.6.0 update themselves to the newest Corefold: every release also carries a copy of
the installer under the old name, because that is the only file their updater looks for. Older
versions than that have to download Corefold by hand once.

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
corefold/pdftools.py           Corefold PDF: merge, split, compress, pictures, passwords (pdfpage.py: its page board)
corefold/organizer.py          Corefold File Organizer (organizerpage.py)
corefold/startup.py            Corefold Startup: switched on and off the way Task Manager does (startuppage.py)
corefold/scan.py               Corefold Scan: Defender's scans and its own checks (scanpage.py; trust.py reads signatures offline)
corefold/unlock.py             Corefold Unlock: which programs have a file open, and closing them (unlockpage.py)
corefold/awake.py              Corefold Awake (awakepage.py)
corefold/colors.py             Corefold Colors (colorspage.py)
corefold/peek.py               Quick look: Ctrl + Space on a file in File Explorer (peekview.py, peekshell.py)
corefold/quicksearch.py        search on the quick menu (quickfind.py, quicksys.py)
corefold/welcome.py            the first start: Choose your language
corefold/report.py             "Report a problem": a zip of the logs, and a filled-in GitHub issue
corefold/settings_backup.py    settings saved to a file and read back, and the copy kept before each update
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
