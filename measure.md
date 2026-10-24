## How light Corefold is

| Moment | Memory (Task Manager) | Private memory | Threads | Handles | CPU |
|---|---|---|---|---|---|
| Waiting in the tray (3 minutes) | **9 MB** | 62.3 MB | 11 | 754 | 0.13 % of one core |
| Window open: Home | **90.6 MB** | 102.6 MB | 15 | 800 | 0.23 % of one core |
| Window open: Converter | **92.1 MB** | 103.3 MB | 16 | 806 |  |
| Window open: Uninstaller (list loaded) | **112.5 MB** | 108.2 MB | 19 | 877 | 0 % of one core |
| Window open: History | **112.5 MB** | 108.2 MB | 19 | 879 |  |
| Window open: Look (wheel previews) | **112.6 MB** | 108.2 MB | 19 | 879 |  |
| Window open: PDF | **115.9 MB** | 111 MB | 20 | 892 |  |
| Window open: Organizer | **118.1 MB** | 112.9 MB | 20 | 897 |  |
| Window open: Startup | **120.8 MB** | 116.1 MB | 21 | 901 |  |
| Window open: Scan | **123 MB** | 117.7 MB | 21 | 903 |  |
| Window just closed | **120.2 MB** | 118.2 MB | 21 | 904 | 0.31 % of one core |
| Window closed for 2 minutes (freed) | **9.2 MB** | 93.3 MB | 15 | 882 | 0.26 % of one core |
| Quick menu open | **59.2 MB** | 98.4 MB | 23 | 941 |  |
| Quick menu just closed | **60 MB** | 98.4 MB | 23 | 942 |  |
| Quick menu closed for 2 minutes | **13.1 MB** | 96.8 MB | 16 | 926 | 0.16 % of one core |
| Just converted a 12 MP photo to WebP | **332.8 MB** | 397.8 MB | 20 | 936 |  |
| Six seconds after converting | **2 MB** | 100.6 MB | 20 | 933 |  |
| A minute after converting | **6.6 MB** | 100.4 MB | 15 | 927 |  |
| Just removed a background (AI model loaded) | **31.9 MB** | 101.9 MB | 16 | 934 |  |
| A minute after removing a background | **9 MB** | 101.8 MB | 13 | 922 |  |
| Just took the sound out of a 1080p video | **25.3 MB** | 101.8 MB | 14 | 925 |  |
| Just made a GIF from it | **25.9 MB** | 101.8 MB | 14 | 933 |  |
| A minute after the video jobs | **6.1 MB** | 101.8 MB | 14 | 922 |  |
| After 10 rounds of open, Uninstaller, close, convert | **2 MB** | 124.5 MB | 18 | 942 |  |
| Two minutes later (window freed, memory tidied) | **10.5 MB** | 104.6 MB | 15 | 924 | 0.23 % of one core |
| Playing a 720p video | **122.6 MB** | 151.8 MB | 36 | 1182 | 52.19 % of one core |

- Started (log file opened) after **0.4 s**
- Most memory used at any moment: 351.1 MB
- Processes still running under Corefold at the end: 0
- App folder: 305 MB
- Machine: GitHub windows-latest, 4 cores

## The built app, end to end

- ok: archives can be opened with Corefold (Open with: "D:\a\corefold\corefold\dist\Corefold\Corefold.exe" --browse "%1")
- ok: Corefold is listed under Open with for .zip
- ok: a double-clicked archive opens in Corefold (while it runs)
- ok: the background remover ran in its helper
- ok: no background remover helper is left running (0)
- ok: the build has Qt's player (FFmpeg plugin)
- ok: a double-clicked video plays in Corefold Player
- ok: …without errors

## Last checks

- ok: a double-clicked archive opens Corefold when it wasn't running
- ok: uninstalling takes Corefold out of Open with
- ok: …for .zip too
- ok: …and the right-click menu
