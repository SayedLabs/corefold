"""The quick menu: a panel over a dimmed screen that opens from any app with a shortcut (like a game
overlay). It has the quick actions a person picked, their tools, their own shortcuts and the files
Corefold made lately. Start typing to search it; arrows and Enter work too; Esc or the shortcut
again closes it.

It's painted rather than made of widgets: it opens in a frame, and only exists once it's been opened."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QDateTime, QEvent, QLocale, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import icons, quick
from .core import APP_NAME
from .i18n import is_rtl, tr

try:  # the design system's sizes (atomic/tokens.py)
    from .atomic.tokens import RADIUS
    PANEL_RADIUS, ITEM_RADIUS = RADIUS["xl"], RADIUS["md"]
except ImportError:
    PANEL_RADIUS, ITEM_RADIUS = 14, 10
PAD = 24
TILE_W, TILE_H, GAP = 116, 92, 10
ROW_H = 44
MAX_RECENT = 6


@dataclass
class Spot:
    """Something on the panel you can point at, and where it is."""
    rect: QRectF
    entry: quick.Entry
    kind: str = "tile"   # tile, row (a recent file), add (the "Add a shortcut" tile)


def _rounded(rect: QRectF, r: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, r, r)
    return path


def _colors(t) -> tuple[QColor, QColor]:
    """The panel's colour and the colour behind the tile under the mouse."""
    tok = getattr(t, "tokens", None)
    if tok is not None:
        return tok.raised, tok.accent_soft
    return t.panel, t.hover


def _alpha(c: QColor, a: int) -> QColor:
    c = QColor(c)
    c.setAlpha(a)
    return c


