## How light Corefold is

| Moment | Memory (Task Manager) | Private memory | Threads | Handles | CPU |
|---|---|---|---|---|---|
| Waiting in the tray (3 minutes) | **7.9 MB** | 40.5 MB | 2 | 374 | 0.28 % of one core |
| Window open: Home | **113.2 MB** | 79.1 MB | 12 | 767 | 3.12 % of one core |
| Window open: Converter | **113.7 MB** | 79.1 MB | 12 | 769 |  |
| Window open: Uninstaller (list loaded) | **114.7 MB** | 79.3 MB | 8 | 757 | 0.31 % of one core |
| Window open: History | **115.4 MB** | 79.8 MB | 8 | 759 |  |
| Window open: Look (wheel previews) | **116.3 MB** | 79.8 MB | 8 | 759 |  |
| Window open: PDF | **116.3 MB** | 79.8 MB | 8 | 759 |  |
| Window open: Organizer | **116.7 MB** | 80.2 MB | 10 | 765 |  |
| Window open: Startup | **116.7 MB** | 80.2 MB | 10 | 765 |  |
| Window open: Scan | **116.7 MB** | 80.2 MB | 10 | 765 |  |
| Window just closed | **115.7 MB** | 80.5 MB | 9 | 766 | 0.47 % of one core |
| Window closed for 2 minutes (freed) | **8.4 MB** | 74.3 MB | 6 | 754 | 0.21 % of one core |
| Quick menu open | **8.4 MB** | 74.3 MB | 6 | 754 |  |
| Quick menu just closed | **8.4 MB** | 74.3 MB | 6 | 754 |  |
| Quick menu closed for 2 minutes | **11.5 MB** | 74.3 MB | 7 | 756 | 0.16 % of one core |
| Just converted a 12 MP photo to WebP | **286 MB** | 326 MB | 15 | 782 |  |
| Six seconds after converting | **42.7 MB** | 77.7 MB | 15 | 779 |  |
| A minute after converting | **6.3 MB** | 73.2 MB | 6 | 762 |  |
| Just removed a background (AI model loaded) | **31.7 MB** | 75.3 MB | 7 | 771 |  |
| A minute after removing a background | **8.5 MB** | 75.3 MB | 5 | 761 |  |
| Just took the sound out of a 1080p video | **26.3 MB** | 75.4 MB | 6 | 768 |  |
| Just made a GIF from it | **26.6 MB** | 75.4 MB | 6 | 776 |  |
| A minute after the video jobs | **5.9 MB** | 75.3 MB | 5 | 765 |  |
| After 10 rounds of open, Uninstaller, close, convert | **2.5 MB** | 82 MB | 6 | 770 |  |
| Two minutes later (window freed, memory tidied) | **6.1 MB** | 77.9 MB | 7 | 767 | 0.23 % of one core |
| After a double-clicked video (Player resting) | **50.8 MB** | 81 MB | 10 | 780 | 0.31 % of one core |

- Started (log file opened) after **0.42 s**
- Most memory used at any moment: 352.9 MB
- Processes still running under Corefold at the end: 0
- App folder: 307 MB
- Machine: GitHub windows-latest, 4 cores

## The built app, end to end

- ok: archives can be opened with Corefold (Open with: "D:\a\corefold\corefold\dist\Corefold\Corefold.exe" --browse "%1")
- ok: Corefold is listed under Open with for .zip
- ok: a double-clicked archive opens in Corefold (while it runs)
- ok: the background remover ran in its helper
- ok: no background remover helper is left running (0)
- ok: the build has Qt's player (FFmpeg plugin)
- ok: a double-clicked video doesn't open the resting Player

## Last checks

- ok: a double-clicked archive opens Corefold when it wasn't running
- ok: uninstalling takes Corefold out of Open with
- ok: …for .zip too
- ok: …and the right-click menu
