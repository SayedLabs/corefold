"""What typing in the quick menu finds besides Corefold's own things: a sum worked out ("12*(3+4)"), a
unit converted ("10 km to mi"), a page of Windows Settings, and a web search as the last choice.

All of it is worked out from the text alone, only while the person types: nothing is indexed, nothing
runs in the background, and nothing is sent anywhere unless they pick the web search."""
from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass
from urllib.parse import quote_plus

from .i18n import tr


@dataclass(frozen=True)
class Answer:
    value: str    # what Enter copies ("84", "6.21371")
    shown: str    # what the row says ("84", "6.21371 mi")
    source: str   # what was understood ("12*(3+4)", "10 km")
    kind: str     # "calc" or "unit"


# ---------------------------------------------------------------------------- numbers as people type them

_DIGITS = str.maketrans({**{chr(0x0660 + i): str(i) for i in range(10)},   # ٠-٩
                         **{chr(0x06F0 + i): str(i) for i in range(10)},   # ۰-۹ (Persian, Urdu)
                         "٫": ".", "٬": "", "،": ",",       # ٫ decimal, ٬ thousands, ، comma
                         "×": "*", "÷": "/", "−": "-", "–": "-", "π": "pi", " ": " "})
_NUMBER = re.compile(r"\d[\d.,]*\d|\d")


def _number_token(tok: str) -> str:
    """One number with its separators made plain: "1,234.5" and "1.234,5" -> 1234.5, "3,5" -> 3.5,
    "1,000" -> 1000 (a comma followed by exactly three digits is taken as thousands)."""
    if "," in tok and "." in tok:
        dec = "," if tok.rfind(",") > tok.rfind(".") else "."
        thousands = "." if dec == "," else ","
        return tok.replace(thousands, "").replace(dec, ".")
    for sep in (",", "."):
        if sep not in tok:
            continue
        parts = tok.split(sep)
        if len(parts) > 2:  # 1,000,000 or 1.000.000
            return "".join(parts) if all(len(p) == 3 for p in parts[1:]) else tok
        whole, frac = parts
        if sep == "," and len(frac) == 3 and whole not in ("0", ""):
            return whole + frac
        return f"{whole}.{frac}"
    return tok


def plain_numbers(text: str) -> str:
    text = text.translate(_DIGITS)
    return _NUMBER.sub(lambda m: _number_token(m.group()), text)


def number_text(x: float, digits: int = 12) -> str:
    """A result as a person would write it: no ".0", no float noise (0.1 + 0.2 is 0.3)."""
    if isinstance(x, int) and abs(x) < 10 ** 15:
        return str(x)
    x = float(x)
    if x == 0:
        return "0"
    if math.isinf(x) or math.isnan(x):
        raise OverflowError
    if abs(x) >= 1e15 or abs(x) < 1e-9:
        mant, exp = f"{x:.{digits - 1}e}".split("e")
        mant = mant.rstrip("0").rstrip(".")
        return f"{mant}e{int(exp)}"
    decimals = max(0, digits - 1 - math.floor(math.log10(abs(x))))
    out = f"{x:.{decimals}f}"
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    return "0" if out in ("-0", "") else out


# ---------------------------------------------------------------------------- the calculator

_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
           ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: pow}
_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
          "sin": lambda x: math.sin(math.radians(x)), "cos": lambda x: math.cos(math.radians(x)),
          "tan": lambda x: math.tan(math.radians(x)), "log": math.log10, "ln": math.log, "exp": math.exp,
          "min": min, "max": max}
_NAMES = {"pi": math.pi, "e": math.e}
MAX_EXPONENT = 10_000
MAX_INT_DIGITS = 400
MAX_ROUND_DIGITS = 15   # round(5, -999999999) would work out 10**999999999 first (and hang)
_ONE_ARG = {"sqrt", "abs", "floor", "ceil", "sin", "cos", "tan", "log", "ln", "exp"}


