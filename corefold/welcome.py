"""The very first start: "Choose your language", before anything else of Corefold shows.

The language Windows is in is picked already. Clicking another one shows this page in it straight
away, so people see what they're choosing. Shown once, after installing; the language button at the
top of the window changes it later."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from . import i18n, winutil
from .atomic import tokens as atomic_tokens
from .atomic.widgets import ChoiceCard
from .i18n import tr
from .settings import WINDOW_TEXT_PT, Settings
from .theme import build_theme, stylesheet

CHOICES = ("en", "ar", "fr", "es", "tr")   # (not "Match Windows": the one Windows is in is picked already)


def should_ask(s: Settings, argv: list[str]) -> bool:
    """Only on the first start after installing, and not when Windows starts Corefold in the background."""
    return s.first_run and "--background" not in argv


class LanguagePicker(QDialog):
    def __init__(self, icon: QIcon, s: Settings, start: str):
        super().__init__(None, Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.s = s
        self.code = start
        self.setObjectName("window")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setWindowIcon(icon)
        self.setMinimumWidth(520)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        canvas = QFrame()
        canvas.setObjectName("canvas")
        outer.addWidget(canvas)
        lay = QVBoxLayout(canvas)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(icon.pixmap(48, 48))
        lay.addWidget(logo)
        lay.addSpacing(6)
        self.hello = QLabel()
        self.hello.setObjectName("eyebrow")
        lay.addWidget(self.hello)
        self.title = QLabel()
        self.title.setObjectName("h1")
        lay.addWidget(self.title)
        self.intro = QLabel()
        self.intro.setObjectName("muted")
        self.intro.setWordWrap(True)
        lay.addWidget(self.intro)
        lay.addSpacing(10)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.cards: dict[str, ChoiceCard] = {}
        self.windows = i18n.windows_language()
        for code in CHOICES:
            card = ChoiceCard(i18n.LANGUAGES[code])
            card.setChecked(code == start)
            card.clicked.connect(lambda _=False, c=code: self.choose(c))
            self.group.addButton(card)
            self.cards[code] = card
            lay.addWidget(card)
        lay.addSpacing(12)
        row = QHBoxLayout()
        row.addStretch()
        self.go = QPushButton()
        self.go.setObjectName("primary")
        self.go.setCursor(Qt.PointingHandCursor)
        self.go.setDefault(True)
        self.go.clicked.connect(self.accept)
        row.addWidget(self.go)
        lay.addLayout(row)
        self._words()
        self.cards[start].setFocus()

    def choose(self, code: str):
        if code == self.code:
            return
        self.code = code
        i18n.set_language(code)
        app = QApplication.instance()
        app.setLayoutDirection(Qt.RightToLeft if i18n.is_rtl() else Qt.LeftToRight)
        _style(app, self.s)  # (the stylesheet has a right-to-left side)
        self._words()

    def _words(self):
        """This page's words, in the language picked."""
        self.setWindowTitle(tr("Welcome"))
        self.hello.setText(tr("Welcome to Corefold"))
        self.title.setText(tr("Choose your language"))
        self.intro.setText(tr("You can change it any time with the language button at the top of Corefold's "
                              "window."))
        self.go.setText(tr("Continue"))
        for code, card in self.cards.items():
            card.chip = tr("Windows language") if code == self.windows else ""
            card.updateGeometry()
            card.update()
        self.adjustSize()


def _style(app: QApplication, s: Settings):
    theme = build_theme(s.appearance, s.accent, s.custom_color, s.wheel_opacity)
    atomic_tokens.use(theme.tokens, s.animations)
    app.setStyleSheet(stylesheet(theme, WINDOW_TEXT_PT.get(s.window_text, 10) / 10))
    return theme


def ask_language(app: QApplication, icon: QIcon, s: Settings) -> str:
    """Shows the page and saves the language picked (closing the page keeps the one picked)."""
    start = i18n.resolve(s.language) if s.language != "system" else i18n.windows_language()
    if start not in CHOICES:
        start = "en"
    i18n.set_language(start)
    app.setLayoutDirection(Qt.RightToLeft if i18n.is_rtl() else Qt.LeftToRight)
    theme = _style(app, s)
    dlg = LanguagePicker(icon, s, start)
    winutil.set_title_bar_dark(dlg, theme.dark)
    dlg.exec()
    code = dlg.code
    dlg.deleteLater()
    s.language = code
    s.save()
    return code
