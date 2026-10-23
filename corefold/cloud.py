"""Corefold Cloud: OneDrive and Dropbox in one window (Google Drive later).

Signing in happens on Microsoft's and Dropbox's own pages, in your browser: Corefold never sees a
password. What it gets back is a key that only lets it reach your files; it's kept on this PC,
locked with Windows' own encryption (only your Windows account can unlock it), and "Sign out"
deletes it. Files go straight between this PC and the service: nothing passes through anyone else.

Everything here talks to the internet, so it runs off the window's thread."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import http.server
import json
import logging
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .cloudkeys import DROPBOX_APP_KEY, ONEDRIVE_CLIENT_ID
from .settings import LOG_DIR

log = logging.getLogger("corefold")

FOLDER = LOG_DIR / "Cloud"
ACCOUNTS_FILE = FOLDER / "accounts.dat"
TIMEOUT = 30
CHUNK = 10 * 320 * 1024        # 3.2 MB pieces for big uploads (OneDrive wants multiples of 320 KB)
SMALL = 4 * 1024 * 1024        # smaller files go up in one request

# (the apps Corefold signs in as: see cloudkeys.py)
DROPBOX_PORT = 53682           # Dropbox needs the exact address registered: http://localhost:53682/

SERVICES = {"onedrive": "OneDrive", "dropbox": "Dropbox"}


class CloudError(Exception):
    """Something the person should see, in plain words."""


class SignInNeeded(CloudError):
    pass


@dataclass
class Entry:
    name: str
    folder: bool
    id: str                  # OneDrive item id, or Dropbox path (lowercase)
    path: str = ""           # shown path ("/Photos/2024")
    size: int = 0
    modified: float = 0.0
    parent: str = ""


@dataclass
class Account:
    service: str             # onedrive or dropbox
    email: str
    name: str = ""
    refresh: str = ""
    access: str = ""
    expires: float = 0.0
    used: int = 0
    total: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.service}:{self.email.lower()}"

    @property
    def title(self) -> str:
        return f"{SERVICES.get(self.service, self.service)} · {self.email}"


# ----------------------------------------------------------------------------- keeping the keys safe

class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def protect(data: bytes) -> bytes:
    """Locks data so only this Windows account can unlock it (Windows' DPAPI)."""
    if os.name != "nt":
        return b"plain:" + data
    src = _BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out = _BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), "Corefold Cloud", None, None, None, 0x1,
                                                  ctypes.byref(out)):
        raise OSError("couldn't lock the cloud keys")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def unprotect(data: bytes) -> bytes:
    if data.startswith(b"plain:"):
        return data[6:] if os.name != "nt" else b""
    if os.name != "nt":
        return b""
    src = _BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out = _BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0x1,
                                                    ctypes.byref(out)):
        raise OSError("couldn't unlock the cloud keys")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


_lock = threading.RLock()


def load_accounts() -> list[Account]:
    with _lock:
        try:
            raw = unprotect(ACCOUNTS_FILE.read_bytes())
            data = json.loads(raw.decode("utf-8")) if raw else []
        except (OSError, ValueError):
            return []
        out = []
        for d in data if isinstance(data, list) else []:
            try:
                out.append(Account(**{k: v for k, v in d.items() if k in Account.__dataclass_fields__}))
            except TypeError:
                continue
        return out


def save_accounts(accounts: list[Account]):
    with _lock:
        FOLDER.mkdir(parents=True, exist_ok=True)
        data = json.dumps([a.__dict__ for a in accounts]).encode("utf-8")
        tmp = ACCOUNTS_FILE.with_suffix(".tmp")
        tmp.write_bytes(protect(data))
        os.replace(tmp, ACCOUNTS_FILE)


def forget_account(key: str):
    with _lock:
        save_accounts([a for a in load_accounts() if a.key != key])


def remember_account(acc: Account):
    with _lock:
        accounts = [a for a in load_accounts() if a.key != acc.key]
        accounts.append(acc)
        save_accounts(accounts)


# ----------------------------------------------------------------------------- the internet

