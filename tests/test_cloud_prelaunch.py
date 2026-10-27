"""Cloud before the first launch: what the pre-launch check found, checked so it stays fixed."""
import socket
import threading
import types
from pathlib import Path

import pytest
from PySide6.QtWidgets import QHBoxLayout, QWidget

from corefold import cloud, theme


def _local(folder: Path) -> cloud.Client:
    folder.mkdir(parents=True, exist_ok=True)
    return cloud.client(cloud.Account("folder", "", extra={"local": str(folder)}))


def test_a_long_name_is_cut_in_its_middle_never_its_ending():
    # (shown as a picture; cut at its 120th letter it would have been saved as a .scr, a program)
    shown = "Holiday" + "_" * 109 + ".scr" + "_" * 40 + ".jpg"
    saved = cloud.local_name(shown)
    assert saved.endswith(".jpg") and ".scr" not in saved and len(saved) <= cloud.NAME_MAX
    assert cloud.local_name("a" * 300 + ".pdf").endswith(".pdf")
    assert cloud.local_name("report.docx") == "report.docx"
    assert cloud.local_name(".gitignore") == ".gitignore"


def test_no_other_program_can_take_the_sign_in_port():
    server = cloud._listen(0)
    try:
        port = server.server_address[1]
        other = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            with pytest.raises(OSError):
                other.bind(("127.0.0.1", port))
        finally:
            other.close()
    finally:
        server.server_close()


def test_a_folder_isnt_moved_into_a_cloud_folder_inside_it(tmp_path):
    onedrive = tmp_path / "OneDrive"
    (onedrive / "Docs").mkdir(parents=True)
    (onedrive / "Docs" / "thesis.docx").write_bytes(b"years of work")
    src, dst = _local(onedrive), _local(onedrive / "Docs")  # (Docs was added to Cloud by hand)
    docs = next(e for e in src.list(None) if e.name == "Docs")
    with pytest.raises(cloud.CloudError) as e:
        cloud.send(src, docs, dst, None, move=True, cancel=threading.Event())
    assert str(e.value) == cloud.INTO_ITSELF
    assert (onedrive / "Docs" / "thesis.docx").read_bytes() == b"years of work"
    # a copy is fine (it never goes into itself over and over), and leaves the folder where it is
    sent, gone = cloud.send(src, docs, dst, None, cancel=threading.Event())
    assert sent == 1 and not gone and (onedrive / "Docs" / "thesis.docx").exists()


def test_a_move_takes_away_only_what_got_there_whole(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "Trip").mkdir(parents=True)
    (a / "Trip" / "notes.txt").write_text("n")
    src, dst = _local(a), _local(b)
    trip = next(e for e in src.list(None) if e.name == "Trip")
    assert cloud.send(src, trip, dst, None, move=True, cancel=threading.Event()) == (1, True)
    assert (b / "Trip" / "notes.txt").read_text() == "n"


def test_a_folder_windows_wont_let_be_read_isnt_uploaded_empty_or_moved_away(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "Work" / "Private").mkdir(parents=True)
    (a / "Work" / "Private" / "contract.pdf").write_bytes(b"c")
    (a / "Work" / "plan.txt").write_text("p")
    src, dst = _local(a), _local(b)
    real = Path.iterdir

    def iterdir(self):
        if self.name == "Private":
            raise PermissionError(13, "Access is denied")
        return real(self)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    work = next(e for e in src.list(None) if e.name == "Work")
    sent, gone = cloud.send(src, work, dst, None, move=True, cancel=threading.Event())
    assert sent == 1 and not gone and dst.unread == ["Private"]
    assert (a / "Work" / "Private" / "contract.pdf").exists()  # (still where it was)
    assert not (b / "Work" / "Private").exists()  # (not made as an empty folder, as if it were all there)


class _Google(cloud.Client):
    """A pretend Google Drive with one Google Doc, which downloads as a .docx."""

    def __init__(self):
        super().__init__(cloud.Account("gdrive", "me@example.com"))
        self.deleted = []

    def list(self, folder):
        return []

    def download(self, entry, target, progress=None, cancel=None):
        target.write_bytes(b"PK docx")

    def delete(self, entry):
        self.deleted.append(entry.name)


def test_a_google_doc_moved_elsewhere_stays_a_google_doc_where_it_was(tmp_path):
    src, dst = _Google(), _local(tmp_path / "b")
    doc = cloud.Entry("Budget", False, "g1", kind="export:application/x-docx:.docx")
    sent, gone = cloud.send(src, doc, dst, None, move=True, cancel=threading.Event())
    assert sent == 1 and not gone and src.deleted == [] and src.converted == ["Budget"]
    assert (tmp_path / "b" / "Budget.docx").exists()
    photo = cloud.Entry("photo.jpg", False, "g2")
    assert cloud.send(src, photo, dst, None, move=True, cancel=threading.Event())[1]
    assert src.deleted == ["photo.jpg"]


