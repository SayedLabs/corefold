"""Corefold Cloud: your own servers. A NAS, a Nextcloud, a web host or any server that speaks WebDAV, FTP or SFTP,
added with its address, a user name and a password (no registration anywhere). They're accounts like the online
services' (cloud.Account, kept encrypted with the rest), and work through the same Client calls, so everything
Cloud does (open, save, send, copy between accounts, move, rename, delete, search, back up) works with them too.

Nothing goes unencrypted over the internet: plain http and plain FTP are only allowed to an address on this
home or office network. An SFTP server's key is kept the first time, and a server that later shows another key is
refused (someone may be pretending to be it). No Qt here."""
from __future__ import annotations

import base64
import ftplib
import ipaddress
import logging
import posixpath
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from email.utils import parsedate_to_datetime

from .cloud import MAX_DEPTH, NOT_SENT_WHOLE, TOO_DEEP, Account, Client, CloudError, Entry, SignInNeeded

log = logging.getLogger("corefold")

KINDS = {"webdav": "WebDAV", "ftp": "FTP", "sftp": "SFTP"}
PORTS = {"webdav": 443, "ftp": 21, "sftp": 22}
TIMEOUT = 30
MAX_LISTING = 64 << 20           # (a folder's listing bigger than this isn't read: a server gone wrong)
SEARCH_LIMIT = 300
SEARCH_SECONDS = 60
NOT_PRIVATE = "plain http and FTP are only allowed on your own network; turn on encryption for this server"
KEY_CHANGED = "this server's key is not the one it had before; someone may be pretending to be it"
WRONG_LOGIN = "the user name or password isn't right"
NO_SERVER = "the server didn't answer: check the address and the port"
NOT_ENCRYPTED_ON = "the server sent this on to an address that isn't encrypted, so Corefold stopped"


# ----------------------------------------------------------------------------- adding a server

def is_private(host: str) -> bool:
    """An address on this home or office network (or this PC), where unencrypted is acceptable."""
    host = host.strip("[]").lower()
    if host in ("localhost",) or host.endswith((".local", ".lan", ".home.arpa")) or "." not in host and ":" not in host:
        return True  # (a bare name, like "nas" or "diskstation", is one on this network)
    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            ips = [ipaddress.ip_address(a[4][0].split("%")[0]) for a in socket.getaddrinfo(host, None)]
        except (OSError, ValueError):
            return False
    return bool(ips) and all(ip.is_private or ip.is_loopback or ip.is_link_local for ip in ips)


def make_account(kind: str, host: str, user: str, password: str, port: int = 0, folder: str = "/",
                 secure: bool = True) -> Account:
    """A server account from what the person typed (not saved: cloud.remember_account does that once it works).
    host can be a whole address ("https://cloud.example.com/remote.php/dav/files/me"), which says the kind,
    port and folder too."""
    host = host.strip()
    if "://" in host:
        u = urllib.parse.urlsplit(host)
        scheme = u.scheme.lower()
        if scheme in ("http", "https", "dav", "davs", "webdav", "webdavs"):
            kind, secure = "webdav", scheme in ("https", "davs", "webdavs")
        elif scheme in ("ftp", "ftps", "ftpes"):
            kind, secure = "ftp", scheme != "ftp"
        elif scheme == "sftp":
            kind, secure = "sftp", True
        host = u.hostname or ""
        port = u.port or port
        if u.path and u.path != "/":
            folder = urllib.parse.unquote(u.path)
        user = user or urllib.parse.unquote(u.username or "")
        password = password or urllib.parse.unquote(u.password or "")
    if kind not in KINDS:
        raise CloudError("pick WebDAV, FTP or SFTP")
    if not host or any(c in host for c in "/\\@ ") or len(host) > 253:
        raise CloudError("that isn't a server address")
    if kind == "sftp":
        secure = True
    port = int(port or (PORTS[kind] if secure or kind != "webdav" else 80))
    if not 0 < port < 65536:
        raise CloudError("that isn't a server address")
    folder = "/" + folder.strip().strip("/") if folder.strip().strip("/") else "/"
    if not secure and not is_private(host):
        raise CloudError(NOT_PRIVATE)
    shown = f"{user}@{host}" if user else host
    if port != (PORTS[kind] if secure or kind != "webdav" else 80):
        shown += f":{port}"
    if folder != "/":
        shown += folder
    return Account(kind, shown, name=f"{KINDS[kind]} · {host}", refresh=password,
                   extra={"server": {"host": host, "port": port, "user": user, "folder": folder, "secure": secure}})


