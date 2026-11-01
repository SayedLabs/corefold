"""A button whose words are shortened to fit must settle: its shortened words mustn't ask for less room
(it then got less room, shortened its words more, asked for more room again, and so on for ever)."""
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from corefold.homewidgets import FitButton


def test_a_shortened_button_settles(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    lay = QHBoxLayout(host)
    lay.addWidget(QLabel("A long heading that takes most of the row"), 1)
    b = FitButton("See all")
    pm = QPixmap(16, 16)
    pm.fill()
    b.setIcon(QIcon(pm))
    lay.addWidget(b)
    host.show()
    for width in range(220, 360, 3):
        host.resize(width, 40)
        qtbot.wait(5)
        texts = []
        for _ in range(6):  # (each round the layout runs again; nothing may change any more)
            qtbot.wait(5)
            texts.append((b.text(), b.width()))
        assert len(set(texts)) == 1, (width, texts)


def test_the_size_asked_for_is_the_whole_words(qtbot):
    b = FitButton("See all")
    qtbot.addWidget(b)
    whole = b.sizeHint()
    for shown in ("S…", ""):
        b.setText(shown)
        assert b.sizeHint() == whole, shown
