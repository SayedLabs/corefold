"""The design system's own widgets (corefold/atomic) clicked the way a person would, with pytest-qt."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QVBoxLayout, QWidget

from corefold import theme
from corefold.atomic.widgets import ChoiceCard, Dropdown, Switch


def test_switch_flips_when_clicked(qtbot):
    sw = Switch("Start with Windows")
    qtbot.addWidget(sw)
    sw.show()
    with qtbot.waitSignal(sw.toggled, timeout=1000) as on:
        qtbot.mouseClick(sw, Qt.LeftButton)
    assert on.args == [True] and sw.isChecked()
    with qtbot.waitSignal(sw.toggled, timeout=1000) as off:
        qtbot.mouseClick(sw, Qt.LeftButton)
    assert off.args == [False] and not sw.isChecked()


def test_only_one_choice_card_is_picked(qtbot):
    box = QWidget()
    qtbot.addWidget(box)
    lay = QVBoxLayout(box)
    group = QButtonGroup(box)
    group.setExclusive(True)
    cards = [ChoiceCard("Light"), ChoiceCard("Dark", note="Easier on the eyes at night")]
    for card in cards:
        lay.addWidget(card)
        group.addButton(card)
    box.show()
    qtbot.mouseClick(cards[0], Qt.LeftButton)
    assert cards[0].isChecked()
    qtbot.mouseClick(cards[1], Qt.LeftButton)
    assert cards[1].isChecked() and not cards[0].isChecked()


def test_dropdown_tells_when_the_pick_changes(qtbot):
    dd = Dropdown()
    qtbot.addWidget(dd)
    dd.addItems(["English", "العربية", "Français"])
    with qtbot.waitSignal(dd.currentTextChanged, timeout=1000) as picked:
        dd.setCurrentIndex(1)
    assert picked.args == ["العربية"]


@pytest.mark.parametrize("appearance", ["light", "dark"])
def test_theme_builds_and_applies(qtbot, qapp, appearance):
    t = theme.build_theme(appearance, "core")
    assert t.dark == (appearance == "dark")
    qss = theme.stylesheet(t)
    assert "QPushButton" in qss
    w = QWidget()
    qtbot.addWidget(w)
    qapp.setStyleSheet(qss)
    w.show()
    qapp.setStyleSheet("")
