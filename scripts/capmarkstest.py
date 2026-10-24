"""Capture's editor, marks used like a person would: drawing an arrow, a box and a text, then clicking one to pick
it, dragging it somewhere else, pulling its ends or corners, changing its colour and size, moving it with the arrow
keys, deleting it, Undo and Redo of all of that, double-clicking a text to change its words, and the picture saved
with the marks where they ended up. Also: the tool bar has every tool, colour and size.
Run: python scripts/capmarkstest.py <folder> [en|ar|fr|es|tr] [dark|light]
Prints one line per check and "capmarkstest: N problem(s)"; pictures of the editor go in the folder."""
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "capmarkstest").resolve()
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
LOOK = sys.argv[3] if len(sys.argv) > 3 else "dark"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-capmarkstest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen" if os.name != "nt" else "windows")
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
if os.name != "nt":  # (a quick try on Linux: Windows' parts are pretend ones)
    import ctypes
    from unittest import mock
    ctypes.windll = mock.MagicMock()
    for name in ("winreg", "winsound"):
        sys.modules[name] = mock.MagicMock()
import faulthandler  # noqa: E402

faulthandler.dump_traceback_later(600, exit=True)

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from corefold import i18n  # noqa: E402

i18n.set_language(LANG)
qapp = QApplication.instance() or QApplication(sys.argv)
if i18n.is_rtl():
    qapp.setLayoutDirection(Qt.RightToLeft)
problems = []
errors = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)


def step(name, fn):
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()


def hook(t, e, tb):
    errors.append("".join(traceback.format_exception(t, e, tb)))
    print("ERROR " + errors[-1], flush=True)


sys.excepthook = hook


def settle(seconds=0.2):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)


class FakeController:
    """Just enough of the app for the editor to open."""

    def __init__(self):
        from corefold.settings import Settings
        from corefold.theme import build_theme
        self.settings = Settings()
        self.theme = build_theme(LOOK, "teal")
        self.icon = QIcon()
        self.toasts = []
        self.toast = self

    def notify(self, title, detail, *a, **k):
        self.toasts.append((title, detail))
        return True

    def tell(self, title, detail, *a, **k):
        self.toasts.append((title, detail))


