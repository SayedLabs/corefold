"""Corefold Cloud: your own servers (cloudnet), WebDAV, FTP and SFTP, each tried for real against a server run on
this PC for the test (wsgidav, pyftpdlib, and a small paramiko SFTP server)."""
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from corefold import cloud, cloudnet


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait(port):
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)


@pytest.fixture
def webdav(tmp_path):
    pytest.importorskip("wsgidav")
    from cheroot import wsgi
    from wsgidav.wsgidav_app import WsgiDAVApp
    root = tmp_path / "dav"
    root.mkdir()
    port = _port()
    app = WsgiDAVApp({
        "provider_mapping": {"/files": str(root)},
        "http_authenticator": {"domain_controller": None, "accept_basic": True, "accept_digest": False,
                               "default_to_digest": False},
        "simple_dc": {"user_mapping": {"*": {"me": {"password": "pw é"}}}},
        "verbose": 0, "logging": {"enable": False},
    })
    server = wsgi.Server(("127.0.0.1", port), app)
    t = threading.Thread(target=server.start, daemon=True)
    t.start()
    _wait(port)
    yield cloudnet.make_account("webdav", "127.0.0.1", "me", "pw é", port, "/files", secure=False), root
    server.stop()


@pytest.fixture
def ftp(tmp_path):
    pytest.importorskip("pyftpdlib")
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
    root = tmp_path / "ftp"
    root.mkdir()
    auth = DummyAuthorizer()
    auth.add_user("me", "secret", str(root), perm="elradfmwMT")
    handler = type("H", (FTPHandler,), {"authorizer": auth, "use_encoding": "utf8"})
    port = _port()
    server = FTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=server.serve_forever, kwargs={"timeout": 0.1}, daemon=True)
    t.start()
    _wait(port)
    yield cloudnet.make_account("ftp", "127.0.0.1", "me", "secret", port, secure=False), root
    server.close_all()


@pytest.fixture
def sftp(tmp_path):
    paramiko = pytest.importorskip("paramiko")
    root = tmp_path / "sftp"
    root.mkdir()
    host_key = paramiko.RSAKey.generate(2048)
    port = _port()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(5)
    stop = threading.Event()

    class Auth(paramiko.ServerInterface):
        def check_auth_password(self, username, password):
            return paramiko.AUTH_SUCCESSFUL if (username, password) == ("me", "s3cret") else paramiko.AUTH_FAILED

        def get_allowed_auths(self, username):
            return "password"

        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED

    def real(path):
        return str(root / path.lstrip("/"))

    def attrs(p, name=None):
        a = paramiko.SFTPAttributes.from_stat(os.stat(p))
        a.filename = name or os.path.basename(p)
        return a

    class Handle(paramiko.SFTPHandle):
        def stat(self):
            return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))

    class Files(paramiko.SFTPServerInterface):
        def _do(self, fn):
            try:
                return fn()
            except OSError as e:
                return paramiko.SFTPServer.convert_errno(e.errno)

        def list_folder(self, path):
            p = real(path)
            return self._do(lambda: [attrs(os.path.join(p, n), n) for n in os.listdir(p)])

        def stat(self, path):
            return self._do(lambda: attrs(real(path)))

        lstat = stat

        def open(self, path, flags, attr):
            def go():
                fd = os.open(real(path), flags | getattr(os, "O_BINARY", 0), 0o644)
                mode = "r+b" if flags & os.O_RDWR else ("wb" if flags & os.O_WRONLY else "rb")
                if flags & os.O_APPEND:
                    mode = "ab"
                f = os.fdopen(fd, mode)
                h = Handle(flags)
                h.readfile = h.writefile = f
                return h
            return self._do(go)

        def remove(self, path):
            return self._do(lambda: os.remove(real(path)) or paramiko.SFTP_OK)

        def rename(self, old, new):
            return self._do(lambda: os.rename(real(old), real(new)) or paramiko.SFTP_OK)

        def mkdir(self, path, attr):
            return self._do(lambda: os.mkdir(real(path)) or paramiko.SFTP_OK)

        def rmdir(self, path):
            return self._do(lambda: os.rmdir(real(path)) or paramiko.SFTP_OK)

        def canonicalize(self, path):
            return "/" + path.strip("/")

    def serve():
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                sock, _ = listener.accept()
            except OSError:
                continue
            t = paramiko.Transport(sock)
            t.add_server_key(host_key)
            t.set_subsystem_handler("sftp", paramiko.SFTPServer, Files)
            t.start_server(server=Auth())
    threading.Thread(target=serve, daemon=True).start()
    yield cloudnet.make_account("sftp", "127.0.0.1", "me", "s3cret", port), root, host_key
    stop.set()
    listener.close()


