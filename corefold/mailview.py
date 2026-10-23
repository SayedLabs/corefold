"""Corefold Viewer for email files (.eml, .msg): the email, its attachments, and Save as PDF.

Emails can come from anyone, so: nothing is ever loaded from the internet (tracking pictures stay
blocked), no script runs, links say where they really go before opening, and attachments that
could run a program warn first."""
from __future__ import annotations

import html as html_lib
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QByteArray, QSizeF, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QTextDocument
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton,
                               QTextBrowser, QVBoxLayout, QWidget)

from . import mail
from .core import APP_NAME, human_size, short_name, unique_path
from .i18n import ltr, tr
from .theme import round_menu
from .winutil import set_title_bar_dark

if TYPE_CHECKING:
    from .app import Controller

# attachments that can run something when opened: Corefold asks first
RISKY = {".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".pif", ".js", ".jse", ".vbs", ".vbe", ".wsf", ".wsh",
         ".ps1", ".hta", ".lnk", ".jar", ".reg", ".cpl", ".msc", ".dll", ".iso", ".img", ".vhd", ".vhdx",
         ".docm", ".xlsm", ".pptm", ".appx", ".msix", ".appinstaller", ".url", ".chm", ".scf"}


def safe_html(text: str) -> str:
    """The email's HTML without scripts, forms, frames and styles that hide things."""
    text = re.sub(r"(?is)<(script|style|iframe|object|embed|form|noscript)\b.*?</\1\s*>", "", text)
    text = re.sub(r"(?is)<(script|iframe|object|embed|form|meta|link|base)\b[^>]*>", "", text)
    text = re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", text)
    return text


def plain_to_html(text: str) -> str:
    """A plain-text email shown with its links clickable."""
    escaped = html_lib.escape(text)
    escaped = re.sub(r"(https?://[^\s<>\"']+)", r'<a href="\1">\1</a>', escaped)
    return f"<div style='white-space: pre-wrap'>{escaped}</div>"


class MailBody(QTextBrowser):
    """Shows the email. Pictures come only from inside the email; anything from the internet is blocked."""

    def __init__(self, message: mail.Message):
        super().__init__()
        self.message = message
        self.blocked = 0
        self.by_cid = {a.content_id: a for a in message.attachments if a.content_id}
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setFrameShape(QFrame.NoFrame)

    def loadResource(self, kind, url: QUrl):
        scheme = url.scheme().lower()
        if scheme == "cid":
            att = self.by_cid.get(url.path() or url.toString()[4:])
            if att is not None and kind == QTextDocument.ImageResource:
                img = QImage()
                if img.loadFromData(QByteArray(att.data)):
                    return img
            return None
        if scheme == "data":
            return super().loadResource(kind, url)
        if scheme in ("http", "https") and kind == QTextDocument.ImageResource:
            self.blocked += 1
        return None  # nothing from the internet or from this PC


