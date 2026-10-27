"""A new look sends every widget a dozen events; the window's guard steps aside for them and is back after."""
from PySide6.QtWidgets import QApplication, QLabel

from corefold import sheet


def test_restyling_skips_the_guard_and_it_comes_back(qtbot, monkeypatch):
    seen = []
    real = sheet._Guard.eventFilter

    def counting(self, obj, e):
        seen.append(e.type())
        return real(self, obj, e)
    monkeypatch.setattr(sheet._Guard, "eventFilter", counting)
    monkeypatch.setattr(sheet, "_guard", None)
    app = QApplication.instance()
    sheet.install(app)
    try:
        labels = [QLabel(f"row {i}") for i in range(50)]
        for lab in labels:
            qtbot.addWidget(lab)
            lab.show()
        css = app.styleSheet()
        seen.clear()
        with sheet.quiet():
            app.setStyleSheet("QLabel { color: red; }")
        assert seen == []
        app.setStyleSheet(css)  # (the guard sees events again)
        assert seen
    finally:
        app.removeEventFilter(sheet._guard)
