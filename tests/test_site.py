"""The website (docs/, made by scripts/build_site.py) lists every release that has notes."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _versions():
    for f in (ROOT / "assets" / "release-notes").glob("*.md"):
        if f.name.endswith(".ar.md"):
            continue
        v = f.name[:-3]
        if tuple(int(x) for x in v.split(".")) >= (2, 0, 0):
            yield v


def test_whats_new_on_the_website_is_up_to_date():
    pages = {lang: (ROOT / "docs" / p).read_text("utf-8") for lang, p in
             (("en", "core/changelog/index.html"), ("ar", "ar/core/changelog/index.html"))}
    missing = [f"{v} ({lang})" for v in _versions() for lang, html in pages.items() if f'id="v{v}"' not in html]
    assert not missing, ("the website's What's new misses " + ", ".join(missing) +
                         ": run  python scripts/build_site.py  and commit docs/")
