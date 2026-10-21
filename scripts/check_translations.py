"""Checks satsuma/lang_ar.py against the app: every text shown through tr() must have an Arabic
version with the same {placeholders}. Run:  python scripts/check_translations.py"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def literal_texts() -> dict[str, str]:
    """Every tr("…") in the source, with where it is."""
    found: dict[str, str] = {}
    for path in sorted((ROOT / "satsuma").glob("*.py")):
        if path.name.startswith("lang_"):
            continue
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.setdefault(arg.value, f"{path.name}:{node.lineno}")
    return found


def table_texts() -> dict[str, str]:
    """Texts kept in tables in English and translated when shown (tr(name))."""
    from satsuma import converters, dialogs, extras, i18n, settings, sounds, wheel, window
    out: dict[str, str] = {}

    def add(values, where):
        for v in values:
            if v:
                out.setdefault(v, where)
    add(converters.CATEGORY_NAMES.values(), "CATEGORY_NAMES")
    add(converters.FORMAT_NAMES.values(), "FORMAT_NAMES")
    add([v[0] for v in converters.RESIZE_PRESETS.values()], "RESIZE_PRESETS")
    add([v[0] for v in converters.ROTATIONS.values()], "ROTATIONS")
    add(extras.POSITIONS.values(), "POSITIONS")
    add([k.capitalize() for k in extras.WM_SIZES], "WM_SIZES")
    add([u for _, u in extras.FIT_PRESETS], "FIT_PRESETS")
    add([t for _, t in dialogs.SPLITS], "SPLITS")
    add(["Fast", "Normal", "Smallest file (slower)"], "PackDialog levels")
    add([v[0] for v in settings.ACCENTS.values()], "ACCENTS")
    for name in ("SHORTCUT_LABELS", "WHEEL_SIZE_NAMES", "WHEEL_STYLES", "LABEL_STYLES", "HOVER_STYLES",
                 "SLICE_COLORS", "TEXT_SIZE_NAMES", "WHEEL_POSITIONS", "APPEARANCES", "MENU_STYLES"):
        add(getattr(settings, name).values(), name)
    add(sounds.DONE_SOUNDS.values(), "DONE_SOUNDS")
    add([i18n.LANGUAGES["system"]], "LANGUAGES")
    add(wheel.GROUP_NAMES.values(), "GROUP_NAMES")
    add(window.PAGES, "PAGES")
    add([converters.PASSWORD_ERROR], "PASSWORD_ERROR")
    return out


def main() -> int:
    from satsuma.lang_ar import STRINGS
    wanted = {**table_texts(), **literal_texts()}
    problems = 0
    for text, where in sorted(wanted.items(), key=lambda kv: kv[1]):
        if text not in STRINGS:
            print(f"missing  {where}: {text!r}")
            problems += 1
            continue
        a, b = set(PLACEHOLDER.findall(text)), set(PLACEHOLDER.findall(STRINGS[text]))
        if a != b:
            print(f"placeholders differ  {where}: {text!r} has {sorted(a)}, Arabic has {sorted(b)}")
            problems += 1
        try:
            STRINGS[text].format(**{n: "x" for n in a})
        except (KeyError, IndexError, ValueError) as e:
            print(f"broken Arabic text  {where}: {text!r} ({e})")
            problems += 1
    unused = sorted(set(STRINGS) - set(wanted))
    for text in unused:
        print(f"unused   {text!r}")
    print(f"{len(wanted)} texts, {problems} problem(s), {len(unused)} unused")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
