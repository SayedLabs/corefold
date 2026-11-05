"""Corefold Cloud's in-window forms: adding a server of your own (a NAS, WebDAV, FTP, SFTP), and a folder backup."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QLineEdit, QSpinBox

from .atomic.widgets import Dropdown
from .dialogs import Base
from .i18n import tr

def kind_names() -> tuple:
    return (("webdav", tr("WebDAV (Nextcloud, ownCloud, Synology, QNAP…)")), ("sftp", "SFTP (SSH)"), ("ftp", "FTP"),
            ("share", tr("A folder shared on the network (\\\\NAS\\folder)")))


def every_names() -> tuple:
    return (("day", tr("Every day")), ("week", tr("Every week")), ("never", tr("Only when I ask")))


def _note(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    return lab


class ServerDialog(Base):
    """A server's address, user name and password. Nothing is sent anywhere but to that server."""

    def __init__(self, icon, dark, account=None):
        super().__init__(tr("Add a server"), tr("Your own NAS or server: no account anywhere else is needed."), icon,
                         dark)
        cfg = (account.extra or {}).get("server", {}) if account is not None else {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.kind = Dropdown()
        self.kind.setAccessibleName(tr("Kind"))
        for key, text in kind_names():
            self.kind.addItem(text, key)
        self.address = QLineEdit(cfg.get("host", ""))
        self.address.setAccessibleName(tr("Address"))
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setSpecialValueText(tr("Usual"))
        self.port.setAccessibleName(tr("Port"))
        self.port.setValue(int(cfg.get("port", 0) or 0))
        self.user = QLineEdit(cfg.get("user", ""))
        self.user.setAccessibleName(tr("User name"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setAccessibleName(tr("Password"))
        self.folder = QLineEdit(cfg.get("folder", "") if cfg.get("folder", "/") != "/" else "")
        self.folder.setPlaceholderText(tr("The top folder (optional)"))
        self.folder.setAccessibleName(tr("Folder"))
        self.rows = []
        for r, (text, w) in enumerate(((tr("Kind"), self.kind), (tr("Address"), self.address), (tr("Port"), self.port),
                                       (tr("User name"), self.user), (tr("Password"), self.password),
                                       (tr("Folder"), self.folder))):
            label = QLabel(text)
            grid.addWidget(label, r, 0)
            grid.addWidget(w, r, 1)
            self.rows.append((label, w))
        grid.setColumnStretch(1, 1)
        self.lay.addLayout(grid)
        self.secure = QCheckBox(tr("Encrypted (keep this on unless the server is on your own network)"))
        self.secure.setChecked(bool(cfg.get("secure", True)))
        self.lay.addWidget(self.secure)
        self.note = _note("")
        self.lay.addWidget(self.note)
        if account is not None:
            self.kind.setCurrentIndex(max(0, self.kind.findData(account.service)))
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.address.textChanged.connect(self._check)
        self.buttons(tr("Add"))
        self._kind_changed()

    def _kind_changed(self, *_):
        kind = self.kind.currentData()
        share = kind == "share"
        for label, w in self.rows[2:]:
            label.setVisible(not share)
            w.setVisible(not share)
        self.secure.setVisible(kind in ("webdav", "ftp"))
        self.address.setPlaceholderText({
            "webdav": "https://cloud.example.com/remote.php/dav/files/me",
            "sftp": "nas.local", "ftp": "ftp.example.com", "share": "\\\\NAS\\Public"}[kind])
        self.note.setText({
            "webdav": tr("Nextcloud, ownCloud and most NAS boxes offer WebDAV. A whole address works too."),
            "sftp": tr("The first time, Corefold keeps the server's key. If it ever changes, Corefold won't connect, "
                       "so no one can pretend to be your server."),
            "ftp": tr("Plain FTP isn't encrypted: Corefold only allows it on your own network."),
            "share": tr("A folder shared by a NAS or another PC. If Windows asks for a password for it, open it once "
                        "in File Explorer first."),
        }[kind])
        self._check()

    def _check(self, *_):
        self.ok.setEnabled(bool(self.address.text().strip()))

    def values(self) -> dict:
        return {"kind": self.kind.currentData(), "host": self.address.text().strip(), "port": self.port.value(),
                "user": self.user.text().strip(), "password": self.password.text(),
                "folder": self.folder.text().strip() or "/", "secure": self.secure.isChecked()}


class BackupDialog(Base):
    """A folder of this PC backed up into the account's folder that's open."""

    def __init__(self, icon, dark, where: str):
        super().__init__(tr("Back up a folder here"), tr("Into {place}", place=where), icon, dark)
        from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QPushButton
        row = QHBoxLayout()
        self.source = QLineEdit()
        self.source.setPlaceholderText(tr("The folder on this PC"))
        self.source.setAccessibleName(tr("Folder"))
        browse = QPushButton(tr("Choose…"))

        def pick():
            folder = QFileDialog.getExistingDirectory(self, tr("Back up a folder"), self.source.text())
            if folder:
                self.source.setText(folder)
        browse.clicked.connect(pick)
        row.addWidget(self.source, 1)
        row.addWidget(browse)
        self.lay.addLayout(row)
        self.every = Dropdown()
        self.every.setAccessibleName(tr("How often"))
        for key, text in every_names():
            self.every.addItem(text, key)
        line = QGridLayout()
        line.addWidget(QLabel(tr("How often")), 0, 0)
        line.addWidget(self.every, 0, 1)
        line.setColumnStretch(1, 1)
        self.lay.addLayout(line)
        self.lay.addWidget(_note(tr("Only one way: what's new or changed on this PC goes up. Nothing is ever deleted "
                                    "in the account, and nothing on this PC is touched. It runs while Corefold is "
                                    "on.")))
        self.buttons(tr("Back up"))
        self.source.textChanged.connect(lambda t: self.ok.setEnabled(bool(t.strip())))
        self.ok.setEnabled(False)

    def values(self) -> tuple[str, str]:
        return self.source.text().strip(), self.every.currentData() or "day"


class BackupsDialog(Base):
    """The folder backups: when each one last ran, and backing one up now or removing it."""

    def __init__(self, icon, dark, backups, names: dict):
        from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QPushButton
        from PySide6.QtCore import Qt
        from .timefmt import time_ago
        super().__init__(tr("Backups"), tr("Folders of this PC kept in a cloud account."), icon, dark)
        self.choice: tuple[str, str] | None = None
        self.list = QListWidget()
        self.list.setAccessibleName(tr("Backups"))
        self.list.setMinimumWidth(460)
        every = dict(every_names())
        for b in backups:
            if b.result == "ok":
                state = tr("Last time: {when}", when=time_ago(b.last))
            elif b.result.startswith("failed:"):
                state = tr("Some files couldn't go up: {count}", count=b.result.split(":", 1)[1])
            elif b.result.startswith("error:"):
                state = tr("Didn't work last time. Back it up now to see why.")
            else:
                state = tr("Not backed up yet")
            place = " / ".join([names.get(b.account, tr("An account that isn't here any more"))] + list(b.target))
            it = QListWidgetItem(f"{b.name}  →  {place}\n{every.get(b.every, '')}   ·   {state}")
            it.setData(Qt.UserRole, b.id)
            it.setToolTip(b.source)
            self.list.addItem(it)
        self.lay.addWidget(self.list, 1)
        if not backups:
            self.lay.addWidget(_note(tr("No backups yet. Open a folder in an account, then choose Back up a folder "
                                        "here.")))
        row = QHBoxLayout()
        self.now = QPushButton(tr("Back up now"))
        self.drop = QPushButton(tr("Remove"))
        for b, what in ((self.now, "run"), (self.drop, "remove")):
            b.clicked.connect(lambda _=False, w=what: self._pick(w))
            b.setEnabled(False)
            row.addWidget(b)
        row.addStretch()
        close = QPushButton(tr("Close"))
        close.setObjectName("primary")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        self.lay.addLayout(row)
        self.list.currentRowChanged.connect(lambda r: [b.setEnabled(r >= 0) for b in (self.now, self.drop)])

    def _pick(self, what: str):
        it = self.list.currentItem()
        if it is not None:
            self.choice = (what, it.data(0x0100))
            self.accept()
