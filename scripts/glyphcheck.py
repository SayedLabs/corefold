"""Every symbol Corefold shows in its words (arrows, stars, ticks, ...) is in Windows' own UI font.

A symbol the font lacks makes Windows look through every font on the PC the first time it's drawn: that
held the Updates page for up to 3 seconds (the ◂ arrow, in Arabic). Draw such a mark as an icon instead
(icons.py). This reads every string in corefold/ (the translations too, not docstrings) and asks Segoe UI,
which every Windows has and which Windows 11's Segoe UI Variable hands Arabic to.

    python scripts/glyphcheck.py      -> one line per missing symbol and "glyphcheck: N problem(s)"
"""
from __future__ import annotations

import ast
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QFont, QFontInfo, QGuiApplication, QRawFont  # noqa: E402

# (strings that are only compared or searched, never drawn)
NOT_DRAWN = {("scan.py", 0x3000)}
# (marks that steer the reading order or join letters: never drawn, so never looked for)
UNDRAWN = {0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}


def strings(path: Path):
    """(line, text) for each string in the file that isn't a docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node.lineno, node.value


def main() -> int:
    QGuiApplication(sys.argv[:1])
    fonts = []
    for family in ("Segoe UI",):
        f = QFont(family)
        if QFontInfo(f).family().lower() == family.lower():  # (the font itself, not one standing in for it)
            fonts.append((family, QRawFont.fromFont(f)))  # (asks the font alone, not the ones Qt falls back to)
    if not fonts:
        print("glyphcheck: Segoe UI isn't on this PC (run it on Windows)")
        return 0
    print("fonts:", ", ".join(name for name, _ in fonts))
    seen: dict[tuple[str, int], list[str]] = {}
    files = sorted((ROOT / "corefold").glob("*.py")) + sorted((ROOT / "corefold" / "atomic").glob("*.py"))
    for path in files:
        for line, text in strings(path):
            for ch in set(text):
                cp = ord(ch)
                if cp < 0x80 or cp in UNDRAWN or unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") \
                        or (path.name, cp) in NOT_DRAWN:
                    continue
                for name, raw in fonts:
                    if not raw.supportsCharacter(cp):
                        seen.setdefault((name, cp), []).append(f"{path.name}:{line}")
    problems = 0
    for (name, cp), where in sorted(seen.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        problems += 1
        ch = chr(cp)
        print(f"FAIL  U+{cp:04X} {unicodedata.name(ch, '?')} isn't in {name}: {', '.join(where[:6])}"
              + (f" (+{len(where) - 6} more)" if len(where) > 6 else ""))
    print(f"glyphcheck: {problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