class QuickMenu(QWidget):
    def __init__(self, qa: "quick.QuickAccess"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setWindowTitle(tr("Quick menu"))
        self.qa = qa
        self.theme = qa.c.theme
        self.query = ""
        self.spots: list[Spot] = []
        self.sections: list[tuple[str, float]] = []  # heading, its y
        self.selected = -1
        self.hovered = -1
        self.panel = QRectF()
        self.view = QRectF()   # the part of the panel under the search line (it scrolls if it has to)
        self.scroll = 0.0
        self.max_scroll = 0.0
        self.t = 1.0  # opening animation, 0..1
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(_motion("fast", 150))
        self.anim.valueChanged.connect(self._animate)
        self.anim.finished.connect(self._anim_done)
        self._closing = False
        self._opened_at = 0.0
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.update)

    # ---- opening and closing

    def open(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.setGeometry(screen.geometry())
        self.query = ""
        self.selected = -1
        self.hovered = -1
        self.scroll = 0.0
        self._closing = False
        self.layout_spots()
        self.selected = 0 if self.spots else -1
        animate = self.qa.c.settings.animations
        self.t = 0.0 if animate else 1.0
        self.setWindowOpacity(0.0 if animate else 1.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        _bring_to_front(int(self.winId()))
        self._opened_at = time.monotonic()
        self.clock.start(15_000)
        if animate:
            self.anim.stop()
            self.anim.setStartValue(0.0)
            self.anim.setEndValue(1.0)
            self.anim.start()

    def close_animated(self):
        if not self.isVisible() or self._closing:
            return
        if not self.qa.c.settings.animations:
            self.hide()
            return
        self._closing = True
        self.anim.stop()
        self.anim.setStartValue(self.t)
        self.anim.setEndValue(0.0)
        self.anim.start()

    def _animate(self, v):
        self.t = float(v)
        self.setWindowOpacity(min(1.0, self.t * 1.4))
        self.update()

    def _anim_done(self):
        if self._closing:
            self._closing = False
            self.hide()

    def hideEvent(self, e):
        self.clock.stop()
        self.anim.stop()
        self.spots = []
        self.qa.c.popup_closed()
        super().hideEvent(e)

    def changeEvent(self, e):
        # clicking another app (or Alt+Tab) puts the menu away, like any popup
        if e.type() == QEvent.ActivationChange and self.isVisible() and not self.isActiveWindow():
            if time.monotonic() - self._opened_at > 0.3:  # (not while Windows is still handing it the focus)
                self.hide()
        super().changeEvent(e)

    def run(self, spot: Spot):
        self.hide()
        if spot.kind == "add":
            self.qa.c.show_window("Quick")
        elif spot.kind == "row":
            quick.open_target(spot.entry.hint)
        else:
            self.qa.run(spot.entry.id)

    # ---- what's on it

    def _recent(self) -> list[quick.Entry]:
        out: list[quick.Entry] = []
        seen: set[str] = set()
        for item in self.qa.c.history.items:
            for path in item.outputs[:3]:
                if len(out) >= MAX_RECENT:
                    return out
                if path in seen:
                    continue
                seen.add(path)
                p = Path(path)
                try:
                    if not p.exists():
                        continue
                except OSError:
                    continue
                out.append(quick.Entry(f"file:{path}", p.name, "folder" if p.is_dir() else "document", path))
        return out

    def _sections(self) -> list[tuple[str, list[quick.Entry], str]]:
        s = self.qa.c.settings
        if self.query:
            q = self.query.lower()
            found = [e for e in quick.choosable(s) if quick.matches(e, q)]
            files = [e for e in self._recent() if quick.matches(e, q)]
            return [(tr("Results"), found, "tile"), (tr("Recent files"), files, "row")]
        return [(tr("Quick actions"), quick.entries(s, s.quick_menu_items), "tile"),
                (tr("Tools"), quick.tool_entries(s), "tile"),
                (tr("Your shortcuts"), quick.link_entries(s), "links"),
                (tr("Recent files"), self._recent(), "row")]

    def layout_spots(self):
        """Works out where everything goes (in screen pixels of this window)."""
        W, H = self.width(), self.height()
        cols = max(3, min(7, int((min(W - 80, 920) - 2 * PAD + GAP) // (TILE_W + GAP))))
        inner_w = cols * TILE_W + (cols - 1) * GAP
        pw = inner_w + 2 * PAD
        spots: list[Spot] = []
        heads: list[tuple[str, float]] = []
        y = PAD + 46 + 18 + 40  # header, search line
        for title, items, kind in self._sections():
            if kind == "links":
                items = list(items) + [quick.Entry("add", tr("Add a shortcut"), "add")] if not self.query else items
            if not items:
                continue
            heads.append((title, y))
            y += 28
            if kind == "row":
                half = (inner_w - GAP) / 2
                for i, e in enumerate(items):
                    col, row = i % 2, i // 2
                    spots.append(Spot(QRectF(PAD + col * (half + GAP), y + row * (ROW_H + 6), half, ROW_H), e, "row"))
                y += math.ceil(len(items) / 2) * (ROW_H + 6) + 12
            else:
                for i, e in enumerate(items):
                    col, row = i % cols, i // cols
                    spots.append(Spot(QRectF(PAD + col * (TILE_W + GAP), y + row * (TILE_H + GAP), TILE_W, TILE_H),
                                      e, "add" if e.id == "add" else "tile"))
                y += math.ceil(len(items) / cols) * (TILE_H + GAP) + 12
        if not spots:
            y += 60
        content_top = PAD + 46 + 18 + 40
        ph = min(y + PAD - 12, H - 60)
        self.panel = QRectF((W - pw) / 2, max(30, (H - ph) / 2 - 20), pw, ph)
        dx, dy = self.panel.left(), self.panel.top()
        rtl = is_rtl()
        for spot in spots:
            r = spot.rect.translated(dx, dy)
            if rtl:  # right to left: the first tile is on the right
                r.moveLeft(self.panel.left() + self.panel.right() - r.right())
            spot.rect = r
        self.spots = spots
        self.sections = [(t, yy + dy) for t, yy in heads]
        # a small screen: what doesn't fit scrolls (mouse wheel, or the arrow keys follow the selection)
        self.view = QRectF(self.panel.left(), dy + content_top - 2, pw, ph - content_top + 2 - 10)
        self.max_scroll = max(0.0, (y + PAD - 12) - ph)
        self.scroll = min(self.scroll, self.max_scroll)
        self.selected = min(self.selected, len(self.spots) - 1)

    def shown(self, rect: QRectF) -> QRectF:
        """Where a spot is on the screen right now (after scrolling)."""
        return rect.translated(0, -self.scroll)

    def _keep_in_view(self, i: int):
        if not 0 <= i < len(self.spots) or not self.max_scroll:
            return
        r = self.shown(self.spots[i].rect)
        if r.top() < self.view.top() + 4:
            self.scroll -= self.view.top() + 4 - r.top() + 24
        elif r.bottom() > self.view.bottom() - 4:
            self.scroll += r.bottom() - self.view.bottom() + 4
        self.scroll = min(max(0.0, self.scroll), self.max_scroll)

    def wheelEvent(self, e):
        if self.max_scroll:
            self.scroll = min(max(0.0, self.scroll - e.angleDelta().y() / 2), self.max_scroll)
            self.hovered = self._spot_at(e.position())
            self.update()

    # ---- keys and mouse

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape:
            if self.query:
                self.query = ""
                self._refilter()
            else:
                self.close_animated()
            return
        if k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and not (k == Qt.Key_Space and self.query):
            if 0 <= self.selected < len(self.spots):
                self.run(self.spots[self.selected])
            return
        if k in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            self._move(k)
            return
        if k == Qt.Key_Tab or k == Qt.Key_Backtab:
            if self.spots:
                step = -1 if k == Qt.Key_Backtab else 1
                self.selected = (self.selected + step) % len(self.spots)
                self._keep_in_view(self.selected)
                self.update()
            return
        if k == Qt.Key_Backspace:
            if self.query:
                self.query = self.query[:-1]
                self._refilter()
            return
        text = e.text()
        if text and text.isprintable() and not (e.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.query = (self.query + text)[:40]
            self._refilter()
            return
        super().keyPressEvent(e)

    def _refilter(self):
        self.scroll = 0.0
        self.layout_spots()
        self.selected = 0 if self.spots else -1
        self.hovered = -1
        self.update()

    def _move(self, key):
        """Arrow keys go to the nearest thing in that direction (they follow what you see, also in Arabic)."""
        if not self.spots:
            return
        if self.selected < 0:
            self.selected = 0
            self.update()
            return
        here = self.spots[self.selected].rect.center()
        dirs = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
        ux, uy = dirs[key]
        best, best_score = -1, float("inf")
        for i, sp in enumerate(self.spots):
            if i == self.selected:
                continue
            c = sp.rect.center()
            dx, dy = c.x() - here.x(), c.y() - here.y()
            along = dx * ux + dy * uy
            if along <= 1:
                continue
            across = abs(dx * uy) + abs(dy * ux)
            score = along + across * 2.5
            if score < best_score:
                best, best_score = i, score
        if best >= 0:
            self.selected = best
            self._keep_in_view(best)
            self.update()

    def _spot_at(self, pos: QPointF) -> int:
        if not self.view.contains(pos):
            return -1
        for i, sp in enumerate(self.spots):
            if self.shown(sp.rect).contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, e):
        i = self._spot_at(e.position())
        if i != self.hovered:
            self.hovered = i
            if i >= 0:
                self.selected = i
            self.setCursor(Qt.PointingHandCursor if i >= 0 else Qt.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        i = self._spot_at(e.position())
        if i >= 0:
            self.run(self.spots[i])
        elif not self.panel.contains(e.position()):
            self.close_animated()

    # ---- drawing

    def paintEvent(self, _):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        ease = 1 - (1 - self.t) ** 3
        p.fillRect(self.rect(), QColor(0, 0, 0, round((120 if t.dark else 90) * ease)))
        p.translate(0, (1 - ease) * 14)
        panel = self.panel
        # a soft shadow, then the panel
        for i, a in ((18, 10), (10, 18), (4, 30)):
            p.fillPath(_rounded(panel.adjusted(-i, -i + 6, i, i + 6), PANEL_RADIUS + i), QColor(0, 0, 0, a))
        p.fillPath(_rounded(panel, PANEL_RADIUS), _alpha(_colors(t)[0], 250))
        p.setPen(QPen(t.border, 1))
        p.drawPath(_rounded(panel.adjusted(0.5, 0.5, -0.5, -0.5), PANEL_RADIUS))
        self._paint_header(p, panel)
        base = QFont(self.font())
        p.save()
        p.setClipRect(self.view)
        p.translate(0, -self.scroll)
        for title, y in self.sections:
            f = QFont(base)
            f.setPointSizeF(base.pointSizeF() * 0.9)
            f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            p.setPen(t.muted)
            p.drawText(QRectF(panel.left() + PAD, y, panel.width() - 2 * PAD, 22),
                       Qt.AlignVCenter | Qt.AlignLeading, title)
        for i, sp in enumerate(self.spots):
            self._paint_spot(p, sp, i == self.selected, i == self.hovered)
        p.restore()
        if self.max_scroll:  # tiles fade out at the edges, and a thin bar says there's more
            from PySide6.QtGui import QLinearGradient
            for edge, shown in ((self.view.top(), self.scroll > 0), (self.view.bottom(), self.scroll < self.max_scroll)):
                if not shown:
                    continue
                down = edge == self.view.top()
                band = QRectF(self.view.left() + 1, edge if down else edge - 18, self.view.width() - 2, 18)
                g = QLinearGradient(0, band.top(), 0, band.bottom())
                g.setColorAt(0 if down else 1, _alpha(_colors(t)[0], 250))
                g.setColorAt(1 if down else 0, _alpha(_colors(t)[0], 0))
                p.fillRect(band, g)
            track = self.view.adjusted(0, 6, 0, -6)
            frac = track.height() / (track.height() + self.max_scroll)
            h = max(24.0, track.height() * frac)
            top = track.top() + (track.height() - h) * (self.scroll / self.max_scroll)
            x = panel.left() + 7 if is_rtl() else panel.right() - 10
            p.setPen(Qt.NoPen)
            p.setBrush(_alpha(t.muted, 110))
            p.drawRoundedRect(QRectF(x, top, 4, h), 2, 2)
        if not self.spots:
            p.setPen(t.muted)
            p.setFont(base)
            p.drawText(QRectF(panel.left(), panel.top() + 130, panel.width(), 40), Qt.AlignCenter,
                       tr("Nothing matches."))
        p.end()

    def _paint_header(self, p: QPainter, panel: QRectF):
        t = self.theme
        rtl = is_rtl()
        base = QFont(self.font())
        top = panel.top() + PAD
        inner = QRectF(panel.left() + PAD, top, panel.width() - 2 * PAD, 46)
        mark = QRectF(inner.right() - 40 if rtl else inner.left(), top + 3, 40, 40)
        p.fillPath(_rounded(mark, ITEM_RADIUS), t.accent)
        icons.paint(p, mark.adjusted(9, 9, -9, -9), "layout", t.on_accent)
        title_rect = QRectF(inner.left(), top, inner.width() - 52, 24)
        if not rtl:
            title_rect.moveLeft(inner.left() + 52)
        f = QFont(base)
        f.setPointSizeF(base.pointSizeF() * 1.25)
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(t.text)
        p.drawText(title_rect, Qt.AlignVCenter | Qt.AlignLeading, APP_NAME)
        from .hotkey import display
        key = display(self.qa.c.settings.quick_menu_key)
        hint = tr("{key} or Esc to close", key=key) if key else tr("Esc to close")
        p.setFont(base)
        p.setPen(t.muted)
        p.drawText(title_rect.translated(0, 22), Qt.AlignVCenter | Qt.AlignLeading, hint)
        # the time, at the other end
        now = QDateTime.currentDateTime()
        from .timefmt import locale
        loc = locale()
        f2 = QFont(base)
        f2.setPointSizeF(base.pointSizeF() * 1.25)
        f2.setWeight(QFont.Weight.DemiBold)
        p.setFont(f2)
        p.setPen(t.text)
        p.drawText(inner.adjusted(0, 0, 0, -22), Qt.AlignVCenter | Qt.AlignTrailing,
                   loc.toString(now.time(), QLocale.ShortFormat))
        p.setFont(base)
        p.setPen(t.muted)
        p.drawText(inner.adjusted(0, 22, 0, 0), Qt.AlignVCenter | Qt.AlignTrailing,
                   loc.toString(now.date(), "dddd d MMMM"))
        # the search line
        box = QRectF(inner.left(), top + 46 + 14, inner.width(), 36)
        p.fillPath(_rounded(box, ITEM_RADIUS), t.field)
        p.setPen(QPen(t.accent if self.query else t.border, 1))
        p.drawPath(_rounded(box.adjusted(0.5, 0.5, -0.5, -0.5), ITEM_RADIUS))
        glass = QRectF(box.right() - 30 if rtl else box.left() + 10, box.top() + 9, 18, 18)
        icons.paint(p, glass, "search", t.muted)
        text_rect = box.adjusted(12 if rtl else 38, 0, -38 if rtl else -12, 0)
        p.setFont(base)
        if self.query:
            p.setPen(t.text)
            shown = self.query + "▏"
            p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeading, shown)
        else:
            p.setPen(t.muted)
            p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeading, tr("Type to search actions, tools and shortcuts"))

    def _paint_spot(self, p: QPainter, sp: Spot, selected: bool, hovered: bool):
        t = self.theme
        r = sp.rect
        path = _rounded(r, ITEM_RADIUS)
        if sp.kind == "add":
            p.setPen(QPen(t.line_strong, 1.2, Qt.DashLine))
            p.setBrush(_colors(t)[1] if (selected or hovered) else Qt.NoBrush)
            p.drawPath(_rounded(r.adjusted(0.6, 0.6, -0.6, -0.6), ITEM_RADIUS))
            p.setBrush(Qt.NoBrush)
        else:
            p.fillPath(path, _colors(t)[1] if (selected or hovered) else t.field)
            p.setPen(QPen(t.accent if selected else t.border, 1.6 if selected else 1))
            p.drawPath(_rounded(r.adjusted(0.5, 0.5, -0.5, -0.5), ITEM_RADIUS))
        base = QFont(self.font())
        if sp.kind == "row":
            rtl = is_rtl()
            ic = QRectF(r.right() - 32 if rtl else r.left() + 12, r.center().y() - 10, 20, 20)
            icons.paint(p, ic, sp.entry.icon, t.accent if selected else t.muted)
            p.setFont(base)
            p.setPen(t.text)
            text = r.adjusted(12 if rtl else 42, 0, -42 if rtl else -12, 0)
            fm = QFontMetrics(base)
            p.drawText(text, Qt.AlignVCenter | Qt.AlignLeading, fm.elidedText(sp.entry.label, Qt.ElideMiddle,
                                                                              int(text.width())))
            return
        ic = QRectF(r.center().x() - 13, r.top() + 16, 26, 26)
        icons.paint(p, ic, sp.entry.icon, t.accent if (selected or sp.kind == "add") else t.text)
        f = QFont(base)
        f.setPointSizeF(base.pointSizeF() * 0.92)
        f.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
        p.setFont(f)
        p.setPen(t.text if sp.kind != "add" else t.muted)
        fm = QFontMetrics(f)
        lines = _two_lines(sp.entry.label, fm, r.width() - 14)
        y = r.top() + 50
        for line in lines:
            p.drawText(QRectF(r.left() + 4, y, r.width() - 8, fm.height()), Qt.AlignCenter, line)
            y += fm.height() - 1


def _motion(name: str, fallback: int) -> int:
    try:
        from .atomic.tokens import MOTION
        return MOTION.get(name, fallback)
    except ImportError:
        return fallback


def _two_lines(text: str, fm: QFontMetrics, width: float) -> list[str]:
    if fm.horizontalAdvance(text) <= width:
        return [text]
    words = text.split()
    first = ""
    for i, w in enumerate(words):
        trial = f"{first} {w}".strip()
        if fm.horizontalAdvance(trial) > width and first:
            rest = " ".join(words[i:])
            return [first, fm.elidedText(rest, Qt.ElideRight, int(width))]
        first = trial
    return [fm.elidedText(text, Qt.ElideRight, int(width))]


def _bring_to_front(hwnd: int):
    """Takes the keyboard from the app in front (Windows only lets an app do that in some cases: if
    it doesn't the first time, it borrows the front app's keyboard for a moment)."""
    import os
    if os.name != "nt":
        return
    try:
        import ctypes
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        u.GetForegroundWindow.restype = ctypes.c_void_p
        if u.SetForegroundWindow(hwnd) and (u.GetForegroundWindow() or 0) == hwnd:
            return
        front = u.GetWindowThreadProcessId(u.GetForegroundWindow(), None)
        mine = k.GetCurrentThreadId()
        if front and front != mine and u.AttachThreadInput(front, mine, True):
            try:
                u.BringWindowToTop.argtypes = [ctypes.c_void_p]
                u.BringWindowToTop(hwnd)
                u.SetForegroundWindow(hwnd)
            finally:
                u.AttachThreadInput(front, mine, False)
    except Exception:
        pass
