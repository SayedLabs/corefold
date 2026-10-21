# Satsuma

**Shift-drag file converter for Windows.** Hold Shift while dragging a file in File Explorer or on
the desktop, and a wheel of formats appears at your cursor. Drop the file on a format and it's
converted. Everything happens on your PC; nothing is uploaded.

| Drop a… | You can |
|---|---|
| Image (PNG, JPG, WebP, HEIC, AVIF, GIF, BMP, TIFF, ICO) | convert to PNG, JPG, WebP, PDF, ICO, BMP · **resize** · **rotate / flip** · **make it fit** a size (Discord, email) · **cut out** the background · **copy the text** in it · **watermark** · **scan** photos of paper into a PDF · turn pictures into a **video** · read QR codes · remove location & camera info |
| Video (MP4, MOV, MKV, WebM, AVI, WMV…) | MP4, MOV, WebM, MKV, GIF · **trim** · **make it fit** · rotate · watermark · save **frames** · smaller copy · MP3 sound |
| Audio (MP3, M4A, WAV, FLAC, OGG, Opus, AIFF, WMA, AAC) | MP3, M4A, WAV, FLAC, OGG, Opus, AIFF · trim · make it fit |
| PDF | compress · make it fit · split · pages to images · extract text · merge several |
| Word, Excel, PowerPoint | PDF (uses Microsoft Office or LibreOffice if installed) |
| Archive (ZIP, RAR, 7z, ISO, TAR, GZ, CAB…) | **extract** (smart: no folder-in-a-folder) · extract to a folder · **browse** inside, open or take out single files · **test** · turn into ZIP or 7z · add / remove files (ZIP, 7z) |
| Any files or folders | make a **ZIP** or **7z**, with a password, max compression, or split into parts |

**How do I know I'm seeing every option?** The middle of the wheel says how many options there are,
and dots show the pages. When there are more than fit, the last slice is **More**: click it, or hold
the dragged file over it for a moment. Options that don't fit the file are **dimmed** (point at one
to see why), and the middle tells you if you hid any in Settings. For the full list with nothing
hidden, drop the file on Satsuma's **Home** page.

Other ways to use it: **right-click** a file → **Satsuma** → *Convert to PNG*, *Resize…*, *Extract here*…
(a normal Windows submenu, like 7-Zip's; on Windows 11 it's under *Show more options*, or Shift +
right-click). *All options…* at the bottom opens Satsuma with the file. Prefer the wheel? Pick
**Satsuma wheel** under *General → Right-click menu*. Or press **Ctrl + V** in Satsuma (or *Convert
what's copied* in the tray menu) to convert a copied picture or copied files.

**English and العربية.** Satsuma follows the language of Windows; change it under *General → Language*.
In Arabic the whole app is right-to-left.

**Light on your PC.** Satsuma waits in the tray using almost no memory or power. Conversions run at
low priority and one at a time (both can be changed under *General → Keep my PC fast*), memory it
doesn't need is handed back to Windows when it's idle, the window is freed a little while after you
close it, and right-clicking many files at once doesn't start many copies of the app.

Drop several files at once to convert them all. The original file is never moved or changed.

## Installing
Run **Satsuma-Setup.exe**, keep the two boxes ticked, click **Install**. That's it: a desktop
icon, a Start menu entry, and Satsuma starts with Windows. No admin rights or Python needed.
Installing a newer version on top upgrades it and keeps your settings.

## Customizing
Open Satsuma from the desktop icon (or click the orange tray icon):
- **General:** language, which key to hold (Shift, Ctrl + Shift or Alt), where new files go, image
  quality, a sound when done, dim or hide formats that don't fit, new names for batches, the
  right-click menu (Windows menu or the wheel), progress popups, start with Windows, and
  *Keep my PC fast* (background priority, one conversion at a time).
- **Look:** match Windows / dark / light, six accent colours or your own, wheel shape (classic,
  petals, slim), size (up to huge), icons and/or text on slices, text size, how a slice lights
  up, colour slices by kind of action, options per page, open under the cursor or in the middle,
  see-through, animations — all with a live preview.
- **Sounds:** four soft "done" sounds made for Satsuma (Satsuma, Peel, Droplet, Glass) or none, a
  volume slider, a gentle sound when something fails, and an optional tick when the wheel opens.
- **Formats:** untick anything you don't use, ★ favourites to pin them to the top, ▲ ▼ to reorder.
- **History:** recent conversions (open them or show them in their folder) and your stats:
  files converted and space saved.

## Website
The `docs` folder is a ready-made download page. To put it online for free: on GitHub open
**Settings → Pages**, under *Branch* choose **main** and **/docs**, click **Save**. A minute later it's
at `https://<your-name>.github.io/satsuma/` with a big download button that always points at your
newest release, plus an "Is it safe?" section that explains the Windows warning.

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
satsuma/core.py             shared building blocks (file types, jobs, output names)
satsuma/archives.py         ZIP / RAR / 7z: extract, browse, test, create (7-Zip + Python fallback)
satsuma/extras.py           make it fit, copy text (OCR), watermark, frames, slideshow, scan
satsuma/browser.py          the archive window
satsuma/panel.py            the Home page drop area with every option as buttons
satsuma/sounds.py           sounds at the chosen volume
satsuma/dialogs.py          resize / rotate / trim / fit / watermark / archive / password windows
satsuma/history.py          recent conversions and stats
satsuma/shellmenu.py        the right-click menu in File Explorer
satsuma/ipc.py              hands right-click requests to the running app without loading it again
satsuma/i18n.py             languages; satsuma/lang_ar.py has the Arabic text
scripts/check_translations.py  checks that every text has an Arabic version
satsuma/updater.py          self-updates from GitHub Releases (checksum-verified)
assets/                     icon, installer pictures and sounds (scripts/make_icon.py, make_sounds.py)
scripts/build.ps1           builds the app, installer and zip
installer/satsuma.iss       Inno Setup installer script
.github/workflows/          automatic builds on GitHub
docs/                       the download website (GitHub Pages)
run-from-source.bat         run your changes without building (needs Python)
remove-old-versions.bat     one-time clean-up of older copies
```
Settings are stored in `%APPDATA%\Satsuma`, logs in `%LOCALAPPDATA%\Satsuma`.
