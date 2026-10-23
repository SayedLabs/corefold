"""Checks the translations (corefold/lang_ar.py, lang_fr.py, lang_es.py, lang_tr.py) against the app:
every text shown through tr() must be translated, with the same {placeholders}.
Run:  python scripts/check_translations.py  [language]"""
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
    for path in sorted((ROOT / "corefold").glob("*.py")):
        if path.name.startswith("lang_"):
            continue
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            func = node.func if isinstance(node, ast.Call) else None
            is_tr = getattr(func, "id", None) == "tr" or (getattr(func, "attr", None) == "tr" and
                                                          getattr(getattr(func, "value", None), "id", "") == "i18n")
            if isinstance(node, ast.Call) and is_tr and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.setdefault(arg.value, f"{path.name}:{node.lineno}")
    return found


def table_texts() -> dict[str, str]:
    """Texts kept in tables in English and translated when shown (tr(name))."""
    from corefold import converters, dialogs, dialogs2, extras, i18n, moretools, settings, sounds, tour, uninstall, \
        wheel, window
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
                 "SLICE_COLORS", "TEXT_SIZE_NAMES", "WHEEL_POSITIONS", "APPEARANCES", "MENU_STYLES",
                 "TOAST_CORNERS", "TOAST_TIMES", "WINDOW_TEXT", "UNUSED_MONTHS", "BACKUP_DAYS", "EXTRACT_MODES",
                 "VIEWER_WHEEL", "SLIDESHOW_SECONDS"):
        add(getattr(settings, name).values(), name)
    add(sounds.DONE_SOUNDS.values(), "DONE_SOUNDS")
    add([i18n.LANGUAGES["system"]], "LANGUAGES")
    add(wheel.GROUP_NAMES.values(), "GROUP_NAMES")
    add([p for p in window.PAGES if p in window.NAV], "PAGES")  # the others are internal
    add([label for _, label, _ in window.SETTINGS_TABS], "SETTINGS_TABS")
    add(window.SETTINGS_SUBTABS.values(), "SETTINGS_SUBTABS")
    add([e[1] for e in window.NAV if isinstance(e, tuple)], "NAV")
    add(window.NAV_LABELS.values(), "NAV_LABELS")
    from corefold import tools as all_tools
    for tool in all_tools.TOOLS:
        add([tool.name, tool.nav, tool.line, *tool.features], "tools.TOOLS")
    add([converters.PASSWORD_ERROR], "PASSWORD_ERROR")
    add([v[0] for v in moretools.CROP_RATIOS.values()], "CROP_RATIOS")
    add(moretools.STICKERS_IMAGE.values(), "STICKERS_IMAGE")
    add(moretools.STICKERS_VIDEO.values(), "STICKERS_VIDEO")
    add([t for _, t in dialogs2.RenameFilesDialog.MODES], "RenameFilesDialog.MODES")
    add(uninstall.REASONS.values(), "REASONS")
    from corefold import appspage
    add(appspage.SHOW.values(), "appspage.SHOW")
    add([x for pair in appspage.UNUSED.values() for x in pair], "appspage.UNUSED")
    from corefold import viewer
    add(viewer.DETAIL_LABELS, "viewer.DETAIL_LABELS")
    for _, title, text in tour.STEPS:
        add([title, text], "tour.STEPS")
    return out


def check(lang: str, wanted: dict[str, str]) -> int:
    from corefold.i18n import table_for
    STRINGS = table_for(lang)
    problems = 0
    for text, where in sorted(wanted.items(), key=lambda kv: kv[1]):
        if text not in STRINGS:
            print(f"[{lang}] missing  {where}: {text!r}")
            problems += 1
            continue
        a, b = set(PLACEHOLDER.findall(text)), set(PLACEHOLDER.findall(STRINGS[text]))
        if a != b:
            print(f"[{lang}] placeholders differ  {where}: {text!r} has {sorted(a)}, translation has {sorted(b)}")
            problems += 1
        try:
            STRINGS[text].format(**{n: "x" for n in a})
        except (KeyError, IndexError, ValueError) as e:
            print(f"[{lang}] broken translation  {where}: {text!r} ({e})")
            problems += 1
    unused = sorted(set(STRINGS) - set(wanted))
    for text in unused:
        print(f"[{lang}] unused   {text!r}")
    print(f"[{lang}] {len(wanted)} texts, {problems} problem(s), {len(unused)} unused")
    return problems


def main() -> int:
    wanted = {**table_texts(), **literal_texts()}
    langs = sys.argv[1:] or ["ar", "fr", "es", "tr"]
    return 1 if sum(check(lang, wanted) for lang in langs) else 0


if __name__ == "__main__":
    sys.exit(main())
