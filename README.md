# Corefold

*Everyday tools for Windows, by Atomic Works.*

Everyday tools in one small app that waits in the tray. Everything happens on your PC; nothing is uploaded.

- **Corefold Converter**: hold Shift while dragging a file in File Explorer or on the desktop, and a
  wheel of formats appears at your cursor. Drop the file on a format and it's converted.
- **Corefold Uninstaller**: removes programs and everything they leave behind (folders, shortcuts,
  registry entries), with a backup you can put back. Shows which programs you haven't used in months.
- **Corefold Archive**: opens ZIP, RAR, 7z, ISO and more with a double-click. Drag files out, change a
  file inside and save it back, rescue damaged archives, and unpack archives inside archives in one go.

Also in the Store: Corefold Viewer, Player, Capture, Space, Clipboard, PDF and Startup. Coming soon: Cloud.

- **Corefold PDF**: merge PDFs and arrange their pages by dragging page pictures (turn, delete, undo),
  split a PDF or take out the pages you need, compress it (three levels), pictures to PDF (on A4 or Letter
  if you want) and pages to pictures, and lock a PDF with a password (with or without printing and
  copying) or unlock one that's yours. Locked PDFs can be used too, once their password is typed.

### The Converter

| Drop a… | You can |
|---|---|
| Image (PNG, JPG, WebP, HEIC, AVIF, GIF, BMP, TIFF, ICO) | convert to PNG, JPG, WebP, PDF, ICO, BMP · **resize** · **rotate / flip** · **crop** (free or square, 4:3, 16:9…) · **make it fit** a size (Discord, email) · **cut out** the background · **copy the text** in it · **watermark** · **stickers** for WhatsApp and Telegram · **scan** photos of paper into a PDF · turn pictures into a **video** · read QR codes · remove location & camera info |
| Video (MP4, MOV, MKV, WebM, AVI, WMV…) | MP4, MOV, WebM, MKV, GIF · **trim** · **make it fit** · rotate · **speed up / slow down** · **mute** · **fix the volume** · animated **stickers** and chat GIFs (WhatsApp, Telegram, Discord) · watermark · save **frames** · smaller copy · MP3 sound |
| Audio (MP3, M4A, WAV, FLAC, OGG, Opus, AIFF, WMA, AAC) | MP3, M4A, WAV, FLAC, OGG, Opus, AIFF · trim · make it fit · speed up / slow down · fix the volume |
| PDF | compress · make it fit · split · pages to images · extract text · merge several · **turn, delete or reorder pages** · save the **pictures** inside · **lock / unlock** with a password |
| Word, Excel, PowerPoint | PDF (uses Microsoft Office or LibreOffice if installed) |
| Archive (ZIP, RAR, 7z, ISO, TAR, GZ, CAB…) | **extract** (smart: no folder-in-a-folder) · extract to a folder · **browse** inside, open or take out single files · **test** · turn into ZIP or 7z · add / remove files (ZIP, 7z) |
| Any files or folders | make a **ZIP** or **7z**, with a password, max compression, or split into parts · **rename** many at once (numbers, replace text, add text — with undo) |
| Folder | convert every picture inside to JPG, PNG or WebP, or shrink them all for sharing |

**How do I know I'm seeing every option?** The middle of the wheel says how many options there are,
and dots show the pages. When there are more than fit, the last slice is **More**: click it, or hold
the dragged file over it for a moment. Options that don't fit the file are **dimmed** (point at one
to see why), and the middle tells you if you hid any in Settings. For the full list with nothing
hidden, drop the file on Corefold's **Home** page.

After a conversion the popup can **Compare** the new picture with the original (drag across it to see
the difference) or **Undo** a rename. **Do it again** (on the Home page and in the tray menu) repeats
your last action, with the same choices, on other files. When the wheel is opened from the
right-click menu, press **1–9** to pick a slice (0 = More).

