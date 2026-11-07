## Memory, step by step

win32, en, dark; numpy loaded at the end: True; Python threads at the end: 2

- Space folders still in memory after the maps were forgotten: 0
- Space folders still in memory at the end: 0

| Step | Private memory | Change | Working set | Python objects / arenas | Widgets alive | Modules |
|---|---|---|---|---|---|---|
| imported | 36.5 MB | +36.5 | 62.8 MB | 14.9 / 17.0 MB | 0 | 244 |
| in the tray | 49.3 MB | +12.8 | 78.7 MB | 19.4 / 21.0 MB | 14 | 268 |
| in the tray, tidied | 49.3 MB | -0.0 | 32.1 MB | 19.4 / 21.0 MB | 14 | 268 |
| window open: Home | 84.4 MB | +35.1 | 128.2 MB | 22.9 / 25.0 MB | 376 | 281 |
| window open: every page seen | 122.3 MB | +37.9 | 177.4 MB | 28.1 / 30.0 MB | 1533 | 301 |
| window closed and freed | 109.0 MB | -13.3 | 47.5 MB | 27.4 / 30.0 MB | 14 | 301 |
| window open: Home | 115.1 MB | +6.0 | 90.1 MB | 26.7 / 30.0 MB | 451 | 301 |
| window closed and freed | 106.3 MB | -8.8 | 45.1 MB | 26.5 / 30.0 MB | 14 | 301 |
| window open: Home | 111.1 MB | +4.8 | 82.0 MB | 26.7 / 30.0 MB | 376 | 301 |
| window closed and freed | 108.1 MB | -3.1 | 44.9 MB | 26.5 / 30.0 MB | 14 | 301 |
| window open: Home | 111.7 MB | +3.6 | 80.8 MB | 26.7 / 30.0 MB | 376 | 301 |
| Space map shown (266,736 files, 79 s) | 227.3 MB | +115.6 | 214.8 MB | 0.0 / 0.0 MB | 504 | 392 |
| Space map forgotten (left for Home) | 205.7 MB | -21.6 | 58.2 MB | 0.0 / 0.0 MB | 511 | 392 |
| window closed and freed | 148.8 MB | -56.9 | 54.0 MB | 34.0 / 64.0 MB | 14 | 392 |
| window open: Home | 154.3 MB | +5.5 | 100.9 MB | 34.1 / 64.0 MB | 376 | 392 |
| window open: every page seen | 172.3 MB | +17.9 | 134.4 MB | 35.4 / 64.0 MB | 1522 | 392 |
| window closed and freed | 151.9 MB | -20.4 | 56.7 MB | 34.7 / 64.0 MB | 14 | 392 |

### Memory map: window open, every page seen

```
process 7184: committed by kind
  mapped                      469.3 MB
  image (program files)       157.8 MB
  private                     114.7 MB
private, by block size (committed)
  < 256 KB            1.3 MB in 49 blocks
  256 KB - 1 MB      33.6 MB in 35 blocks
  1 - 4 MB           24.5 MB in 8 blocks
  4 - 16 MB          55.4 MB in 4 blocks
  16 - 64 MB          0.0 MB in 0 blocks
  > 64 MB             0.0 MB in 0 blocks
biggest private blocks
  0x013b195e0000     15.8 MB
  0x013b78010000     15.8 MB
  0x013b7a010000     15.8 MB
  0x013b768a0000      8.0 MB
  0x013b00b60000      4.0 MB
  0x013b746b0000      4.0 MB
  0x013b040e0000      4.0 MB
  0x013b7c110000      4.0 MB
  0x013b7f860000      3.1 MB
  0x013b13fd0000      2.4 MB
  0x013b742b0000      2.0 MB
  0x013b796f0000      1.0 MB
  0x013b192e0000      1.0 MB
  0x013b193e0000      1.0 MB
  0x013b194e0000      1.0 MB
  0x013b1a830000      1.0 MB
  0x013b1ab10000      1.0 MB
  0x013b1ac20000      1.0 MB
  0x013b73e50000      1.0 MB
  0x013b741b0000      1.0 MB
threads, by the module they started in
    7 ntdll.dll
    5 Qt6Core.dll
    4 ucrtbase.dll
    1 python.exe
    1 combase.dll
    1 WINMM.dll
    1 CRYPT32.dll
```

### Memory map: window closed and freed

```
process 7184: committed by kind
  mapped                      466.0 MB
  image (program files)       157.8 MB
  private                     101.5 MB
private, by block size (committed)
  < 256 KB            1.3 MB in 49 blocks
  256 KB - 1 MB      33.9 MB in 35 blocks
  1 - 4 MB           21.4 MB in 7 blocks
  4 - 16 MB          45.0 MB in 4 blocks
  16 - 64 MB          0.0 MB in 0 blocks
  > 64 MB             0.0 MB in 0 blocks
biggest private blocks
  0x013b78010000     14.3 MB
  0x013b7a010000     12.2 MB
  0x013b195e0000     10.6 MB
  0x013b768a0000      7.9 MB
  0x013b00b60000      4.0 MB
  0x013b746b0000      4.0 MB
  0x013b040e0000      4.0 MB
  0x013b7c110000      4.0 MB
  0x013b13fd0000      2.4 MB
  0x013b742b0000      2.0 MB
  0x013b796f0000      1.0 MB
  0x013b192e0000      1.0 MB
  0x013b193e0000      1.0 MB
  0x013b194e0000      1.0 MB
  0x013b1a830000      1.0 MB
  0x013b1ab10000      1.0 MB
  0x013b1ac20000      1.0 MB
  0x013b73e50000      1.0 MB
  0x013b741b0000      1.0 MB
  0x013b744b0000      1.0 MB
threads, by the module they started in
    7 ntdll.dll
    5 Qt6Core.dll
    4 ucrtbase.dll
    1 python.exe
    1 combase.dll
    1 WINMM.dll
    1 CRYPT32.dll
```

### Memory map: Space map forgotten

```
process 7184: committed by kind
  mapped                      469.3 MB
  image (program files)       182.0 MB
  private                     163.5 MB
private, by block size (committed)
  < 256 KB            1.2 MB in 42 blocks
  256 KB - 1 MB      67.3 MB in 68 blocks
  1 - 4 MB           28.7 MB in 10 blocks
  4 - 16 MB          66.4 MB in 7 blocks
  16 - 64 MB          0.0 MB in 0 blocks
  > 64 MB             0.0 MB in 0 blocks
biggest private blocks
  0x013b78010000     15.1 MB
  0x013b7a010000     13.9 MB
  0x013b195e0000     12.1 MB
  0x013b768a0000      8.0 MB
  0x013b1ad20000      7.5 MB
  0x013b1d4d0000      5.3 MB
  0x013b1f2b0000      4.4 MB
  0x013b00b60000      4.0 MB
  0x013b746b0000      4.0 MB
  0x013b040e0000      4.0 MB
  0x013b7c110000      4.0 MB
  0x013b1c350000      3.1 MB
  0x013b13fd0000      2.4 MB
  0x013b20980000      2.4 MB
  0x013b742b0000      2.0 MB
  0x013b1bdf0000      1.8 MB
  0x013b796f0000      1.0 MB
  0x013b192e0000      1.0 MB
  0x013b193e0000      1.0 MB
  0x013b194e0000      1.0 MB
threads, by the module they started in
    5 Qt6Core.dll
    4 ucrtbase.dll
    2 ntdll.dll
    1 python.exe
    1 WINMM.dll
```
