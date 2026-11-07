## Memory, step by step

win32, en, dark; numpy loaded at the end: True; Python threads at the end: 2

- Space folders still in memory after the maps were forgotten: 0
- Space folders still in memory at the end: 0

| Step | Private memory | Change | Working set | Python objects / arenas | Widgets alive | Modules |
|---|---|---|---|---|---|---|
| imported | 38.9 MB | +38.9 | 65.4 MB | 14.9 / 17.0 MB | 0 | 244 |
| in the tray | 46.1 MB | +7.1 | 76.9 MB | 19.4 / 21.0 MB | 14 | 268 |
| in the tray, tidied | 45.9 MB | -0.2 | 32.3 MB | 19.4 / 21.0 MB | 14 | 268 |
| window open: Home | 85.8 MB | +39.9 | 130.4 MB | 22.9 / 25.0 MB | 376 | 281 |
| window open: every page seen | 120.6 MB | +34.8 | 176.6 MB | 28.2 / 31.0 MB | 1555 | 301 |
| window closed and freed | 99.7 MB | -20.9 | 47.6 MB | 27.4 / 31.0 MB | 14 | 301 |
| window open: Home | 108.3 MB | +8.6 | 90.9 MB | 26.7 / 31.0 MB | 451 | 301 |
| window closed and freed | 96.7 MB | -11.6 | 46.1 MB | 26.5 / 31.0 MB | 14 | 301 |
| window open: Home | 105.1 MB | +8.4 | 83.6 MB | 26.7 / 31.0 MB | 376 | 301 |
| window closed and freed | 96.2 MB | -8.9 | 45.0 MB | 26.5 / 31.0 MB | 14 | 301 |
| window open: Home | 104.7 MB | +8.6 | 82.6 MB | 26.7 / 31.0 MB | 376 | 301 |
| Space map shown (266,736 files, 144 s) | 198.3 MB | +93.5 | 183.2 MB | 0.0 / 0.0 MB | 504 | 392 |
| Space map forgotten (left for Home) | 166.3 MB | -32.0 | 57.6 MB | 0.0 / 0.0 MB | 511 | 392 |
| window closed and freed | 134.1 MB | -32.1 | 54.5 MB | 34.0 / 64.0 MB | 14 | 392 |
| window open: Home | 143.0 MB | +8.8 | 102.1 MB | 34.1 / 64.0 MB | 376 | 392 |
| window open: every page seen | 158.8 MB | +15.8 | 131.9 MB | 35.4 / 64.0 MB | 1522 | 392 |
| window closed and freed | 138.1 MB | -20.7 | 57.0 MB | 34.7 / 64.0 MB | 14 | 392 |

### Memory map: window open, every page seen

```
process 8844: committed by kind
  mapped                      469.3 MB
  image (program files)       157.8 MB
  private                     113.1 MB
private, by block size (committed)
  < 256 KB            1.1 MB in 43 blocks
  256 KB - 1 MB      32.3 MB in 33 blocks
  1 - 4 MB           19.9 MB in 6 blocks
  4 - 16 MB          59.8 MB in 5 blocks
  16 - 64 MB          0.0 MB in 0 blocks
  > 64 MB             0.0 MB in 0 blocks
biggest private blocks
  0x026164000000     15.2 MB
  0x026180000000     13.7 MB
  0x026160000000     13.4 MB
  0x026181000000      8.9 MB
  0x02615d000000      8.7 MB
  0x02616a5a0000      4.0 MB
  0x02616db00000      4.0 MB
  0x026162b50000      4.0 MB
  0x0261692d0000      3.1 MB
  0x026163000000      2.4 MB
  0x02617d9f0000      2.4 MB
  0x02615c820000      1.0 MB
  0x02615ca90000      1.0 MB
  0x02615cbc0000      1.0 MB
  0x02615ccd0000      1.0 MB
  0x02615cdd0000      1.0 MB
  0x02615ced0000      1.0 MB
  0x02615e000000      1.0 MB
  0x02615f8f0000      1.0 MB
  0x026161800000      1.0 MB
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
process 8844: committed by kind
  mapped                      466.0 MB
  image (program files)       157.8 MB
  private                      92.2 MB
private, by block size (committed)
  < 256 KB            1.1 MB in 43 blocks
  256 KB - 1 MB      32.3 MB in 33 blocks
  1 - 4 MB           20.7 MB in 6 blocks
  4 - 16 MB          38.1 MB in 4 blocks
  16 - 64 MB          0.0 MB in 0 blocks
  > 64 MB             0.0 MB in 0 blocks
biggest private blocks
  0x026160000000     11.6 MB
  0x026164000000     10.0 MB
  0x02615d000000      8.7 MB
  0x026180000000      7.8 MB
  0x02616a5a0000      4.0 MB
  0x02616db00000      4.0 MB
  0x026162b50000      4.0 MB
  0x026181000000      3.9 MB
  0x026163000000      2.4 MB
  0x02617d9f0000      2.4 MB
  0x02615c820000      1.0 MB
  0x02615ca90000      1.0 MB
  0x02615cbc0000      1.0 MB
  0x02615ccd0000      1.0 MB
  0x02615cdd0000      1.0 MB
  0x02615ced0000      1.0 MB
  0x02615e000000      1.0 MB
  0x02615f8f0000      1.0 MB
  0x026161800000      1.0 MB
  0x026161900000      1.0 MB
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
process 8844: committed by kind
  mapped                      469.3 MB
  image (program files)       182.0 MB
  private                     169.3 MB
private, by block size (committed)
  < 256 KB            0.9 MB in 38 blocks
  256 KB - 1 MB      65.3 MB in 66 blocks
  1 - 4 MB           17.5 MB in 5 blocks
  4 - 16 MB          85.6 MB in 9 blocks
  16 - 64 MB          0.0 MB in 0 blocks
  > 64 MB             0.0 MB in 0 blocks
biggest private blocks
  0x026164000000     14.0 MB
  0x026160000000     13.0 MB
  0x026180000000     11.8 MB
  0x026181000000     11.8 MB
  0x026163000000     11.7 MB
  0x02615d000000      8.8 MB
  0x026183000000      5.5 MB
  0x026186000000      5.0 MB
  0x026185000000      4.1 MB
  0x02616a5a0000      4.0 MB
  0x02616db00000      4.0 MB
  0x026162b50000      4.0 MB
  0x026182000000      3.1 MB
  0x02617d9f0000      2.4 MB
  0x02615c820000      1.0 MB
  0x02615ca90000      1.0 MB
  0x02615cbc0000      1.0 MB
  0x02615ccd0000      1.0 MB
  0x02615cdd0000      1.0 MB
  0x02615ced0000      1.0 MB
threads, by the module they started in
    4 ntdll.dll
    4 ucrtbase.dll
    4 Qt6Core.dll
    1 python.exe
    1 WINMM.dll
```
