"""Lists what takes the space in a built Corefold folder (dist\\Corefold), biggest first.

    python scripts/sizereport.py dist\\Corefold [release-folder]
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path


def mb(n: int) -> str:
    return f"{n / 1048576:8.1f} MB"


def main() -> None:
    root = Path(sys.argv[1])
    files = [(p, p.stat().st_size) for p in root.rglob("*") if p.is_file()]
    total = sum(s for _, s in files)
    print(f"App folder: {mb(total)} in {len(files)} files\n")

    for depth in (1, 2, 3):
        groups: dict[str, int] = defaultdict(int)
        for p, s in files:
            parts = p.relative_to(root).parts
            groups["/".join(parts[:depth]) if len(parts) > depth else "(files)/" + "/".join(parts[:depth - 1])] += s
        print(f"== Folders {depth} deep (over 1 MB)")
        for k, s in sorted(groups.items(), key=lambda kv: -kv[1]):
            if s >= 1048576:
                print(f"{mb(s)}  {k}")
        print()

    print("== By kind")
    kinds: dict[str, int] = defaultdict(int)
    for p, s in files:
        kinds[p.suffix.lower() or "(none)"] += s
    for k, s in sorted(kinds.items(), key=lambda kv: -kv[1])[:20]:
        print(f"{mb(s)}  {k}")
    print()

    print("== Biggest 120 files")
    for p, s in sorted(files, key=lambda f: -f[1])[:120]:
        print(f"{mb(s)}  {p.relative_to(root).as_posix()}")
    print()

    print("== Every Qt file (Qt6*.dll, plugins, translations)")
    for p, s in sorted(files, key=lambda f: f[0].as_posix().lower()):
        rel = p.relative_to(root).as_posix()
        if "PySide6" in rel and (p.suffix.lower() in (".dll", ".pyd", ".qm", ".exe") or "/plugins/" in rel):
            print(f"{mb(s)}  {rel}")
    print()

    if len(sys.argv) > 2:
        print("== Release files")
        for p in sorted(Path(sys.argv[2]).iterdir()):
            if p.is_file():
                print(f"{mb(p.stat().st_size)}  {p.name}")


if __name__ == "__main__":
    main()