class _Bad(Exception):
    pass


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.Name) and node.id in _NAMES:
        return _NAMES[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(b) > MAX_EXPONENT:
                raise _Bad
            if isinstance(a, int) and isinstance(b, int) and abs(a) > 1 and b * math.log10(abs(a)) > MAX_INT_DIGITS:
                a = float(a)  # (a huge whole number would take ages and pages: as a float it overflows instead)
        out = _BINOPS[type(node.op)](a, b)
        if isinstance(out, complex):
            raise _Bad
        if isinstance(out, int) and abs(out) > 10 ** MAX_INT_DIGITS:
            raise _Bad
        return out
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS
            and node.args and not node.keywords and len(node.args) <= 8):
        name = node.func.id
        args = [_eval(a) for a in node.args]
        if name in _ONE_ARG and len(args) != 1:
            raise _Bad
        if name == "round" and (len(args) > 2 or (len(args) == 2 and (type(args[1]) is not int
                                                                      or abs(args[1]) > MAX_ROUND_DIGITS))):
            raise _Bad
        return _FUNCS[name](*args)
    raise _Bad


_PERCENT_OF = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:of|من)\s*", re.I)
_ADD_PERCENT = re.compile(r"^(.+?)\s*([+-])\s*(\d+(?:\.\d+)?)\s*%\s*$")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_ROOT = re.compile(r"√\s*(\d+(?:\.\d+)?)")
_TIMES_X = re.compile(r"(?<=[\d)])\s*[xX]\s*(?=[\d(])")


def calculate(text: str) -> Answer | None:
    """The result of a sum typed in the menu, or None if the text isn't one (or can't be worked out)."""
    raw = text.strip()
    if not raw or len(raw) > 120:
        return None
    s = plain_numbers(raw).replace(";", ",").replace("^", "**").lower()
    if not re.search(r"\d", s):
        return None
    s = _ROOT.sub(r"sqrt(\1)", s)
    s = _TIMES_X.sub("*", s)
    s = _PERCENT_OF.sub(r"(\1/100)*", s)
    m = _ADD_PERCENT.match(s)
    if m:  # 80 + 15% is 92, like on a calculator
        s = f"({m.group(1)})*(1{m.group(2)}{m.group(3)}/100)"
    s = _PERCENT.sub(r"(\1/100)", s)
    s = s.replace("mod", "%")
    try:
        tree = ast.parse(s, mode="eval")
    except (SyntaxError, ValueError):
        return None
    # a number on its own isn't a sum (typing "7" shouldn't answer "= 7")
    if isinstance(tree.body, ast.Constant) or (isinstance(tree.body, ast.UnaryOp)
                                               and isinstance(tree.body.operand, ast.Constant)):
        return None
    try:
        value = _eval(tree)
        if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
            value = int(value)
        shown = number_text(value)
    except (_Bad, ArithmeticError, ValueError, TypeError, RecursionError):
        return None
    return Answer(shown, shown, raw, "calc")


# ---------------------------------------------------------------------------- converting units

# unit -> (kind, how many of the kind's base unit it is, how it's written in the answer)
_UNITS: dict[str, tuple[str, float, str]] = {}


def _add(kind: str, factor: float, symbol: str, *names: str):
    for n in (symbol, *names):
        _UNITS[n.lower().replace(" ", "")] = (kind, factor, symbol)


for _f, _s, *_n in ((1e-3, "mm", "millimeter", "millimeters", "millimetre", "millimetres"),
                    (1e-2, "cm", "centimeter", "centimeters", "centimetre", "centimetres"),
                    (1.0, "m", "meter", "meters", "metre", "metres"),
                    (1e3, "km", "kilometer", "kilometers", "kilometre", "kilometres"),
                    (0.0254, "in", "inch", "inches", '"'), (0.3048, "ft", "foot", "feet", "'"),
                    (0.9144, "yd", "yard", "yards"), (1609.344, "mi", "mile", "miles"),
                    (1852.0, "nmi", "nautical mile", "nautical miles")):
    _add("length", _f, _s, *_n)
