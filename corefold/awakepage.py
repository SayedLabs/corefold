"""Corefold Awake's page: keep the PC awake (until it's switched off, for a while, or until a time), by
itself while chosen programs are open, and turn the PC off later."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import QLocale, QTime, QTimer, Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from . import awake, icons
from .atomic.widgets import FlowLayout, IconButton, Switch, keep_wrapped_text
from .i18n import tr
from .theme import round_menu

if TYPE_CHECKING:
    from .app import Controller

# how long to stay awake (English, shown through tr())
MODES = [("on", "Until I turn it off"), ("for", "For a while"), ("until", "Until a time")]
SHUTDOWN_MODES = [("in", "After a while"), ("at", "At a time")]
SEP = "  ·  "


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def _pill(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("subtab")
    b.setCheckable(True)
    b.setCursor(Qt.PointingHandCursor)
    return b


class Pills(QWidget):
    """A row of pill buttons, one of them picked (wraps on a narrow window)."""

    picked = Signal(object)

    def __init__(self, choices: list[tuple[object, str]], current):
        super().__init__()
        flow = FlowLayout(self, spacing=6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict = {}
        for key, text in choices:
            b = _pill(tr(text))
            b.setChecked(key == current)
            b.clicked.connect(lambda _=False, k=key: self.picked.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            flow.addWidget(b)
        self.flow = flow

    def set_current(self, key):
        for k, b in self.buttons.items():
            b.setChecked(k == key)

    def pick(self, key):
        """As if that one was clicked."""
        self.set_current(key)
        self.picked.emit(key)


class DurationPicker(QWidget):
    """30 minutes, 1 hour, 2 hours, 4 hours, or a number of minutes of one's own."""

    changed = Signal(int)

    def __init__(self, minutes: int):
        super().__init__()
        choices = [(m, text) for m, text in awake.DURATIONS.items()] + [("custom", "Custom")]
        current = minutes if minutes in awake.DURATIONS else "custom"
        self.pills = Pills(choices, current)
        self.pills.picked.connect(self._picked)
        self.spin = QSpinBox()
        self.spin.setRange(1, awake.MAX_MINUTES)
        self.spin.setValue(max(1, min(awake.MAX_MINUTES, minutes)))
        self.spin.setAccessibleName(tr("minutes"))
        self.spin.setMinimumWidth(90)
        self.spin.valueChanged.connect(lambda v: self.changed.emit(v))
        self.unit = _label(tr("minutes"), "muted")
        for w in (self.spin, self.unit):
            self.pills.flow.addWidget(w)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.pills)
        self._show_custom(current == "custom")

    def _show_custom(self, on: bool):
        self.spin.setVisible(on)
        self.unit.setVisible(on)
        self.pills.flow.invalidate()

    def _picked(self, key):
        self._show_custom(key == "custom")
        self.changed.emit(self.minutes())

    def minutes(self) -> int:
        b = self.pills.group.checkedButton()
        key = next((k for k, v in self.pills.buttons.items() if v is b), "custom")
        return self.spin.value() if key == "custom" else int(key)