def _request(method: str, url: str, token: str = "", data=None, headers: dict | None = None,
             timeout: float = TIMEOUT, raw: bool = False):
    h = dict(headers or {})
    body = data
    if isinstance(data, (dict, list)):
        body = json.dumps(data).encode("utf-8")
        h.setdefault("Content-Type", "application/json")
    elif isinstance(data, str):
        body = data.encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    if token:  # (not passed on when a download is sent to another address)
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:600]
        except Exception:
            pass
        if e.code == 401:
            raise SignInNeeded(detail or "signed out") from e
        raise CloudError(_explain(e.code, detail)) from e
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise CloudError("no internet connection, or the service didn't answer") from e
    if raw:
        return resp
    with resp:
        payload = resp.read()
    if not payload:
        return {}
    try:
        return json.loads(payload.decode("utf-8"))
    except ValueError:
        return {}


def _explain(code: int, detail: str) -> str:
    text = detail
    try:
        d = json.loads(detail)
        err = d.get("error")
        if isinstance(err, dict):
            text = err.get("message") or err.get(".tag") or detail
        elif isinstance(err, str):
            text = d.get("error_description") or d.get("error_summary") or err
        elif d.get("error_summary"):
            text = d["error_summary"]
    except ValueError:
        pass
    if code == 507 or "insufficient_space" in detail or "quotaLimitReached" in detail:
        return "there's no space left in this account"
    if code == 404 or "not_found" in detail:
        return "it isn't there any more"
    if code == 429:
        return "the service asks to wait a moment, try again soon"
    return f"{code}: {text}".strip()


# ----------------------------------------------------------------------------- signing in