def editor():
    from unittest import mock

    from corefold import capview
    from corefold.capview import CaptureEditor
    from corefold.theme import stylesheet
    c = FakeController()
    qapp.setStyleSheet(stylesheet(c.theme))
    img = QImage(800, 450, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor("#f4f4f4"))
    ed = CaptureEditor(c, img)
    ed.resize(1100, 680)
    ed.show()
    settle(0.4)
    cv = ed.canvas
    check(all(b.isVisible() for b in ed.tool_buttons.values()) and len(ed.tool_buttons) == len(capview.TOOLS),
          f"every tool is in the bar ({len(ed.tool_buttons)})")
    check(all(not b.icon().isNull() for b in ed.tool_buttons.values()), "every tool has its picture")
    check(len(ed.swatches.buttons()) == len(capview.COLORS) and len(ed.size_buttons) == 3,
          "the colours and three sizes are there")
    bar = ed.tool_buttons["select"].parentWidget()
    check(bar.sizeHint().width() <= ed.width(), f"the bar fits the window ({bar.sizeHint().width()} px)")

    def at(x, y) -> QPoint:
        return cv.to_widget(QRectF(x, y, 1, 1)).center().toPoint()

    def drag(a, b, mods=Qt.NoModifier):
        QTest.mousePress(cv, Qt.LeftButton, mods, a)
        for k in range(1, 7):
            QTest.mouseMove(cv, a + (b - a) * (k / 6))
            qapp.processEvents()
        QTest.mouseRelease(cv, Qt.LeftButton, mods, b)
        settle(0.05)

    # an arrow, then a box
    ed.tool_buttons["arrow"].click()
    drag(at(100, 100), at(300, 200))
    check(len(cv.shapes) == 1 and cv.selected == 0, "an arrow is drawn, and stays picked")
    ed.tool_buttons["box"].click()
    drag(at(400, 100), at(600, 260))
    check(len(cv.shapes) == 2 and cv.selected == 1, "a box is drawn")
    ed.grab().save(str(OUT / f"{LANG}-{LOOK}-1-drawn.png"))
    # click the arrow's line: picks it (no new mark)
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(200, 150))
    settle(0.05)
    check(cv.selected == 0 and len(cv.shapes) == 2, f"clicking the arrow picks it (picked {cv.selected})")
    # drag it 50 px right and down
    drag(at(200, 150), at(250, 200))
    a = cv.shapes[0].points
    check(abs(a[0].x() - 150) < 3 and abs(a[0].y() - 150) < 3 and abs(a[-1].x() - 350) < 3,
          f"dragging moves it ({a[0].x():.0f},{a[0].y():.0f} → {a[-1].x():.0f},{a[-1].y():.0f})")
    # pull its tip
    drag(at(350, 250), at(380, 330))
    a = cv.shapes[0].points
    check(abs(a[-1].x() - 380) < 3 and abs(a[-1].y() - 330) < 3 and abs(a[0].x() - 150) < 3,
          "pulling its tip moves only the tip")
    # a click inside the box (not on its line) starts a new mark there instead of picking the box
    before = len(cv.shapes)
    drag(at(480, 160), at(520, 200))
    check(len(cv.shapes) == before + 1, "inside a box a new mark can still be drawn")
    cv.undo()
    settle(0.05)
    check(len(cv.shapes) == before, "Undo takes the new mark back")
    # pick the box by its line, pull its corner
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(400, 180))
    settle(0.05)
    check(cv.selected == 1, f"clicking the box's line picks it (picked {cv.selected})")
    drag(at(600, 260), at(700, 300))
    r = cv.shapes[1].rect()
    check(abs(r.right() - 700) < 3 and abs(r.bottom() - 300) < 3 and abs(r.left() - 400) < 3,
          f"pulling a corner makes it bigger ({r.width():.0f} × {r.height():.0f})")
    # colour and size change the picked mark
    green = capview.COLORS[2]
    ed.swatches.buttons()[2].click()
    ed.size_buttons["thick"].click()
    settle(0.05)
    check(cv.shapes[1].color == QColor(green) and cv.shapes[1].width == 7.0, "a colour and size change the picked mark")
    check(cv.shapes[0].color != QColor(green), "…and not the others")
    # arrow keys
    cv.setFocus()
    x0 = cv.shapes[1].rect().left()
    QTest.keyClick(cv, Qt.Key_Right)
    QTest.keyClick(cv, Qt.Key_Right, Qt.ShiftModifier)
    settle(0.05)
    check(abs(cv.shapes[1].rect().left() - x0 - 11) < 0.5, "the arrow keys nudge it (Shift: 10 at a time)")
    ed.grab().save(str(OUT / f"{LANG}-{LOOK}-2-picked.png"))
    # undo, redo
    for _ in range(2):
        cv.undo()
    settle(0.05)
    check(abs(cv.shapes[1].rect().left() - x0) < 0.5, "Undo takes the moves back one by one")
    cv.redo()
    settle(0.05)
    check(abs(cv.shapes[1].rect().left() - x0 - 1) < 0.5, "Redo puts one back")
    # delete
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(200 + 50, 150 + 50 + 20))
    settle(0.05)
    picked = cv.selected
    QTest.keyClick(ed, Qt.Key_Delete)
    settle(0.05)
    check(picked == 0 and len(cv.shapes) == 1 and cv.shapes[0].kind == "box", "Delete removes the picked mark")
    cv.undo()
    settle(0.05)
    check(len(cv.shapes) == 2 and cv.shapes[0].kind == "arrow", "…and Undo brings it back")
    # Esc lets go of the mark, a second Esc would close
    cv.selected = 1
    QTest.keyClick(ed, Qt.Key_Escape)
    settle(0.05)
    check(cv.selected is None and ed.isVisible(), "Esc lets go of the mark (the editor stays open)")
    # text: write, then double-click to change it
    ed.tool_buttons["text"].click()
    with mock.patch.object(capview.QInputDialog, "getText", return_value=("Hello", True)):
        QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(120, 330))
        settle(0.05)
    check(cv.shapes[-1].kind == "text" and cv.shapes[-1].text == "Hello", "a text is put on the picture")
    tb = cv.shapes[-1].bounds()
    with mock.patch.object(capview.QInputDialog, "getText", return_value=("Hello there", True)):
        QTest.mouseDClick(cv, Qt.LeftButton, Qt.NoModifier, cv.to_widget(tb).center().toPoint())
        settle(0.05)
    check(cv.shapes[-1].text == "Hello there" and sum(m.kind == "text" for m in cv.shapes) == 1,
          f"double-clicking a text changes its words ({cv.shapes[-1].text!r})")
    # the saved picture has the marks where they ended up
    out = cv.result()
    box = cv.shapes[1].rect()
    px = out.pixelColor(round(box.right()), round(box.center().y()))
    check(px.green() > px.red() and px.green() > 100, f"the picture has the green box where it was moved ({px.name()})")
    check(out.pixelColor(450, 50) == QColor("#f4f4f4"), "…and nothing where it was before")
    ed.grab().save(str(OUT / f"{LANG}-{LOOK}-3-text.png"))
    ed.close()


def copy_text():
    """The "copy the text on the screen" shortcut, without its screen part: a picked picture is read and its text
    goes on the clipboard, with a note; nothing found says so."""
    from unittest import mock

    from PySide6.QtGui import QFont, QGuiApplication, QPainter

    from corefold import capture, extras
    c = FakeController()
    c.settings.cap_text_shortcut = "off"
    c.settings.cap_shortcut = "off"
    tool = capture.CaptureTool(c)
    check(hasattr(tool, "text_key"), "Capture has the copy-text shortcut")
    w1, w2 = extras.OcrWord("Hello", 10, 10, 50, 20), extras.OcrWord("there", 70, 10, 50, 20)
    tool._text_read([extras.OcrLine([w1, w2])], "", False)
    check(QGuiApplication.clipboard().text() == "Hello there" and c.toasts and "Hello there" in c.toasts[-1][1],
          f"the read text is copied, with a note ({QGuiApplication.clipboard().text()!r})")
    c.toasts.clear()
    tool._text_read([], "", False)
    check(c.toasts and c.toasts[-1][0] == i18n.tr("No text found"), "no text: it says so")
    if os.name == "nt" and extras.ocr_available():
        img = QImage(700, 120, QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor("white"))
        p = QPainter(img)
        p.setFont(QFont("Segoe UI", 28))
        p.setPen(QColor("black"))
        p.drawText(20, 75, "Corefold copies this text")
        p.end()
        QGuiApplication.clipboard().clear()
        with mock.patch.object(extras, "ocr_available", return_value=True):
            tool._text_picked(img, None)
        end = time.time() + 20
        while tool._reader is not None and time.time() < end:
            settle(0.1)
        got = QGuiApplication.clipboard().text()
        check(got == "Corefold copies this text", f"a real picture is read and copied ({got!r})")
    tool.stop()


def main():
    step("the editor", editor)
    step("copy the text on the screen", copy_text)
    check(not errors, f"no errors ({len(errors)})")
    print(f"capmarkstest: {len(problems)} problem(s)", flush=True)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
