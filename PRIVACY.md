# Rakiza Core privacy policy

Last changed: November 5, 2026. This is the same policy as the one on
[the website](https://sayedlabs.github.io/corefold/privacy.html).

Rakiza Core is a free Windows app made by Rakiza Labs. In short: **your files stay on your PC, there are
no Rakiza Core accounts, no ads and no tracking, and we have no server that could collect anything.**
Rakiza Core only goes online by itself to check for its own updates. If you choose to use Rakiza Core Cloud,
it works with the cloud folders already on your PC, or talks straight to your own OneDrive or Dropbox if you
sign in, and to nobody else.

## Your files

Everything Rakiza Core does to your files (converting, shrinking, removing backgrounds, reading text,
opening archives and emails, taking screenshots, uninstalling apps) happens on your own PC. Nothing is
uploaded, to us or to anyone else, unless you upload it yourself with Rakiza Core Cloud.

## Rakiza Core Cloud (OneDrive, Dropbox, Google Drive and iCloud)

Cloud shows your clouds in one window. It's optional, and it works in two ways.

**Folders on your PC.** Cloud finds the OneDrive, Dropbox, Google Drive and iCloud folders that those
companies' own apps keep on your PC. It finds them by reading what those apps write on your PC (OneDrive's
settings in the Windows registry, Dropbox's `info.json`, Google Drive's drive). For these folders Rakiza Core
doesn't go online at all: it opens, copies, renames and deletes files in them on your PC, and the company's
own app sends the changes to its service, as it does for anything you save there. Deleted files go to the
Recycle Bin. Nothing about these folders is kept by Rakiza Core.

**Signing in online (OneDrive and Dropbox),** when your copy of Rakiza Core offers it. Nothing happens until
you sign in to an account.

- **Signing in happens on Microsoft's or Dropbox's own page,** in your web browser. Rakiza Core never sees
  your password. While you sign in, Rakiza Core listens on a local address on your own PC
  (`http://localhost`) to receive the answer; nothing outside your PC can reach it.
- **What Rakiza Core asks for:** permission to see, add, change and delete your files, to see your name,
  email address and how much space you use, and to stay signed in so you don't have to sign in every time.
- **What Rakiza Core keeps:** the key the service gives back (not your password), plus your name, email
  address and storage use so the account can be shown. They're kept only on your PC, in
  `%LOCALAPPDATA%\Rakiza Core\Cloud`, locked with Windows' own encryption so only your Windows account can
  unlock them.
- **Your files go straight between your PC and the service.** They don't pass through Rakiza Labs or
  anyone else. Copying from one cloud to another goes through your PC too.
- **Files you open** from Cloud are downloaded to a temporary folder on your PC so the right app can open
  them. Changes stay on your PC until you upload the file again.
- **Sign out** deletes Rakiza Core's key for that account from your PC, and for Dropbox also asks Dropbox to
  cancel it. To also take back the permission on
  the service's side, use [your Microsoft account's app permissions](https://account.live.com/consent/Manage)
  (work or school accounts: [My Apps](https://myapps.microsoft.com)) or
  [Dropbox's connected apps](https://www.dropbox.com/account/connected_apps).

Your files on OneDrive and Dropbox are covered by those companies' own policies:
[Microsoft's privacy statement](https://privacy.microsoft.com/privacystatement) and
[Dropbox's privacy policy](https://www.dropbox.com/privacy).

## What goes over the internet

| When | What is sent | To whom |
|---|---|---|
| Checking for updates (a few times a day, can be turned off in *Updates*) | A request for the latest release, with the app's name and version. Like any web request, it carries your IP address. | GitHub, which hosts Rakiza Core's releases ([GitHub's privacy statement](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement)) |
| Downloading an update, when you choose to or turn on automatic updates | A download of the installer. | GitHub |
| Using Rakiza Core Cloud, only after you sign in to an account (folders on your PC send nothing) | Your sign-in, the files and folders you open, upload, download, rename, delete or search for, and the searches you type. | Microsoft (OneDrive) or Dropbox, whichever you signed in to |
| Running a speed test in Rakiza Core Network, only when you press *Test my speed* | Requests for throw-away data and uploads of random data, for about 15 seconds. Like any web request, they carry your IP address. Nothing about you, your PC or your files is sent, and the results stay on your PC. | Cloudflare, whose speed test it uses (`speed.cloudflare.com`, [Cloudflare's privacy policy](https://www.cloudflare.com/privacypolicy/)) |
| When you click *Send feedback*, *Website*, *Privacy* or similar links | Your web browser opens that page. Anything you then write in a feedback form is public on GitHub. | GitHub |

Rakiza Core doesn't send usage statistics, crash reports or any information about your files, and it
doesn't count its users. If a future version offers to send a crash report, it will ask you first and
show you what would be sent.

Rakiza Core Network shows which programs are connected and where, by reading what Windows already knows on
your PC (its list of connections and the names it looked up lately). It never asks a server about them, and
nothing it shows is sent anywhere.

Emails you open in Rakiza Core can't reach the internet either: pictures from the internet are blocked, so
the sender can't tell you opened the email.

## What stays on your PC

Rakiza Core keeps these in your user folders (`%APPDATA%\Rakiza Core` and `%LOCALAPPDATA%\Rakiza Core`), and
nowhere else:

- your settings;
- a list of recent conversions (you can clear it in *History*);
- your clipboard history, if you use Clipboard (copies that password managers mark as private are never kept);
- your Cloud accounts' keys, locked as described above;
- the speed tests you ran in Network (speeds and the city of the test server, never your address), and
  which programs it has seen using the internet before, to point out new ones;
- a small log of what the app did, to help find problems, which never leaves your PC unless you send it;
- the backups made when you uninstall apps with Rakiza Core.

When you uninstall Rakiza Core, it offers to delete all of this. The backups of uninstalled apps are kept,
because they're yours; you can delete them yourself.

## The website

The website is hosted by GitHub Pages, which may keep logs of visits (like your IP address) for
security. The site itself has no cookies, analytics or trackers, and loads nothing from other companies.

## Children

Rakiza Core doesn't collect personal information from anyone, including children.

## Your choices

Because Rakiza Labs doesn't collect or keep any information about you, there's nothing for us to show,
change or delete. Everything Rakiza Core keeps is on your own PC, where you can delete it. For files and
accounts on OneDrive or Dropbox, use those services' own settings.

## Changes and contact

If this policy changes, the new version will be here and on the website with a new date, and Rakiza Core's
release notes will say so. Questions? [Open an issue on GitHub](https://github.com/SayedLabs/corefold/issues/new).
