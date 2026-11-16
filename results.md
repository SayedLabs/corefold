## How light Rakiza Core is

| Moment | Memory (Task Manager) | Private memory | Threads | Handles | CPU |
|---|---|---|---|---|---|
| Waiting in the tray (3 minutes) | **8 MB** | 41.7 MB | 2 | 376 | 0.2 % of one core |
| Window open: Home | **116.1 MB** | 82.5 MB | 12 | 776 | 3.52 % of one core |
| Window open: Converter | **116.5 MB** | 82.6 MB | 12 | 778 |  |
| Window open: Uninstaller (list loaded) | **129.8 MB** | 86.4 MB | 11 | 831 | 0 % of one core |
| Window open: History | **130.1 MB** | 86.4 MB | 11 | 831 |  |
| Window open: Look (wheel previews) | **130.7 MB** | 86.4 MB | 11 | 831 |  |
| Window open: PDF | **130.7 MB** | 86.4 MB | 11 | 831 |  |
| Window open: Organizer | **134.3 MB** | 89.3 MB | 12 | 843 |  |
| Window open: Startup | **139.2 MB** | 92.6 MB | 13 | 850 |  |
| Window open: Scan | **140.2 MB** | 93.7 MB | 13 | 850 |  |
| Window just closed | **137.8 MB** | 94.2 MB | 13 | 852 | 0.08 % of one core |
| Window closed for 2 minutes (freed) | **8.7 MB** | 83.5 MB | 6 | 830 | 0.26 % of one core |
| Quick menu open | **8.7 MB** | 83.5 MB | 6 | 830 |  |
| Quick menu just closed | **8.7 MB** | 83.5 MB | 6 | 830 |  |
| Quick menu closed for 2 minutes | **12.4 MB** | 83.5 MB | 8 | 832 | 0.31 % of one core |
| Just converted a 12 MP photo to WebP | **298 MB** | 343.4 MB | 16 | 859 |  |
| Six seconds after converting | **2.4 MB** | 87.5 MB | 16 | 856 |  |
| A minute after converting | **6.1 MB** | 87.1 MB | 6 | 839 |  |
| Just removed a background (AI model loaded) | **31.7 MB** | 87.6 MB | 7 | 846 |  |
| A minute after removing a background | **8.9 MB** | 87.5 MB | 5 | 838 |  |
| Just took the sound out of a 1080p video | **26.1 MB** | 87.5 MB | 6 | 841 |  |
| Just made a GIF from it | **27 MB** | 87.5 MB | 6 | 849 |  |
| A minute after the video jobs | **6.1 MB** | 87.5 MB | 5 | 838 |  |
| After 10 rounds of open, Uninstaller, close, convert | **2.6 MB** | 96.3 MB | 6 | 845 |  |
| Two minutes later (window freed, memory tidied) | **6.2 MB** | 89.5 MB | 7 | 840 | 0.13 % of one core |
| Window open again: Home | **68.2 MB** | 95.4 MB | 12 | 863 |  |
| Window open behind another program (2 minutes) | **9.5 MB** | 95.2 MB | 4 | 838 | 0.08 % of one core |
| Space map of Program Files shown (93 s) | **113.8 MB** | 142.2 MB | 7 | 857 |  |
| Space left for 5 minutes (map let go) | **4.2 MB** | 145.4 MB | 4 | 847 | 0.23 % of one core |
| Window closed for 2 minutes after Space | **11 MB** | 132.2 MB | 7 | 848 | 0 % of one core |

- Started (log file opened) after **0.43 s**
- Most memory used at any moment: 356.4 MB
- Processes still running under Rakiza Core at the end: 0
- App folder: 353 MB
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
