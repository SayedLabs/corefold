"""Builds the Rakiza Labs website (rakizalabs.com) into docs/, in English and Arabic.

    python scripts/build_site.py                 # write every page
    python scripts/build_site.py --shots DIR     # also refresh the app pictures from a "README pictures" run
                                                 # (the "readme" folder of the "screenshots" branch)

Pages: the Rakiza Labs home, Rakiza Core (with What's new from assets/release-notes), Rakiza OnAir (coming soon),
Privacy and a 404 page. English lives at the top, Arabic under ar/. Every link is relative, so the site works both
at https://sayedlabs.github.io/corefold/ and at https://rakizalabs.com/. Run it again after new release notes, so
What's new on the site lists them. The page text is in this file; the look is in docs/assets/css/site.css.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
NOTES = ROOT / "assets" / "release-notes"
SITE = "https://rakizalabs.com/"
REPO = "https://github.com/SayedLabs/corefold"
RELEASES = REPO + "/releases/latest"
ALL_RELEASES = REPO + "/releases"
ISSUES = REPO + "/issues/new"
ASSET_V = "1"  # bump to make browsers fetch the stylesheet and scripts again

# ------------------------------------------------------------------------------------------------ icons
# Small line icons drawn for this site (24 x 24, stroke = currentColor).
_I = {
    "download": '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M5 20h14"/>',
    "arrow": '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
    "feather": '<path d="M20 4c-7 0-12 5-12 12v4"/><path d="M8 16c5 0 9-3 10-8"/><path d="M8 12h6"/>',
    "globe": '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17"/><path d="M12 3.5c2.6 2.4 3.8 5.2 3.8 8.5s-1.2 6.1-3.8 8.5c-2.6-2.4-3.8-5.2-3.8-8.5s1.2-6.1 3.8-8.5Z"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "files": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/>',
    "wheel": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3"/><path d="M12 3.5V9M12 15v5.5M3.5 12H9M15 12h5.5"/>',
    "pdf": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/><path d="M8.5 16.5c2-1 4.5-4.5 4-6.5-.4-1.6-2.2-.6-1.5 1.5.6 2 2.6 4.4 4.5 4.6"/>',
    "pie": '<path d="M12 3.5a8.5 8.5 0 1 0 8.5 8.5H12Z"/><path d="M15 3.9A8.5 8.5 0 0 1 20.1 9H15Z"/>',
    "trash": '<path d="M4 7h16"/><path d="M9 7V4.5h6V7"/><path d="M6.5 7l1 13h9l1-13"/><path d="M10 11v5.5M14 11v5.5"/>',
    "cloud": '<path d="M7 18.5a4.5 4.5 0 0 1-.6-9A6 6 0 0 1 18 9a4.75 4.75 0 0 1-.5 9.5Z"/>',
    "unlock": '<rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8 10.5V8a4 4 0 0 1 7.6-1.7"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/>',
    "archive": '<rect x="3.5" y="4" width="17" height="5" rx="1.5"/><path d="M5 9v9.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V9"/><path d="M10 13h4"/>',
    "network": '<path d="M5 12.5a10 10 0 0 1 14 0"/><path d="M8 15.5a5.5 5.5 0 0 1 8 0"/><circle cx="12" cy="19" r="1.2" fill="currentColor"/><path d="M2 9.5a14.5 14.5 0 0 1 20 0"/>',
    "rename": '<path d="M4 7h9M4 12h6M4 17h9"/><path d="m15 18 5-5-2-2-5 5-.5 2.5Z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
    "moon": '<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10Z"/>',
    "auto": '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17a8.5 8.5 0 0 0 0-17Z" fill="currentColor"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "message": '<path d="M5 5h14a1.5 1.5 0 0 1 1.5 1.5v9A1.5 1.5 0 0 1 19 17H10l-4.5 3.5V17H5a1.5 1.5 0 0 1-1.5-1.5v-9A1.5 1.5 0 0 1 5 5Z"/>',
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/><circle cx="12" cy="7.8" r="1" fill="currentColor"/>',
    "layers": '<path d="m12 3.5 8.5 4.5-8.5 4.5L3.5 8Z"/><path d="m3.5 12 8.5 4.5 8.5-4.5"/><path d="m3.5 16 8.5 4.5 8.5-4.5"/>',
    "spark": '<path d="M12 3.5c.6 4.4 3.1 6.9 7.5 7.5-4.4.6-6.9 3.1-7.5 7.5-.6-4.4-3.1-6.9-7.5-7.5 4.4-.6 6.9-3.1 7.5-7.5Z"/>',
    "plug": '<path d="M9 3.5V8M15 3.5V8"/><path d="M6.5 8h11v3a5.5 5.5 0 0 1-11 0Z"/><path d="M12 16.5v4"/>',
    "keyboard": '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10h.01M11 10h.01M15 10h.01M7 14h10"/>',
    "shield": '<path d="M12 3.5 19 6v5.5c0 4.3-3 7.8-7 9-4-1.2-7-4.7-7-9V6Z"/><path d="m9 12 2 2 4-4"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.5-4.5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.5 4.5L20 16"/><path d="M20 20v-4h-4"/>',
    "github": '<path d="M12 3a9 9 0 0 0-2.85 17.54c.45.08.62-.2.62-.43v-1.6c-2.5.54-3.03-1.2-3.03-1.2-.41-1.04-1-1.32-1-1.32-.82-.56.06-.55.06-.55.9.06 1.38.93 1.38.93.8 1.38 2.11.98 2.62.75.08-.58.31-.98.57-1.2-2-.23-4.1-1-4.1-4.45 0-.98.35-1.79.93-2.42-.1-.23-.4-1.14.09-2.38 0 0 .76-.24 2.47.93a8.6 8.6 0 0 1 4.5 0c1.71-1.17 2.47-.93 2.47-.93.49 1.24.18 2.15.09 2.38.58.63.93 1.44.93 2.42 0 3.46-2.1 4.22-4.11 4.44.32.28.61.83.61 1.67v2.48c0 .24.16.52.62.43A9 9 0 0 0 12 3Z" fill="currentColor" stroke="none"/>',
}


def icon(name: str, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return (f'<svg{c} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{_I[name]}</svg>')


MARK = ('<svg viewBox="0 0 96 96" aria-hidden="true"><g fill="currentColor"><rect x="22" y="16" width="15" height="50" rx="3"/>'
        '<path d="M37 16H53A18 18 0 0 1 53 52H37V40H53A6 6 0 0 0 53 28H37Z"/><path d="M46 52H61L74 66H59Z"/></g>'
        '<rect x="14" y="73" width="68" height="8" rx="4" fill="var(--gold)"/></svg>')

# ------------------------------------------------------------------------------------------------ text
# Each entry: (English, Arabic). Arabic is plain Modern Standard Arabic, like the app.
T = {
    "labs": ("Rakiza Labs", "ركيزة لابز"),
    "core": ("Rakiza Core", "ركيزة كور"),
    "onair": ("Rakiza OnAir", "ركيزة أون إير"),
    "skip": ("Skip to content", "تخطَّ إلى المحتوى"),
    "nav_products": ("Products", "البرامج"),
    "nav_core": ("Rakiza Core", "ركيزة كور"),
    "nav_onair": ("OnAir", "أون إير"),
    "nav_privacy": ("Privacy", "الخصوصية"),
    "nav_new": ("What's new", "الجديد"),
    "menu": ("Menu", "القائمة"),
    "theme": ("Light or dark", "فاتح أو داكن"),
    "theme_auto": ("Look: follows your system. Switch to light or dark", "المظهر: يتبع النظام. بدّل إلى فاتح أو داكن"),
    "theme_light": ("Look: light. Switch", "المظهر: فاتح. بدّل"),
    "theme_dark": ("Look: dark. Switch", "المظهر: داكن. بدّل"),
    "other_lang": ("العربية", "English"),
    "other_lang_label": ("اقرأ هذه الصفحة بالعربية", "Read this page in English"),
    # footer
    "foot_about": ("Calm, light software for Windows. Your files stay on your PC.",
                   "برامج هادئة وخفيفة لويندوز، وملفاتك تبقى على جهازك."),
    "foot_products": ("Products", "البرامج"),
    "foot_company": ("Rakiza Labs", "ركيزة لابز"),
    "foot_help": ("Help", "المساعدة"),
    "foot_report": ("Report a problem", "أبلغ عن مشكلة"),
    "foot_idea": ("Suggest an idea", "اقترح فكرة"),
    "foot_releases": ("All releases", "كل الإصدارات"),
    "foot_principles": ("How we build", "كيف نبني"),
    "foot_contact": ("Contact", "تواصل معنا"),
    "foot_rights": ("Rakiza Labs", "ركيزة لابز"),
    "foot_note": ("No cookies, no analytics, no trackers.", "بلا ملفات تعريف ارتباط، وبلا إحصاءات أو تتبّع."),
    "soon": ("Coming soon", "قريبًا"),
    "early": ("Early access", "إصدار مبكر"),
    "learn": ("Learn more", "اعرف المزيد"),
}


def t(key: str, lang: str) -> str:
    en, ar = T[key]
    return ar if lang == "ar" else en


def esc(s: str) -> str:
    return html.escape(s, quote=True)


# ------------------------------------------------------------------------------------------------ pages
PAGES = {  # key: (English path, Arabic path), relative to docs/
    "home": ("index.html", "ar/index.html"),
    "core": ("core/index.html", "ar/core/index.html"),
    "changelog": ("core/changelog/index.html", "ar/core/changelog/index.html"),
    "onair": ("onair/index.html", "ar/onair/index.html"),
    "privacy": ("privacy.html", "ar/privacy.html"),
}


def url_of(key: str, lang: str) -> str:
    """The page's address relative to the site root, as people see it (folders end with /)."""
    p = PAGES[key][1 if lang == "ar" else 0]
    return p[:-len("index.html")] if p.endswith("index.html") else p