class MailWindow(QWidget):
    def __init__(self, controller: "Controller", path: Path, icon, message: mail.Message | None = None):
        super().__init__()
        self.c, self.path = controller, path
        self.setObjectName("window")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowIcon(icon)
        self.resize(900, 760)
        self.m = message if message is not None else mail.read(path)
        m = self.m
        self.setWindowTitle(f"{m.subject or path.name} - {APP_NAME}")

        subject = QLabel(ltr(m.subject) or tr("(no subject)"))
        subject.setObjectName("h1")
        subject.setWordWrap(True)
        subject.setTextInteractionFlags(Qt.TextSelectableByMouse)
        head = QVBoxLayout()
        head.setSpacing(4)
        head.addWidget(subject)
        for name, value in ((tr("From"), m.sender), (tr("To"), m.to), (tr("Cc"), m.cc),
                            (tr("Date"), self._date())):
            if not value:
                continue
            row = QLabel(f"<span style='opacity:.65'>{html_lib.escape(name)}:</span> {html_lib.escape(ltr(value))}")
            row.setObjectName("muted")
            row.setWordWrap(True)
            row.setTextInteractionFlags(Qt.TextSelectableByMouse)
            head.addWidget(row)

        self.pdf_btn = QPushButton(tr("Save as PDF"))
        self.pdf_btn.setObjectName("primary")
        self.pdf_btn.setCursor(Qt.PointingHandCursor)
        self.pdf_btn.clicked.connect(self.save_pdf)
        more = QPushButton(tr("More"))
        more.setCursor(Qt.PointingHandCursor)
        menu = QMenu(more)
        round_menu(menu)
        menu.addAction(tr("Show the technical details (headers)"), self.show_headers)
        if path.suffix.lower() == ".msg":
            menu.addAction(tr("Save as .eml"), self.save_eml)
        more.setMenu(menu)
        top = QHBoxLayout()
        top.addLayout(head, 1)
        top.addWidget(self.pdf_btn, 0, Qt.AlignTop)
        top.addWidget(more, 0, Qt.AlignTop)

        self.body = MailBody(m)
        self.body.anchorClicked.connect(self._link)
        if m.html:
            self.body.setHtml(safe_html(m.html))
        else:
            self.body.setHtml(plain_to_html(m.text or ""))
        self.note = QLabel(tr("Pictures from the internet are blocked, so the sender can't tell you opened this "
                              "email."))
        self.note.setObjectName("muted")
        self.note.setVisible(self.body.blocked > 0)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        lay.addLayout(top)
        atts = m.visible_attachments()
        if atts:
            lay.addWidget(self._attachments(atts))
        lay.addWidget(self.note)
        frame = QFrame()
        frame.setObjectName("card")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(4, 4, 4, 4)
        fl.addWidget(self.body)
        lay.addWidget(frame, 1)

    def _date(self) -> str:
        d = self.m.date
        if d is None:
            return ""
        try:
            from .window import locale
            from PySide6.QtCore import QDateTime
            stamp = QDateTime.fromSecsSinceEpoch(int(d.timestamp()))
            return locale().toString(stamp, "d MMMM yyyy, HH:mm")
        except Exception:
            return d.strftime("%Y-%m-%d %H:%M")

    # ---- attachments

    def _attachments(self, atts: list[mail.Attachment]) -> QWidget:
        from .panel import FlowLayout
        box = QFrame()
        box.setObjectName("card")
        outer = QVBoxLayout(box)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel(tr("{count} attachments", count=len(atts)) if len(atts) != 1 else tr("1 attachment"))
        title.setObjectName("muted")
        head.addWidget(title)
        head.addStretch()
        outer.addLayout(head)
        lay = FlowLayout(spacing=6)
        outer.addLayout(lay)
        for a in atts[:40]:
            b = QPushButton(f"{short_name(a.name, 34)}  ·  {human_size(a.size)}")
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tr("Click to open, right-click to save"))
            b.clicked.connect(lambda _=False, att=a: self.open_attachment(att))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _pos, att=a: self.save_attachment(att))
            lay.addWidget(b)
        save_all = QPushButton(tr("Save all…"))
        save_all.setCursor(Qt.PointingHandCursor)
        save_all.clicked.connect(lambda: self.save_all(atts))
        head.addWidget(save_all)
        return box

    def open_attachment(self, a: mail.Attachment):
        if a.message is not None:  # an email inside the email: in its own window
            win = MailWindow(self.c, self.path, self.windowIcon(), a.message)
            self.c.viewers.append(win)
            win.show()
            return
        ext = Path(a.name).suffix.lower()
        if ext in RISKY:
            box = QMessageBox(QMessageBox.Warning, tr("Open this attachment?"),
                              tr("{name} can run a program on your PC. Only open it if you trust the sender and "
                                 "expected this file.", name=a.name), parent=self)
            go = box.addButton(tr("Open anyway"), QMessageBox.DestructiveRole)
            box.addButton(tr("Cancel"), QMessageBox.RejectRole)
            box.exec()
            chosen = box.clickedButton() is go
            box.deleteLater()
            if not chosen:
                return
        folder = Path(tempfile.mkdtemp(prefix="corefold-open-"))
        out = folder / a.name
        out.write_bytes(a.data)
        _mark_from_internet(out)
        from .core import os_open
        try:
            os_open(out)
        except OSError as e:
            QMessageBox.warning(self, tr("Couldn't open the file"), str(e))

    def save_attachment(self, a: mail.Attachment):
        name, _ = QFileDialog.getSaveFileName(self, tr("Save attachment"), str(self.path.parent / a.name))
        if name:
            Path(name).write_bytes(a.data)
            _mark_from_internet(Path(name))

    def save_all(self, atts: list[mail.Attachment]):
        folder = QFileDialog.getExistingDirectory(self, tr("Save all attachments to"), str(self.path.parent))
        if not folder:
            return
        saved = []
        for a in atts:
            out = unique_path(Path(folder) / a.name)
            out.write_bytes(a.data)
            _mark_from_internet(out)
            saved.append(out)
        self.c.toast.notify(tr("Saved"), tr("{count} attachments saved", count=len(saved)), linger_ms=3000)

    # ---- saving the email

    def save_pdf(self):
        from PySide6.QtGui import QPageSize, QPdfWriter
        default = unique_path(self.path.with_name(_file_name(self.m.subject or self.path.stem) + ".pdf"))
        name, _ = QFileDialog.getSaveFileName(self, tr("Save as PDF"), str(default), "PDF (*.pdf)")
        if not name:
            return
        doc = QTextDocument()
        doc.setDefaultFont(self.body.font())
        rows = "".join(f"<tr><td style='color:#666;padding-right:10px'>{html_lib.escape(k)}</td>"
                       f"<td>{html_lib.escape(v)}</td></tr>"
                       for k, v in ((tr("From"), self.m.sender), (tr("To"), self.m.to), (tr("Cc"), self.m.cc),
                                    (tr("Date"), self._date())) if v)
        atts = self.m.visible_attachments()
        att_line = (f"<p style='color:#666'>{html_lib.escape(tr('Attachments'))}: "
                    f"{html_lib.escape(', '.join(a.name for a in atts))}</p>") if atts else ""
        body = self.body.toHtml()
        doc.setHtml(f"<h2>{html_lib.escape(self.m.subject)}</h2><table>{rows}</table>{att_line}<hr>{body}")
        for cid, att in self.body.by_cid.items():  # the email's own pictures
            img = QImage()
            if img.loadFromData(QByteArray(att.data)):
                doc.addResource(QTextDocument.ImageResource, QUrl(f"cid:{cid}"), img)
        writer = QPdfWriter(name)
        writer.setPageSize(QPageSize(QPageSize.A4))
        writer.setResolution(110)
        doc.setPageSize(QSizeF(writer.width(), writer.height()))
        doc.print_(writer)
        self.c.toast.notify(tr("Saved"), Path(name).name, linger_ms=3000)

    def save_eml(self):
        default = unique_path(self.path.with_suffix(".eml"))
        name, _ = QFileDialog.getSaveFileName(self, tr("Save as .eml"), str(default), "Email (*.eml)")
        if name:
            Path(name).write_bytes(mail.to_eml(self.m))

    def show_headers(self):
        box = QMessageBox(QMessageBox.NoIcon, tr("Technical details"), "", parent=self)
        box.setDetailedText(self.m.headers or tr("This email has no technical details."))
        box.setText(tr("The email's headers: where it came from and how it got to you."))
        box.exec()
        box.deleteLater()

    def _link(self, url: QUrl):
        """Links show where they really go first (emails can hide a different address behind the text)."""
        scheme = url.scheme().lower()
        if scheme not in ("http", "https", "mailto"):
            return
        target = url.toString()
        box = QMessageBox(QMessageBox.Question, tr("Open this link?"),
                          tr("It goes to:") + "\n\n" + short_name(target, 300), parent=self)
        go = box.addButton(tr("Open"), QMessageBox.AcceptRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        chosen = box.clickedButton() is go
        box.deleteLater()
        if chosen:
            QDesktopServices.openUrl(url)

    def showEvent(self, e):
        super().showEvent(e)
        set_title_bar_dark(self, self.c.theme.dark)


def _file_name(text: str) -> str:
    return re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", text).strip(" .")[:120] or "email"


def _mark_from_internet(path: Path):
    """Attachments keep Windows' "this came from the internet" warning, like files saved from a browser."""
    import os
    if os.name != "nt":
        return
    try:
        with open(str(path) + ":Zone.Identifier", "w", encoding="ascii") as fh:
            fh.write("[ZoneTransfer]\r\nZoneId=3\r\n")
    except OSError:
        pass
