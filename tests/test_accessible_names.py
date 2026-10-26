"""Everything you can reach with the keyboard has words a screen reader can say: its own text, a name, or
a tooltip. A list or slider whose label only sits beside it takes that label's words."""
from PySide6.QtWidgets import (QAbstractButton, QAbstractSlider, QAbstractSpinBox, QComboBox, QLineEdit, QWidget)

from tests.test_one_window import window  # noqa: F401  (the fixture)


def _says_nothing(c: QWidget) -> bool:
    text = c.text() if isinstance(c, QAbstractButton) else c.placeholderText() if isinstance(c, QLineEdit) else ""
    return not (text.strip() or c.accessibleName().strip() or c.toolTip().strip())


def test_every_control_on_every_page_has_words(window, qtbot):  # noqa: F811
    w = window
    silent = []
    for name in list(w._builders):
        w.open_page(name)
        qtbot.wait(20)
        page = w.stack.currentWidget()
        for c in page.findChildren(QWidget):
            if not isinstance(c, (QAbstractButton, QLineEdit, QComboBox, QAbstractSpinBox, QAbstractSlider)):
                continue
            if not c.isVisibleTo(page) or c.focusPolicy() == 0 or c.objectName() == "qt_spinbox_lineedit":
                continue
            if _says_nothing(c):
                silent.append(f"{name}: {type(c).__name__} {c.objectName()!r}")
    assert not silent, silent


def test_a_settings_list_is_named_after_its_label(window):  # noqa: F811
    w = window
    w.open_page("General")
    assert w.language.accessibleName() == "Language" and w.volume.accessibleName() == "Volume"
    w.open_page("ArchiveSettings")
    assert w.extract_mode.accessibleName() == "Extract here"