def rel(from_key: str, lang: str) -> str:
    """The way from a page back to the site root ("", "../", "../../")."""
    p = PAGES[from_key][1 if lang == "ar" else 0]
    return "../" * p.count("/")


def link(from_key: str, lang: str, to_key: str, to_lang: str | None = None, anchor: str = "") -> str:
    u = rel(from_key, lang) + url_of(to_key, to_lang or lang)
    return (u or "./") + anchor


def picture(r: str, name: str, lang: str, alt: str, eager: bool = False, cls: str = "") -> str:
    """A screenshot of the app that follows the page's light or dark look."""
    base = f"{r}assets/img/shots/{name}-{lang}"
    size = SHOT_SIZES.get(f"{name}-{lang}-light", (1100, 720))
    load = 'fetchpriority="high"' if eager else 'loading="lazy"'
    c = f' class="{cls}"' if cls else ""
    return (f'<picture{c}><source data-dark media="(prefers-color-scheme: dark)" srcset="{base}-dark.webp">'
            f'<img src="{base}-light.webp" width="{size[0]}" height="{size[1]}" alt="{esc(alt)}" {load} decoding="async"></picture>')


SHOT_SIZES: dict[str, tuple[int, int]] = {}


def head(lang: str, key: str, title: str, desc: str, extra_head: str = "") -> str:
    r = rel(key, lang)
    canon = SITE + url_of(key, lang)
    alt_en = SITE + url_of(key, "en")
    alt_ar = SITE + url_of(key, "ar")
    og = f"{SITE}assets/img/og-{lang}.png"
    home_flag = f' data-home-en="{r or "./"}"' if (key == "home" and lang == "en") else ""
    d = "rtl" if lang == "ar" else "ltr"
    locale = "ar_AR" if lang == "ar" else "en_US"
    return f"""<!doctype html>
<html lang="{lang}" dir="{d}"{home_flag}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; font-src 'self'; connect-src https://api.github.com; base-uri 'none'; form-action 'none'; object-src 'none'">
<meta name="referrer" content="strict-origin-when-cross-origin">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{canon}">
<link rel="alternate" hreflang="en" href="{alt_en}">
<link rel="alternate" hreflang="ar" href="{alt_ar}">
<link rel="alternate" hreflang="x-default" href="{alt_en}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{esc(t('labs', lang))}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{canon}">
<meta property="og:image" content="{og}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:locale" content="{locale}">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#FAF9F5" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0A0F0D" media="(prefers-color-scheme: dark)">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="{r}favicon.svg" type="image/svg+xml">
<link rel="icon" href="{r}icon.png" type="image/png" sizes="512x512">
<link rel="apple-touch-icon" href="{r}apple-touch-icon.png">
<link rel="preload" href="{r}assets/fonts/readex-pro-{'arabic' if lang == 'ar' else 'latin'}-wght-normal.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{r}assets/css/site.css?v={ASSET_V}">
<script src="{r}assets/js/theme.js?v={ASSET_V}"></script>
<script src="{r}assets/js/site.js?v={ASSET_V}" defer></script>
{extra_head}</head>
"""


def header(lang: str, key: str) -> str:
    other = "en" if lang == "ar" else "ar"

    def nav(k, label, anchor=""):
        cur = ' aria-current="page"' if k == key and not anchor else ""
        return f'<a href="{link(key, lang, k, anchor=anchor)}"{cur}>{esc(label)}</a>'

    brand_word = (f'<span class="word"><b>{esc("ركيزة")}</b><small class="ltr">RAKIZA LABS</small></span>' if lang == "ar"
                  else '<span class="word"><b>Rakiza</b><small>LABS</small></span>')
    return f"""<body>
<a class="skip" href="#main">{esc(t('skip', lang))}</a>
<header class="site-head">
  <div class="wrap head-row">
    <a class="brand" href="{link(key, lang, 'home')}" aria-label="{esc(t('labs', lang))}">{MARK}{brand_word}</a>
    <nav class="nav" id="site-nav" aria-label="{esc(t('nav_products', lang))}">
      {nav('core', t('nav_core', lang))}
      {nav('onair', t('nav_onair', lang))}
      {nav('changelog', t('nav_new', lang))}
      {nav('privacy', t('nav_privacy', lang))}
    </nav>
    <div class="tools">
      <a class="chip-btn" href="{link(key, lang, key, other)}" hreflang="{other}" lang="{other}" data-set-lang="{other}" aria-label="{esc(t('other_lang_label', lang))}">{icon('globe')}<span>{t('other_lang', lang)}</span></a>
      <button class="chip-btn theme-btn" type="button" aria-label="{esc(t('theme_auto', lang))}" data-label-auto="{esc(t('theme_auto', lang))}" data-label-light="{esc(t('theme_light', lang))}" data-label-dark="{esc(t('theme_dark', lang))}">{icon('sun', 'i-sun')}{icon('moon', 'i-moon')}{icon('auto', 'i-auto')}</button>
      <button class="chip-btn menu-btn" type="button" aria-label="{esc(t('menu', lang))}" aria-expanded="false" aria-controls="site-nav">{icon('menu')}</button>
    </div>
  </div>
</header>
<main id="main">
"""


