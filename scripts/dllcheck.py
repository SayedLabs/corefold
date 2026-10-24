"""Checks that every program and library in a built Corefold folder finds the DLLs it needs, so
nothing taken out of the build (see scripts\\build.ps1) is still used by something left in it.

    python scripts/dllcheck.py dist\\Corefold

Prints each missing DLL and "dllcheck: N problem(s)". Needs pefile (comes with PyInstaller)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pefile


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    files = [p for p in root.rglob("*") if p.suffix.lower() in (".dll", ".pyd", ".exe") and p.is_file()]
    here = {}
    for p in files:
        here.setdefault(p.name.lower(), []).append(p.parent)
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    # folders Windows looks in for a DLL that the app's own code loads (PyInstaller adds these)
    shared = [root, root / "_internal", root / "_internal" / "PySide6", root / "_internal" / "shiboken6"]
    problems = 0
    for p in sorted(files):
        try:
            pe = pefile.PE(str(p), fast_load=True)
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                                   pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        except pefile.PEFormatError:
            continue
        names = [e.dll.decode(errors="replace") for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])]
        delayed = {e.dll.decode(errors="replace").lower() for e in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])}
        names += sorted(delayed)
        pe.close()
        for name in names:
            low = name.lower()
            if low.startswith(("api-ms-win-", "ext-ms-")):
                continue
            if (p.parent / name).exists() or any((d / name).exists() for d in shared) or (system / name).exists():
                continue
            if low in here and low not in delayed:
                # found elsewhere in the folder: fine when it's loaded from there first (Qt plugins)
                continue
            kind = "may need (loaded later)" if low in delayed else "needs"
            print(f"missing  {p.relative_to(root).as_posix()} {kind} {name}")
            problems += 0 if low in delayed else 1
    print(f"dllcheck: {problems} problem(s) in {len(files)} files")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
