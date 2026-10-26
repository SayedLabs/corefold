"""The fonts of Corefold PDF's page editor: finding the font a PDF's words were written in, so a changed
line keeps the same look, and the fonts offered for new text.

A line's font is looked for on this PC (Windows' own fonts and any the user installed), then among the
fonts Corefold brings (assets/fonts: much-used Arabic and English fonts Windows doesn't have, free under
the SIL Open Font License, and only ones whose Arabic stays searchable in the saved PDF), and when it's in neither, the closest look is used (serif, plain or
fixed-width, with every letter of the line). The copy of the font inside a PDF isn't used: it usually
has only the letters that were on its pages (none for new words) and has lost Arabic's joined letter
forms. Nothing here is loaded until the editor is opened."""
from __future__ import annotations

import logging
import re
import unicodedata

from .core import bundled

log = logging.getLogger("corefold")

# names PDFs use for fonts that are known by another name on Windows (keys: lower case, letters only)
ALIASES = {
    "helvetica": "Arial", "helveticaneue": "Arial", "arialunicodems": "Arial", "arimo": "Arial",
    "liberationsans": "Arial", "nimbussans": "Arial", "dejavusans": "Verdana",
    "times": "Times New Roman", "timesroman": "Times New Roman", "tinos": "Times New Roman",
    "liberationserif": "Times New Roman", "nimbusroman": "Times New Roman",
    "courier": "Courier New", "cousine": "Courier New", "liberationmono": "Courier New",
    "nimbusmono": "Courier New", "dejavusansmono": "Consolas",
    "carlito": "Calibri", "caladea": "Cambria", "symbol": "Symbol", "zapfdingbats": "Wingdings",
    # Arabic fonts of other systems -> a font with the same look
    "geezapro": "Tahoma", "droidarabicnaskh": "Markazi Text", "notonaskharabic": "Markazi Text",
    "amiri": "Markazi Text", "scheherazade": "Markazi Text", "scheherazadenew": "Markazi Text",
    "lateef": "Markazi Text", "kfgqpcuthmanicscripthafs": "Markazi Text", "droidarabickufi": "Cairo",
    "notokufiarabic": "Cairo", "notosansarabic": "IBM Plex Sans Arabic", "vazirmatn": "IBM Plex Sans Arabic",
    "adobearabic": "Traditional Arabic", "lotus": "Traditional Arabic", "badr": "Traditional Arabic",
    "mitra": "Traditional Arabic", "nazanin": "Traditional Arabic", "dinnextltarabic": "Tajawal",
    "gessone": "Tajawal", "gesstwo": "Tajawal", "gessthree": "Tajawal", "dubai": "Tajawal",
    "frutigerltarabic": "Almarai", "neosansarabic": "Almarai", "hacen": "Cairo", "reemkufi": "El Messiri",
}
# when the font isn't found: the first of these that has every letter (by the look of the PDF's font)
FALLBACK = {
    "fixed": ("Courier New", "Consolas", "Lucida Console"),
    "serif": ("Times New Roman", "Markazi Text", "Traditional Arabic", "Georgia"),
    "sans": ("Arial", "Segoe UI", "Tahoma", "IBM Plex Sans Arabic", "Verdana"),
}
# offered first for new text (then every other font on the PC that writes English or Arabic)
POPULAR = ("Arial", "Calibri", "Segoe UI", "Times New Roman", "Georgia", "Cambria", "Courier New", "Tahoma",
           "Verdana", "Roboto", "Open Sans", "Montserrat", "Poppins", "Cairo", "Tajawal", "Almarai", "IBM Plex Sans Arabic", "El Messiri",
           "Markazi Text", "Sakkal Majalla", "Traditional Arabic", "Simplified Arabic", "Arabic Typesetting")

_ready = False
_families: dict[str, str] | None = None   # key -> family, of every font Qt can use


def key(name: str) -> str:
    return re.sub(r"[^0-9a-z]", "", name.lower())


def ready():
    """The fonts Corefold brings, put in Qt's list (once, when the editor first needs a font)."""
    global _ready, _families
    if _ready:
        return
    _ready = True
    from PySide6.QtGui import QFontDatabase
    folder = bundled("assets", "fonts")
    for f in sorted(folder.glob("*.ttf")) if folder else ():
        if QFontDatabase.addApplicationFont(str(f)) < 0:
            log.debug("a font couldn't be loaded: %s", f.name)
    _families = None