class TimeBox(QSpinBox):
    """A time of day as Windows writes it (like the taskbar's clock), in a box like every other number
    box: type it, or step it 5 minutes at a time with the arrows or the mouse wheel."""

    def __init__(self, t: QTime):
        super().__init__()
        self.loc = QLocale.system()
        fmt = self.loc.timeFormat(QLocale.FormatType.ShortFormat)
        self.fmt = fmt if "m" in fmt else "HH:mm"
        self.setRange(0, 24 * 60 - 1)
        self.setWrapping(True)
        self.setSingleStep(5)
        self.setValue(t.hour() * 60 + t.minute())
        self.setMinimumWidth(110)

    def time(self) -> QTime:
        return QTime(self.value() // 60, self.value() % 60)

    def textFromValue(self, value: int) -> str:
        return self.loc.toString(QTime(value // 60, value % 60), self.fmt)

    def _parse(self, text: str) -> QTime | None:
        text = text.strip()
        for loc, fmt in ((self.loc, self.fmt), (QLocale.c(), "H:mm"), (QLocale.c(), "H.mm"), (QLocale.c(), "Hmm"),
                         (QLocale.c(), "h:mm AP"), (QLocale.c(), "h:mmAP"), (QLocale.c(), "h AP"), (QLocale.c(), "H")):
            t = loc.toTime(text, fmt)
            if t.isValid():
                return t
        return None

    def valueFromText(self, text: str) -> int:
        t = self._parse(text)
        return self.value() if t is None else t.hour() * 60 + t.minute()

    def validate(self, text: str, pos: int):
        from PySide6.QtGui import QValidator
        if self._parse(text) is not None:
            return QValidator.State.Acceptable, text, pos
        return QValidator.State.Intermediate, text, pos  # (being typed; put back as it was if left unfinished)


class TimePicker(QWidget):
    """A time of day, and a word when that's tomorrow."""

    changed = Signal(QTime)

    def __init__(self, hhmm: str):
        super().__init__()
        t = QTime.fromString(hhmm, "HH:mm") if hhmm else QTime()
        if not t.isValid():  # (to begin with: two hours from now, on the hour)
            soon = datetime.now() + timedelta(hours=2)
            t = QTime(soon.hour, 0)
        self.edit = TimeBox(t)
        self.edit.setAccessibleName(tr("Time"))
        self.edit.valueChanged.connect(self._changed)
        self.hint = _label("", "muted", wrap=True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(self.edit)
        lay.addWidget(self.hint, 1)
        self.refresh()

    def _changed(self, _value: int):
        self.refresh()
        self.changed.emit(self.edit.time())

    def set_time(self, t: QTime):
        self.edit.setValue(t.hour() * 60 + t.minute())

    def when(self) -> datetime:
        return awake.next_time(self.edit.time())

    def hhmm(self) -> str:
        return self.edit.time().toString("HH:mm")

    def refresh(self):
        tomorrow = awake.is_tomorrow(self.when())
        self.hint.setText(tr("Tomorrow, as that time has already passed today") if tomorrow else "")


class AwakePage(QScrollArea):
    def __init__(self, controller: "Controller", header):
        super().__init__()
        self.c = controller
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setObjectName("scrollContent")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(14)
        lay.addLayout(header)
        lay.addWidget(self._awake_card())
        lay.addWidget(self._auto_card())
        lay.addWidget(self._shutdown_card())
        lay.addStretch()
        self.setWidget(inner)
        keep_wrapped_text(inner)
        self.ticker = QTimer(self)  # the time left, while the page is on screen
        self.ticker.setInterval(20_000)
        self.ticker.timeout.connect(self.refresh)
        # applying a new length a moment after the last change (not at every click of the arrows)
        self.reapply = QTimer(self, singleShot=True, interval=600)
        self.reapply.timeout.connect(self._reapply)
        tool = self.tool
        if tool is not None:
            tool.changed.connect(self.refresh)
        self.refresh()

    @property
    def tool(self):
        if self.c.awake is None:
            self.c._awake_tool()
        return self.c.awake

    # ---- staying awake

    def _awake_card(self) -> QFrame:
        s = self.c.settings
        box = QFrame()
        box.setObjectName("card")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(16)
        self.state_icon = QLabel()
        self.state_icon.setFixedSize(44, 44)
        self.state_icon.setAlignment(Qt.AlignCenter)
        top.addWidget(self.state_icon, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(3)
        self.state_title = _label("", "h2", wrap=True)
        self.state_detail = _label("", "muted", wrap=True)
        words.addWidget(self.state_title)
        words.addWidget(self.state_detail)
        top.addLayout(words, 1)
        self.power = QPushButton()
        self.power.setObjectName("primary")
        self.power.setCursor(Qt.PointingHandCursor)
        self.power.setMinimumWidth(140)
        self.power.clicked.connect(self._power_clicked)
        top.addWidget(self.power, 0, Qt.AlignVCenter)
        lay.addLayout(top)
        line = QFrame()
        line.setObjectName("line")
        lay.addWidget(line)
        lay.addWidget(_label(tr("How long"), "section"))
        mode = s.awake_mode if s.awake_mode in awake.MODES else "on"
        self.mode_pills = Pills(MODES, mode)
        self.mode_pills.picked.connect(self._mode_picked)
        lay.addWidget(self.mode_pills)
        self.duration = DurationPicker(s.awake_minutes)
        self.duration.changed.connect(self._minutes_changed)
        lay.addWidget(self.duration)
        self.until = TimePicker(s.awake_until)
        self.until.changed.connect(self._time_changed)
        lay.addWidget(self.until)
        self.screen = Switch(tr("Keep the screen on too"),
                             tr("Otherwise the screen turns off as usual, while the PC keeps running."))
        self.screen.setChecked(s.awake_screen)
        self.screen.toggled.connect(self._screen_toggled)
        lay.addWidget(self.screen)
        self.problem = _label(tr("Windows didn't let Corefold keep the PC awake."), "warning", wrap=True)
        self.problem.hide()
        lay.addWidget(self.problem)
        self._show_mode(mode)
        return box

    def _show_mode(self, mode: str):
        self.duration.setVisible(mode == "for")
        self.until.setVisible(mode == "until")

    def _power_clicked(self):
        tool = self.tool
        if tool is None:
            return
        if tool.on:
            tool.let_sleep()
        else:
            self._remember()
            tool.start()

    def _remember(self):
        s = self.c.settings
        s.awake_minutes = self.duration.minutes()
        s.awake_until = self.until.hhmm()
        self.c.settings_changed()

    def _mode_picked(self, mode: str):
        self.c.settings.awake_mode = mode
        self._show_mode(mode)
        self._remember()
        if self.tool is not None and self.tool.on:
            self.tool.start()

    def _minutes_changed(self, _minutes: int):
        self._remember()
        if self.tool is not None and self.tool.on and self.c.settings.awake_mode == "for":
            self.reapply.start()

    def _time_changed(self, _t):
        self._remember()
        if self.tool is not None and self.tool.on and self.c.settings.awake_mode == "until":
            self.reapply.start()

    def _reapply(self):
        if self.tool is not None and self.tool.on:
            self.tool.start()

    def _screen_toggled(self, on: bool):
        if self.tool is not None:
            self.tool.set_screen(on)

    # ---- by itself

    def _auto_card(self) -> QFrame:
        s = self.c.settings
        box = QFrame()
        box.setObjectName("card")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        lay.addWidget(_label(tr("By itself"), "section"))
        lay.addWidget(_label(tr("Stay awake while one of these programs is open"), wrap=True))
        lay.addWidget(_label(tr("Handy for a download, a game, a video call or a long export. It lets the PC sleep "
                                "again once they're all closed."), "muted", wrap=True))
        self.apps_box = QVBoxLayout()
        self.apps_box.setContentsMargins(0, 0, 0, 0)
        self.apps_box.setSpacing(4)
        lay.addLayout(self.apps_box)
        row = QHBoxLayout()
        self.add_app = QPushButton(tr("Add a program…"))
        self.add_app.setCursor(Qt.PointingHandCursor)
        self.add_app.clicked.connect(self._add_menu)
        row.addWidget(self.add_app)
        row.addStretch()
        lay.addLayout(row)
        line = QFrame()
        line.setObjectName("line")
        lay.addWidget(line)
        self.low_battery = Switch(tr("Let it sleep when the battery runs low"),
                                  tr("On battery, with less than {percent}% left.", percent=awake.LOW_BATTERY))
        self.low_battery.setChecked(s.awake_low_battery)
        self.low_battery.toggled.connect(self._low_battery_toggled)
        lay.addWidget(self.low_battery)
        self._show_apps()
        return box

    def _show_apps(self):
        while self.apps_box.count():
            item = self.apps_box.takeAt(0)
            old = item.widget()
            if old is not None:
                old.hide()
                old.deleteLater()
        t = self.c.theme
        for path in self.c.settings.awake_apps:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(10)
            icon = QLabel()
            icon.setFixedSize(24, 24)
            if os.path.isfile(path):
                from PySide6.QtCore import QFileInfo
                from PySide6.QtWidgets import QFileIconProvider
                icon.setPixmap(QFileIconProvider().icon(QFileInfo(path)).pixmap(20, 20))
            h.addWidget(icon)
            name = _label(awake.program_title(path))
            name.setToolTip(path)
            h.addWidget(name, 1)
            remove = IconButton(tr("Remove"), 28)
            remove.setIcon(icons.icon("dismiss", t.muted))
            remove.clicked.connect(lambda _=False, p=path: self._remove_app(p))
            h.addWidget(remove)
            row.path = path
            self.apps_box.addWidget(row)

    def _add_menu(self):
        m = QMenu(self)
        round_menu(m)
        have = {os.path.basename(p).lower() for p in self.c.settings.awake_apps}
        found = [p for p in awake.window_programs() if os.path.basename(p).lower() not in have]
        if found:
            m.addSection(tr("Open now"))
        else:
            m.addAction(tr("No other program has a window open")).setEnabled(False)
        for path in sorted(found, key=lambda p: awake.program_title(p).lower()):
            m.addAction(awake.program_title(path), lambda p=path: self.add_program(p))
        m.addSeparator()
        m.addAction(tr("Choose a program file…"), self._browse)
        m.exec(self.add_app.mapToGlobal(self.add_app.rect().bottomLeft()))
        m.deleteLater()

    def _browse(self):
        start = os.environ.get("ProgramFiles", "")
        path, _ = QFileDialog.getOpenFileName(self, tr("Choose a program file…"), start, tr("Programs") + " (*.exe)")
        if path:
            self.add_program(os.path.normpath(path))

    def add_program(self, path: str):
        if self.tool is not None:
            self.tool.set_programs(self.c.settings.awake_apps + [path])
        self._show_apps()
        self.refresh()

    def _remove_app(self, path: str):
        if self.tool is not None:
            self.tool.set_programs([p for p in self.c.settings.awake_apps if p != path])
        self._show_apps()
        self.refresh()

    def _low_battery_toggled(self, on: bool):
        self.c.settings.awake_low_battery = bool(on)
        self.c.settings_changed()
        if self.tool is not None:
            self.tool.watch()

    # ---- turning the PC off later

    def _shutdown_card(self) -> QFrame:
        box = QFrame()
        box.setObjectName("card")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(16)
        self.off_icon = QLabel()
        self.off_icon.setFixedSize(44, 44)
        self.off_icon.setAlignment(Qt.AlignCenter)
        top.addWidget(self.off_icon, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(3)
        self.off_title = _label("", "h2", wrap=True)
        self.off_detail = _label("", "muted", wrap=True)
        words.addWidget(self.off_title)
        words.addWidget(self.off_detail)
        top.addLayout(words, 1)
        self.cancel_btn = QPushButton(tr("Cancel shutdown"))
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._cancel_shutdown)
        top.addWidget(self.cancel_btn, 0, Qt.AlignVCenter)
        lay.addLayout(top)
        self.plan_box = QWidget()
        pl = QVBoxLayout(self.plan_box)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(12)
        line = QFrame()
        line.setObjectName("line")
        pl.addWidget(line)
        self.off_mode = "in"
        self.off_pills = Pills(SHUTDOWN_MODES, self.off_mode)
        self.off_pills.picked.connect(self._off_mode_picked)
        pl.addWidget(self.off_pills)
        self.off_after = DurationPicker(60)
        pl.addWidget(self.off_after)
        self.off_at = TimePicker("")
        self.off_at.hide()
        pl.addWidget(self.off_at)
        row = QHBoxLayout()
        self.plan_btn = QPushButton(tr("Turn off later…"))
        self.plan_btn.setCursor(Qt.PointingHandCursor)
        self.plan_btn.clicked.connect(self._plan_clicked)
        row.addWidget(self.plan_btn)
        row.addStretch()
        pl.addLayout(row)
        lay.addWidget(self.plan_box)
        self.off_problem = _label("", "warning", wrap=True)
        self.off_problem.hide()
        lay.addWidget(self.off_problem)
        return box

    def _off_mode_picked(self, mode: str):
        self.off_mode = mode
        self.off_after.setVisible(mode == "in")
        self.off_at.setVisible(mode == "at")
        self.off_problem.hide()

    def shutdown_time(self) -> datetime:
        if self.off_mode == "at":
            return self.off_at.when()
        return datetime.now() + timedelta(minutes=self.off_after.minutes())

    def _plan_clicked(self):
        tool = self.tool
        if tool is None:
            return
        at = self.shutdown_time()
        when = awake.time_text(at)
        title = tr("Turn off the PC at {time} tomorrow?", time=when) if awake.is_tomorrow(at) else \
            tr("Turn off the PC at {time}?", time=when)
        box = QMessageBox(QMessageBox.Question, title,
                          tr("Windows turns it off then, even if Corefold is closed. Programs still open are closed "
                             "without saving, so save your work first. You can cancel it here until then."),
                          parent=self.window())
        yes = box.addButton(tr("Plan it"), QMessageBox.AcceptRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        chosen = box.clickedButton() is yes
        box.deleteLater()
        if chosen:
            self.plan(at)

    def plan(self, at: datetime, ask_to_replace: bool = True) -> int:
        tool = self.tool
        code = tool.plan_shutdown(at)
        if code == awake.ERROR_SHUTDOWN_IS_SCHEDULED and ask_to_replace:
            box = QMessageBox(QMessageBox.Question, tr("Windows already has a shutdown or restart planned"),
                              tr("Cancel that one, and turn the PC off at the time you picked instead?"),
                              parent=self.window())
            yes = box.addButton(tr("Replace it"), QMessageBox.AcceptRole)
            box.addButton(tr("Keep that one"), QMessageBox.RejectRole)
            box.exec()
            chosen = box.clickedButton() is yes
            box.deleteLater()
            if not chosen:
                return code
            code = tool.plan_shutdown(at, replace=True)
        self.off_problem.setVisible(code != 0)
        if code:
            self.off_problem.setText(tr("Windows couldn't plan it (error {code}).", code=code))
        self.refresh()
        return code

    def _cancel_shutdown(self):
        tool = self.tool
        if tool is None:
            return
        code = tool.cancel_shutdown()
        ok = code in (0, awake.ERROR_NO_SHUTDOWN_IN_PROGRESS)
        self.off_problem.setVisible(not ok)
        if not ok:
            self.off_problem.setText(tr("Windows couldn't cancel it (error {code}).", code=code))
        self.refresh()

    # ---- keeping it up to date

    def refresh(self):
        tool = self.tool
        t = self.c.theme
        dpr = self.devicePixelRatioF()
        on = tool is not None and tool.on
        self.state_icon.setPixmap(icons.pixmap("coffee" if on else "weather_moon", 34, t.accent if on else t.muted,
                                               dpr))
        self.state_title.setText(tool.status() if tool is not None else tr("Your PC can sleep as usual"))
        if on:
            bits = []
            left = tool.seconds_left()
            if left is not None:
                bits.append(tr("{left} left", left=awake.left_text(left)))
            bits.append(tr("The screen stays on too.") if self.c.settings.awake_screen else
                        tr("The screen can still turn off."))
            self.state_detail.setText(SEP.join(bits))
        else:
            self.state_detail.setText(tr("Windows puts it to sleep when you leave it, as set in its power settings."))
        self.power.setText(tr("Let it sleep") if on else tr("Keep awake"))
        self.power.setEnabled(tool is not None)
        if self.screen.isChecked() != self.c.settings.awake_screen:  # (changed from somewhere else)
            self.screen.blockSignals(True)
            self.screen.setChecked(self.c.settings.awake_screen)
            self.screen.blockSignals(False)
        self.problem.setVisible(bool(on and tool.failed))
        # the shutdown timer
        at = tool.planned_shutdown() if tool is not None else None
        self.off_icon.setPixmap(icons.pixmap("timer", 34, t.accent if at else t.muted, dpr))
        if at is None:
            self.off_title.setText(tr("Turn off the PC later"))
            self.off_detail.setText(tr("Windows turns it off at the time you pick, even if Corefold is closed by "
                                       "then."))
        else:
            when = awake.time_text(at)
            self.off_title.setText(tr("Your PC turns off at {time} tomorrow", time=when) if awake.is_tomorrow(at)
                                   else tr("Your PC turns off at {time}", time=when))
            self.off_detail.setText(tr("That's in {left}. Programs still open then are closed without saving.",
                                       left=awake.left_text((at - datetime.now()).total_seconds())))
        self.cancel_btn.setVisible(at is not None)
        self.plan_box.setVisible(at is None)
        self.plan_btn.setEnabled(tool is not None)
        self.until.refresh()
        self.off_at.refresh()

    def apply_theme(self, _t):
        self.refresh()

    def showEvent(self, e):
        super().showEvent(e)
        self.ticker.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.ticker.stop()