def _battery(acc, root: Path, tmp_path: Path):
    """What Cloud does with an account, against the real server."""
    cloudnet.check(acc)
    c = cloud.client(acc)
    assert c.list(None) == []
    folder = c.new_folder("Photos", None)
    assert folder.folder and (root / "Photos").is_dir()
    src = tmp_path / "up" / "notes é.txt"
    src.parent.mkdir()
    data = os.urandom(300_000)
    src.write_bytes(data)
    seen = []
    a = c.upload(src, folder, lambda d, t: seen.append((d, t)))
    b = c.upload(src, folder)  # (the same name again: never over the first one)
    assert a.name == "notes é.txt" and b.name == "notes é (2).txt"
    assert seen and seen[-1][0] == len(data)
    assert (root / "Photos" / "notes é.txt").read_bytes() == data
    listed = {e.name: e for e in c.list(folder)}
    assert listed["notes é.txt"].size == len(data) and not listed["notes é.txt"].folder
    down = tmp_path / "down"
    down.mkdir()
    assert cloud.download_tree(c, folder, down) == 2
    assert (down / "Photos" / "notes é.txt").read_bytes() == data
    renamed = c.rename(listed["notes é (2).txt"], "kept.txt")
    assert (root / "Photos" / "kept.txt").exists() and renamed.name == "kept.txt"
    assert {e.name for e in c.search("KEPT")} == {"kept.txt"}
    sub = tmp_path / "tree"
    (sub / "inner").mkdir(parents=True)
    (sub / "inner" / "x.bin").write_bytes(b"x" * 10)
    assert cloud.upload_tree(c, sub, None) == 1
    assert (root / "tree" / "inner" / "x.bin").read_bytes() == b"x" * 10
    c.delete(next(e for e in c.list(None) if e.name == "tree"))  # (a whole folder, with what's in it)
    assert not (root / "tree").exists()
    with pytest.raises(cloud.CloudError):
        c.rename(renamed, "a/b")
    with pytest.raises(cloud.CloudError):
        c.delete(cloud.Entry("", True, acc.extra["server"]["folder"]))  # (never the account's own top folder)
    closer = getattr(c, "_close", None)
    if closer:
        closer()


def test_webdav_server(webdav, tmp_path):
    acc, root = webdav
    _battery(acc, root, tmp_path)


def test_ftp_server(ftp, tmp_path):
    acc, root = ftp
    _battery(acc, root, tmp_path)


def test_sftp_server(sftp, tmp_path):
    acc, root, _key = sftp
    _battery(acc, root, tmp_path)
    assert acc.extra["server"]["key"].startswith("ssh-rsa SHA256:")


def test_sftp_refuses_a_changed_key(sftp):
    acc, _root, _key = sftp
    acc.extra["server"]["key"] = "ssh-rsa SHA256:somethingelse"
    with pytest.raises(cloud.CloudError) as e:
        cloud.client(acc).list(None)
    assert str(e.value) == cloudnet.KEY_CHANGED


def test_wrong_password_says_so(webdav, ftp, sftp):
    for acc in (webdav[0], ftp[0], sftp[0]):
        acc.refresh = "nope"
        with pytest.raises(cloud.SignInNeeded):
            cloudnet.check(acc)


def test_copy_between_two_servers(webdav, sftp, tmp_path):
    (webdav[1] / "a.txt").write_bytes(b"hello")
    src, dst = cloud.client(webdav[0]), cloud.client(sftp[0])
    entry = next(e for e in src.list(None) if e.name == "a.txt")
    count, moved = cloud.send(src, entry, dst, None, move=True)
    assert count == 1 and moved
    assert (sftp[1] / "a.txt").read_bytes() == b"hello" and not (webdav[1] / "a.txt").exists()


def test_make_account_from_an_address():
    acc = cloudnet.make_account("", "https://cloud.example.com/remote.php/dav/files/me", "me", "pw")
    cfg = acc.extra["server"]
    assert acc.service == "webdav" and cfg["secure"] and cfg["port"] == 443
    assert cfg["folder"] == "/remote.php/dav/files/me"
    assert acc.refresh == "pw" and "pw" not in repr(acc)
    sftp = cloudnet.make_account("", "sftp://me@nas.local:2222/volume1", "", "pw")
    assert sftp.service == "sftp" and sftp.extra["server"]["port"] == 2222 and sftp.extra["server"]["user"] == "me"
    assert cloudnet.make_account("ftp", "192.168.1.5", "me", "pw", secure=False).extra["server"]["port"] == 21


