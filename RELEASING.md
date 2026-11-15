# Releasing Rakiza Core

Rakiza Core's code is private. It lives in this repository. The downloads, the website and the
problem reports live in the public repository named in `update-repo.txt` (`SayedLabs/rakiza-core`),
because every copy of Rakiza Core, and of the app under its earlier names, looks for its updates there.
Never make that repository private, rename it or delete it: every copy would stop updating, without
saying so.

## Publishing a new version
1. Write what's new in `assets/release-notes/<version>.md` and `<version>.ar.md`, bump
   `corefold/__init__.py`, run `python scripts/build_site.py` (the website's What's new), and merge into `main`.
2. Here, open **Actions → Build Rakiza Core → Run workflow**, type the version (`2.3.0`) and click
   **Run workflow**. It runs every release check, then builds and tests the installer: about 40 minutes.
3. In the public repository, open **Releases**. The new version is there as a **Draft**, with the
   installer, the portable zip, `SHA256SUMS.txt`, the copy for old versions, FFmpeg's source code and
   What's new in English and Arabic. Check it, then click **Edit → Publish release**. From that moment
   every copy can update to it.
4. Then run **Actions → Automatic update** here: a real older copy updates itself to it.

Leave a draft unpublished, or delete it, and nothing happens: no copy sees drafts.

Run the workflow without a version for a test build: the files are on the run's page, under
*Artifacts*, and nothing is published.

## The website and the public README
`docs/` (the website and privacy policy), `README.md`, `PRIVACY.md`, `LICENSE`, `EULA.txt`,
`THIRD_PARTY_NOTICES.md` and `licenses/` are copied to the public repository by
**Update the public page**, by itself when one of them changes on `main` (or by hand from the
Actions tab). The public repository then holds those files and nothing else. Anything in `README.md`
from a `<!-- private -->` line on stays here.

## The token both workflows use
`PUBLIC_RELEASE_TOKEN` (in this repository's **Settings → Secrets and variables → Actions**) is a
fine-grained token that can only change the public repository's files and releases. When it
expires, make a new one the same way:
1. github.com → your picture → **Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token**.
2. Name `Rakiza Core releases`, expiration up to a year, **Repository access → Only select
   repositories →** `SayedLabs/rakiza-core`.
3. **Repository permissions → Contents → Read and write** (Metadata: read-only is added by itself).
   Nothing else.
4. **Generate token**, copy it, and in this repository open **Settings → Secrets and variables →
   Actions → New repository secret**, name `PUBLIC_RELEASE_TOKEN`, paste, **Add secret**.

## Actions minutes
A private repository's Actions runs come out of the account's monthly minutes, and Windows minutes
count double. The Windows checks that run on every push (Developer checks, No flashing windows,
Smoothness, Security checks) and the long ones (Bug hunt, Measure, Memory) use most of them: see
**Settings → Billing** before turning more on.

## Licences, in short
- Rakiza Core itself: `LICENSE` and `EULA.txt` (shown in the installer). Code published under MIT
  (up to version 2.2.1) stays MIT for those copies.
- **No GPL or AGPL library may ever be imported into Rakiza Core**, and no GPL code copied into it:
  that would force the whole app to be open source. GPL programs that Rakiza Core only *runs*, like
  `ffmpeg.exe`, are fine.
- LGPL libraries (Qt, PySide6, libheif, Paramiko, the FFmpeg DLLs inside Qt Multimedia) are fine as
  long as they stay separate files the user can replace. Keep the build a folder (never PyInstaller
  `--onefile`, never static linking) and don't change those libraries.
- Every release must carry FFmpeg's source code (the build attaches it; if the build warns that it
  couldn't, don't publish) and the notices in `THIRD_PARTY_NOTICES.md` and `licenses/`.
- A new component: add it to `THIRD_PARTY_NOTICES.md`, and check its licence first.