def footer(lang: str, key: str) -> str:
    def a(k, label, anchor=""):
        return f'<li><a href="{link(key, lang, k, anchor=anchor)}">{esc(label)}</a></li>'
    return f"""</main>
<footer class="site-foot">
  <div class="wrap">
    <div class="foot-grid">
      <div class="about">
        <a class="brand" href="{link(key, lang, 'home')}" aria-label="{esc(t('labs', lang))}">{MARK}{'<span class="word"><b>ركيزة</b><small class="ltr">RAKIZA LABS</small></span>' if lang == 'ar' else '<span class="word"><b>Rakiza</b><small>LABS</small></span>'}</a>
        <p>{esc(t('foot_about', lang))}</p>
      </div>
      <div><h4>{esc(t('foot_products', lang))}</h4><ul>
        {a('core', t('core', lang))}
        {a('onair', t('onair', lang))}
        {a('changelog', t('nav_new', lang))}
      </ul></div>
      <div><h4>{esc(t('foot_company', lang))}</h4><ul>
        {a('home', t('foot_principles', lang), '#principles')}
        {a('privacy', t('nav_privacy', lang))}
        {a('home', t('foot_contact', lang), '#contact')}
      </ul></div>
      <div><h4>{esc(t('foot_help', lang))}</h4><ul>
        <li><a href="{ISSUES}?labels=bug" rel="noopener">{esc(t('foot_report', lang))}</a></li>
        <li><a href="{ISSUES}?labels=idea" rel="noopener">{esc(t('foot_idea', lang))}</a></li>
        <li><a href="{ALL_RELEASES}" rel="noopener">{esc(t('foot_releases', lang))}</a></li>
      </ul></div>
    </div>
    <div class="foot-base">
      <span class="base-line">© <span data-year>2026</span> {esc(t('foot_rights', lang))}</span>
      <span>{esc(t('foot_note', lang))}</span>
    </div>
  </div>
</footer>
</body>
</html>
"""


def page(lang, key, title, desc, body, extra_head=""):
    return head(lang, key, title, desc, extra_head) + header(lang, key) + body + footer(lang, key)


# ------------------------------------------------------------------------------------------------ home
def home(lang: str) -> str:
    ar = lang == "ar"
    k = "home"
    r = rel(k, lang)
    L = lambda en, a: a if ar else en  # noqa: E731
    title = L("Rakiza Labs: calm, light software for Windows", "ركيزة لابز: برامج هادئة وخفيفة لويندوز")
    desc = L("Rakiza Labs makes light, private apps for Windows. Meet Rakiza Core: everyday tools in one calm window, "
             "and your files never leave your PC.",
             "ركيزة لابز تصنع برامج خفيفة تحترم خصوصيتك لويندوز. تعرّف على ركيزة كور: أدواتك اليومية في نافذة واحدة "
             "هادئة، وملفاتك لا تغادر جهازك.")
    principles = [
        ("lock", L("Your files stay yours", "ملفاتك تبقى لك"),
         L("Everything happens on your PC. No accounts, no ads, no tracking, and no server of ours that could collect anything.",
           "كل شيء يحدث على جهازك. لا حسابات ولا إعلانات ولا تتبّع، ولا خادم لدينا يمكنه جمع أي شيء.")),
        ("feather", L("Light by design", "خفيفة من الأساس"),
         L("Our apps wait quietly and hand memory back to Windows. A tool you switch off isn't loaded at all.",
           "تنتظر برامجنا بهدوء وتعيد الذاكرة إلى Windows. والأداة التي توقفها لا تُحمَّل أبدًا.")),
        ("globe", L("Arabic and English, done properly", "عربي وإنجليزي بإتقان"),
         L("Right-to-left that feels native, not flipped. Rakiza Core speaks five languages.",
           "واجهة من اليمين إلى اليسار تبدو أصيلة لا مقلوبة. وركيزة كور يتحدث خمس لغات.")),
        ("undo", L("Careful with what matters", "حريصة على ما يهمّك"),
         L("Originals are never changed. What you remove goes to the Recycle Bin or a backup you can put back.",
           "لا نغيّر ملفاتك الأصلية. وما تحذفه يذهب إلى سلة المحذوفات أو إلى نسخة احتياطية يمكنك استرجاعها.")),
    ]
    pr = "".join(f'<div class="principle reveal"><span class="ic">{icon(i)}</span><h3>{esc(h)}</h3><p>{esc(p)}</p></div>'
                 for i, h, p in principles)
    facts = [
        ("lock", L("No account needed", "بلا حساب"), L("Download, install, use. That's all.", "حمّل وثبّت واستخدم. هذا كل شيء.")),
        ("shield", L("Nothing about you is collected", "لا نجمع عنك شيئًا"),
         L("No usage statistics, no crash reports sent behind your back.", "لا إحصاءات استخدام، ولا تقارير أعطال تُرسل دون علمك.")),
        ("refresh", L("Steady, honest updates", "تحديثات منتظمة وصادقة"),
         L("Each release says plainly what changed, in English and Arabic.", "كل إصدار يقول بوضوح ما الذي تغيّر، بالعربية والإنجليزية.")),
    ]
    fc = "".join(f'<div class="fact"><span class="ic">{icon(i)}</span><div><h3>{esc(h)}</h3><p>{esc(p)}</p></div></div>'
                 for i, h, p in facts)
    hero_title = (L('Software you can <span class="base">build on.</span>', 'برامج <span class="base">تبني عليها.</span>'))
    body = f"""
<section class="hero">
  <div class="wrap">
    <div class="hero-inner">
      <span class="eyebrow">{esc(t('labs', lang))}</span>
      <h1 class="display">{hero_title}</h1>
      <p class="lead">{esc(L("We make calm, light apps for Windows that keep your files on your PC. One app at a time, and each one done properly.",
                              "نصنع برامج هادئة وخفيفة لويندوز، تبقى فيها ملفاتك على جهازك. برنامج واحد في كل مرة، ونتقن كل واحد منها."))}</p>
      <div class="row">
        <a class="btn btn-primary btn-lg" href="{link(k, lang, 'core')}">{esc(L('Meet Rakiza Core', 'تعرّف على ركيزة كور'))}{icon('arrow', 'flip')}</a>
        <a class="btn btn-ghost btn-lg" href="#principles">{esc(L('How we build', 'كيف نبني'))}</a>
      </div>
    </div>
    <div class="stage reveal">
      <div class="window">{picture(r, 'store', lang, L('Rakiza Core: the Files workspace', 'ركيزة كور: مساحة عمل الملفات'), eager=True)}</div>
    </div>
  </div>
</section>

<section id="products" class="pt-s">
  <div class="wrap">
    <div class="sec-head reveal">
      <span class="eyebrow">{esc(L('Products', 'البرامج'))}</span>
      <h2 class="h2">{esc(L('Made to be lived with, every day.', 'صُنعت لتعيش معك كل يوم.'))}</h2>
    </div>
    <div class="products">
      <a class="product core reveal" href="{link(k, lang, 'core')}">
        <div class="top"><img class="appicon" src="{r}assets/img/rakiza-core-192.png" width="64" height="64" alt=""><span class="pill core live"><i></i>{esc(t('early', lang))}</span></div>
        <div><h3>{esc(t('core', lang))}</h3></div>
        <p>{esc(L("Everyday tools for Windows in one light app: convert any file, edit PDFs, open archives, see what fills your drive and remove programs cleanly.",
                  "أدواتك اليومية لويندوز في برنامج واحد خفيف: حوّل أي ملف، وعدّل ملفات PDF، وافتح الملفات المضغوطة، واعرف ما يملأ قرصك، وأزل البرامج دون أن تترك أثرًا."))}</p>
        <span class="cta">{esc(t('learn', lang))}{icon('arrow', 'flip')}</span>
        <div class="shot">{picture(r, 'home', lang, L('Rakiza Core home page', 'الصفحة الرئيسية في ركيزة كور'))}</div>
      </a>
      <a class="product reveal" href="{link(k, lang, 'onair')}">
        <div class="top"><img class="appicon" src="{r}assets/img/onair-icon.svg" width="64" height="64" alt=""><span class="pill air"><i></i>{esc(t('soon', lang))}</span></div>
        <h3>{esc(t('onair', lang))}</h3>
        <p>{esc(L("Your whole stream setup in one plugin for OBS Studio. We're building it now.",
                  "كل ما يحتاجه بثّك في إضافة واحدة لبرنامج OBS Studio. نعمل عليها الآن."))}</p>
        <span class="cta">{esc(t('learn', lang))}{icon('arrow', 'flip')}</span>
      </a>
      <div class="product more reveal">
        <div class="dots" aria-hidden="true"><span></span><span></span><span></span></div>
        <h3>{esc(L('More on the way', 'والمزيد في الطريق'))}</h3>
        <p>{esc(L("We have more apps in the works. We'll show them when they're ready, not before.",
                  "لدينا برامج أخرى قيد العمل، وسنعرضها عندما تكون جاهزة، لا قبل ذلك."))}</p>
      </div>
    </div>
  </div>
</section>

<section id="principles">
  <div class="wrap">
    <div class="sec-head reveal">
      <span class="eyebrow">{esc(L('How we build', 'كيف نبني'))}</span>
      <h2 class="h2">{esc(L('A few promises we keep in every app.', 'وعود قليلة نلتزم بها في كل برنامج.'))}</h2>
      <p class="lead">{esc(L('"Rakiza" means a pillar: the thing you lean on without thinking about it. That is the bar we hold our software to.',
                             '«ركيزة» هي ما تستند إليه دون أن تفكّر فيه. وهذا هو المستوى الذي نطلبه من برامجنا.'))}</p>
    </div>
    <div class="principles">{pr}</div>
  </div>
</section>

<section class="band">
  <div class="wrap band-grid">
    <div class="sec-head reveal mb-0">
      <span class="eyebrow">{esc(L('Privacy', 'الخصوصية'))}</span>
      <h2 class="h2">{esc(L('We have no server that could collect your data. On purpose.', 'ليس لدينا خادم يمكنه جمع بياناتك. عن قصد.'))}</h2>
      <p class="lead">{esc(L("Our apps only go online to check for their own updates, and to the cloud accounts you choose to sign in to. Everything else stays on your PC.",
                             "لا تتصل برامجنا بالإنترنت إلا للتحقق من تحديثاتها، وللحسابات السحابية التي تختار تسجيل الدخول إليها. وكل ما عدا ذلك يبقى على جهازك."))}</p>
      <div class="row mt-s"><a class="btn btn-ghost" href="{link(k, lang, 'privacy')}">{esc(L('Read the privacy policy', 'اقرأ سياسة الخصوصية'))}{icon('arrow', 'flip')}</a></div>
    </div>
    <div class="facts reveal">{fc}</div>
  </div>
</section>

<section id="contact">
  <div class="wrap">
    <div class="contact reveal">
      <div class="stack">
        <span class="eyebrow">{esc(L('Contact', 'تواصل معنا'))}</span>
        <h2>{esc(L('Questions, ideas or something not working?', 'سؤال أو فكرة أو شيء لا يعمل؟'))}</h2>
        <p class="muted">{esc(L("We don't run a mailing list or collect email addresses. Write to us on GitHub, where every answer helps the next person too.",
                                "لا نجمع عناوين البريد ولا نرسل نشرات. اكتب لنا على GitHub، حيث يفيد كل جواب من يأتي بعدك أيضًا."))}</p>
      </div>
      <div class="row">
        <a class="btn btn-primary" href="{ISSUES}" rel="noopener">{icon('github')}{esc(L('Write to us on GitHub', 'اكتب لنا على GitHub'))}</a>
      </div>
    </div>
  </div>
</section>
"""
    return page(lang, k, title, desc, body)