def test_unencrypted_only_on_your_own_network():
    with pytest.raises(cloud.CloudError) as e:
        cloudnet.make_account("ftp", "8.8.8.8", "me", "pw", secure=False)
    assert str(e.value) == cloudnet.NOT_PRIVATE
    with pytest.raises(cloud.CloudError):
        cloudnet.make_account("webdav", "http://8.8.8.8/dav", "me", "pw")
    for host in ("192.168.1.2", "10.0.0.7", "nas", "diskstation.local", "127.0.0.1", "fe80::1"):
        assert cloudnet.is_private(host), host
    with pytest.raises(cloud.CloudError):
        cloudnet.make_account("webdav", "bad host/x", "me", "pw")


def test_server_accounts_are_kept_encrypted_with_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, "ACCOUNTS_FILE", tmp_path / "accounts.dat")
    monkeypatch.setattr(cloud, "FOLDER", tmp_path)
    acc = cloudnet.make_account("ftp", "192.168.1.5", "me", "pw-secret", secure=False)
    cloud.remember_account(acc)
    assert b"pw-secret" not in (tmp_path / "accounts.dat").read_bytes() or os.name != "nt"
    back = cloud.load_accounts()[0]
    assert cloudnet.is_server(back) and back.refresh == "pw-secret"
    assert isinstance(cloud.client(back), cloudnet.Ftp)


# ----------------------------------------------------------------------------- folder backups (cloudbackup)

@pytest.fixture
def kept(tmp_path, monkeypatch):
    from corefold import cloudbackup
    monkeypatch.setattr(cloud, "ACCOUNTS_FILE", tmp_path / "accounts.dat")
    monkeypatch.setattr(cloud, "FOLDER", tmp_path)
    monkeypatch.setattr(cloudbackup, "BACKUPS_FILE", tmp_path / "backups.json")
    monkeypatch.setattr("corefold.cloudlocal.find", lambda *a, **k: [])
    return cloudbackup


def test_backup_goes_up_then_only_what_changed(ftp, kept, tmp_path):
    acc, root = ftp
    cloud.remember_account(acc)
    src = tmp_path / "Documents"
    (src / "sub" / "deeper").mkdir(parents=True)
    (src / "a.txt").write_text("one")
    (src / "sub" / "b.bin").write_bytes(os.urandom(5000))
    (src / "sub" / "deeper" / "c.txt").write_text("c")
    (src / "desktop.ini").write_text("x")  # (Windows' own: never backed up)
    b = kept.add(src, acc, ["Backups", "PC"], "day")
    assert kept.due(b)
    r = kept.run(b)
    assert r["sent"] == 3 and not r["failed"], r
    up = root / "Backups" / "PC"
    assert (up / "a.txt").read_text() == "one" and (up / "sub" / "deeper" / "c.txt").read_text() == "c"
    assert not (up / "desktop.ini").exists()
    b = kept.load()[0]
    assert b.result == "ok" and b.last and not kept.due(b)
    again = kept.run(b)
    assert again["sent"] == 0 and again["same"] == 3
    time.sleep(0.01)
    (src / "a.txt").write_text("two, longer")
    os.utime(src / "a.txt", (time.time() + 5, time.time() + 5))
    (src / "new.txt").write_text("new")
    (src / "sub" / "b.bin").unlink()  # (deleted on this PC: kept in the backup)
    third = kept.run(kept.load()[0])
    assert third["sent"] == 2 and not third["failed"]
    assert (up / "a.txt").read_text() == "two, longer"
    assert not any(p.name.startswith("a (") for p in up.iterdir())  # (the changed file took the old one's place)
    assert (up / "sub" / "b.bin").exists() and (up / "new.txt").exists()
    assert (src / "a.txt").read_text() == "two, longer"  # (this PC's files are never touched)


def test_backup_says_when_the_account_is_gone(kept, tmp_path):
    src = tmp_path / "s"
    src.mkdir()
    acc = cloudnet.make_account("ftp", "127.0.0.1", "me", "pw", 1, secure=False)
    b = kept.add(src, acc, [], "week")
    r = kept.run(b)
    assert "error" in r and kept.load()[0].result.startswith("error:")
    assert kept.load()[0].last == 0 and not kept.due(kept.load()[0])  # (tried again an hour later, not at once)
    kept.remove(b.id)
    assert kept.load() == []


def test_backup_never_into_itself(kept, tmp_path):
    src = tmp_path / "cloudfolder"
    (src / "inner").mkdir(parents=True)
    acc = cloud.Account("folder", "", extra={"local": str(src / "inner")})
    with pytest.raises(cloud.CloudError):
        kept.add(src, acc, [], "day")


