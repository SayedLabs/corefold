## How light Rakiza Core is

| Moment | Memory (Task Manager) | Private memory | Threads | Handles | CPU |
|---|---|---|---|---|---|
| Waiting in the tray (3 minutes) | **8 MB** | 42 MB | 2 | 376 | 0.15 % of one core |
| Window open: Home | **116.5 MB** | 82.5 MB | 12 | 776 | 3.52 % of one core |
| Window open: Converter | **116.8 MB** | 82.6 MB | 12 | 778 |  |
| Window open: Uninstaller (list loaded) | **130 MB** | 86.3 MB | 11 | 831 | 0.16 % of one core |
| Window open: History | **130.1 MB** | 86.3 MB | 11 | 833 |  |
| Window open: Look (wheel previews) | **130.5 MB** | 86.3 MB | 11 | 833 |  |
| Window open: PDF | **130.5 MB** | 86.3 MB | 11 | 833 |  |
| Window open: Organizer | **133.9 MB** | 89 MB | 11 | 842 |  |
| Window open: Startup | **134.5 MB** | 89.7 MB | 11 | 842 |  |
| Window open: Scan | **134.5 MB** | 89.7 MB | 11 | 842 |  |
| Window just closed | **132.5 MB** | 90.1 MB | 8 | 835 | 0.31 % of one core |
| Window closed for 2 minutes (freed) | **8.5 MB** | 80.2 MB | 6 | 823 | 0.16 % of one core |
| Quick menu open | **8.5 MB** | 80.2 MB | 6 | 823 |  |
| Quick menu just closed | **8.5 MB** | 80.2 MB | 6 | 823 |  |
| Quick menu closed for 2 minutes | **11.9 MB** | 80.2 MB | 7 | 825 | 0.21 % of one core |
| Just converted a 12 MP photo to WebP | **312.7 MB** | 355.9 MB | 15 | 854 |  |
| Six seconds after converting | **2.4 MB** | 84.3 MB | 15 | 851 |  |
| A minute after converting | **6.2 MB** | 83.9 MB | 6 | 834 |  |
| Just removed a background (AI model loaded) | **31.8 MB** | 85.2 MB | 7 | 841 |  |
| A minute after removing a background | **8.8 MB** | 85.1 MB | 5 | 831 |  |
| Just took the sound out of a 1080p video | **26 MB** | 85.1 MB | 6 | 834 |  |
| Just made a GIF from it | **26.8 MB** | 85.1 MB | 6 | 842 |  |
| A minute after the video jobs | **6 MB** | 85.1 MB | 5 | 831 |  |
| After 10 rounds of open, Uninstaller, close, convert | **2.7 MB** | 94 MB | 6 | 839 |  |
| Two minutes later (window freed, memory tidied) | **6.2 MB** | 86.1 MB | 7 | 834 | 0.16 % of one core |
| Window open again: Home | **67.3 MB** | 93 MB | 12 | 857 |  |
| Window open behind another program (2 minutes) | **7.2 MB** | 92.7 MB | 5 | 832 | 0.08 % of one core |
| Space map of Program Files shown (63 s) | **112.7 MB** | 138.5 MB | 6 | 849 |  |
| Space left for 5 minutes (map let go) | **4.6 MB** | 132.1 MB | 4 | 841 | 0.08 % of one core |
| Window closed for 2 minutes after Space | **9.3 MB** | 127.8 MB | 5 | 838 | 0.16 % of one core |

- Started (log file opened) after **0.38 s**
- Most memory used at any moment: 355.3 MB
- Processes still running under Rakiza Core at the end: 0
- App folder: 346 MB
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