def is_server(acc: Account) -> bool:
    return acc.service in KINDS and isinstance((acc.extra or {}).get("server"), dict)


def _cfg(acc: Account) -> dict:
    cfg = (acc.extra or {}).get("server")
    if not isinstance(cfg, dict) or not cfg.get("host"):
        raise SignInNeeded("this server needs to be added again")
    return cfg


def _join(folder: Entry | None, base: str, name: str) -> str:
    parent = folder.id if folder is not None else base
    if "/" in name or name in ("", ".", ".."):
        raise CloudError("that name isn't allowed")
    return posixpath.join(parent.rstrip("/") or "/", name)


def _free_name(names: set[str], name: str) -> str:
    """name, or "name (2).ext"… when the folder has one already (an upload never replaces anything)."""
    if name.lower() not in names:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    n = 2
    while True:
        cand = f"{stem} ({n})" + (f".{ext}" if ext else "")
        if cand.lower() not in names:
            return cand
        n += 1


class _Server(Client):
    """What the three kinds share: the password is the account's key (there's nothing to refresh)."""

    def __init__(self, account: Account):
        super().__init__(account)
        self.cfg = _cfg(account)
        self.base = self.cfg.get("folder") or "/"
        self._lock = threading.RLock()

    def token(self) -> str:
        return self.acc.refresh

    def _refresh(self):
        raise SignInNeeded("signed out")

    def fill_profile(self):
        self.list(None)  # (proves the address and the password work)

    def search(self, text, cancel=None):
        """Every file and folder whose name has text in it (looked for folder by folder: these servers have no
        search of their own), up to SEARCH_LIMIT of them or SEARCH_SECONDS."""
        want = text.casefold().strip()
        out, waiting, start = [], [None], time.monotonic()
        while waiting and len(out) < SEARCH_LIMIT and time.monotonic() - start < SEARCH_SECONDS:
            if cancel is not None and cancel.is_set():
                break
            try:
                inside = self.list(waiting.pop(0))
            except CloudError:
                continue
            for e in inside:
                if want in e.name.casefold():
                    out.append(e)
                if e.folder:
                    waiting.append(e)
        return out[:SEARCH_LIMIT]

    def _names(self, folder: Entry | None) -> set[str]:
        return {e.name.lower() for e in self.list(folder)}


# ----------------------------------------------------------------------------- WebDAV

DAV = "{DAV:}"
PROPFIND = (b'<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/>'
            b'<d:getcontentlength/><d:getlastmodified/><d:quota-used-bytes/><d:quota-available-bytes/>'
            b'</d:prop></d:propfind>')


class _Reader:
    """A file sent as a request's body, telling how far it got."""

    def __init__(self, f, size, progress, cancel):
        self.f, self.size, self.progress, self.cancel, self.done = f, size, progress, cancel, 0

    def read(self, n=-1):
        if self.cancel is not None and self.cancel.is_set():
            raise CloudError("cancelled")
        chunk = self.f.read(256 * 1024 if n is None or n < 0 else min(n, 256 * 1024))
        self.done += len(chunk)
        if self.progress is not None and chunk:
            self.progress(self.done, self.size)
        return chunk

    def __len__(self):
        return self.size


