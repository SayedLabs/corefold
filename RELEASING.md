# Releasing Corefold

Corefold's code is private. It lives in this repository. The downloads, the website and the
problem reports live in the public repository named in `update-repo.txt` (`SayedLabs/corefold`),
because every copy of Corefold, and of the app under its old name, looks for its updates there.
Never make that repository private, rename it or delete it: every copy would stop updating, without
saying so.

## Publishing a new version
1. Put the release notes in `release-notes/<version>.md` (for example `release-notes/1.8.0.md`) and
   bump `corefold/__init__.py`, then merge into `main`.
2. Here, open **Actions → Build Corefold → Run workflow**, type the version (`1.8.0`) and click
   **Run workflow**. It takes about 15 minutes.
3. In the public repository, open **Releases**. The new version is there as a **Draft**, with the
   installer, the portable zip, `SHA256SUMS.txt`, the copy for old versions and FFmpeg's source code.
   Check it, then click **Edit → Publish release**. From that moment every copy can update to it.

Leave a draft unpublished, or delete it, and nothing happens: no copy sees drafts.

Run the workflow without a version for a test build: the files are on the run's page, under
*Artifacts*, and nothing is published.

## The website and the public README
`docs/` (the website and privacy policy), `README.md`, `PRIVACY.md`, `LICENSE`, `EULA.txt`,
`THIRD_PARTY_NOTICES.md` and `licenses/` are copied to the public repository by
**Actions → Update the public page → Run workflow**. Run it after any of them change. The public
repository then holds those files and nothing else. Anything in `README.md` from a
`<!-- private -->` line on stays here.

## The token both workflows use
`PUBLIC_RELEASE_TOKEN` (in this repository's **Settings → Secrets and variables → Actions**) is a
fine-grained token that can only change the public repository's files and releases. When it
expires, make a new one the same way:
1. github.com → your picture → **Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token**.
2. Name `Corefold releases`, expiration up to a year, **Repository access → Only select
   repositories →** `SayedLabs/corefold`.
3. **Repository permissions → Contents → Read and write** (Metadata: read-only is added by itself).
   Nothing else.
4. **Generate token**, copy it, and in this repository open **Settings → Secrets and variables →
   Actions → New repository secret**, name `PUBLIC_RELEASE_TOKEN`, paste, **Add secret**.

## Licences, in short
- Corefold itself: `LICENSE` and `EULA.txt` (shown in the installer). Code published under MIT
  before October 2026 stays MIT for those copies.
- **No GPL or AGPL library may ever be imported into Corefold**, and no GPL code copied into it:
  that would force the whole app to be open source. GPL programs that Corefold only *runs*, like
  `ffmpeg.exe`, are fine.
- LGPL libraries (Qt, PySide6, libheif, the FFmpeg DLLs inside Qt Multimedia) are fine as long as
  they stay separate DLL files the user can replace. Keep the build a folder (never PyInstaller
  `--onefile`, never static linking) and don't change those libraries.
- Every release must carry FFmpeg's source code (the build attaches it; if the build warns that it
  couldn't, don't publish) and the notices in `THIRD_PARTY_NOTICES.md` and `licenses/`.
- A new component: add it to `THIRD_PARTY_NOTICES.md`, and check its licence first.