def _all() -> dict[str, str]:
    global _families
    if _families is None:
        from PySide6.QtGui import QFontDatabase
        _families = {}
        for fam in QFontDatabase.families():
            if not fam.startswith("@"):
                _families.setdefault(key(fam), fam)
    return _families


def find(name: str) -> str:
    """The font on this PC (or brought by Corefold) for a PDF's font name, or "" when there's none:
    "Times New Roman", "TimesNewRoman", "Helvetica", "Cairo SemiBold" -> "Cairo", …"""
    if not name:
        return ""
    ready()
    fams = _all()
    words = name.replace("_", " ").split()
    while words:
        k = key(" ".join(words))
        for k2 in (k, key(ALIASES.get(k, ""))):
            if k2 and k2 in fams:
                return fams[k2]
        words.pop()  # ("Calibri Light", "Segoe UI Semibold", "Cairo Black": the family without the weight)
    # "TimesNewRomanPS", "ArialNarrow": the longest family the name starts with
    k = key(name)
    best = ""
    for k2, fam in fams.items():
        if len(k2) >= 4 and k.startswith(k2) and len(k2) > len(key(best)):
            best = fam
    return best


def covers(family: str, text: str, bold: bool = False, italic: bool = False) -> bool:
    """Whether the font has every letter of the text."""
    from PySide6.QtGui import QFont, QRawFont
    f = QFont(family)
    f.setBold(bold)
    f.setItalic(italic)
    raw = QRawFont.fromFont(f)
    if not raw.isValid() or key(raw.familyName()) != key(family):
        return False
    letters = {ch for ch in unicodedata.normalize("NFC", text) if not ch.isspace() and
               unicodedata.category(ch)[0] not in "CZ"}
    return all(raw.supportsCharacter(ord(ch)) for ch in letters)


def fallback(serif: bool, fixed: bool, text: str, bold: bool = False, italic: bool = False) -> str:
    fams = _all()
    names = FALLBACK["fixed" if fixed else "serif" if serif else "sans"]
    have = [fams[key(n)] for n in names if key(n) in fams]
    for fam in have:
        if covers(fam, text, bold, italic):
            return fam
    return have[0] if have else "Arial"


def _resolve(name: str, d: dict, text: str) -> str:
    bold, italic = bool(d.get("bold")), bool(d.get("italic"))
    fam = find(name)
    if fam and covers(fam, text, bold, italic):
        return fam
    k = key(name)
    serif = (bool(d.get("serif")) or any(w in k for w in ("serif", "naskh", "roman", "garamond", "times"))) \
        and "sans" not in k
    fixed = bool(d.get("fixed")) or any(w in k for w in ("mono", "courier", "code"))
    return fallback(serif, fixed, text, bold, italic)


def for_line(d: dict) -> tuple[str, str]:
    """The fonts a line of the PDF's own text is changed in: the same fonts, or the closest ones.
    (font, font of its Arabic words: "" when they're in the same font.)"""
    ready()
    text = d.get("text", "")
    arabic = "".join(ch for ch in text if unicodedata.bidirectional(ch) in ("R", "AL"))
    other = "".join(ch for ch in text if unicodedata.bidirectional(ch) not in ("R", "AL"))
    if not arabic or not other.strip(" 0123456789.,:;-()/"):
        name = (d.get("font_ar") if arabic else d.get("font_en")) or d.get("font", "")
        return _resolve(name, d, text), ""
    main = _resolve(d.get("font_en") or d.get("font", ""), d, other)
    ar = _resolve(d.get("font_ar") or d.get("font", ""), d, arabic)
    return main, "" if ar == main else ar


def offered() -> list[str]:
    """The fonts offered for new text: the much-used ones first, then every other font on the PC that
    writes English or Arabic, A to Z."""
    from PySide6.QtGui import QFontDatabase
    ready()
    fams = _all()
    out: list[str] = []
    for name in POPULAR:
        fam = fams.get(key(name))
        if fam and fam not in out:
            out.append(fam)
    writes = set(QFontDatabase.families(QFontDatabase.Latin)) | set(QFontDatabase.families(QFontDatabase.Arabic))
    # (not "Almarai Bold" when there's "Almarai": the bold button gives it)
    weights = (" bold", " semibold", " light", " black", " medium", " thin")
    rest = sorted((f for f in writes if not f.startswith("@") and f not in out and
                   not QFontDatabase.isPrivateFamily(f) and
                   not any(f.lower().endswith(w) and key(f[:-len(w)]) in fams for w in weights)), key=str.lower)
    return out + rest or ["Arial"]