Other ways to use it: **right-click** a file → **Corefold** → *Convert to PNG*, *Resize…*, *Extract here*…
(a normal Windows submenu, like 7-Zip's; on Windows 11 it's under *Show more options*, or Shift +
right-click). *All options…* at the bottom opens Corefold with the file. Prefer the wheel? Pick
**Corefold wheel** under *General → Right-click menu*. Or press **Ctrl + V** in Corefold (or *Convert
what's copied* in the tray menu) to convert a copied picture or copied files.

**English, العربية, Français, Español and Türkçe.** Corefold follows the language of Windows; change it
under *General → Language*. In Arabic the whole app is right-to-left.

**Uninstall apps, without the leftovers.** The **Apps** page lists every installed program (and Store
app) with its size and install date. **Uninstall** runs the app's own uninstaller, then looks for what
it left behind: its folders in Program Files, AppData and ProgramData, shortcuts, startup entries and
registry keys. Things that clearly belong to the app are ticked; things that only might are shown
unticked, and shared places (Windows, Microsoft, Common Files…) are never touched. Nothing is deleted
outright: files are moved into a backup and registry keys are exported first, so **Backups…** can put
everything back for 30 days. Also: quiet uninstall for several apps at once, an optional System
Restore point first, and *Remove leftovers only* for apps whose uninstaller is missing.

**Light on your PC.** Corefold waits in the tray using almost no memory or power. Conversions run at
low priority and one at a time (both can be changed under *General → Keep my PC fast*), memory it
doesn't need is handed back to Windows when it's idle, the window is freed a little while after you
close it, and right-clicking many files at once doesn't start many copies of the app.

Drop several files at once to convert them all. The original file is never moved or changed.

## Installing
Run **Corefold-Setup.exe**, keep the two boxes ticked, click **Install**. That's it: a desktop
icon, a Start menu entry, and Corefold starts with Windows. No admin rights or Python needed.
Installing a newer version on top upgrades it and keeps your settings.

## Customizing
Open Corefold from the desktop icon (or click the teal tray icon):
- **General:** language, which key to hold (Shift, Ctrl + Shift or Alt), where new files go, image
  quality, a sound when done, dim or hide formats that don't fit, new names for batches, the
  right-click menu (Windows menu or the wheel), progress popups, start with Windows, and
  *Keep my PC fast* (background priority, one conversion at a time).
- **Look:** match Windows / dark / light, seven accent colours (Corefold teal to start with) or your own, wheel shape (classic,
  petals, slim, bubbles, neon, glass), size (up to huge), icons and/or text on slices, text size, how a
  slice lights up, colour slices by kind of action or in a rainbow, options per page, open under the cursor or in the middle,
  see-through, animations, number keys — all with a live preview. Also the text size in Corefold's
  windows, which corner popups appear in and how long they stay, and icons in the sidebar.
- **Sounds:** four soft "done" sounds made for Corefold (Corefold, Peel, Droplet, Glass) or none, a
  volume slider, a gentle sound when something fails, and an optional tick when the wheel opens.
- **Formats:** untick anything you don't use, ★ favourites to pin them to the top, ▲ ▼ to reorder.
- **History:** recent conversions (open them or show them in their folder) and your stats:
  files converted and space saved.

## Website
The `docs` folder is a ready-made download page. To put it online for free: on GitHub open
**Settings → Pages**, under *Branch* choose **main** and **/docs**, click **Save**. A minute later it's
at `https://<your-name>.github.io/corefold/` with a big download button that always points at your
newest release, plus an "Is it safe?" section that explains the Windows warning.

## Updates
Corefold updates itself, so nobody has to download it again. It checks your GitHub releases page
a few seconds after starting and every few hours. When there's a newer version it shows
**Update now**; one click downloads it, checks it isn't damaged, installs it and restarts Corefold
with all settings kept. On the **Updates** page you can let it install updates by itself.

**To publish an update:** change the files in your GitHub repository, then go to
**Releases → Draft a new release**, type a higher tag (e.g. `v1.3.0`) and click **Publish**.
GitHub builds it in about 10 minutes, and every copy of Corefold picks it up.

Updates need to know where your releases are. Copies built by GitHub know automatically. For copies
built with `build.bat`, create a file called `update-repo.txt` next to `build.bat` containing your
repository, e.g. `yourname/corefold`.

## Updating from versions before 1.7.0
Version 1.7.0 gave the app its new name and look. Installing it over an older version upgrades it
(same installer ID), and the first start moves the old settings, history and uninstall backups into
the Corefold folders and replaces the old right-click entries. Nothing is deleted that couldn't be
moved.

Versions 1.5.3 to 1.6.0 update themselves to the newest Corefold: every release also carries a copy of
the installer under the old name, because that is the only file their updater looks for. Older
versions than that have to download Corefold by hand once.

## How light is it?
**Actions → Measure memory and CPU → Run workflow** builds Corefold on one of GitHub's Windows
PCs, starts it in the tray and writes down its memory, CPU use and start-up time on the run's
summary page. Nothing is published.

## Uninstalling
Any of these works:
- In Corefold: **About → Uninstall Corefold…**
- Start menu: right-click **Corefold → Uninstall**
- Windows **Settings → Apps → Installed apps → Corefold → Uninstall**

It asks whether to delete your settings too. Files you converted are never touched.
(Portable version: **About → Remove settings and quit**, then delete the folder.)

---

## Releasing it (no Python needed for the people who download it)

### Option A — let GitHub build it (recommended)
1. Make a free account at github.com, then click **New repository**, name it `corefold`, make it **Public**.
2. On the new repo page click **uploading an existing file**, drag in *everything in this folder*,
   and click **Commit changes**. The website sometimes skips the `.github` folder, so check that
   `.github/workflows/release.yml` is there. If it isn't: **Add file → Create new file**, type
   `.github/workflows/release.yml` as the name, paste in that file's contents, and commit.
3. Go to **Releases → Draft a new release**. In "Choose a tag" type `v1.0.0` and click *Create new tag*.
   Give it a title and click **Publish release**.
4. Wait about 10 minutes (watch the **Actions** tab). The release page will then have:
   - `Corefold-Setup-1.0.0.exe` — the installer (creates the desktop icon)
   - `Corefold-1.0.0-portable.zip` — runs without installing
   - `ffmpeg-source-….tar.gz` — FFmpeg's source code and build scripts (its licence asks for these)

   That release page link is what you share with people.
5. For an update, change the code, then publish a new release with a higher number (`v1.3.0`).
   Everyone's Corefold updates itself (see **Updates** above).

### Option B — build on your own PC
Double-click **`build.bat`**. It downloads everything it needs (installing Python and Inno Setup
for you if they're missing), builds the installer, then asks **"Install Corefold on this PC now?"**.
Press **Y** to install it, or **N** to open the folder with the installer to share it.

---

## Good to know before you share it
- **"Windows protected your PC" warning:** new apps without a code-signing certificate show this.
  People click *More info → Run anyway*. To remove it, sign the app (e.g. Microsoft
  *Trusted Signing* or a code-signing certificate from a certificate authority).
- **Antivirus:** apps packaged with PyInstaller are occasionally flagged by mistake. If that
  happens, submit the file as a false positive (Microsoft: https://www.microsoft.com/wdsi/filesubmission).
- **Licenses:** Corefold's code is MIT. It bundles FFmpeg (GPL) and other open-source parts listed in
  `THIRD_PARTY_NOTICES.md`; keep that file included and keep the source public (Option A does both).
  Every release gets FFmpeg's source attached automatically; if the build warns that it couldn't
  get it, don't publish that build.
- **Privacy:** `docs/privacy.html` is the privacy policy (the same text is in `PRIVACY.md`), linked
  from the website, from *About* and from the Cloud page in the app. When something new goes online,
  update both files and the date.

## Project layout
```
main.py                        entry point
corefold/app.py                tray icon, Shift-drag detection, jobs, startup
corefold/window.py             the Corefold window (Home, Apps, General, Look, Formats, History, Updates, About)
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
corefold/i18n.py               languages; corefold/lang_ar.py has the Arabic text
corefold/updater.py            self-updates from GitHub Releases (checksum-verified)
corefold/uninstall.py          the Apps page: uninstalling and leftovers, with backups
corefold/pdftools.py          Corefold PDF's work: merge, split, compress, pictures, passwords (pypdf, PDFium)
corefold/pdfpage.py           Corefold PDF's page: the cards and the page board
corefold/startup.py            what starts with Windows, switched on and off the way Task Manager does
corefold/startuppage.py        Corefold Startup's page
scripts/check_translations.py  checks that every text has a translation
assets/                        icon, installer pictures and sounds (scripts/make_icon.py, make_sounds.py)
scripts/build.ps1              builds the app, installer and zip
installer/corefold.iss         Inno Setup installer script
.github/workflows/             automatic builds on GitHub, and the memory/CPU measurement
docs/                          the download website and privacy policy (GitHub Pages)
run-from-source.bat            run your changes without building (needs Python)
```
Settings are stored in `%APPDATA%\Corefold`, logs in `%LOCALAPPDATA%\Corefold`.