# ----------------------------------------------------------------------------- on the Cloud page

def _page(qtbot, tmp_path, monkeypatch):
    import types
    from corefold import cloudpage, theme
    monkeypatch.setattr(cloud, "ACCOUNTS_FILE", tmp_path / "accounts.dat")
    monkeypatch.setattr(cloud, "FOLDER", tmp_path)
    monkeypatch.setattr("corefold.cloudlocal.find", lambda *a, **k: [])
    monkeypatch.setattr("corefold.cloudbackup.BACKUPS_FILE", tmp_path / "backups.json")
    page = cloudpage.CloudPage(types.SimpleNamespace(theme=theme.build_theme("light", "core")),
                               __import__("PySide6.QtWidgets", fromlist=["QHBoxLayout"]).QHBoxLayout())
    qtbot.addWidget(page)
    return page


def test_add_a_server_from_the_page(qtbot, tmp_path, monkeypatch, ftp):
    from corefold import clouddialogs, sheet
    page = _page(qtbot, tmp_path, monkeypatch)
    page._fill_add_menu()
    texts = [a.text() for a in page.add_menu.actions()]
    assert "A server of your own (NAS, WebDAV, SFTP, FTP)…" in texts and "Backups…" in texts
    acc, root = ftp
    cfg = acc.extra["server"]

    def answer(dlg):
        assert isinstance(dlg, clouddialogs.ServerDialog)
        dlg.kind.setCurrentIndex(dlg.kind.findData("ftp"))
        dlg.address.setText(cfg["host"])
        dlg.port.setValue(cfg["port"])
        dlg.user.setText("me")
        dlg.password.setText("secret")
        dlg.secure.setChecked(False)
        assert dlg.ok.isEnabled()
        return 1
    monkeypatch.setattr(sheet, "run", answer)
    page.add_server()
    qtbot.waitUntil(lambda: page.job is None, timeout=15000)
    kept = cloud.load_accounts()
    assert len(kept) == 1 and kept[0].service == "ftp" and kept[0].refresh == "secret", page.status.text()
    assert any(a.key == kept[0].key for a in page.accounts)
    assert page.account_menu(kept[0]).actions()[-1].text() == "Remove from the list"


def test_add_a_server_with_a_wrong_password_says_so(qtbot, tmp_path, monkeypatch, ftp):
    from corefold import sheet
    page = _page(qtbot, tmp_path, monkeypatch)
    cfg = ftp[0].extra["server"]

    def answer(dlg):
        dlg.kind.setCurrentIndex(dlg.kind.findData("ftp"))
        dlg.address.setText(cfg["host"])
        dlg.port.setValue(cfg["port"])
        dlg.user.setText("me")
        dlg.password.setText("wrong")
        dlg.secure.setChecked(False)
        return 1
    monkeypatch.setattr(sheet, "run", answer)
    page.add_server()
    qtbot.waitUntil(lambda: page.job is None, timeout=15000)
    assert page.status.text() == "The user name or password isn't right."
    assert cloud.load_accounts() == []


def test_back_up_from_the_page(qtbot, tmp_path, monkeypatch, ftp):
    from corefold import cloudbackup, clouddialogs, sheet
    page = _page(qtbot, tmp_path, monkeypatch)
    acc, root = ftp
    cloud.remember_account(acc)
    page.load_accounts(acc.key)
    page.account = acc
    page.trail = []
    src = tmp_path / "Pictures"
    src.mkdir()
    (src / "p.jpg").write_bytes(b"jpg" * 100)

    def answer(dlg):
        if isinstance(dlg, clouddialogs.BackupDialog):
            dlg.source.setText(str(src))
            dlg.every.setCurrentIndex(dlg.every.findData("week"))
            assert dlg.ok.isEnabled()
            return 1
        return 0
    monkeypatch.setattr(sheet, "run", answer)
    page.add_backup()
    qtbot.waitUntil(lambda: page.job is None and not page.queue, timeout=15000)
    assert (root / "p.jpg").read_bytes() == b"jpg" * 100, page.status.text()
    assert page.status.text().startswith("Backed up Pictures")
    b = cloudbackup.load()[0]
    assert b.every == "week" and b.result == "ok"
    dlg = clouddialogs.BackupsDialog(page.windowIcon(), False, cloudbackup.load(), {acc.key: "FTP"})
    assert dlg.list.count() == 1 and "Pictures" in dlg.list.item(0).text()
    dlg.list.setCurrentRow(0)
    dlg.drop.click()
    assert dlg.choice == ("remove", b.id)