for _f, _s, *_n in ((1e-6, "mg", "milligram", "milligrams"), (1e-3, "g", "gram", "grams"),
                    (1.0, "kg", "kilo", "kilos", "kilogram", "kilograms"),
                    (1e3, "t", "tonne", "tonnes", "ton", "tons"), (0.028349523125, "oz", "ounce", "ounces"),
                    (0.45359237, "lb", "lbs", "pound", "pounds"), (6.35029318, "st", "stone", "stones")):
    _add("weight", _f, _s, *_n)
for _f, _s, *_n in ((1.0, "B", "byte", "bytes"), (1024.0, "KB", "kib", "kilobyte", "kilobytes"),
                    (1024.0 ** 2, "MB", "mib", "megabyte", "megabytes"),
                    (1024.0 ** 3, "GB", "gib", "gigabyte", "gigabytes"),
                    (1024.0 ** 4, "TB", "tib", "terabyte", "terabytes"),
                    (1024.0 ** 5, "PB", "pib", "petabyte", "petabytes"), (1 / 8, "bit", "bits"),
                    (1e3 / 8, "kbit", "kilobit", "kilobits"), (1e6 / 8, "Mbit", "megabit", "megabits"),
                    (1e9 / 8, "Gbit", "gigabit", "gigabits")):
    _add("data", _f, _s, *_n)
for _f, _s, *_n in ((1.0, "m/s", "mps", "meters per second"), (1 / 3.6, "km/h", "kmh", "kph", "kmph"),
                    (0.44704, "mph", "mi/h"), (1852 / 3600, "kn", "knot", "knots", "kt"),
                    (0.3048, "ft/s", "fps")):
    _add("speed", _f, _s, *_n)
for _f, _s, *_n in ((1e-3, "ml", "milliliter", "milliliters", "millilitre", "millilitres", "cm3", "cc"),
                    (1e-2, "cl"), (0.1, "dl"), (1.0, "l", "liter", "liters", "litre", "litres"), (1e3, "m3"),
                    (3.785411784, "gal", "gallon", "gallons"), (0.946352946, "qt", "quart", "quarts"),
                    (0.473176473, "pt", "pint", "pints"), (0.2365882365, "cup", "cups"),
                    (0.0295735295625, "fl oz", "floz", "fluid ounce", "fluid ounces"),
                    (0.01478676478125, "tbsp", "tablespoon", "tablespoons"),
                    (0.00492892159375, "tsp", "teaspoon", "teaspoons")):
    _add("volume", _f, _s, *_n)
for _f, _s, *_n in ((1e-6, "mm²", "mm2"), (1e-4, "cm²", "cm2"), (1.0, "m²", "m2", "sqm", "sq m"),
                    (1e6, "km²", "km2", "sqkm", "sq km"), (1e4, "ha", "hectare", "hectares"),
                    (4046.8564224, "acre", "acres", "ac"), (0.00064516, "in²", "in2", "sqin", "sq in"),
                    (0.09290304, "ft²", "ft2", "sqft", "sq ft"), (0.83612736, "yd²", "yd2", "sqyd", "sq yd"),
                    (2589988.110336, "mi²", "mi2", "sqmi", "sq mi")):
    _add("area", _f, _s, *_n)
for _f, _s, *_n in ((1e-3, "ms", "millisecond", "milliseconds"), (1.0, "s", "sec", "secs", "second", "seconds"),
                    (60.0, "min", "mins", "minute", "minutes"), (3600.0, "h", "hr", "hrs", "hour", "hours"),
                    (86400.0, "d", "day", "days"), (604800.0, "wk", "week", "weeks"),
                    (2629746.0, "mo", "month", "months"), (31556952.0, "yr", "y", "yrs", "year", "years")):
    _add("time", _f, _s, *_n)
for _s, *_n in (("°C", "c", "celsius"), ("°F", "f", "fahrenheit"), ("K", "k", "kelvin")):
    _add("temperature", 0.0, _s, *_n)

_TO_KELVIN = {"°C": lambda v: v + 273.15, "°F": lambda v: (v - 32) * 5 / 9 + 273.15, "K": lambda v: v}
_FROM_KELVIN = {"°C": lambda k: k - 273.15, "°F": lambda k: (k - 273.15) * 9 / 5 + 32, "K": lambda k: k}
_LEADING_NUMBER = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)\s*(.+)$")


