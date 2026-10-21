# Satsuma

**Shift-drag file converter for Windows.** Hold Shift while dragging a file in File Explorer or on
the desktop, and a wheel of formats appears at your cursor. Drop the file on a format and it's
converted. Everything happens on your PC; nothing is uploaded.

| Drop a… | You can make |
|---|---|
| Image (PNG, JPG, WebP, HEIC, AVIF, GIF, BMP, TIFF, ICO) | PNG, JPG, WebP, PDF, ICO, BMP, **resize**, **rotate / flip**, **cut out (remove the background)**, a smaller copy for sharing, read a QR code, remove location & camera info |
| Audio (MP3, M4A, WAV, FLAC, OGG, Opus, AIFF, WMA, AAC) | MP3, M4A, WAV, FLAC, OGG, Opus, AIFF, **trim** |
| Video (MP4, MOV, MKV, WebM, AVI, WMV…) | MP4, MOV, WebM, MKV, GIF, **trim**, **rotate / flip**, a smaller copy, MP3 sound |
| PDF | **Compress**, split into pages, pages to images, extract text, merge several PDFs |
| Word, Excel, PowerPoint (DOCX, XLSX, PPTX, ODT…) | PDF (uses Microsoft Office or LibreOffice if installed) |

Formats that don't work for a file (for example *MP3* on a video without sound) show up **dimmed**,
and pointing at one tells you why. Prefer a tidier wheel? Choose **Hide them** in Settings.

Other ways to use it: **right-click** a file → *Convert with Satsuma* (Windows 11: *Show more options*),
or drag a file onto the box on Satsuma's Home page.

Drop several files at once to convert them all (several images on **PDF** makes one combined PDF).
Turn on *ask for a new name* to get `vacation-1.jpg`, `vacation-2.jpg`… The original file is never
moved or changed.

## Installing
Run **Satsuma-Setup.exe**, keep the two boxes ticked, click **Install**. That's it: a desktop
icon, a Start menu entry, and Satsuma starts with Windows. No admin rights or Python needed.
Installing a newer version on top upgrades it and keeps your settings.

## Customizing
Open Satsuma from the desktop icon (or click the orange tray icon):
- **General:** which key to hold (Shift, Ctrl + Shift or Alt), where new files go, image quality,
  a sound when done, dim or hide formats that don't fit, new names for batches, right-click menu,
  progress popups, start with Windows.
- **Look:** match Windows / dark / light, six accent colours or your own, wheel shape (classic,
  petals, slim), size and see-through, animations on or off, with a live preview.
- **Formats:** untick anything you don't use, and ★ your favourites to pin them to the top.
- **History:** recent conversions (open them or show them in their folder) and your stats:
  files converted and space saved.

## Updates
Satsuma updates itself, so nobody has to download it again. It checks your GitHub releases page
a few seconds after starting and every few hours. When there's a newer version it shows
**Update now**; one click downloads it, checks it isn't damaged, installs it and restarts Satsuma
with all settings kept. On the **Updates** page you can let it install updates by itself.

**To publish an update:** change the files in your GitHub repository, then go to
**Releases → Draft a new release**, type a higher tag (e.g. `v1.3.0`) and click **Publish**.
GitHub builds it in about 10 minutes, and every copy of Satsuma picks it up.

Updates need to know where your releases are. Copies built by GitHub know automatically. For copies
built with `build.bat`, create a file called `update-repo.txt` next to `build.bat` containing your
repository, e.g. `yourname/satsuma`.

## Removing old copies
Double-click **`remove-old-versions.bat`**. It finds older Satsuma copies (the very first
`run.bat` version, older project folders, old zip downloads), shows you the list and asks before
deleting anything. It never touches the installed app or your settings. The installer also stops
and unhooks the very first version automatically.

## Uninstalling
Any of these works:
- In Satsuma: **About → Uninstall Satsuma…**
- Start menu: right-click **Satsuma → Uninstall**
- Windows **Settings → Apps → Installed apps → Satsuma → Uninstall**

It asks whether to delete your settings too. Files you converted are never touched.
(Portable version: **About → Remove settings and quit**, then delete the folder.)

---

## Releasing it (no Python needed for the people who download it)

### Option A — let GitHub build it (recommended)
1. Make a free account at github.com, then click **New repository**, name it `satsuma`, make it **Public**.
2. On the new repo page click **uploading an existing file**, drag in *everything in this folder*,
   and click **Commit changes**. The website sometimes skips the `.github` folder, so check that
   `.github/workflows/release.yml` is there. If it isn't: **Add file → Create new file**, type
   `.github/workflows/release.yml` as the name, paste in that file's contents, and commit.
3. Go to **Releases → Draft a new release**. In "Choose a tag" type `v1.0.0` and click *Create new tag*.
   Give it a title and click **Publish release**.
4. Wait about 10 minutes (watch the **Actions** tab). The release page will then have:
   - `Satsuma-Setup-1.0.0.exe` — the installer (creates the desktop icon)
   - `Satsuma-1.0.0-portable.zip` — runs without installing

   That release page link is what you share with people.
5. For an update, change the code, then publish a new release with a higher number (`v1.3.0`).
   Everyone's Satsuma updates itself (see **Updates** above).

### Option B — build on your own PC
Double-click **`build.bat`**. It downloads everything it needs (installing Python and Inno Setup
for you if they're missing), builds the installer, then asks **"Install Satsuma on this PC now?"**.
Press **Y** to install it, or **N** to open the folder with the installer to share it.

---

## Good to know before you share it
- **"Windows protected your PC" warning:** new apps without a code-signing certificate show this.
  People click *More info → Run anyway*. To remove it, sign the app (e.g. Microsoft
  *Trusted Signing* or a code-signing certificate from a certificate authority).
- **Antivirus:** apps packaged with PyInstaller are occasionally flagged by mistake. If that
  happens, submit the file as a false positive (Microsoft: https://www.microsoft.com/wdsi/filesubmission).
- **Licenses:** Satsuma's code is MIT. It bundles FFmpeg (GPL) and other open-source parts listed in
  `THIRD_PARTY_NOTICES.md`; keep that file included and keep the source public (Option A does both).
- **The name:** search the Microsoft Store and the web for "Satsuma" before launching, in case
  another app already uses it. Renaming means changing `APP_NAME` in `satsuma/app.py`,
  `AppName` in `installer/satsuma.iss` and `--name` in `scripts/build.ps1`.

## Project layout
```
main.py                     entry point
satsuma/app.py              tray icon, Shift-drag detection, jobs, startup
satsuma/window.py           the Satsuma window (Home, General, Look, Formats, About)
satsuma/wheel.py            the format wheel and its preview
satsuma/theme.py            light/dark themes and accent colours
satsuma/toast.py            progress / result popup
satsuma/settings.py         saved settings, start with Windows, uninstall helpers
satsuma/converters.py       all the conversions (Pillow, FFmpeg, pypdf, PDFium, zxing, ONNX Runtime)
satsuma/dialogs.py          resize / rotate / trim / rename windows
satsuma/history.py          recent conversions and stats
satsuma/updater.py          self-updates from GitHub Releases (checksum-verified)
assets/                     icon, installer pictures and sounds (scripts/make_icon.py, make_sounds.py)
scripts/build.ps1           builds the app, installer and zip
installer/satsuma.iss       Inno Setup installer script
.github/workflows/          automatic builds on GitHub
run-from-source.bat         run your changes without building (needs Python)
remove-old-versions.bat     one-time clean-up of older copies
```
Settings are stored in `%APPDATA%\Satsuma`, logs in `%LOCALAPPDATA%\Satsuma`.
