## How light Rakiza Core is

| Moment | Memory (Task Manager) | Private memory | Threads | Handles | CPU |
|---|---|---|---|---|---|
| Waiting in the tray (3 minutes) | **7.9 MB** | 41.4 MB | 2 | 376 | 0.34 % of one core |
| Window open: Home | **114.6 MB** | 80.4 MB | 12 | 776 | 3.75 % of one core |
| Window open: Converter | **115.2 MB** | 80.7 MB | 12 | 778 |  |
| Window open: Uninstaller (list loaded) | **128.7 MB** | 84.9 MB | 11 | 831 | 0.23 % of one core |
| Window open: History | **129.2 MB** | 85.2 MB | 11 | 833 |  |
| Window open: Look (wheel previews) | **129.4 MB** | 85.2 MB | 11 | 833 |  |
| Window open: PDF | **129.4 MB** | 85.1 MB | 8 | 833 |  |
| Window open: Organizer | **129.9 MB** | 85.7 MB | 10 | 839 |  |
| Window open: Startup | **130.6 MB** | 86.4 MB | 10 | 839 |  |
| Window open: Scan | **130.6 MB** | 86.4 MB | 10 | 839 |  |
| Window just closed | **128.4 MB** | 86.8 MB | 8 | 835 | 0.16 % of one core |
| Window closed for 2 minutes (freed) | **8.5 MB** | 81 MB | 6 | 819 | 0.31 % of one core |
| Quick menu open | **8.5 MB** | 81 MB | 6 | 819 |  |
| Quick menu just closed | **8.5 MB** | 81 MB | 6 | 819 |  |
| Quick menu closed for 2 minutes | **13.4 MB** | 81.1 MB | 8 | 821 | 0.36 % of one core |
| Just converted a 12 MP photo to WebP | **305.4 MB** | 347.5 MB | 16 | 850 |  |
| Six seconds after converting | **2.4 MB** | 83.1 MB | 16 | 847 |  |
| A minute after converting | **6.3 MB** | 78.9 MB | 6 | 830 |  |
| Just removed a background (AI model loaded) | **31.9 MB** | 81.2 MB | 7 | 837 |  |
| A minute after removing a background | **8.6 MB** | 81.1 MB | 5 | 827 |  |
| Just took the sound out of a 1080p video | **25.6 MB** | 81.1 MB | 6 | 830 |  |
| Just made a GIF from it | **26.6 MB** | 81.1 MB | 6 | 838 |  |
| A minute after the video jobs | **6 MB** | 81.1 MB | 5 | 827 |  |
| After 10 rounds of open, Uninstaller, close, convert | **2.6 MB** | 88.8 MB | 6 | 835 |  |
| Two minutes later (window freed, memory tidied) | **6.2 MB** | 81.9 MB | 7 | 830 | 0.18 % of one core |

- Started (log file opened) after **0.41 s**
- Most memory used at any moment: 355.4 MB
- Processes still running under Rakiza Core at the end: 0
- App folder: 345 MB
- Machine: GitHub windows-latest, 4 cores

## The built app, end to end

- ok: archives can be opened with Rakiza Core (Open with: "D:\a\corefold\corefold\dist\RakizaCore\RakizaCore.exe" --browse "%1")
- ok: Rakiza Core is listed under Open with for .zip
- ok: a double-clicked archive opens in Rakiza Core (while it runs)
- ok: the background remover ran in its helper
- ok: no background remover helper is left running (0)
- ok: the build has Qt's player (FFmpeg plugin) for Quick look

## Last checks

- ok: a double-clicked archive opens Rakiza Core when it wasn't running
- ok: uninstalling takes Rakiza Core out of Open with
- ok: …for .zip too
- ok: …and the right-click menu
