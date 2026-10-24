## How light Corefold is

| Moment | Memory (Task Manager) | Private memory | Threads | Handles | CPU |
|---|---|---|---|---|---|
| Waiting in the tray (3 minutes) | **8.3 MB** | 56.6 MB | 8 | 739 | 0.13 % of one core |
| Window open: Home | **68.7 MB** | 75.4 MB | 15 | 794 | 2.27 % of one core |
| Window open: Converter | **70 MB** | 76.5 MB | 15 | 796 |  |
| Window open: Uninstaller (list loaded) | **84.2 MB** | 80.8 MB | 15 | 847 | 0.08 % of one core |
| Window open: History | **84.3 MB** | 80.8 MB | 15 | 849 |  |
| Window open: Look (wheel previews) | **85.3 MB** | 81.6 MB | 15 | 849 |  |
| Window open: PDF | **88.1 MB** | 83.8 MB | 19 | 862 |  |
| Window open: Organizer | **89.9 MB** | 85.3 MB | 16 | 867 |  |
| Window open: Startup | **98.2 MB** | 87.7 MB | 20 | 898 |  |
| Window open: Scan | **100.2 MB** | 89 MB | 20 | 900 |  |
| Window just closed | **98.1 MB** | 89.5 MB | 20 | 901 | 0.08 % of one core |
| Window closed for 2 minutes (freed) | **8.6 MB** | 81.6 MB | 14 | 880 | 0.36 % of one core |
| Quick menu open | **53.5 MB** | 86.2 MB | 22 | 932 |  |
| Quick menu just closed | **53.8 MB** | 86.2 MB | 22 | 933 |  |
| Quick menu closed for 2 minutes | **11.2 MB** | 77.8 MB | 15 | 911 | 0.42 % of one core |
| Just converted a 12 MP photo to WebP | **292.3 MB** | 340.1 MB | 19 | 923 |  |
| Six seconds after converting | **1.9 MB** | 82.2 MB | 19 | 920 |  |
| A minute after converting | **5.9 MB** | 82 MB | 14 | 915 |  |
| Just removed a background (AI model loaded) | **31 MB** | 83.5 MB | 15 | 924 |  |
| A minute after removing a background | **8.6 MB** | 83.5 MB | 14 | 920 |  |
| Just took the sound out of a 1080p video | **25 MB** | 83.5 MB | 15 | 923 |  |
| Just made a GIF from it | **25.5 MB** | 83.5 MB | 15 | 931 |  |
| A minute after the video jobs | **5.7 MB** | 83.4 MB | 12 | 912 |  |
| After 10 rounds of open, Uninstaller, close, convert | **2.1 MB** | 91.1 MB | 17 | 933 |  |
| Two minutes later (window freed, memory tidied) | **9.9 MB** | 83.1 MB | 14 | 914 | 0.1 % of one core |
| Playing a 720p video | **121 MB** | 137.2 MB | 35 | 1170 | 60.62 % of one core |

- Started (log file opened) after **0.49 s**
- Most memory used at any moment: 348.8 MB
- Processes still running under Corefold at the end: 0
- App folder: 303 MB
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
