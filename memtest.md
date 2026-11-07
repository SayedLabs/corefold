## Memory, step by step

win32, en, dark; numpy loaded at the end: True; Python threads at the end: 2

- Space folders still in memory after the maps were forgotten: 0
- Space folders still in memory at the end: 0

| Step | Private memory | Change | Working set | Python objects / arenas | Widgets alive | Modules |
|---|---|---|---|---|---|---|
| imported | 35.7 MB | +35.7 | 61.7 MB | 14.9 / 17.0 MB | 0 | 244 |
| in the tray | 48.6 MB | +12.9 | 78.8 MB | 19.4 / 21.0 MB | 14 | 268 |
| in the tray, tidied | 48.6 MB | +0.0 | 32.1 MB | 19.4 / 21.0 MB | 14 | 268 |
| window open: Home | 84.3 MB | +35.8 | 128.3 MB | 22.9 / 25.0 MB | 376 | 281 |
| window open: every page seen | 123.6 MB | +39.2 | 178.6 MB | 28.1 / 30.0 MB | 1533 | 301 |
| window closed and freed | 110.7 MB | -12.8 | 47.5 MB | 27.4 / 30.0 MB | 14 | 301 |
| window open: Home | 116.9 MB | +6.2 | 90.7 MB | 26.7 / 30.0 MB | 451 | 301 |
| window closed and freed | 107.4 MB | -9.5 | 45.2 MB | 26.4 / 30.0 MB | 14 | 301 |
| window open: Home | 113.6 MB | +6.2 | 82.0 MB | 26.6 / 30.0 MB | 376 | 301 |
| window closed and freed | 105.3 MB | -8.3 | 44.9 MB | 26.4 / 30.0 MB | 14 | 301 |
| window open: Home | 110.4 MB | +5.1 | 79.9 MB | 26.7 / 30.0 MB | 376 | 301 |
| Space map shown (266,736 files, 116 s) | 228.9 MB | +118.4 | 215.4 MB | 0.0 / 0.0 MB | 504 | 392 |
| Space map forgotten (left for Home) | 208.5 MB | -20.3 | 58.4 MB | 0.0 / 0.0 MB | 511 | 392 |
| window closed and freed | 156.7 MB | -51.8 | 54.4 MB | 34.0 / 64.0 MB | 14 | 392 |
| window open: Home | 165.2 MB | +8.5 | 101.5 MB | 34.1 / 64.0 MB | 376 | 392 |
| window open: every page seen | 178.3 MB | +13.1 | 134.3 MB | 35.3 / 63.0 MB | 1511 | 392 |
| window closed and freed | 157.7 MB | -20.6 | 56.9 MB | 34.7 / 63.0 MB | 14 | 392 |

### Memory map: window open, every page seen

```
process 2996: committed by kind
  mapped                      469.3 MB
  image (program files)       157.8 MB
  private                     116.0 MB
private, by block size (committed)
  < 256 KB            1.4 MB in 55 blocks
  256 KB - 1 MB      33.3 MB in 34 blocks
  1 - 4 MB           25.9 MB in 9 blocks
  4 - 16 MB          55.4 MB in 4 blocks
  16 - 64 MB          0.0 MB in 0 blocks
  > 64 MB             0.0 MB in 0 blocks
biggest private blocks
  0x0169082a0000     15.8 MB
  0x01690a420000     15.8 MB
  0x0169298e0000     15.8 MB
  0x016906b30000      8.0 MB
  0x016904a40000      4.0 MB
  0x016910e80000      4.0 MB
  0x0169143e0000      4.0 MB
  0x01690c4b0000      4.0 MB
  0x01690fb80000      3.1 MB
  0x0169242d0000      2.4 MB
  0x016904640000      2.0 MB
  0x01692b230000      1.4 MB
  0x016909a00000      1.0 MB
  0x016904160000      1.0 MB
  0x016904540000      1.0 MB
  0x016904840000      1.0 MB
  0x016904940000      1.0 MB
  0x016904e40000      1.0 MB
  0x016904f40000      1.0 MB
  0x016905040000      1.0 MB
threads, by the module they started in
    7 ntdll.dll
    7 ucrtbase.dll
    5 Qt6Core.dll
    1 python.exe
    1 combase.dll
    1 WINMM.dll
    1 CRYPT32.dll
```

### Memory map: window closed and freed

```
process 2996: committed by kind
  mapped                      466.0 MB
  image (program files)       157.8 MB
  private                     103.2 MB
private, by block size (committed)
  < 256 KB            1.4 MB in 55 blocks
  256 KB - 1 MB      33.3 MB in 34 blocks
  1 - 4 MB           23.6 MB in 8 blocks
  4 - 16 MB          44.9 MB in 4 blocks
  16 - 64 MB          0.0 MB in 0 blocks
  > 64 MB             0.0 MB in 0 blocks
biggest private blocks
  0x0169082a0000     13.9 MB
  0x01690a420000     12.5 MB
  0x0169298e0000     10.5 MB
  0x016906b30000      8.0 MB
  0x016904a40000      4.0 MB
  0x016910e80000      4.0 MB
  0x0169143e0000      4.0 MB
  0x01690c4b0000      4.0 MB
  0x0169242d0000      2.4 MB
  0x01692b230000      2.2 MB
  0x016904640000      2.0 MB
  0x016909a00000      1.0 MB
  0x016904160000      1.0 MB
  0x016904540000      1.0 MB
  0x016904840000      1.0 MB
  0x016904940000      1.0 MB
  0x016904e40000      1.0 MB
  0x016904f40000      1.0 MB
  0x016905040000      1.0 MB
  0x0169068f0000      1.0 MB
threads, by the module they started in
    7 ntdll.dll
    7 ucrtbase.dll
    5 Qt6Core.dll
    1 python.exe
    1 combase.dll
    1 WINMM.dll
    1 CRYPT32.dll
```

### Memory map: Space map forgotten

```
process 2996: committed by kind
  mapped                      469.3 MB
  image (program files)       182.0 MB
  private                     163.0 MB
private, by block size (committed)
  < 256 KB            1.1 MB in 42 blocks
  256 KB - 1 MB      67.3 MB in 68 blocks
  1 - 4 MB           32.7 MB in 11 blocks
  4 - 16 MB          62.0 MB in 6 blocks
  16 - 64 MB          0.0 MB in 0 blocks
  > 64 MB             0.0 MB in 0 blocks
biggest private blocks
  0x0169082a0000     14.2 MB
  0x01690a420000     13.9 MB
  0x0169298e0000     13.4 MB
  0x016906b30000      7.8 MB
  0x01692d140000      7.6 MB
  0x01692b230000      5.1 MB
  0x016904a40000      4.0 MB
  0x016910e80000      4.0 MB
  0x0169143e0000      4.0 MB
  0x01690c4b0000      4.0 MB
  0x01692ef20000      3.7 MB
  0x01692c200000      3.1 MB
  0x0169305f0000      2.8 MB
  0x0169242d0000      2.4 MB
  0x016904640000      2.0 MB
  0x01690f940000      1.8 MB
  0x016909a00000      1.0 MB
  0x016904160000      1.0 MB
  0x016904540000      1.0 MB
  0x016904840000      1.0 MB
threads, by the module they started in
    4 ucrtbase.dll
    4 Qt6Core.dll
    3 ntdll.dll
    1 python.exe
    1 WINMM.dll
```