# ------------------------------------------------------------------------------------------------ release notes
def read_notes(lang: str, since: tuple = (2, 0, 0)) -> list[dict]:
    out = []
    for f in NOTES.glob("*.md"):
        is_ar = f.name.endswith(".ar.md")
        if is_ar != (lang == "ar"):
            continue
        ver = f.name[: -len(".ar.md")] if is_ar else f.name[:-3]
        try:
            vt = tuple(int(x) for x in ver.split("."))
        except ValueError:
            continue
        if vt < since:
            continue
        text = f.read_text("utf-8")
        date = ""
        m = re.match(r"\s*date:\s*(\S+)", text)
        if m:
            date = m.group(1)
            text = text[m.end():]
        groups, cur = [], None
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("- "):
                if cur is None:
                    cur = {"title": "", "items": []}
                    groups.append(cur)
                cur["items"].append(s[2:].strip())
            else:
                cur = {"title": s, "items": []}
                groups.append(cur)
        out.append({"ver": ver, "vt": vt, "date": date, "groups": [g for g in groups if g["items"]]})
    out.sort(key=lambda n: n["vt"], reverse=True)
    return out


def inline_md(s: str) -> str:
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def nice_date(d: str, lang: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", d)
    if not m:
        return d
    y, mo, da = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{da} {MONTHS_AR[mo - 1]} {y}" if lang == "ar" else f"{MONTHS_EN[mo - 1]} {da}, {y}"


def release_cards(notes: list[dict], lang: str) -> str:
    out = []
    for n in notes:
        gs = "".join(f'<h4>{esc(g["title"])}</h4>' * bool(g["title"]) + "<ul>" +
                     "".join(f"<li>{inline_md(i)}</li>" for i in g["items"]) + "</ul>" for g in n["groups"])
        out.append(f'<article class="release reveal" id="v{n["ver"]}"><div class="ver"><b>{esc(n["ver"])}</b>'
                   f'<time datetime="{esc(n["date"])}">{esc(nice_date(n["date"], lang))}</time></div><div>{gs}</div></article>')
    return "".join(out)


# ------------------------------------------------------------------------------------------------ Rakiza Core
def download_block(lang: str, k: str, center: bool = True) -> str:
    ar = lang == "ar"
    L = lambda en, a: a if ar else en  # noqa: E731
    return f"""<div class="row{' center' if center else ''}">
        <a class="btn btn-core btn-dl" href="{RELEASES}" data-download rel="noopener">{icon('download')}<b>{esc(L('Download for Windows', 'حمّل لويندوز'))}</b><small data-dl-version>{esc(L('Newest version · free', 'أحدث إصدار · مجانًا'))}</small></a>
      </div>
      <div class="dl-meta"><span>{esc(L('Windows 10 and 11, 64-bit', 'Windows 10 و 11، نظام 64 بت'))}</span><span><a href="{RELEASES}" data-dl-zip rel="noopener">{esc(L('Portable ZIP', 'نسخة محمولة ZIP'))}</a></span><span><a href="{RELEASES}" data-dl-sums rel="noopener">{esc(L('SHA-256 checksums', 'بصمات SHA-256'))}</a></span></div>"""


def core(lang: str) -> str:
    ar = lang == "ar"
    k = "core"
    r = rel(k, lang)
    L = lambda en, a: a if ar else en  # noqa: E731
    title = L("Rakiza Core: everyday tools for Windows, in one light app", "ركيزة كور: أدواتك اليومية لويندوز في برنامج واحد خفيف")
    desc = L("Convert any file, edit and merge PDFs, open every archive, see what fills your drive and remove programs cleanly. "
             "One calm window, five languages, and your files never leave your PC.",
             "حوّل أي ملف، وعدّل ملفات PDF وادمجها، وافتح كل الملفات المضغوطة، واعرف ما يملأ قرصك، وأزل البرامج دون أثر. "
             "نافذة واحدة هادئة بخمس لغات، وملفاتك لا تغادر جهازك.")

    def feat(num, shot, eyebrow, h, p, items, flip=False, alt=""):
        lis = "".join(f"<li>{icon('check')}<span>{x}</span></li>" for x in items)
        return f"""<div class="feature{' flip-row' if flip else ''}">
      <div class="copy reveal">
        <span class="num">{num} · {esc(eyebrow)}</span>
        <h2 class="h2 feat">{esc(h)}</h2>
        <p class="lead sm">{esc(p)}</p>
        <ul>{lis}</ul>
      </div>
      <div class="media reveal"><div class="window">{picture(r, shot, lang, alt or h)}</div></div>
    </div>"""

    features = "".join([
        feat("01", "store", L("Files", "الملفات"), L("One workspace for every file job.", "مساحة عمل واحدة لكل ما تفعله بملفاتك."),
             L("Drop your files in, tick the ones you want, pick a tool from the library and run it with one button. "
               "Activity shows what's running and where each result was saved.",
               "أفلت ملفاتك، وحدّد ما تريد منها، واختر أداة من المكتبة وشغّلها بزر واحد. "
               "ويعرض «النشاط» ما يجري الآن وأين حُفظت كل نتيجة."),
             [L("Convert pictures, video, music and Office files", "حوّل الصور والفيديو والصوت وملفات Office"),
              L("Merge, split, compress and lock PDFs", "ادمج ملفات PDF وقسّمها واضغطها واحمها بكلمة مرور"),
              L("Make and open ZIP, RAR, 7z and more", "أنشئ وافتح ملفات ZIP و RAR و 7z وغيرها"),
              L("Rename hundreds of files at once, with undo", "أعد تسمية مئات الملفات دفعة واحدة، مع إمكانية التراجع")]),
        feat("02", "wheel", L("Convert", "التحويل"), L("Hold Shift, drag, drop. Done.", "اضغط Shift واسحب وأفلت. انتهى."),
             L("Hold Shift while you drag any file in File Explorer, and a wheel of formats opens at your cursor. Drop it on one and the copy appears next to the original, which is never changed.",
               "اضغط Shift أثناء سحب أي ملف في مستكشف الملفات، فتظهر عجلة من الصيغ عند المؤشر. أفلته على إحداها فتظهر النسخة الجديدة بجانب الأصل الذي لا يتغيّر أبدًا."),
             [L("Resize, crop, rotate and shrink to fit", "غيّر الحجم، واقصص، وأدر، وصغّر الملف ليناسب ما تريد"),
              L("Trim video and music, make GIFs and stickers", "اقصص الفيديو والصوت، واصنع صور GIF وملصقات"),
              L("Copy the text out of any picture", "انسخ النص من أي صورة"),
              L("Also from the right-click menu", "ومتاح أيضًا من قائمة النقر بزر الماوس الأيمن")], flip=True,
             alt=L("The format wheel around a dragged photo", "عجلة الصيغ حول صورة مسحوبة")),
        feat("03", "pdf", "PDF", L("Arrange PDFs by dragging their pages.", "رتّب ملفات PDF بسحب صفحاتها."),
             L("Put several PDFs on one board, drag pages into order, turn or remove them, and save. You can also change what's on a page, add bookmarks and number the pages.",
               "ضع عدة ملفات PDF على لوحة واحدة، واسحب الصفحات لترتيبها، وأدرها أو احذفها، ثم احفظ. ويمكنك أيضًا تعديل محتوى الصفحة، وإضافة إشارات مرجعية، وترقيم الصفحات."),
             [L("Merge, split and reorder", "ادمج وقسّم وأعد الترتيب"),
              L("Pictures to PDF, PDF to pictures", "حوّل الصور إلى PDF، وPDF إلى صور"),
              L("Make big PDFs small enough to send", "صغّر ملفات PDF الكبيرة لتُرسل بسهولة")]),
        feat("04", "space", L("Space", "المساحة"), L("See what's filling your drive.", "اعرف ما يملأ قرصك."),
             L("A map shows every folder by its size, down to the files. Free up space safely: anything you remove from the map goes to the Recycle Bin.",
               "خريطة تعرض كل مجلد بحسب حجمه وصولًا إلى الملفات. وفّر المساحة بأمان: كل ما تحذفه من الخريطة يذهب إلى سلة المحذوفات."),
             [L("Clean up temporary files, caches and old installers", "نظّف الملفات المؤقتة وذاكرة التخزين المؤقت وملفات التثبيت القديمة"),
              L("Find duplicate files and similar photos", "اعثر على الملفات المكررة والصور المتشابهة"),
              L("“See what fills this” right from File Explorer", "«اعرف ما يملأ هذا» مباشرة من مستكشف الملفات")], flip=True,
             alt=L("Space: a map of a folder by size", "المساحة: خريطة مجلد بحسب الحجم")),
        feat("05", "apps", L("Uninstaller", "إزالة البرامج"), L("Remove programs, and what they leave behind.", "أزل البرامج، وما تتركه خلفها."),
             L("Uninstall a program, then clear the folders, shortcuts and registry entries it left. Everything removed is kept as a backup for 30 days, so you can put it back.",
               "أزل البرنامج، ثم نظّف المجلدات والاختصارات وإدخالات السجل التي تركها. ويُحفظ كل ما أُزيل نسخةً احتياطية لمدة 30 يومًا، فيمكنك استرجاعه."),
             [L("Desktop and Microsoft Store apps in one list", "برامج سطح المكتب وتطبيقات Microsoft Store في قائمة واحدة"),
              L("Sizes, install dates and when you last used them", "الأحجام وتواريخ التثبيت وآخر استخدام")]),
        feat("06", "cloud", L("Cloud", "السحابة"), L("All your clouds in one window.", "كل حساباتك السحابية في نافذة واحدة."),
             L("OneDrive, Dropbox, Google Drive and iCloud, found by themselves. Search every cloud at once and drag files from one to another. Files go straight between your PC and the service.",
               "OneDrive و Dropbox و Google Drive و iCloud، يعثر عليها البرنامج تلقائيًا. ابحث فيها كلها معًا، واسحب الملفات من سحابة إلى أخرى. وتنتقل الملفات مباشرة بين جهازك والخدمة."),
             [L("Sign in to as many accounts as you like", "سجّل الدخول إلى أي عدد من الحسابات"),
              L("Your password is never seen by Rakiza Core", "لا يرى ركيزة كور كلمة مرورك أبدًا")], flip=True),
    ])

    also = [
        ("search", L("Find anything with Ctrl + K", "ابحث عن أي شيء بـ Ctrl + K"),
         L("Any tool, page or setting, by typing a few letters.", "أي أداة أو صفحة أو إعداد، بكتابة بضعة أحرف.")),
        ("unlock", L("Unlock", "فك القفل"),
         L("Finds which program holds a file you can't delete or move, and asks it to let go.",
           "يعرف أي برنامج يمسك ملفًا لا تستطيع حذفه أو نقله، ويطلب منه تركه.")),
        ("archive", L("Archives", "الملفات المضغوطة"),
         L("Open ZIP, RAR, 7z, ISO and more, change a file inside and save it back.",
           "افتح ZIP و RAR و 7z و ISO وغيرها، وعدّل ملفًا بداخلها واحفظه مكانه.")),
        ("network", L("Network", "الشبكة"),
         L("See which programs use the internet and how fast it is. Off until you switch it on.",
           "اعرف أي البرامج تستخدم الإنترنت وما سرعته. متوقفة حتى تشغّلها.")),
        ("layers", L("Only what you use", "ما تستخدمه فقط"),
         L("Switch off the tools you don't need: a tool that's off isn't loaded at all.",
           "أوقف الأدوات التي لا تحتاجها: الأداة المتوقفة لا تُحمَّل أبدًا.")),
        ("refresh", L("Updates inside the app", "التحديثات من داخل البرنامج"),
         L("New versions arrive by themselves, with notes on what changed.", "تصل الإصدارات الجديدة تلقائيًا، مع ملاحظات بما تغيّر.")),
    ]
    al = "".join(f'<div class="card reveal"><span class="ic">{icon(i)}</span><h3>{esc(h)}</h3><p>{esc(p)}</p></div>' for i, h, p in also)

    faqs = [
        (L("Which Windows do I need?", "ما إصدار Windows المطلوب؟"),
         [L("Windows 10 or Windows 11, 64-bit.", "Windows 10 أو Windows 11، نظام 64 بت.")]),
        (L("Why does Windows say “Windows protected your PC”?", "لماذا يظهر تنبيه «Windows protected your PC»؟"),
         [L("Rakiza Core isn't code-signed yet, so Windows doesn't recognise it. Click More info, then Run anyway.",
            "لأن ركيزة كور غير موقّع رقميًا حتى الآن، فلا يتعرّف عليه Windows. انقر More info ثم Run anyway."),
          L(f'Each release lists <a href="{RELEASES}" rel="noopener">SHA-256 checksums</a>, so you can check that your download is the real file.',
            f'وكل إصدار يضم <a href="{RELEASES}" rel="noopener">بصمات SHA-256</a>، لتتأكد أن الملف الذي حمّلته هو الملف الأصلي.')]),
        (L("Does it send anything over the internet?", "هل يرسل شيئًا عبر الإنترنت؟"),
         [L(f'Only a check for its own updates on GitHub (you can turn it off), and your cloud accounts if you choose to sign in. No statistics, no tracking. <a href="{link(k, lang, "privacy")}">Read the privacy policy</a>.',
            f'فقط التحقق من تحديثاته على GitHub (ويمكنك إيقافه)، وحساباتك السحابية إن اخترت تسجيل الدخول إليها. لا إحصاءات ولا تتبّع. <a href="{link(k, lang, "privacy")}">اقرأ سياسة الخصوصية</a>.')]),
        (L("What does “early access” mean?", "ماذا يعني «إصدار مبكر»؟"),
         [L("Rakiza Core works today, and we use it every day, but we are still polishing it. A few tools are resting while we focus on the essentials; they come back in later updates.",
            "ركيزة كور يعمل اليوم ونستخدمه كل يوم، لكننا ما زلنا نصقله. بعض الأدوات متوقفة مؤقتًا بينما نركّز على الأساسيات، وستعود في تحديثات قادمة.")]),
        (L("Does it ask for administrator rights?", "هل يطلب صلاحيات المسؤول؟"),
         [L("Rakiza Core installs in Program Files for everyone on the PC, so Windows asks for permission when you install it.",
            "يُثبَّت ركيزة كور في مجلد Program Files لكل مستخدمي الجهاز، لذلك يطلب Windows الإذن عند التثبيت.")]),
        (L("How do I remove it?", "كيف أزيله؟"),
         [L("Like any program, from Windows Settings, Apps. It offers to delete its settings too.",
            "مثل أي برنامج، من «الإعدادات» ثم «التطبيقات» في Windows. ويعرض عليك حذف إعداداته أيضًا.")]),
        (L("Where do I report a problem or suggest something?", "أين أبلغ عن مشكلة أو أقترح فكرة؟"),
         [L(f'On <a href="{ISSUES}" rel="noopener">GitHub</a>. We read everything.', f'على <a href="{ISSUES}" rel="noopener">GitHub</a>. نقرأ كل ما يصلنا.')]),
    ]
    fq = "".join(f'<details><summary>{esc(q)}<span class="pm" aria-hidden="true"></span></summary><div class="ans">{"".join(f"<p>{a}</p>" for a in ans)}</div></details>'
                 for q, ans in faqs)

    notes = read_notes(lang)[:3]
    langs = "".join(f'<span lang="{c}">{n}</span>' for c, n in
                    [("en", "English"), ("ar", "العربية"), ("fr", "Français"), ("es", "Español"), ("tr", "Türkçe")])
    body = f"""
<section class="p-hero">
  <div class="wrap">
    <div class="inner">
      <img class="appicon" src="{r}assets/img/rakiza-core-192.png" width="92" height="92" alt="">
      <span class="pill core live"><i></i>{esc(t('early', lang))}</span>
      <h1 class="display sm">{esc(L('Everything your PC needs. One light app.', 'كل ما يحتاجه جهازك، في برنامج واحد خفيف.'))}</h1>
      <p class="lead">{esc(L("Rakiza Core brings your everyday tools into one calm window: convert, PDFs, archives, disk space, uninstalling and your clouds. Your files never leave your PC.",
                              "يجمع ركيزة كور أدواتك اليومية في نافذة واحدة هادئة: التحويل، وملفات PDF، والملفات المضغوطة، ومساحة القرص، وإزالة البرامج، وحساباتك السحابية. وملفاتك لا تغادر جهازك."))}</p>
      {download_block(lang, k)}
    </div>
    <div class="stage reveal">
      <div class="window">{picture(r, 'home', lang, L('Rakiza Core: the home page', 'ركيزة كور: الصفحة الرئيسية'), eager=True)}</div>
    </div>
  </div>
</section>
<hr class="rule">

<section id="features">
  <div class="wrap">{features}</div>
</section>

<section id="more" class="pt-0">
  <div class="wrap">
    <div class="sec-head reveal">
      <span class="eyebrow">{esc(L('Also inside', 'وأيضًا'))}</span>
      <h2 class="h2">{esc(L('The small things that make it feel right.', 'تفاصيل صغيرة تجعله مريحًا.'))}</h2>
    </div>
    <div class="also">{al}</div>
  </div>
</section>

<section class="band" id="private">
  <div class="wrap band-grid">
    <div class="sec-head reveal mb-0">
      <span class="eyebrow">{esc(L('Private and light', 'خاص وخفيف'))}</span>
      <h2 class="h2">{esc(L('Your files never leave your PC.', 'ملفاتك لا تغادر جهازك.'))}</h2>
      <p class="lead">{esc(L("No account, no ads, no tracking. Rakiza Core waits in the tray using very little memory, runs its work at low priority, and hands memory back to Windows when it's idle.",
                             "لا حساب ولا إعلانات ولا تتبّع. ينتظر ركيزة كور في شريط المهام مستخدمًا القليل جدًا من الذاكرة، وينفّذ أعماله بأولوية منخفضة، ويعيد الذاكرة إلى Windows عندما يكون خاملًا."))}</p>
    </div>
    <div class="facts reveal">
      <div class="fact"><span class="ic">{icon('lock')}</span><div><h3>{esc(L('Nothing is uploaded', 'لا يُرفع شيء'))}</h3><p>{esc(L('Converting, PDFs, archives and cleaning all happen on your PC.', 'التحويل وملفات PDF والملفات المضغوطة والتنظيف، كلها تحدث على جهازك.'))}</p></div></div>
      <div class="fact"><span class="ic">{icon('undo')}</span><div><h3>{esc(L('Originals stay untouched', 'الأصل يبقى كما هو'))}</h3><p>{esc(L('New files are made next to the old ones, and big changes can be undone.', 'تُنشأ الملفات الجديدة بجانب القديمة، ويمكن التراجع عن التغييرات الكبيرة.'))}</p></div></div>
      <div class="fact"><span class="ic">{icon('feather')}</span><div><h3>{esc(L('Light on your PC', 'خفيف على جهازك'))}</h3><p>{esc(L('A tool you switch off loads nothing at all.', 'الأداة التي توقفها لا تحمّل أي شيء.'))}</p></div></div>
    </div>
  </div>
</section>

<section id="languages">
  <div class="wrap langs">
    <div class="sec-head reveal mb-0">
      <span class="eyebrow">{esc(L('In your language', 'بلغتك'))}</span>
      <h2 class="h2">{esc(L('Arabic that feels native, not flipped.', 'عربية أصيلة، لا واجهة مقلوبة.'))}</h2>
      <p class="lead">{esc(L("Rakiza Core asks for your language the first time it starts and switches any time without restarting. In Arabic, the whole app reads right to left.",
                             "يسألك ركيزة كور عن لغتك عند أول تشغيل، ويمكنك تغييرها في أي وقت دون إعادة التشغيل. وبالعربية تُقرأ الواجهة كلها من اليمين إلى اليسار."))}</p>
      <div class="lang-list">{langs}</div>
    </div>
    <div class="media reveal"><div class="window">{picture(r, 'home', 'en' if ar else 'ar', L('Rakiza Core in Arabic', 'ركيزة كور بالإنجليزية'))}</div></div>
  </div>
</section>

<section id="early" class="pt-0">
  <div class="wrap">
    <div class="note reveal">
      <span class="ic">{icon('info')}</span>
      <div><h3>{esc(L('Early access, honestly', 'إصدار مبكر، بصراحة'))}</h3>
      <p>{esc(L("Rakiza Core works today and gets better with every release. Some tools are resting while we put our full attention on Files; nothing of them runs, and they come back in later updates. If something feels off, tell us.",
                "ركيزة كور يعمل اليوم ويتحسّن مع كل إصدار. بعض الأدوات متوقفة مؤقتًا بينما نركّز كامل اهتمامنا على «الملفات»؛ لا يعمل منها شيء، وستعود في تحديثات قادمة. وإن لاحظت ما لا يعجبك فأخبرنا."))}</p></div>
    </div>
  </div>
</section>

<section id="new" class="pt-0">
  <div class="wrap">
    <div class="sec-head reveal">
      <span class="eyebrow">{esc(L("What's new", 'الجديد'))}</span>
      <h2 class="h2">{esc(L('Recent releases', 'أحدث الإصدارات'))}</h2>
    </div>
    <div class="releases">{release_cards(notes, lang)}</div>
    <div class="row mt-m"><a class="link" href="{link(k, lang, 'changelog')}">{esc(L('Every release since 2.0', 'كل الإصدارات منذ 2.0'))}{icon('arrow', 'flip')}</a></div>
  </div>
</section>

<section id="faq" class="pt-0">
  <div class="wrap faq">
    <div class="sec-head reveal mb-0">
      <span class="eyebrow">{esc(L('Questions', 'أسئلة'))}</span>
      <h2 class="h2">{esc(L('Good to know', 'من المفيد أن تعرف'))}</h2>
    </div>
    <div class="faq-list reveal">{fq}</div>
  </div>
</section>

<section class="band" id="download">
  <div class="wrap center-stack">
    <img class="appicon" src="{r}assets/img/rakiza-core-192.png" width="80" height="80" alt="">
    <h2 class="h2">{esc(L('Try Rakiza Core.', 'جرّب ركيزة كور.'))}</h2>
    <p class="lead mx-auto">{esc(L('Free download for Windows 10 and 11.', 'تحميل مجاني لـ Windows 10 و 11.'))}</p>
    {download_block(lang, k)}
  </div>
</section>
"""
    return page(lang, k, title, desc, body)


def changelog(lang: str) -> str:
    ar = lang == "ar"
    k = "changelog"
    L = lambda en, a: a if ar else en  # noqa: E731
    title = L("What's new in Rakiza Core", "الجديد في ركيزة كور")
    desc = L("Every Rakiza Core release since 2.0 and what changed in it.", "كل إصدارات ركيزة كور منذ 2.0 وما تغيّر في كل منها.")
    body = f"""
<section class="doc-hero">
  <div class="wrap"><div class="inner">
    <span class="eyebrow">{esc(t('core', lang))}</span>
    <h1 class="display xs">{esc(L("What's new", 'الجديد'))}</h1>
    <p class="lead">{esc(L('Every release since Rakiza Core 2.0. The app brings new versions by itself; you can also download them from GitHub.',
                           'كل الإصدارات منذ ركيزة كور 2.0. يجلب البرنامج الإصدارات الجديدة بنفسه، ويمكنك أيضًا تحميلها من GitHub.'))}</p>
    <div class="row"><a class="btn btn-core" href="{RELEASES}" data-download rel="noopener">{icon('download')}{esc(L('Download the newest version', 'حمّل أحدث إصدار'))}</a><a class="link" href="{ALL_RELEASES}" rel="noopener">{esc(L('All releases on GitHub', 'كل الإصدارات على GitHub'))}{icon('arrow', 'flip')}</a></div>
  </div></div>
</section>
<section class="pt-xs">
  <div class="wrap"><div class="releases">{release_cards(read_notes(lang), lang)}</div></div>
</section>
"""
    return page(lang, k, title, desc, body)


# ------------------------------------------------------------------------------------------------ OnAir
ONAIR_ART = """<svg viewBox="0 0 980 490" role="img" aria-label="{label}" preserveAspectRatio="xMidYMid slice">
<defs>
<linearGradient id="oa-g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#E9B0FF"/><stop offset="1" stop-color="#7A2DBE"/></linearGradient>
<radialGradient id="oa-r" cx=".5" cy=".42" r=".6"><stop offset="0" stop-color="#B565F0" stop-opacity=".35"/><stop offset="1" stop-color="#B565F0" stop-opacity="0"/></radialGradient>
</defs>
<rect width="980" height="490" fill="var(--sunken)"/>
<rect width="980" height="490" fill="url(#oa-r)"/>
<g fill="none" stroke="var(--line-strong)" stroke-width="1.5">
<rect x="70" y="70" width="560" height="315" rx="14" fill="var(--surface)"/>
<rect x="660" y="70" width="250" height="150" rx="14" fill="var(--surface)"/>
<rect x="660" y="235" width="250" height="150" rx="14" fill="var(--surface)"/>
<rect x="70" y="400" width="840" height="46" rx="12" fill="var(--surface)"/>
</g>
<g fill="var(--line-strong)">
<rect x="684" y="96" width="120" height="10" rx="5"/><rect x="684" y="118" width="190" height="8" rx="4" opacity=".6"/><rect x="684" y="136" width="160" height="8" rx="4" opacity=".6"/>
<rect x="684" y="180" width="70" height="20" rx="10"/><rect x="762" y="180" width="70" height="20" rx="10" opacity=".6"/>
<rect x="684" y="261" width="100" height="10" rx="5"/>
<rect x="684" y="288" width="200" height="6" rx="3" opacity=".5"/><rect x="684" y="306" width="200" height="6" rx="3" opacity=".5"/><rect x="684" y="324" width="200" height="6" rx="3" opacity=".5"/>
<circle cx="740" cy="291" r="7" fill="url(#oa-g)"/><circle cx="820" cy="309" r="7" fill="url(#oa-g)"/><circle cx="700" cy="327" r="7" fill="url(#oa-g)"/>
<rect x="94" y="416" width="90" height="14" rx="7"/><rect x="196" y="416" width="90" height="14" rx="7" opacity=".6"/><rect x="298" y="416" width="90" height="14" rx="7" opacity=".6"/>
</g>
<rect x="94" y="94" width="512" height="267" rx="10" fill="var(--raised)"/>
<g transform="translate(350 227)">
<circle r="18" fill="url(#oa-g)"/>
<g fill="none" stroke="url(#oa-g)" stroke-width="7" stroke-linecap="round">
<path d="M-38 -38A54 54 0 0 0 -38 38"/><path d="M38 -38A54 54 0 0 1 38 38"/>
<path d="M-66 -66A94 94 0 0 0 -66 66" opacity=".55"/><path d="M66 -66A94 94 0 0 1 66 66" opacity=".55"/>
</g>
</g>
<g transform="translate(114 114)"><rect width="92" height="28" rx="14" fill="#E11D48"/><circle cx="18" cy="14" r="5" fill="#fff"/><text x="32" y="19" font-family="Readex Pro, sans-serif" font-size="13" font-weight="600" fill="#fff" letter-spacing="1.5">ON AIR</text></g>
<rect x="836" y="414" width="58" height="18" rx="9" fill="url(#oa-g)"/>
</svg>"""


def onair(lang: str) -> str:
    ar = lang == "ar"
    k = "onair"
    r = rel(k, lang)
    L = lambda en, a: a if ar else en  # noqa: E731
    title = L("Rakiza OnAir: your stream setup in one plugin (coming soon)", "ركيزة أون إير: كل ما يحتاجه بثّك في إضافة واحدة (قريبًا)")
    desc = L("Rakiza OnAir is a coming plugin for OBS Studio that brings together the tools streamers usually install one by one.",
             "ركيزة أون إير إضافة قادمة لبرنامج OBS Studio تجمع الأدوات التي يثبّتها صنّاع البث عادةً واحدة تلو الأخرى.")
    aims = [
        ("plug", L("One install instead of many", "تثبيت واحد بدل الكثير"),
         L("The plugins you'd normally hunt down one by one, together in one place.", "الإضافات التي تبحث عنها عادةً واحدة تلو الأخرى، مجتمعة في مكان واحد.")),
        ("layers", L("Feels like one tool", "تبدو أداةً واحدة"),
         L("One look, one set of settings, and everything updates together.", "شكل واحد، وإعدادات واحدة، وكل شيء يُحدَّث معًا.")),
        ("globe", L("Arabic and English from day one", "عربي وإنجليزي من اليوم الأول"),
         L("Built by the team behind Rakiza Core, with the same care for both languages.", "من الفريق نفسه الذي صنع ركيزة كور، وبالعناية نفسها باللغتين.")),
    ]
    am = "".join(f'<div class="card reveal"><span class="ic">{icon(i)}</span><h3>{esc(h)}</h3><p>{esc(p)}</p></div>' for i, h, p in aims)
    idea = ISSUES + "?title=Rakiza%20OnAir%3A%20"
    body = f"""
<section class="p-hero air">
  <div class="wrap">
    <div class="inner">
      <img class="appicon" src="{r}assets/img/onair-icon.svg" width="92" height="92" alt="">
      <span class="pill air"><i></i>{esc(t('soon', lang))}</span>
      <h1 class="display sm">{esc(L('Your whole stream setup, in one plugin.', 'كل ما يحتاجه بثّك، في إضافة واحدة.'))}</h1>
      <p class="lead">{esc(L("Rakiza OnAir is a plugin for OBS Studio that brings together the tools streamers usually install one by one, so they look, update and work as one. We're building it now.",
                              "ركيزة أون إير إضافة لبرنامج OBS Studio تجمع الأدوات التي يثبّتها صنّاع البث عادةً واحدة تلو الأخرى، لتبدو وتعمل وتُحدَّث كأنها أداة واحدة. نعمل عليها الآن."))}</p>
      <div class="row"><a class="btn btn-primary btn-lg" href="{idea}" rel="noopener">{icon('message')}{esc(L('Tell us what you stream with', 'أخبرنا بما تستخدمه في بثّك'))}</a></div>
    </div>
    <div class="onair-visual reveal">{ONAIR_ART.format(label=esc(L('A sketch of a streaming setup', 'رسم توضيحي لإعداد بث')))}</div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="sec-head reveal">
      <span class="eyebrow">{esc(L("What we're aiming for", 'ما نسعى إليه'))}</span>
      <h2 class="h2">{esc(L('Less setup. More streaming.', 'إعداد أقل، وبث أكثر.'))}</h2>
      <p class="lead">{esc(L("OnAir is early. These are the goals we're building towards, and your ideas shape what goes in first.",
                             "أون إير في بدايته. هذه هي الأهداف التي نبني نحوها، وأفكارك تحدد ما يأتي أولًا."))}</p>
    </div>
    <div class="aims">{am}</div>
  </div>
</section>

<section class="pt-0">
  <div class="wrap">
    <div class="contact reveal">
      <div class="stack">
        <h2>{esc(L('Which plugins can’t you stream without?', 'ما الإضافات التي لا تستغني عنها في بثّك؟'))}</h2>
        <p class="muted">{esc(L("Tell us on GitHub. We don't collect email addresses; when OnAir is ready, it will be announced here and on Rakiza Labs' GitHub.",
                                "أخبرنا على GitHub. لا نجمع عناوين البريد، وعندما تجهز أون إير سنعلن عنها هنا وعلى GitHub."))}</p>
      </div>
      <div class="row"><a class="btn btn-primary" href="{idea}" rel="noopener">{icon('github')}{esc(L('Share an idea', 'شارك فكرة'))}</a></div>
    </div>
    <p class="muted fine">{esc(L("OBS Studio is a trademark of its respective owners. Rakiza OnAir is an independent plugin and is not made or endorsed by the OBS Project.",
                                                                                    "OBS Studio علامة تجارية لمالكيها. ركيزة أون إير إضافة مستقلة، وليست من صنع مشروع OBS ولا معتمدة منه."))}</p>
  </div>
</section>
"""
    return page(lang, k, title, desc, body)


# ------------------------------------------------------------------------------------------------ privacy
def privacy(lang: str) -> str:
    k = "privacy"
    src = (Path(__file__).resolve().parent / "site_text" / f"privacy.{lang}.html").read_text("utf-8")
    m = re.search(r"<!-- title: (.+?) -->\s*<!-- description: (.+?) -->\s*<!-- updated: (.+?) -->", src)
    title, desc, updated = m.group(1), m.group(2), m.group(3)
    body_html = src[m.end():].strip()
    ar = lang == "ar"
    body = f"""
<section class="doc-hero">
  <div class="wrap"><div class="inner">
    <span class="eyebrow">{esc(t('labs', lang))}</span>
    <h1 class="display xs">{esc('الخصوصية' if ar else 'Privacy')}</h1>
    <p class="muted">{esc(updated)}</p>
  </div></div>
</section>
<div class="wrap"><article class="doc">
{body_html}
</article></div>
"""
    return page(lang, k, title, desc, body)


# ------------------------------------------------------------------------------------------------ 404
def not_found() -> str:
    # GitHub Pages shows this for any missing address, at any depth, so its links start at the site root.
    return f"""<!doctype html>
<html lang="en" dir="ltr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'">
<title>Page not found · Rakiza Labs</title>
<meta name="robots" content="noindex">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/assets/css/site.css?v={ASSET_V}">
<script src="/assets/js/theme.js?v={ASSET_V}"></script>
</head>
<body>
<main class="lost wrap">
  <div class="inner">
    <span class="big">404</span>
    <h1 class="h2">This page isn't here.</h1>
    <p class="lead" lang="ar" dir="rtl">هذه الصفحة غير موجودة.</p>
    <div class="row center">
      <a class="btn btn-primary" href="/">Rakiza Labs</a>
      <a class="btn btn-ghost" href="/ar/" lang="ar">الصفحة الرئيسية</a>
    </div>
  </div>
</main>
</body>
</html>
"""


# ------------------------------------------------------------------------------------------------ pictures
SHOTS = ["home", "store", "wheel", "pdf", "space", "apps", "cloud"]


def refresh_shots(src: Path):
    from PIL import Image
    out = DOCS / "assets" / "img" / "shots"
    out.mkdir(parents=True, exist_ok=True)
    for name in SHOTS:
        for lang in ("en", "ar"):
            for look in ("light", "dark"):
                f = src / f"{name}-{lang}-{look}.png"
                if not f.exists():
                    print("missing", f.name)
                    continue
                im = Image.open(f).convert("RGBA")
                w, h = im.size
                m = 36  # the margin README pictures add around a window, for its shadow
                im = im.crop((m, m, w - m, h - m))
                im.save(out / f"{name}-{lang}-{look}.webp", "WEBP", quality=88, method=6)
    print("pictures refreshed")


def read_sizes():
    from PIL import Image
    for f in (DOCS / "assets" / "img" / "shots").glob("*.webp"):
        with Image.open(f) as im:
            SHOT_SIZES[f.stem] = im.size


# ------------------------------------------------------------------------------------------------ write
def write(path: str, text: str):
    p = DOCS / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8", newline="\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", type=Path, help='the "readme" folder of a README pictures run')
    a = ap.parse_args()
    if a.shots:
        refresh_shots(a.shots)
    read_sizes()
    builders = {"home": home, "core": core, "changelog": changelog, "onair": onair, "privacy": privacy}
    for key, fn in builders.items():
        for lang in ("en", "ar"):
            write(PAGES[key][1 if lang == "ar" else 0], fn(lang))
    write("404.html", not_found())
    urls = [SITE + url_of(key, lang) for key in builders for lang in ("en", "ar")]
    write("sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
          "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls) + "</urlset>\n")
    write("robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {SITE}sitemap.xml\n")
    print(f"wrote {len(urls)} pages")


if __name__ == "__main__":
    sys.exit(main())