def test_a_shortcut_to_a_folder_is_looked_for_in_every_drive(monkeypatch):
    g = cloud.GoogleDrive(cloud.Account("gdrive", "me@example.com"))
    shortcut = g._entry({"id": "s1", "name": "Team", "mimeType": g.SHORTCUT, "driveId": "SHARED",
                         "shortcutDetails": {"targetId": "f1", "targetMimeType": g.FOLDER_TYPE}}, "SHARED")
    assert shortcut.own == "s1" and shortcut.id == "f1" and shortcut.drive == ""
    asked = []
    monkeypatch.setattr(g, "call", lambda method, url, **kw: asked.append(url) or {"files": []})
    g.list(shortcut)
    assert "corpora=allDrives" in asked[0] and "driveId=SHARED" not in asked[0]


def test_names_on_buttons_and_in_menus_show_as_written(qtbot):
    from corefold import cloudpage
    crumbs = cloudpage.Crumbs(types.SimpleNamespace(go_to=lambda i: None))
    qtbot.addWidget(crumbs)
    crumbs.show_trail("Google Drive", ["R&D"])
    texts = [crumbs.lay.itemAt(i).widget().text() for i in range(crumbs.lay.count())
             if crumbs.lay.itemAt(i).widget() is not None]
    assert "R&&D" in [t.strip("\u200e\u200f\u2066\u2067\u2068\u2069") for t in texts]


def test_only_a_web_address_is_opened_as_a_files_page(monkeypatch):
    from corefold import cloudpage
    opened = []
    monkeypatch.setattr(cloudpage.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    for bad in ("file://server/share/x.exe", "\\\\server\\share\\x.exe", "javascript:alert(1)", ""):
        assert not cloudpage._open_web(bad)
    assert cloudpage._open_web("https://docs.google.com/document/d/1")
    assert opened == ["https://docs.google.com/document/d/1"]


def test_download_is_on_the_page_once_something_is_picked(qtbot, tmp_path, monkeypatch):
    from corefold import cloudlocal, cloudpage
    monkeypatch.setattr(cloud, "ACCOUNTS_FILE", tmp_path / "accounts.dat")
    monkeypatch.setattr(cloudlocal, "find", lambda: [])
    t = theme.build_theme("light", "core")
    page = cloudpage.CloudPage(types.SimpleNamespace(theme=t), QHBoxLayout())
    qtbot.addWidget(page)
    acc = cloud.Account("folder", "", extra={"local": str(tmp_path)})
    page.accounts, page.account = [acc], acc
    page._show([(acc, cloud.Entry("a.txt", False, str(tmp_path / "a.txt")))])
    assert not page.download_btn.isEnabled()
    page.files.topLevelItem(0).setSelected(True)
    assert page.download_btn.isEnabled()


def test_on_a_narrow_window_the_buttons_show_their_icons_not_cut_words(qtbot, tmp_path, monkeypatch):
    from corefold import cloudlocal, cloudpage
    monkeypatch.setattr(cloud, "ACCOUNTS_FILE", tmp_path / "accounts.dat")
    monkeypatch.setattr(cloudlocal, "find", lambda: [])
    t = theme.build_theme("light", "core")
    page = cloudpage.CloudPage(types.SimpleNamespace(theme=t), QHBoxLayout())
    qtbot.waitUntil(lambda: page.found is not None)
    page.found = [cloud.Account("folder", "", extra={"local": str(tmp_path)})]
    page.load_accounts()
    window = QWidget()  # (inside a window, like in the app: a window keeps its pages at its own size)
    qtbot.addWidget(window)
    page.setParent(window)
    page.setStyleSheet("QPushButton { font-size: 22px; }")  # (words as long as French ones at 150 %)
    window.resize(1400, 600)
    page.resize(1400, 600)
    window.show()
    qtbot.wait(50)
    assert not page.compact and page.download_btn.text()
    page.resize(600, 600)
    qtbot.waitUntil(lambda: page.compact, timeout=2000)
    for b, text, *_ in page.actions:
        assert b.text() == "" and text in b.toolTip() and not b.icon().isNull()
    page.resize(1400, 600)
    qtbot.waitUntil(lambda: not page.compact, timeout=2000)
    assert page.download_btn.text() == page.actions[1][1]