def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class _Catch(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" not in q and "error" not in q:
            self.send_response(404)
            self.end_headers()
            return
        self.server.answer = {k: v[0] for k, v in q.items()}
        page = ("<!doctype html><meta charset=utf-8><title>Corefold</title>"
                "<body style='font-family:Segoe UI,sans-serif;background:#111;color:#eee;display:grid;"
                "place-items:center;height:90vh'><div style='text-align:center'><h2>"
                + ("You're signed in" if "code" in q else "Signing in was cancelled")
                + "</h2><p>You can close this tab and go back to Corefold.</p></div>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *a):
        pass


def _listen(port: int = 0) -> http.server.HTTPServer:
    server = http.server.HTTPServer(("127.0.0.1", port), _Catch)
    server.answer = None
    server.timeout = 1
    return server


def sign_in(service: str, open_page, cancel: threading.Event | None = None, wait_s: float = 300) -> Account:
    """Opens the service's own sign-in page (open_page(url) opens the browser) and waits for it to
    send the person back. Returns the new account."""
    if service == "onedrive":
        if not ONEDRIVE_CLIENT_ID:
            raise CloudError("OneDrive isn't set up in this copy of Corefold yet")
        server = _listen(0)
        redirect = f"http://localhost:{server.server_address[1]}/"
    elif service == "dropbox":
        if not DROPBOX_APP_KEY:
            raise CloudError("Dropbox isn't set up in this copy of Corefold yet")
        try:
            server = _listen(DROPBOX_PORT)
        except OSError as e:
            raise CloudError("another program is using the port Dropbox signs in through; close it and try again") \
                from e
        redirect = f"http://localhost:{DROPBOX_PORT}/"
    else:
        raise CloudError("unknown service")
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    if service == "onedrive":
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?" + urllib.parse.urlencode({
            "client_id": ONEDRIVE_CLIENT_ID, "response_type": "code", "redirect_uri": redirect,
            "scope": "offline_access Files.ReadWrite User.Read", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": state, "prompt": "select_account"})
    else:
        url = "https://www.dropbox.com/oauth2/authorize?" + urllib.parse.urlencode({
            "client_id": DROPBOX_APP_KEY, "response_type": "code", "redirect_uri": redirect,
            "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
            "token_access_type": "offline", "force_reapprove": "false"})
    open_page(url)
    started = time.monotonic()
    try:
        while server.answer is None:
            if cancel is not None and cancel.is_set():
                raise CloudError("cancelled")
            if time.monotonic() - started > wait_s:
                raise CloudError("signing in took too long; try again")
            server.handle_request()
    finally:
        server.server_close()
    answer = server.answer
    if answer.get("state") != state:
        raise CloudError("the answer from the sign-in page didn't match; try again")
    if "code" not in answer:
        raise CloudError("signing in was cancelled")
    if service == "onedrive":
        tok = _request("POST", "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                       data=urllib.parse.urlencode({
                           "client_id": ONEDRIVE_CLIENT_ID, "grant_type": "authorization_code",
                           "code": answer["code"], "redirect_uri": redirect, "code_verifier": verifier,
                           "scope": "offline_access Files.ReadWrite User.Read"}),
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    else:
        tok = _request("POST", "https://api.dropboxapi.com/oauth2/token",
                       data=urllib.parse.urlencode({
                           "client_id": DROPBOX_APP_KEY, "grant_type": "authorization_code",
                           "code": answer["code"], "redirect_uri": redirect, "code_verifier": verifier}),
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    acc = Account(service, "", refresh=tok.get("refresh_token", ""), access=tok.get("access_token", ""),
                  expires=time.time() + float(tok.get("expires_in", 3600)) - 60)
    if not acc.access:
        raise CloudError("the service didn't let Corefold in")
    client(acc).fill_profile()
    remember_account(acc)
    return acc


# ----------------------------------------------------------------------------- the services

class Client:
    def __init__(self, account: Account):
        self.acc = account

    def token(self) -> str:
        acc = self.acc
        if acc.access and time.time() < acc.expires:
            return acc.access
        if not acc.refresh:
            raise SignInNeeded("signed out")
        try:
            tok = self._refresh()
        except CloudError as e:
            raise SignInNeeded(str(e)) from e
        acc.access = tok["access_token"]
        acc.refresh = tok.get("refresh_token") or acc.refresh
        acc.expires = time.time() + float(tok.get("expires_in", 3600)) - 60
        remember_account(acc)
        return acc.access

    def _refresh(self) -> dict:
        raise NotImplementedError

    def call(self, method, url, **kw):
        try:
            return _request(method, url, self.token(), **kw)
        except SignInNeeded:
            self.acc.expires = 0  # the key ran out early: one more try with a fresh one
            return _request(method, url, self.token(), **kw)

    # what every service does
    def fill_profile(self): ...
    def list(self, folder: Entry | None) -> list[Entry]: ...
    def download(self, entry: Entry, target: Path, progress=None, cancel=None): ...
    def upload(self, src: Path, folder: Entry | None, progress=None, cancel=None) -> Entry: ...
    def new_folder(self, name: str, folder: Entry | None) -> Entry: ...
    def rename(self, entry: Entry, name: str) -> Entry: ...
    def delete(self, entry: Entry): ...
    def search(self, text: str) -> list[Entry]: ...


def _iso(text: str) -> float:
    if not text:
        return 0.0
    try:
        from datetime import datetime
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _stream_to(resp, target: Path, size: int, progress, cancel):
    tmp = target.with_name(target.name + ".corefold-part")
    done = 0
    try:
        with resp, open(tmp, "wb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    raise CloudError("cancelled")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, size)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


class OneDrive(Client):
    API = "https://graph.microsoft.com/v1.0"
    SELECT = "$select=id,name,size,lastModifiedDateTime,folder,file,parentReference"

    def _refresh(self) -> dict:
        return _request("POST", "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                        data=urllib.parse.urlencode({"client_id": ONEDRIVE_CLIENT_ID, "grant_type": "refresh_token",
                                                     "refresh_token": self.acc.refresh,
                                                     "scope": "offline_access Files.ReadWrite User.Read"}),
                        headers={"Content-Type": "application/x-www-form-urlencoded"})

    def fill_profile(self):
        me = self.call("GET", f"{self.API}/me?$select=displayName,mail,userPrincipalName")
        self.acc.email = me.get("mail") or me.get("userPrincipalName") or "OneDrive"
        self.acc.name = me.get("displayName", "")
        try:
            drive = self.call("GET", f"{self.API}/me/drive?$select=quota")
            q = drive.get("quota", {})
            self.acc.used, self.acc.total = int(q.get("used", 0)), int(q.get("total", 0))
        except CloudError:
            pass

    def _entry(self, d: dict) -> Entry:
        parent = d.get("parentReference", {}) or {}
        return Entry(d.get("name", ""), "folder" in d, d.get("id", ""), (parent.get("path", "") or "")
                     .split("root:", 1)[-1] + "/" + d.get("name", ""), int(d.get("size", 0) or 0),
                     _iso(d.get("lastModifiedDateTime", "")), parent.get("id", ""))

    def list(self, folder):
        where = f"items/{folder.id}" if folder is not None else "root"
        url = f"{self.API}/me/drive/{where}/children?{self.SELECT}&$top=500"
        out = []
        while url:
            page = self.call("GET", url)
            out += [self._entry(d) for d in page.get("value", [])]
            url = page.get("@odata.nextLink", "")
        return out

    def download(self, entry, target, progress=None, cancel=None):
        resp = _request("GET", f"{self.API}/me/drive/items/{entry.id}/content", self.token(), raw=True, timeout=120)
        _stream_to(resp, target, entry.size, progress, cancel)

    def upload(self, src, folder, progress=None, cancel=None):
        parent = f"items/{folder.id}" if folder is not None else "root"
        name = urllib.parse.quote(src.name)
        size = src.stat().st_size
        if size <= SMALL:
            d = self.call("PUT", f"{self.API}/me/drive/{parent}:/{name}:/content?@microsoft.graph.conflictBehavior=rename",
                          data=src.read_bytes(), headers={"Content-Type": "application/octet-stream"}, timeout=120)
            if progress is not None:
                progress(size, size)
            return self._entry(d)
        session = self.call("POST", f"{self.API}/me/drive/{parent}:/{name}:/createUploadSession",
                            data={"item": {"@microsoft.graph.conflictBehavior": "rename"}})
        url = session["uploadUrl"]
        done = 0
        d: dict = {}
        with open(src, "rb") as f:
            while done < size:
                if cancel is not None and cancel.is_set():
                    try:
                        _request("DELETE", url)
                    except CloudError:
                        pass
                    raise CloudError("cancelled")
                piece = f.read(CHUNK)
                end = done + len(piece) - 1
                d = _request("PUT", url, data=piece, timeout=180, headers={
                    "Content-Length": str(len(piece)), "Content-Range": f"bytes {done}-{end}/{size}"})
                done = end + 1
                if progress is not None:
                    progress(done, size)
        return self._entry(d) if d.get("id") else Entry(src.name, False, "", size=size)

    def new_folder(self, name, folder):
        parent = f"items/{folder.id}" if folder is not None else "root"
        d = self.call("POST", f"{self.API}/me/drive/{parent}/children",
                      data={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "rename"})
        return self._entry(d)

    def rename(self, entry, name):
        return self._entry(self.call("PATCH", f"{self.API}/me/drive/items/{entry.id}", data={"name": name}))

    def delete(self, entry):
        self.call("DELETE", f"{self.API}/me/drive/items/{entry.id}")

    def search(self, text):
        q = urllib.parse.quote(text.replace("'", "''"))
        page = self.call("GET", f"{self.API}/me/drive/root/search(q='{q}')?{self.SELECT}&$top=200")
        return [self._entry(d) for d in page.get("value", [])]


class Dropbox(Client):
    API = "https://api.dropboxapi.com/2"
    CONTENT = "https://content.dropboxapi.com/2"

    def _refresh(self) -> dict:
        return _request("POST", "https://api.dropboxapi.com/oauth2/token",
                        data=urllib.parse.urlencode({"client_id": DROPBOX_APP_KEY, "grant_type": "refresh_token",
                                                     "refresh_token": self.acc.refresh}),
                        headers={"Content-Type": "application/x-www-form-urlencoded"})

    def fill_profile(self):
        me = self.call("POST", f"{self.API}/users/get_current_account", data="null",
                       headers={"Content-Type": "application/json"})
        self.acc.email = me.get("email") or "Dropbox"
        self.acc.name = (me.get("name") or {}).get("display_name", "")
        try:
            u = self.call("POST", f"{self.API}/users/get_space_usage", data="null",
                          headers={"Content-Type": "application/json"})
            self.acc.used = int(u.get("used", 0))
            self.acc.total = int((u.get("allocation") or {}).get("allocated", 0))
        except CloudError:
            pass

    @staticmethod
    def _entry(d: dict) -> Entry:
        folder = d.get(".tag") == "folder"
        path = d.get("path_display", "")
        return Entry(d.get("name", ""), folder, d.get("path_lower", path.lower()), path, int(d.get("size", 0) or 0),
                     _iso(d.get("server_modified", "")), path.rsplit("/", 1)[0].lower())

    def list(self, folder):
        page = self.call("POST", f"{self.API}/files/list_folder",
                         data={"path": folder.id if folder is not None else "", "limit": 2000})
        out = [self._entry(d) for d in page.get("entries", [])]
        while page.get("has_more"):
            page = self.call("POST", f"{self.API}/files/list_folder/continue", data={"cursor": page["cursor"]})
            out += [self._entry(d) for d in page.get("entries", [])]
        return [e for e in out if e.name]

    def download(self, entry, target, progress=None, cancel=None):
        resp = _request("POST", f"{self.CONTENT}/files/download", self.token(), raw=True, timeout=120,
                        headers={"Dropbox-API-Arg": json.dumps({"path": entry.id}, ensure_ascii=True)})
        _stream_to(resp, target, entry.size, progress, cancel)

    def upload(self, src, folder, progress=None, cancel=None):
        base = folder.id if folder is not None else ""
        dest = f"{base}/{src.name}"
        size = src.stat().st_size
        arg = {"path": dest, "mode": "add", "autorename": True}
        if size <= SMALL:
            d = self.call("POST", f"{self.CONTENT}/files/upload", data=src.read_bytes(), timeout=120,
                          headers={"Content-Type": "application/octet-stream",
                                   "Dropbox-API-Arg": json.dumps(arg, ensure_ascii=True)})
            if progress is not None:
                progress(size, size)
            return self._entry({**d, ".tag": "file"})
        with open(src, "rb") as f:
            first = f.read(CHUNK)
            s = self.call("POST", f"{self.CONTENT}/files/upload_session/start", data=first, timeout=180,
                          headers={"Content-Type": "application/octet-stream",
                                   "Dropbox-API-Arg": json.dumps({"close": False})})
            sid, done = s["session_id"], len(first)
            if progress is not None:
                progress(done, size)
            while done < size:
                if cancel is not None and cancel.is_set():
                    raise CloudError("cancelled")
                piece = f.read(CHUNK)
                last = done + len(piece) >= size
                if last:
                    d = self.call("POST", f"{self.CONTENT}/files/upload_session/finish", data=piece, timeout=180,
                                  headers={"Content-Type": "application/octet-stream", "Dropbox-API-Arg": json.dumps(
                                      {"cursor": {"session_id": sid, "offset": done}, "commit": arg},
                                      ensure_ascii=True)})
                else:
                    self.call("POST", f"{self.CONTENT}/files/upload_session/append_v2", data=piece, timeout=180,
                              headers={"Content-Type": "application/octet-stream", "Dropbox-API-Arg": json.dumps(
                                  {"cursor": {"session_id": sid, "offset": done}, "close": False})})
                done += len(piece)
                if progress is not None:
                    progress(done, size)
        return self._entry({**d, ".tag": "file"})

    def new_folder(self, name, folder):
        base = folder.id if folder is not None else ""
        d = self.call("POST", f"{self.API}/files/create_folder_v2", data={"path": f"{base}/{name}", "autorename": True})
        return self._entry({**d.get("metadata", {}), ".tag": "folder"})

    def rename(self, entry, name):
        parent = entry.id.rsplit("/", 1)[0]
        d = self.call("POST", f"{self.API}/files/move_v2", data={"from_path": entry.id, "to_path": f"{parent}/{name}",
                                                                  "autorename": True})
        return self._entry(d.get("metadata", {}))

    def delete(self, entry):
        self.call("POST", f"{self.API}/files/delete_v2", data={"path": entry.id})

    def search(self, text):
        d = self.call("POST", f"{self.API}/files/search_v2", data={"query": text, "options": {"max_results": 200}})
        out = []
        for m in d.get("matches", []):
            meta = (m.get("metadata") or {}).get("metadata") or {}
            if meta:
                out.append(self._entry(meta))
        return out


def client(acc: Account) -> Client:
    return OneDrive(acc) if acc.service == "onedrive" else Dropbox(acc)


def available(service: str) -> bool:
    return bool(ONEDRIVE_CLIENT_ID if service == "onedrive" else DROPBOX_APP_KEY if service == "dropbox" else "")