class _StayEncrypted(urllib.request.HTTPRedirectHandler):
    """A download sent on elsewhere (servers do that for big files) may go to another address, but never from an
    encrypted one to an unencrypted one, where anyone on the way could read it or change it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.full_url.lower().startswith("https:") and not newurl.lower().startswith("https:"):
            raise CloudError(NOT_ENCRYPTED_ON)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class WebDav(_Server):
    def __init__(self, account):
        super().__init__(account)
        c = self.cfg
        scheme = "https" if c.get("secure", True) else "http"
        default = 443 if scheme == "https" else 80
        host = f"[{c['host']}]" if ":" in c["host"] else c["host"]
        self.origin = f"{scheme}://{host}" + ("" if int(c["port"]) == default else f":{c['port']}")
        if scheme == "http" and not is_private(c["host"]):
            raise CloudError(NOT_PRIVATE)

    def _url(self, path: str) -> str:
        return self.origin + urllib.parse.quote(path, safe="/")

    def _send(self, method: str, path: str, data=None, headers: dict | None = None, raw: bool = False,
              timeout: float = TIMEOUT):
        h = dict(headers or {})
        req = urllib.request.Request(self._url(path), data=data, method=method, headers=h)
        auth = base64.b64encode(f"{self.cfg.get('user', '')}:{self.acc.refresh}".encode("utf-8")).decode("ascii")
        req.add_unredirected_header("Authorization", f"Basic {auth}")  # (never passed on to another address)
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()),
                                             _StayEncrypted())
        try:
            resp = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            e.close()
            if e.code == 401:
                raise SignInNeeded(WRONG_LOGIN) from e
            if e.code == 404:
                raise CloudError("it isn't there any more") from e
            if e.code in (405, 409, 412) and method in ("MKCOL", "MOVE"):
                raise CloudError("something with that name is already there") from e
            if e.code == 403:
                raise CloudError("this account doesn't allow that") from e
            if e.code == 507:
                raise CloudError("there's no space left in this account") from e
            raise CloudError(f"{e.code}: {e.reason}") from e
        except ssl.SSLError as e:
            raise CloudError("the server's certificate isn't valid") from e
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLError):
                raise CloudError("the server's certificate isn't valid") from e
            raise CloudError(NO_SERVER) from e
        if raw:
            return resp
        with resp:
            return resp.read(MAX_LISTING)

    def _propfind(self, path: str, depth: str) -> list[tuple[str, dict]]:
        body = self._send("PROPFIND", path, PROPFIND, {"Depth": depth, "Content-Type": "application/xml"})
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            raise CloudError("this isn't a WebDAV server, or the folder is wrong") from e
        out = []
        for r in root.iter(DAV + "response"):
            href = r.findtext(DAV + "href") or ""
            props = {}
            for ps in r.iter(DAV + "propstat"):
                if "200" not in (ps.findtext(DAV + "status") or "200"):
                    continue
                for p in ps.iter(DAV + "prop"):
                    for child in p:
                        props[child.tag] = child
            out.append((urllib.parse.unquote(urllib.parse.urlsplit(href).path), props))
        return out

    def _entry(self, path: str, props: dict) -> Entry:
        rt = props.get(DAV + "resourcetype")
        folder = rt is not None and rt.find(DAV + "collection") is not None
        clean = path.rstrip("/") or "/"
        try:
            size = int((props.get(DAV + "getcontentlength").text or "0").strip()) \
                if props.get(DAV + "getcontentlength") is not None else 0
        except ValueError:
            size = 0
        mod = props.get(DAV + "getlastmodified")
        try:
            modified = parsedate_to_datetime(mod.text).timestamp() if mod is not None and mod.text else 0.0
        except (TypeError, ValueError):
            modified = 0.0
        shown = clean[len(self.base.rstrip("/")):] or "/"
        return Entry(posixpath.basename(clean), folder, clean, shown, 0 if folder else size, modified,
                     posixpath.dirname(clean), web=self._url(clean))

    def fill_profile(self):
        for _path, props in self._propfind(self.base, "0")[:1]:
            try:
                used = int((props[DAV + "quota-used-bytes"].text or "0"))
                free = int((props[DAV + "quota-available-bytes"].text or "0"))
                if used >= 0 and free >= 0:
                    self.acc.used, self.acc.total = used, used + free
            except (KeyError, ValueError, TypeError):
                pass

    def list(self, folder):
        path = folder.id if folder is not None else self.base
        me = path.rstrip("/") or "/"
        out = []
        for p, props in self._propfind(path.rstrip("/") + "/", "1"):
            if (p.rstrip("/") or "/") == me:
                continue
            e = self._entry(p, props)
            if e.name and e.name not in (".", ".."):
                out.append(e)
        return out

    def download(self, entry, target, progress=None, cancel=None):
        from .cloud import _stream_to
        resp = self._send("GET", entry.id, raw=True, timeout=120)
        _stream_to(resp, target, entry.size, progress, cancel, self.origin + "/")

    def upload(self, src, folder, progress=None, cancel=None):
        name = _free_name(self._names(folder), src.name)
        path = _join(folder, self.base, name)
        size = src.stat().st_size
        with open(src, "rb") as f:
            self._send("PUT", path, _Reader(f, size, progress, cancel),
                       {"Content-Type": "application/octet-stream", "Content-Length": str(size)}, timeout=600)
        got = next((e for e in self.list(folder) if e.id == path), None)
        if got is None or got.size != size:
            log.warning("cloud upload doesn't match what was sent (webdav)")
            try:
                self.delete(Entry(name, False, path))
            except CloudError:
                pass
            raise CloudError(NOT_SENT_WHOLE)
        return got

    def new_folder(self, name, folder):
        name = _free_name(self._names(folder), name)
        path = _join(folder, self.base, name)
        self._send("MKCOL", path)
        return Entry(name, True, path, path[len(self.base.rstrip("/")):] or "/", parent=posixpath.dirname(path))

    def rename(self, entry, name):
        path = posixpath.join(posixpath.dirname(entry.id), name)
        if "/" in name or name in ("", ".", ".."):
            raise CloudError("that name isn't allowed")
        self._send("MOVE", entry.id, headers={"Destination": self._url(path + ("/" if entry.folder else "")),
                                              "Overwrite": "F"})
        return Entry(name, entry.folder, path, path[len(self.base.rstrip("/")):] or "/", entry.size,
                     entry.modified, entry.parent)

    def delete(self, entry):
        if (entry.id.rstrip("/") or "/") == (self.base.rstrip("/") or "/"):
            raise CloudError("this account doesn't allow that")
        self._send("DELETE", entry.id + ("/" if entry.folder else ""))


# ----------------------------------------------------------------------------- FTP

class Ftp(_Server):
    def __init__(self, account):
        super().__init__(account)
        self._ftp: ftplib.FTP | None = None

    @contextmanager
    def _conn(self):
        """One connection, made when needed and used again (a new one when the old one dropped meanwhile)."""
        with self._lock:
            if self._ftp is not None:
                try:
                    self._ftp.voidcmd("NOOP")
                except (*ftplib.all_errors, EOFError):
                    self._close()
            if self._ftp is None:
                self._ftp = self._connect()
            try:
                yield self._ftp
            except (EOFError, ConnectionError, ftplib.error_temp, ftplib.error_reply, socket.timeout,
                    TimeoutError) as e:
                self._close()
                raise CloudError(NO_SERVER) from e
            except ssl.SSLError as e:
                self._close()
                raise CloudError(NO_SERVER) from e

    def _connect(self) -> ftplib.FTP:
        c = self.cfg
        secure = c.get("secure", True)
        if not secure and not is_private(c["host"]):
            raise CloudError(NOT_PRIVATE)
        ftp = ftplib.FTP_TLS(context=ssl.create_default_context(), timeout=TIMEOUT) if secure else \
            ftplib.FTP(timeout=TIMEOUT)
        ftp.encoding = "utf-8"
        try:
            ftp.connect(c["host"], int(c["port"]))
            if secure:
                ftp.auth()
            ftp.login(c.get("user") or "anonymous", self.acc.refresh)
            if secure:
                ftp.prot_p()
        except ftplib.error_perm as e:
            ftp.close()
            if str(e)[:3] in ("530", "430"):
                raise SignInNeeded(WRONG_LOGIN) from e
            if secure and str(e)[:3] in ("500", "502", "504", "534"):
                raise CloudError("this server doesn't offer encryption") from e
            raise CloudError(str(e)) from e
        except ssl.SSLError as e:
            ftp.close()
            raise CloudError("the server's certificate isn't valid") from e
        except (OSError, EOFError) as e:
            ftp.close()
            raise CloudError(NO_SERVER) from e
        return ftp

    def _close(self):
        if self._ftp is not None:
            try:
                self._ftp.close()
            except Exception:
                pass
            self._ftp = None

    @staticmethod
    def _perm(e: ftplib.error_perm) -> CloudError:
        code = str(e)[:3]
        if code == "550":
            return CloudError("it isn't there any more, or this account doesn't allow that")
        if code == "552":
            return CloudError("there's no space left in this account")
        if code == "553":
            return CloudError("that name isn't allowed")
        return CloudError(str(e))

    def _entry(self, path: str, name: str, folder: bool, size: int, modified: float) -> Entry:
        full = posixpath.join(path.rstrip("/") or "/", name)
        return Entry(name, folder, full, full[len(self.base.rstrip("/")):] or "/", 0 if folder else size, modified,
                     path)

    def list(self, folder):
        path = folder.id if folder is not None else self.base
        out = []
        with self._conn() as ftp:
            try:
                for name, facts in ftp.mlsd(path, ["type", "size", "modify"]):
                    kind = facts.get("type", "").lower()
                    if kind in ("cdir", "pdir") or name in (".", "..") or "/" in name:
                        continue
                    mod = facts.get("modify", "")
                    try:
                        modified = time.mktime(time.strptime(mod[:14], "%Y%m%d%H%M%S")) - time.timezone if mod else 0.0
                    except ValueError:
                        modified = 0.0
                    out.append(self._entry(path, name, kind == "dir", int(facts.get("size", 0) or 0), modified))
                return out
            except ftplib.error_perm as e:
                if str(e)[:3] not in ("500", "501", "502"):
                    raise self._perm(e) from e
            lines: list[str] = []  # (an old server without MLSD: its "ls -l" lines)
            try:
                ftp.retrlines(f"LIST -a {path}" if path else "LIST", lines.append)
            except ftplib.error_perm as e:
                raise self._perm(e) from e
        for line in lines:
            parts = line.split(None, 8)
            if len(parts) < 9 or parts[8] in (".", ".."):
                continue
            try:
                size = int(parts[4])
            except ValueError:
                size = 0
            name = parts[8].split(" -> ")[0] if parts[0].startswith("l") else parts[8]
            out.append(self._entry(path, name, parts[0].startswith("d"), size, 0.0))
        return out

    def download(self, entry, target, progress=None, cancel=None):
        from .core import mark_from_internet
        tmp = target.with_name(target.name + ".corefold-part")
        done = [0]
        try:
            with self._conn() as ftp, open(tmp, "wb") as f:
                def got(chunk):
                    if cancel is not None and cancel.is_set():
                        raise CloudError("cancelled")
                    f.write(chunk)
                    done[0] += len(chunk)
                    if progress is not None:
                        progress(done[0], entry.size)
                try:
                    ftp.retrbinary(f"RETR {entry.id}", got, 256 * 1024)
                except ftplib.error_perm as e:
                    raise self._perm(e) from e
                except CloudError:
                    self._close()  # (a transfer stopped half way leaves the connection unusable)
                    raise
            if entry.size and done[0] != entry.size:
                raise CloudError("the download was cut off; try again")
            tmp.rename(target)
            c = self.cfg
            mark_from_internet(target, f"ftp://{c['host']}/")
        finally:
            tmp.unlink(missing_ok=True)

    def upload(self, src, folder, progress=None, cancel=None):
        name = _free_name(self._names(folder), src.name)
        path = _join(folder, self.base, name)
        size = src.stat().st_size
        done = [0]

        def sent(chunk):
            done[0] += len(chunk)
            if progress is not None:
                progress(done[0], size)
            if cancel is not None and cancel.is_set():
                raise CloudError("cancelled")
        with self._conn() as ftp, open(src, "rb") as f:
            try:
                ftp.storbinary(f"STOR {path}", f, 256 * 1024, sent)
            except ftplib.error_perm as e:
                raise self._perm(e) from e
            except CloudError:
                self._close()
                with self._conn() as again:
                    try:
                        again.delete(path)
                    except ftplib.all_errors:
                        pass
                raise
            try:
                ftp.voidcmd("TYPE I")
                got = ftp.size(path)
            except ftplib.all_errors:
                got = None
        if got is not None and got != size:
            log.warning("cloud upload doesn't match what was sent (ftp)")
            try:
                self.delete(Entry(name, False, path))
            except CloudError:
                pass
            raise CloudError(NOT_SENT_WHOLE)
        return self._entry(posixpath.dirname(path), name, False, size, time.time())

    def new_folder(self, name, folder):
        name = _free_name(self._names(folder), name)
        path = _join(folder, self.base, name)
        with self._conn() as ftp:
            try:
                ftp.mkd(path)
            except ftplib.error_perm as e:
                raise self._perm(e) from e
        return self._entry(posixpath.dirname(path), name, True, 0, time.time())

    def rename(self, entry, name):
        if "/" in name or name in ("", ".", ".."):
            raise CloudError("that name isn't allowed")
        path = posixpath.join(posixpath.dirname(entry.id), name)
        if name.lower() in self._names(Entry("", True, posixpath.dirname(entry.id))) and name != entry.name:
            raise CloudError("something with that name is already there")
        with self._conn() as ftp:
            try:
                ftp.rename(entry.id, path)
            except ftplib.error_perm as e:
                raise self._perm(e) from e
        return self._entry(posixpath.dirname(path), name, entry.folder, entry.size, entry.modified)

    def delete(self, entry, _depth: int = 0):
        if (entry.id.rstrip("/") or "/") == (self.base.rstrip("/") or "/"):
            raise CloudError("this account doesn't allow that")
        if entry.folder:
            # A link to a folder is listed as a folder ("type=dir") by many servers: emptying it would delete what's
            # in the folder it leads to, somewhere else on the server. Removing it as a file first takes only the
            # link away (a real folder can't be removed that way), and only a real folder is gone into.
            with self._conn() as ftp:
                try:
                    ftp.delete(entry.id)
                    return
                except ftplib.error_perm:
                    pass
            if _depth > MAX_DEPTH:
                raise CloudError(TOO_DEEP)
            for e in self.list(entry):
                self.delete(e, _depth + 1)
        with self._conn() as ftp:
            try:
                ftp.rmd(entry.id) if entry.folder else ftp.delete(entry.id)
            except ftplib.error_perm as e:
                raise self._perm(e) from e


# ----------------------------------------------------------------------------- SFTP

def key_text(key) -> str:
    """A server key as kept in the account: its type and its SHA-256 fingerprint."""
    import hashlib
    return f"{key.get_name()} SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


class Sftp(_Server):
    def __init__(self, account):
        super().__init__(account)
        self._t = None
        self._s = None

    @contextmanager
    def _conn(self):
        import paramiko
        with self._lock:
            if self._t is not None and not self._t.is_active():
                self._close()
            if self._s is None:
                self._connect()
            try:
                yield self._s
            except CloudError:
                raise
            except (EOFError, ConnectionError, socket.timeout, TimeoutError, paramiko.SSHException) as e:
                self._close()
                raise CloudError(NO_SERVER) from e
            except OSError as e:
                raise self._os(e) from e

    @staticmethod
    def _os(e: OSError) -> CloudError:
        import errno
        if "size mismatch" in str(e):  # (paramiko's check after an upload: not all of it arrived)
            return CloudError(NOT_SENT_WHOLE)
        if isinstance(e, FileNotFoundError) or e.errno == errno.ENOENT:
            return CloudError("it isn't there any more")
        if isinstance(e, PermissionError) or e.errno == errno.EACCES:
            return CloudError("this account doesn't allow that")
        return CloudError(str(e) or "the server said no")

    def _connect(self):
        import paramiko
        c = self.cfg
        try:
            sock = socket.create_connection((c["host"], int(c["port"])), timeout=TIMEOUT)
        except OSError as e:
            raise CloudError(NO_SERVER) from e
        t = paramiko.Transport(sock)
        t.banner_timeout = TIMEOUT
        try:
            t.start_client(timeout=TIMEOUT)
            seen = key_text(t.get_remote_server_key())
            kept = c.get("key")
            if kept and kept != seen:
                log.warning("sftp: %s shows another key than before", c["host"])
                raise CloudError(KEY_CHANGED)
            t.auth_password(c.get("user", ""), self.acc.refresh)
            if not t.is_authenticated():
                raise SignInNeeded(WRONG_LOGIN)
        except paramiko.AuthenticationException as e:
            t.close()
            raise SignInNeeded(WRONG_LOGIN) from e
        except paramiko.SSHException as e:
            t.close()
            raise CloudError(NO_SERVER) from e
        except BaseException:
            t.close()
            raise
        if not kept:
            c["key"] = seen  # (kept with the account when it's saved: from now on only this key is believed)
        self._t, self._s = t, paramiko.SFTPClient.from_transport(t)
        self._s.get_channel().settimeout(TIMEOUT * 4)

    def _close(self):
        for x in (self._s, self._t):
            try:
                if x is not None:
                    x.close()
            except Exception:
                pass
        self._t = self._s = None

    def _entry(self, parent: str, a) -> Entry:
        import stat
        full = posixpath.join(parent.rstrip("/") or "/", a.filename)
        folder = stat.S_ISDIR(a.st_mode or 0)
        return Entry(a.filename, folder, full, full[len(self.base.rstrip("/")):] or "/",
                     0 if folder else int(a.st_size or 0), float(a.st_mtime or 0), parent)

    def list(self, folder):
        import stat
        path = folder.id if folder is not None else self.base
        with self._conn() as s:
            items = s.listdir_attr(path)
        return [self._entry(path, a) for a in items
                if a.filename not in (".", "..") and "/" not in a.filename and not stat.S_ISLNK(a.st_mode or 0)]

    def download(self, entry, target, progress=None, cancel=None):
        from .core import mark_from_internet
        tmp = target.with_name(target.name + ".corefold-part")
        done = 0
        try:
            with self._conn() as s, s.open(entry.id, "rb") as r, open(tmp, "wb") as f:
                r.prefetch(entry.size or None)
                while True:
                    if cancel is not None and cancel.is_set():
                        raise CloudError("cancelled")
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, entry.size)
            if entry.size and done != entry.size:
                raise CloudError("the download was cut off; try again")
            tmp.rename(target)
            mark_from_internet(target, f"sftp://{self.cfg['host']}/")
        finally:
            tmp.unlink(missing_ok=True)

    def upload(self, src, folder, progress=None, cancel=None):
        name = _free_name(self._names(folder), src.name)
        path = _join(folder, self.base, name)
        size = src.stat().st_size

        def sent(done, total):
            if progress is not None:
                progress(done, size)
            if cancel is not None and cancel.is_set():
                raise CloudError("cancelled")
        try:
            with self._conn() as s:
                a = s.put(str(src), path, callback=sent, confirm=True)
        except CloudError:
            try:
                with self._conn() as s:
                    s.remove(path)
            except (CloudError, OSError):
                pass
            raise
        a.filename = name
        return self._entry(posixpath.dirname(path), a)

    def new_folder(self, name, folder):
        name = _free_name(self._names(folder), name)
        path = _join(folder, self.base, name)
        with self._conn() as s:
            s.mkdir(path)
            a = s.stat(path)
        a.filename = name
        return self._entry(posixpath.dirname(path), a)

    def rename(self, entry, name):
        if "/" in name or name in ("", ".", ".."):
            raise CloudError("that name isn't allowed")
        path = posixpath.join(posixpath.dirname(entry.id), name)
        with self._conn() as s:
            try:
                s.stat(path)
                if name != entry.name:
                    raise CloudError("something with that name is already there")
            except FileNotFoundError:
                pass
            s.rename(entry.id, path)
        return Entry(name, entry.folder, path, path[len(self.base.rstrip("/")):] or "/", entry.size, entry.modified,
                     entry.parent)

    def delete(self, entry, _depth: int = 0):
        import stat
        if (entry.id.rstrip("/") or "/") == (self.base.rstrip("/") or "/"):
            raise CloudError("this account doesn't allow that")
        with self._conn() as s:
            mode = s.lstat(entry.id).st_mode or 0
        if not stat.S_ISDIR(mode):  # (a file, or a link: only the link goes, never what it leads to)
            with self._conn() as s:
                s.remove(entry.id)
            return
        if _depth > MAX_DEPTH:
            raise CloudError(TOO_DEEP)
        with self._conn() as s:
            inside = [a for a in s.listdir_attr(entry.id) if a.filename not in (".", "..") and "/" not in a.filename]
        for a in inside:  # (links inside too: listed nowhere else, they'd keep the folder from being removed)
            self.delete(self._entry(entry.id, a), _depth + 1)
        with self._conn() as s:
            s.rmdir(entry.id)


def client(acc: Account) -> Client:
    return {"webdav": WebDav, "ftp": Ftp, "sftp": Sftp}[acc.service](acc)


def check(acc: Account) -> Account:
    """Connects to a server just added, to see the address and password work (and keep an SFTP server's key)."""
    c = client(acc)
    try:
        c.list(None)
        try:
            c.fill_profile()
        except CloudError:
            pass
    finally:
        closer = getattr(c, "_close", None)
        if closer is not None:
            closer()
    return acc

