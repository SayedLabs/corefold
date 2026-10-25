"""Cloud, File Organizer and Unlock: what the 1.10 review found, checked so it stays fixed."""
import io
import time
import types
import urllib.error

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from corefold import cloud, organizer, theme


@pytest.fixture
def accounts_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, "FOLDER", tmp_path)
    monkeypatch.setattr(cloud, "ACCOUNTS_FILE", tmp_path / "accounts.dat")
    return tmp_path


def _http_error(code: int, body: bytes = b"{}") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.com/", code, "x", {}, io.BytesIO(body))


def test_a_signed_out_account_isnt_brought_back(accounts_file, monkeypatch):
    acc = cloud.Account("dropbox", "me@example.com", refresh="r", access="old", expires=0)
    cloud.remember_account(acc)
    cloud.forget_account(acc.key)
    cloud.update_account(acc)  # (a key renewed, or the space measured, while signing out)
    assert cloud.load_accounts() == []
    # signing out of Dropbox with a key that ran out: the fresh one is used, never kept
    monkeypatch.setattr(cloud.Dropbox, "_refresh", lambda self: {"access_token": "new", "expires_in": 3600})
    sent = []
    monkeypatch.setattr(cloud, "_request", lambda *a, **k: sent.append(a[2]) or {})
    cloud.Dropbox(acc).sign_out()
    assert sent == ["new"] and cloud.load_accounts() == []


def test_a_renewed_key_is_kept_for_an_account_still_there(accounts_file, monkeypatch):
    acc = cloud.Account("onedrive", "me@example.com", refresh="r", access="old", expires=0)
    cloud.remember_account(acc)
    monkeypatch.setattr(cloud.OneDrive, "_refresh", lambda self: {"access_token": "new", "expires_in": 3600})
    assert cloud.OneDrive(acc).token() == "new"
    assert [a.access for a in cloud.load_accounts()] == ["new"]


def test_no_internet_while_renewing_the_key_isnt_signed_out(accounts_file, monkeypatch):
    acc = cloud.Account("onedrive", "me@example.com", refresh="r", access="", expires=0)

    def offline(*a, **k):
        raise urllib.error.URLError("down")
    monkeypatch.setattr(cloud.urllib.request, "urlopen", offline)
    monkeypatch.setattr(cloud.time, "sleep", lambda s: None)
    with pytest.raises(cloud.CloudError) as e:
        cloud.OneDrive(acc).token()
    assert not isinstance(e.value, cloud.SignInNeeded)

    def refused(*a, **k):
        raise _http_error(400, b'{"error": "invalid_grant"}')
    monkeypatch.setattr(cloud.urllib.request, "urlopen", refused)
    with pytest.raises(cloud.SignInNeeded):
        cloud.OneDrive(acc).token()


def test_too_many_requests_is_tried_again_even_when_sending(monkeypatch):
    calls = []

    def busy_once(*a):
        calls.append(a[0])
        if len(calls) == 1:
            raise cloud._Again(cloud.CloudError("busy"), 2, busy=True)
        return {"ok": 1}
    waits = []
    monkeypatch.setattr(cloud, "_request_once", busy_once)
    monkeypatch.setattr(cloud.time, "sleep", waits.append)
    assert cloud._request("POST", "https://example.com/") == {"ok": 1}
    assert calls == ["POST", "POST"] and waits == [2]
    # (any other trouble while sending is still tried only once: making a folder twice isn't safe)
    calls.clear()

    def down(*a):
        calls.append(a[0])
        raise cloud._Again(cloud.CloudError("down"))
    monkeypatch.setattr(cloud, "_request_once", down)
    with pytest.raises(cloud.CloudError):
        cloud._request("POST", "https://example.com/")
    assert calls == ["POST"]


def test_the_sign_in_catcher_doesnt_wait_forever_on_a_silent_connection():
    server = cloud._listen(0)
    try:
        assert server.RequestHandlerClass.timeout and server.RequestHandlerClass.timeout <= 10
    finally:
        server.server_close()


def test_a_dropbox_upload_of_a_file_that_got_shorter_stops(tmp_path, monkeypatch):
    src = tmp_path / "big.bin"
    src.write_bytes(b"x" * (cloud.SMALL + 10))
    acc = cloud.Account("dropbox", "me@example.com", access="a", expires=time.time() + 3600)
    calls = []

    def call(self, method, url, **kw):
        calls.append(url)
        if url.endswith("/start"):
            with open(src, "r+b") as f:  # (another app cuts it short while it goes up)
                f.truncate(cloud.CHUNK)
            return {"session_id": "s"}
        return {}
    monkeypatch.setattr(cloud.Dropbox, "call", call)
    with pytest.raises(cloud.CloudError):
        cloud.Dropbox(acc).upload(src, None)
    assert len(calls) < 5


def test_what_waited_during_sign_in_starts_after_it(qtbot):
    from corefold.cloudpage import CloudPage
    page = QWidget()
    qtbot.addWidget(page)
    started = []
    page.job, page.queue, page.status = object(), ["upload"], QLabel()
    page._busy = lambda on, *a, **k: None
    page._run = started.append
    CloudPage._signed_in(page, cloud.CloudError("cancelled"))
    assert started == ["upload"] and page.queue == []


def _controller():
    t = theme.build_theme("light", "core")
    settings = types.SimpleNamespace(organize_auto_folder="", save=lambda: None)
    return types.SimpleNamespace(theme=t, settings=settings, keep_tidy=lambda: None)


def test_leaving_one_file_out_doesnt_leave_its_kind_out_for_good(qtbot, tmp_path):
    from corefold.organizerpage import OrganizerPage
    page = OrganizerPage(_controller(), QHBoxLayout())
    qtbot.addWidget(page)
    organizer.set_off(set())
    p = organizer.Plan(tmp_path, folders={"documents": tmp_path / "Documents"})
    p.items = [organizer.Item(tmp_path / "a.pdf", 1, "documents", tmp_path / "Documents" / "a.pdf")]
    page.plan = p
    page._fill()
    head = page.tree.topLevelItem(0)
    child = head.child(0)
    child.setCheckState(0, Qt.Unchecked)  # (Qt unticks the kind too: its only file is out)
    assert head.checkState(0) == Qt.Unchecked and "documents" not in organizer.kinds_off()
    head.setCheckState(0, Qt.Checked)
    head.setCheckState(0, Qt.Unchecked)  # (the kind itself unticked: kept for next time)
    assert "documents" in organizer.kinds_off()
    organizer.set_off(set())


def test_unlock_looks_at_what_was_asked_for_while_busy(qtbot, tmp_path):
    from corefold.unlockpage import UnlockPage
    page = UnlockPage(_controller(), QHBoxLayout())  # (not given to qtbot: its close() closes programs)
    asked = []
    page.check = lambda paths, keep_states=False: asked.append(paths)
    page.paths = [tmp_path / "gone.txt"]
    page.busy = True
    page.queued = [tmp_path / "next.txt"]
    page._moved(OSError("in use"), "")
    assert asked == [[tmp_path / "next.txt"]] and page.queued == []
    page.deleteLater()
