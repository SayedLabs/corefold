"""The quick menu: a panel over a dimmed screen that opens from any app with a shortcut (like a game
overlay). It has the quick actions a person picked, their tools, their own shortcuts and the files
Corefold made lately. Start typing to search it; arrows and Enter work too; Esc or the shortcut
again closes it. Typing also does sums, converts units, finds pages of Windows Settings and offers a
web search (quickfind.py), and finds apps, files and the windows already open (quicksearch.py). A leading
"?" only searches the web.

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
ANSWER_H = 58
CONFIRM_AFTER_S = 0.6  # "Restart now" only counts this long after it was asked (a double press never restarts)
MAX_RECENT = 6


@dataclass
class Spot:
    """Something on the panel you can point at, and where it is."""
    rect: QRectF
    entry: quick.Entry
    kind: str = "tile"   # tile, row (a recent file), add (the "Add a shortcut" tile), answer (a sum or a
    #                      unit), wide (a Settings page, the web search, the "are you sure?" choices),
    #                      item (an app or a file found as you type, with its own icon)


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
        self.confirm = ""      # the Windows command being asked about ("restart"…), see ask()
        self.asked_at = 0.0
        self.pressed_at = 0.0  # when the mouse button last went down (a click that began before the question
        #                        doesn't answer it)
        self.copied = False
        self.copy_timer = QTimer(self)
        self.copy_timer.setSingleShot(True)
        self.copy_timer.timeout.connect(self.close_animated)
        self.finder = None     # quicksearch.Finder, once apps and files are looked for
        self.found_items: dict = {}   # an item's entry id -> its quicksearch.App, Found or Window
        self.windows: list = []       # the windows open when it opened (quicksearch.Window, front to back)
        self.item_icons: dict = {}    # an item's entry id -> its icon (QPixmap or None)

    # ---- opening and closing

    def open(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.setGeometry(screen.geometry())
        self.query = ""
        self.selected = -1
        self.hovered = -1
        self.scroll = 0.0
        self._closing = False
        self.confirm = ""
        self.copied = False
        self.copy_timer.stop()
        if self.finder is not None:
            self.finder.cancel()
        if self.qa.c.settings.quick_find:
            self._finder().warm()
            from .quicksearch import open_windows
            self.windows = open_windows()  # (before this one is in front)
        else:
            self.windows = []
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
        if self.finder is not None:
            self.finder.cancel()  # (what comes back now is dropped)
        self.qa.c.popup_closed()
        super().hideEvent(e)

    def changeEvent(self, e):
        # clicking another app (or Alt+Tab) puts the menu away, like any popup
        if e.type() == QEvent.ActivationChange and self.isVisible() and not self.isActiveWindow():
            if time.monotonic() - self._opened_at > 0.3:  # (not while Windows is still handing it the focus)
                self.hide()
        super().changeEvent(e)

    def ask(self, action_id: str):
        """Shows "Restart the PC?" (and the like) with a button that does it: a second, deliberate
        Enter or click. Esc goes back."""
        self.confirm = action_id
        self.asked_at = time.monotonic()
        self._refilter()

    def run(self, spot: Spot, mods=Qt.NoModifier):
        sid = spot.entry.id
        if spot.kind == "answer":  # copied, said so on the row, then put away
            QGuiApplication.clipboard().setText(spot.entry.en)
            self.copied = True
            self.update()
            self.copy_timer.start(650)
            return
        if sid == "cancel":
            self.confirm = ""
            self._refilter()
            return
        if sid.startswith("confirm:"):
            if time.monotonic() - self.asked_at < CONFIRM_AFTER_S:  # (not the press that asked, nor a double one)
                return
            self.hide()
            self.qa.run(sid[8:], confirmed=True)
            return
        from .quicksys import CONFIRM
        if spot.kind == "tile" and sid in CONFIRM:
            self.qa.ask(sid)
            return
        self.hide()
        if spot.kind == "item":
            self._open_item(spot.entry, mods)
        elif sid == "web":
            from .quickfind import search_url
            quick.open_target(search_url(self.qa.c.settings.quick_search_engine, self.query.removeprefix("?")))
        elif sid.startswith("ms-settings:"):
            _open_settings(sid)
        elif spot.kind == "add":
            self.qa.c.show_window("Quick")
        elif spot.kind == "row":
            quick.open_target(spot.entry.hint)
        else:
            self.qa.run(spot.entry.id)

    def _open_item(self, e: quick.Entry, mods):
        """An app (Ctrl + Enter: as administrator) or a found file or folder (Shift or Ctrl + Enter: shown
        in File Explorer)."""
        from . import quicksearch as qs
        item = self.found_items.get(e.id)
        ok = True
        if isinstance(item, qs.App):
            ok = qs.launch(item, admin=bool(mods & Qt.ControlModifier))
        elif isinstance(item, qs.Window):
            if not qs.switch_to(item):
                self.qa.c.tell(tr("Couldn't switch to it"), tr("That window has been closed."))
            return
        elif isinstance(item, qs.Found):
            ok = qs.open_found(item, reveal=bool(mods & (Qt.ShiftModifier | Qt.ControlModifier)))
        if not ok:
            self.qa.c.tell(tr("Couldn't open it"), tr("{name} isn't there any more, or can't be opened.",
                                                       name=e.label))

    # ---- apps and files found as you type (quicksearch.py)

    def _finder(self):
        if self.finder is None:
            from .quicksearch import Finder
            self.finder = Finder(self)
            self.finder.updated.connect(self._found_more)
            self.finder.apps_ready.connect(self._found_more)
        return self.finder

    def _found_more(self):
        """New apps or files came in while the menu is open: shown, keeping what was picked."""
        if not self.isVisible() or not self.query or self.confirm:
            return
        picked = self.spots[self.selected].entry.id if 0 <= self.selected < len(self.spots) else ""
        self.layout_spots()
        ids = [sp.entry.id for sp in self.spots]
        self.selected = ids.index(picked) if picked in ids and self.selected > 0 else (0 if self.spots else -1)
        self.hovered = -1
        self.update()

    def _open_windows(self, query: str) -> tuple[int, list[quick.Entry]]:
        """(how well the best one fits, the windows already open that fit what's typed)."""
        from . import quicksearch as qs
        if len(query.strip()) < qs.MIN_CHARS or not self.windows:
            return 0, []
        out, best = [], 0
        for score, w in qs.match_windows(self.windows, query):
            eid = f"win:{w.hwnd}"
            self.found_items[eid] = w
            from .clipboard import app_name
            out.append(quick.Entry(eid, w.title, "layout", app_name(w.exe) if w.exe else ""))
            best = max(best, qs.level(score))
        return best, out

    def _apps_and_files(self, s, query: str, skip: set[str]):
        """(how well the best app fits, the apps) and the files for what's typed."""
        from . import quicksearch as qs
        if not s.quick_find or len(query.strip()) < qs.MIN_CHARS:
            return 0, [], []
        f = self._finder()
        app_entries, best = [], 0
        for score, app in f.apps(query):
            if app.name.lower() in skip:
                continue  # (Corefold's own "Task Manager" is there already)
            eid = "app:" + app.target
            self.found_items[eid] = app
            app_entries.append(quick.Entry(eid, app.name, "rocket"))
            best = max(best, qs.level(score))
        files = f.files if f.files_for == query.strip() else [
            x for x in f.files if qs.file_score(query, x.name) > 10]  # (the last ones, while it looks again)
        file_entries = []
        for x in files:
            eid = "find:" + x.path
            self.found_items[eid] = x
            file_entries.append(quick.Entry(eid, x.name, "folder" if x.folder else "document",
                                            str(Path(x.path).parent)))
        return best, app_entries, file_entries

    def item_icon(self, e: quick.Entry, size: int):
        """An app's or a file's own icon (looked up once)."""
        if e.id not in self.item_icons:
            from . import quicksearch as qs
            item = self.found_items.get(e.id)
            px = round(size * self.devicePixelRatioF())
            pm = None
            if isinstance(item, qs.App):
                pm = qs.app_icon(item, px)
            elif isinstance(item, qs.Window):
                pm = qs.file_icon(item.exe, px) if item.exe else None
            elif isinstance(item, qs.Found):
                pm = qs.file_icon(item.path, px)
            if pm is not None and pm.width():
                pm.setDevicePixelRatio(pm.width() / size)
            if len(self.item_icons) > 300:
                self.item_icons.clear()
            self.item_icons[e.id] = pm
        return self.item_icons[e.id]

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
                if path.startswith(("\\\\", "//")):  # (a network share isn't asked: offline, it holds the menu up)
                    out.append(quick.Entry(f"file:{path}", p.name, "document" if p.suffix else "folder", path))
                    continue
                try:
                    if not p.exists():
                        continue
                except OSError:
                    continue
                out.append(quick.Entry(f"file:{path}", p.name, "folder" if p.is_dir() else "document", path))
        return out

    def _sections(self) -> list[tuple[str, list[quick.Entry], str]]:
        s = self.qa.c.settings
        if self.confirm:
            from .quicksys import CONFIRM, recycle_bin
            question, yes, icon = CONFIRM[self.confirm]
            size = ""
            if self.confirm == "recycle":
                from .core import human_size
                size = human_size(recycle_bin()[1])
            return [(tr(question), [quick.Entry(f"confirm:{self.confirm}", tr(yes), icon, size),
                                    quick.Entry("cancel", tr("Cancel"), "dismiss")], "wide")]
        if self.query:
            return self._found(s, self.query)
        return [(tr("Quick actions"), quick.entries(s, s.quick_menu_items), "tile"),
                (tr("Tools"), quick.tool_entries(s), "tile"),
                (tr("Your shortcuts"), quick.link_entries(s), "links"),
                (tr("Recent files"), self._recent(), "row")]

    def _found(self, s, query: str) -> list[tuple[str, list[quick.Entry], str]]:
        """What typing finds: a sum or a unit first, then Corefold's own things and Windows' Settings
        pages (whichever fits better first), recent files, and a web search last."""
        from . import quickfind
        q = query.lower()
        out = []
        if query.startswith("?"):  # only the web search
            return [("", [self._web_row(s, query[1:])], "wide")] if query[1:].strip() else []
        ans = quickfind.answer(query)
        if ans is not None:
            out.append(("", [quick.Entry("answer", ans.shown, "calculator" if ans.kind == "calc" else "ruler",
                                         ans.source, en=ans.value)], "answer"))
        scored = [(quickfind.score(q, e.label, e.en, e.hint), i, e) for i, e in enumerate(quick.choosable(s))]
        scored = sorted((x for x in scored if x[0]), key=lambda x: (-x[0], x[1]))
        pages = quickfind.settings_pages(query)
        found = (tr("Results"), [e for _, _, e in scored], "tile")
        windows = (tr("Windows settings"), [quick.Entry(p.uri, tr(p.name), "settings", en=p.name)
                                            for _, p in pages], "wide")
        skip = {t.lower() for _, _, e in scored for t in (e.label, e.en) if t}
        self.found_items = {}
        best_app, apps, files = self._apps_and_files(s, query, skip)
        best_win, open_now = self._open_windows(query)
        # whichever fits best first (on a tie: Corefold's own, then the windows open, apps, Windows' settings)
        groups = [((scored[0][0] if scored else 0), 0, found), (best_win, 1, (tr("Open windows"), open_now, "item")),
                  (best_app, 2, (tr("Apps"), apps, "item")), ((pages[0][0] if pages else 0), 3, windows)]
        out += [g for _, _, g in sorted(groups, key=lambda x: (-x[0], x[1]))]
        recent = [e for e in self._recent() if quick.matches(e, q)]
        made = {e.hint for e in recent}
        out.append((tr("Files and folders"), [e for e in files if e.id[5:] not in made], "item"))
        out.append((tr("Recent files"), recent, "row"))
        if query.strip():
            out.append(("", [self._web_row(s, query)], "wide"))
        return out

    def _web_row(self, s, text: str) -> quick.Entry:
        from .settings import QUICK_SEARCH_ENGINES
        engine = QUICK_SEARCH_ENGINES.get(s.quick_search_engine, "")
        return quick.Entry("web", tr("Search the web for “{text}”", text=_isolate(text.strip())), "globe", engine)

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
            if title:
                heads.append((title, y))
                y += 28
            if kind in ("answer", "wide", "item"):  # the whole width, one under the other
                h = ANSWER_H if kind == "answer" else ROW_H
                for e in items:
                    spots.append(Spot(QRectF(PAD, y, inner_w, h), e, kind))
                    y += h + 6
                y += 8
            elif kind == "row":
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
        if k == Qt.Key_Escape and self.confirm:  # (back to what was there, not closed)
            self.confirm = ""
            self._refilter()
            return
        if self.confirm and (e.isAutoRepeat() or k == Qt.Key_Space):
            return  # (a held Enter never confirms, nor does Space)
        if k == Qt.Key_Escape:
            if self.query:
                self.query = ""
                self._refilter()
            else:
                self.close_animated()
            return
        if k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and not (k == Qt.Key_Space and self.query):
            if 0 <= self.selected < len(self.spots):
                self.run(self.spots[self.selected], e.modifiers())
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
        if self.confirm:
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
        self.copied = False
        self.copy_timer.stop()
        self.scroll = 0.0
        if self.qa.c.settings.quick_find and not self.confirm:
            q = self.query.strip()
            self._finder().search("" if q.startswith("?") else q)  # (after a short pause, on a thread)
        elif self.finder is not None:
            self.finder.cancel()
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

    def mousePressEvent(self, e):
        self.pressed_at = time.monotonic()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        i = self._spot_at(e.position())
        if i >= 0 and self.confirm and self.pressed_at < self.asked_at:
            return  # (the click that asked, still going)
        if i >= 0:
            self.run(self.spots[i], e.modifiers())
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
            shown = _isolate(self.query) + "|"  # ("10 km to mi" keeps its order in Arabic too)
            p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeading, shown)
        else:
            p.setPen(t.muted)
            hint = (tr("Type to search apps, files, tools and actions") if self.qa.c.settings.quick_find
                    else tr("Type to search actions, tools and shortcuts"))
            p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeading, hint)

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
        if sp.kind == "item":
            self._paint_item(p, sp, selected, base)
            return
        if sp.kind in ("answer", "wide"):
            self._paint_wide(p, sp, selected, base)
            return
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


    def _side_note(self, sp: Spot, selected: bool) -> str:
        """The small "what Enter does" on the right of a row."""
        sid = sp.entry.id
        if sp.kind == "answer":
            return tr("Copied") if self.copied else tr("Enter to copy")
        if not selected:
            return ""
        if sid.startswith("confirm:"):
            return tr("Enter to confirm")
        if sid == "cancel":
            return "Esc"
        if sp.kind == "item":
            from . import quicksearch as qs
            item = self.found_items.get(sid)
            if isinstance(item, qs.Found):
                return tr("Shift + Enter: show in folder")
            if isinstance(item, qs.Window):
                return tr("Enter to switch to it")
            if isinstance(item, qs.App) and item.admin:
                return tr("Ctrl + Enter: run as administrator")
        return tr("Enter to open")

    def _paint_item(self, p: QPainter, sp: Spot, selected: bool, base: QFont):
        """An app or a file found as you type: its own icon, its name and (for a file) its folder."""
        t = self.theme
        r = sp.rect
        rtl = is_rtl()
        size = 22
        ic = QRectF(r.right() - 14 - size if rtl else r.left() + 14, r.center().y() - size / 2, size, size)
        pm = self.item_icon(sp.entry, size)
        if pm is not None:
            p.drawPixmap(ic.topLeft(), pm)
        else:
            icons.paint(p, ic.adjusted(1, 1, -1, -1), sp.entry.icon, t.accent if selected else t.muted)
        inner = r.adjusted(14 if rtl else 14 + size + 12, 0, -(14 + size + 12) if rtl else -14, 0)
        small = QFont(base)
        small.setPointSizeF(base.pointSizeF() * 0.88)
        fm_s = QFontMetrics(small)
        side = self._side_note(sp, selected)
        side_w = 0
        if side:
            side_w = min(fm_s.horizontalAdvance(side) + 16, int(inner.width() * 0.45))
            p.setFont(small)
            p.setPen(t.muted)
            p.drawText(inner, Qt.AlignVCenter | Qt.AlignTrailing, fm_s.elidedText(side, Qt.ElideRight, side_w - 8))
        text = inner.adjusted(side_w if rtl else 0, 0, 0 if rtl else -side_w, 0)
        f = QFont(base)
        f.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
        fm = QFontMetrics(f)
        label = fm.elidedText(sp.entry.label, Qt.ElideMiddle, int(text.width() * (0.6 if sp.entry.hint else 1)))
        p.setFont(f)
        p.setPen(t.text)
        p.drawText(text, Qt.AlignVCenter | Qt.AlignLeading, label)
        if sp.entry.hint:  # the folder it's in
            used = fm.horizontalAdvance(label) + 12
            rest = text.adjusted(0 if rtl else used, 0, -used if rtl else 0, 0)
            if rest.width() > 30:
                p.setFont(small)
                p.setPen(t.muted)
                p.drawText(rest, Qt.AlignVCenter | Qt.AlignLeading,
                           fm_s.elidedText(_isolate(sp.entry.hint), Qt.ElideMiddle, int(rest.width())))

    def _paint_wide(self, p: QPainter, sp: Spot, selected: bool, base: QFont):
        """A row the width of the panel: a sum's or a unit's answer, a Settings page, the web search, or
        one of the "are you sure?" choices."""
        t = self.theme
        r = sp.rect
        rtl = is_rtl()
        answer = sp.kind == "answer"
        ic_size = 24 if answer else 20
        ic = QRectF(r.right() - 14 - ic_size if rtl else r.left() + 14, r.center().y() - ic_size / 2,
                    ic_size, ic_size)
        icons.paint(p, ic, sp.entry.icon, t.accent if (selected or answer) else t.muted)
        inner = r.adjusted(14 if rtl else 14 + ic_size + 12, 0, -(14 + ic_size + 12) if rtl else -14, 0)
        side = self._side_note(sp, selected)
        small = QFont(base)
        small.setPointSizeF(base.pointSizeF() * 0.88)
        side_w = 0
        if side:
            fm_s = QFontMetrics(small)
            side_w = fm_s.horizontalAdvance(side) + 16
            p.setFont(small)
            p.setPen(t.accent if (answer and self.copied) else t.muted)
            p.drawText(inner, Qt.AlignVCenter | Qt.AlignTrailing, side)
        text = inner.adjusted(side_w if rtl else 0, 0, 0 if rtl else -side_w, 0)
        if answer:  # what was understood, small, and the answer under it, big
            p.setFont(small)
            p.setPen(t.muted)
            top = QRectF(text.left(), r.top() + 7, text.width(), 18)
            p.drawText(top, Qt.AlignVCenter | Qt.AlignLeading,
                       QFontMetrics(small).elidedText(_ltr(sp.entry.hint), Qt.ElideRight, int(top.width())))
            big = QFont(base)
            big.setPointSizeF(base.pointSizeF() * 1.35)
            big.setWeight(QFont.Weight.Bold)
            p.setFont(big)
            p.setPen(t.text)
            bottom = QRectF(text.left(), r.top() + 24, text.width(), r.height() - 30)
            p.drawText(bottom, Qt.AlignVCenter | Qt.AlignLeading,
                       QFontMetrics(big).elidedText(_ltr("= " + sp.entry.label), Qt.ElideRight, int(bottom.width())))
            return
        f = QFont(base)
        f.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
        fm = QFontMetrics(f)
        label = fm.elidedText(sp.entry.label, Qt.ElideRight, int(text.width()))
        p.setFont(f)
        p.setPen(t.text)
        p.drawText(text, Qt.AlignVCenter | Qt.AlignLeading, label)
        if sp.entry.hint:  # (the search engine, the size of the Recycle Bin)
            used = fm.horizontalAdvance(label) + 10
            rest = text.adjusted(0 if rtl else used, 0, -used if rtl else 0, 0)
            if rest.width() > 30:
                p.setFont(small)
                p.setPen(t.muted)
                p.drawText(rest, Qt.AlignVCenter | Qt.AlignLeading,
                           QFontMetrics(small).elidedText(sp.entry.hint, Qt.ElideRight, int(rest.width())))


def _ltr(text: str) -> str:
    """A sum or a unit stays in its own order in Arabic ("= 84", not "84 =")."""
    return f"\u202a{text}\u202c" if is_rtl() else text


def _isolate(text: str) -> str:
    """What the person typed, kept in its own direction inside a sentence of the other one."""
    return f"\u2068{text}\u2069"


def _open_settings(uri: str):
    """A page of Windows Settings (ms-settings:display…)."""
    import os
    try:
        if os.name == "nt":
            os.startfile(uri)  # type: ignore[attr-defined]
            return
    except OSError:
        pass
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl(uri))


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
