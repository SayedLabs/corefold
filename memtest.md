## Memory, step by step

win32, en, dark; numpy loaded at the end: True; Python threads at the end: 2

- Space folders still in memory after the maps were forgotten: 0
- Space folders still in memory at the end: 0

| Step | Private memory | Change | Working set | Python objects / arenas | Widgets alive | Modules |
|---|---|---|---|---|---|---|
| imported | 36.5 MB | +36.5 | 62.2 MB | 15.0 / 17.0 MB | 0 | 245 |
| in the tray | 49.0 MB | +12.5 | 79.5 MB | 19.6 / 21.0 MB | 14 | 270 |
| in the tray, tidied | 49.0 MB | +0.0 | 32.4 MB | 19.6 / 21.0 MB | 14 | 270 |
| window open: Home | 86.2 MB | +37.2 | 129.9 MB | 23.4 / 26.0 MB | 392 | 284 |
| window open: every page seen | 125.8 MB | +39.6 | 181.1 MB | 28.9 / 31.0 MB | 1605 | 304 |
| window closed and freed | 112.4 MB | -13.4 | 48.9 MB | 28.1 / 31.0 MB | 14 | 304 |
| window open: Home | 117.9 MB | +5.5 | 89.5 MB | 28.5 / 31.0 MB | 467 | 304 |
| window closed and freed | 114.8 MB | -3.1 | 49.2 MB | 28.3 / 31.0 MB | 14 | 304 |
| window open: Home | 118.4 MB | +3.6 | 85.7 MB | 28.5 / 31.0 MB | 392 | 304 |
| window closed and freed | 115.4 MB | -3.1 | 49.1 MB | 28.3 / 31.0 MB | 14 | 304 |
| window open: Home | 118.5 MB | +3.1 | 85.1 MB | 28.5 / 31.0 MB | 392 | 304 |
| Space map shown (266,736 files, 208 s) | 162.8 MB | +44.3 | 128.4 MB | 59.8 / 66.0 MB | 520 | 393 |
| Space map forgotten (left for Home) | 165.4 MB | +2.6 | 59.6 MB | 35.9 / 66.0 MB | 527 | 393 |
| window closed and freed | 152.3 MB | -13.1 | 58.2 MB | 35.6 / 66.0 MB | 14 | 393 |
| window open: Home | 156.4 MB | +4.2 | 82.7 MB | 35.8 / 66.0 MB | 392 | 393 |
| window open: every page seen | 168.1 MB | +11.7 | 132.0 MB | 35.9 / 65.0 MB | 1583 | 393 |
| window closed and freed | 150.7 MB | -17.5 | 57.9 MB | 35.2 / 65.0 MB | 14 | 393 |

### Memory map: window open, every page seen

```
process 7204: committed by kind
  mapped                      469.3 MB
  image (program files)       157.8 MB
  private                     118.2 MB
private, by block size (committed)
  < 256 KB            1.3 MB in 48 blocks
  256 KB - 1 MB      34.3 MB in 35 blocks
  1 - 4 MB           27.3 MB in 9 blocks
  4 - 16 MB          55.4 MB in 4 blocks
  16 - 64 MB          0.0 MB in 0 blocks
  > 64 MB             0.0 MB in 0 blocks
biggest private blocks
  0x0286ac680000     15.8 MB
  0x0286ae680000     15.8 MB
  0x0286cdc30000     15.8 MB
  0x0286ab250000      8.0 MB
  0x0286a8d10000      4.0 MB
  0x0286b52d0000      4.0 MB
  0x0286b8830000      4.0 MB
  0x0286b0880000      4.0 MB
  0x0286b3fd0000      3.1 MB
  0x0286cf420000      2.8 MB
  0x0286c8720000      2.4 MB
  0x0286a88f0000      2.0 MB
  0x0286add60000      1.0 MB
  0x0286a8490000      1.0 MB
  0x0286a87f0000      1.0 MB
  0x0286a8b00000      1.0 MB
  0x0286a8c10000      1.0 MB
  0x0286a9110000      1.0 MB
  0x0286a9210000      1.0 MB
  0x0286a9340000      1.0 MB
threads, by the module they started in
    6 ntdll.dll
    5 Qt6Core.dll
    4 ucrtbase.dll
    1 python.exe
    1 combase.dll
    1 WINMM.dll
    1 CRYPT32.dll
```

### Memory map: window closed and freed

```
process 7204: committed by kind
  mapped                      466.0 MB
  image (program files)       157.8 MB
  private                     104.9 MB
private, by block size (committed)
  < 256 KB            1.3 MB in 48 blocks
  256 KB - 1 MB      34.3 MB in 35 blocks
  1 - 4 MB           23.8 MB in 8 blocks
  4 - 16 MB          45.5 MB in 4 blocks
  16 - 64 MB          0.0 MB in 0 blocks
  > 64 MB             0.0 MB in 0 blocks
biggest private blocks
  0x0286ac680000     14.5 MB
  0x0286ae680000     12.6 MB
  0x0286cdc30000     10.6 MB
  0x0286ab250000      7.8 MB
  0x0286a8d10000      4.0 MB
  0x0286b52d0000      4.0 MB
  0x0286b8830000      4.0 MB
  0x0286b0880000      4.0 MB
  0x0286cf420000      2.5 MB
  0x0286c8720000      2.4 MB
  0x0286a88f0000      2.0 MB
  0x0286add60000      1.0 MB
  0x0286a8490000      1.0 MB
  0x0286a87f0000      1.0 MB
  0x0286a8b00000      1.0 MB
  0x0286a8c10000      1.0 MB
  0x0286a9110000      1.0 MB
  0x0286a9210000      1.0 MB
  0x0286a9340000      1.0 MB
  0x0286aac30000      1.0 MB
threads, by the module they started in
    6 ntdll.dll
    5 Qt6Core.dll
    4 ucrtbase.dll
    1 python.exe
    1 combase.dll
    1 WINMM.dll
    1 CRYPT32.dll
```

### Memory map: Space map forgotten

```
process 7204: committed by kind
  mapped                      469.3 MB
  image (program files)       182.0 MB
  private                     158.1 MB
private, by block size (committed)
  < 256 KB            1.1 MB in 42 blocks
  256 KB - 1 MB      69.3 MB in 70 blocks
  1 - 4 MB           26.3 MB in 9 blocks
  4 - 16 MB          61.5 MB in 5 blocks
  16 - 64 MB          0.0 MB in 0 blocks
  > 64 MB             0.0 MB in 0 blocks
biggest private blocks
  0x0286ac680000     15.5 MB
  0x0286ae680000     15.4 MB
  0x0286cdc30000     14.1 MB
  0x0286cf420000      8.5 MB
  0x0286ab250000      8.0 MB
  0x0286a8d10000      4.0 MB
  0x0286b52d0000      4.0 MB
  0x0286b8830000      4.0 MB
  0x0286b0880000      4.0 MB
  0x0286d03f0000      3.1 MB
  0x0286c8720000      2.4 MB
  0x0286a88f0000      2.0 MB
  0x0286d0710000      1.8 MB
  0x0286add60000      1.0 MB
  0x0286a8490000      1.0 MB
  0x0286a87f0000      1.0 MB
  0x0286a8b00000      1.0 MB
  0x0286a8c10000      1.0 MB
  0x0286a9110000      1.0 MB
  0x0286a9210000      1.0 MB
threads, by the module they started in
    4 ucrtbase.dll
    4 Qt6Core.dll
    3 ntdll.dll
    1 python.exe
    1 WINMM.dll
```