def unit(name: str) -> tuple[str, float, str] | None:
    key = name.strip().lower().replace("°", "").replace("²", "2").replace("³", "3").replace(" ", "")
    if not key:
        return None
    if key in _UNITS:
        return _UNITS[key]
    return _UNITS.get(key.rstrip("."))


def _connectors() -> set[str]:
    words = {"to", "in", "into", "as", "=", "→", "->", "إلى", "الى"}
    words.add(tr("to").strip().lower())
    return {w for w in words if w}


def convert(text: str) -> Answer | None:
    """"10 km to mi" -> 6.21371 mi; also "5 ft in cm", "100 f = c", "3.5 GB → MB"."""
    s = plain_numbers(text.strip())
    if len(s) > 60:
        return None
    s = re.sub(r"\s*(→|->|=)\s*", r" \1 ", s)
    m = _LEADING_NUMBER.match(s)
    if not m:
        return None
    number, rest = float(m.group(1)), m.group(2).strip()
    words = rest.split()
    joins = _connectors()
    for i in range(1, len(words) - 1):
        if words[i].lower() not in joins:
            continue
        a, b = unit(" ".join(words[:i])), unit(" ".join(words[i + 1:]))
        if a is None or b is None or a[0] != b[0]:
            continue
        if a[0] == "temperature":
            value = _FROM_KELVIN[b[2]](_TO_KELVIN[a[2]](number))
        else:
            value = number * a[1] / b[1]
        try:
            shown = number_text(value, 6)
        except (OverflowError, ValueError):
            return None
        return Answer(shown, f"{shown} {b[2]}", f"{number_text(number, 12)} {a[2]}", "unit")
    return None


def answer(text: str) -> Answer | None:
    """The calculator's or the converter's answer to what's typed, if it is one of those."""
    return convert(text) or calculate(text)


# ---------------------------------------------------------------------------- matching what's typed

def score(query: str, *texts: str, extra: tuple[str, ...] = ()) -> int:
    """How well the typed text fits a name (0: not at all; 4: it's the name). Words that only describe it
    (extra) count less than its name."""
    q = query.strip().lower()
    if not q:
        return 0
    best = 0
    for text, cap in [(t, 4) for t in texts] + [(t, 2) for t in extra]:
        t = (text or "").lower()
        if not t or q not in t:
            continue
        if t == q:
            s = 4
        elif t.startswith(q):
            s = 3
        elif any(w.startswith(q) for w in re.split(r"[\s&,/()\-]+", t)):
            s = 2
        else:
            s = 1 if cap == 4 else 0  # (inside a describing word doesn't count: "so" isn't "cursor")
        best = max(best, min(s, cap))
    return best


# ---------------------------------------------------------------------------- pages of Windows Settings

@dataclass(frozen=True)
class SettingsPage:
    name: str       # English, shown through tr()
    uri: str
    words: tuple[str, ...] = ()   # other words people search for it with (English)


SETTINGS_PAGES: tuple[SettingsPage, ...] = (
    SettingsPage("Display", "ms-settings:display", ("screen", "brightness", "resolution", "scale", "monitor")),
    SettingsPage("Night light", "ms-settings:nightlight", ("blue light", "warm")),
    SettingsPage("Sound", "ms-settings:sound", ("volume", "speakers", "microphone", "audio", "headphones")),
    SettingsPage("Volume mixer", "ms-settings:apps-volume", ("app volume",)),
    SettingsPage("Notifications", "ms-settings:notifications", ("alerts", "do not disturb")),
    SettingsPage("Focus", "ms-settings:quiethours", ("focus assist", "do not disturb", "quiet")),
    SettingsPage("Power & battery", "ms-settings:powersleep", ("sleep", "battery", "power plan", "screen off")),
    SettingsPage("Storage", "ms-settings:storagesense", ("disk", "space", "storage sense", "drive")),
    SettingsPage("Multitasking", "ms-settings:multitasking", ("snap", "alt tab", "desktops")),
    SettingsPage("Clipboard", "ms-settings:clipboard", ("copy", "paste", "history")),
    SettingsPage("About", "ms-settings:about", ("pc name", "specs", "version", "system", "rename")),
    SettingsPage("Troubleshoot", "ms-settings:troubleshoot", ("fix", "problems")),
    SettingsPage("Recovery", "ms-settings:recovery", ("reset", "reinstall", "advanced startup")),
    SettingsPage("Bluetooth & devices", "ms-settings:bluetooth", ("bluetooth", "devices", "pair", "headphones")),
    SettingsPage("Printers & scanners", "ms-settings:printers", ("printer", "scanner", "print")),
    SettingsPage("Mouse", "ms-settings:mousetouchpad", ("cursor", "pointer", "scroll")),
    SettingsPage("Touchpad", "ms-settings:devices-touchpad", ("trackpad", "gestures")),
    SettingsPage("Keyboard", "ms-settings:typing", ("typing", "autocorrect", "spelling")),
    SettingsPage("Wi-Fi", "ms-settings:network-wifi", ("wifi", "wireless", "internet")),
    SettingsPage("Network & internet", "ms-settings:network", ("ethernet", "internet", "proxy", "dns")),
    SettingsPage("VPN", "ms-settings:network-vpn", ()),
    SettingsPage("Mobile hotspot", "ms-settings:network-mobilehotspot", ("hotspot", "share internet")),
    SettingsPage("Personalization", "ms-settings:personalization", ("look", "appearance")),
    SettingsPage("Background", "ms-settings:personalization-background", ("wallpaper", "desktop picture")),
    SettingsPage("Colors", "ms-settings:personalization-colors", ("dark mode", "light mode", "accent", "colours")),
    SettingsPage("Themes", "ms-settings:themes", ("theme", "icons")),
    SettingsPage("Lock screen", "ms-settings:lockscreen", ("screen saver",)),
    SettingsPage("Taskbar", "ms-settings:taskbar", ("system tray", "tray")),
    SettingsPage("Start menu", "ms-settings:personalization-start", ("start",)),
    SettingsPage("Apps", "ms-settings:appsfeatures", ("installed apps", "uninstall", "programs")),
    SettingsPage("Default apps", "ms-settings:defaultapps", ("open with", "browser", "file types")),
    SettingsPage("Startup apps", "ms-settings:startupapps", ("startup", "boot", "login")),
    SettingsPage("Accounts", "ms-settings:yourinfo", ("account", "user", "profile")),
    SettingsPage("Sign-in options", "ms-settings:signinoptions", ("password", "pin", "windows hello", "login")),
    SettingsPage("Date & time", "ms-settings:dateandtime", ("clock", "time zone", "date")),
    SettingsPage("Language & region", "ms-settings:regionlanguage", ("language", "region", "keyboard layout")),
    SettingsPage("Privacy & security", "ms-settings:privacy", ("permissions", "privacy", "security")),
    SettingsPage("Windows Update", "ms-settings:windowsupdate", ("updates", "update")),
    SettingsPage("Backup", "ms-settings:backup", ("onedrive", "back up")),
)
MAX_SETTINGS_ROWS = 5


def settings_pages(query: str) -> list[tuple[int, SettingsPage]]:
    """The Settings pages that fit what's typed, best first (searched in English and in the app's language)."""
    found = []
    for i, page in enumerate(SETTINGS_PAGES):
        s = score(query, tr(page.name), page.name, extra=page.words)
        if s:
            found.append((s, i, page))
    found.sort(key=lambda x: (-x[0], x[1]))
    return [(s, page) for s, _, page in found[:MAX_SETTINGS_ROWS]]


# ---------------------------------------------------------------------------- the web search (last choice)

SEARCH_URLS = {"duckduckgo": "https://duckduckgo.com/?q={q}", "google": "https://www.google.com/search?q={q}",
               "bing": "https://www.bing.com/search?q={q}"}


def search_url(engine: str, text: str) -> str:
    return SEARCH_URLS.get(engine, SEARCH_URLS["duckduckgo"]).format(q=quote_plus(text.strip()))
