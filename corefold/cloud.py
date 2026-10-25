"""Corefold Cloud: OneDrive and Dropbox in one window, online (this file) or through the folders their own
apps keep on this PC (cloudlocal.py, which never goes online).

Signing in happens on Microsoft's and Dropbox's own pages, in your browser: Corefold never sees a
password. What it gets back is a key that only lets it reach your files; it's kept on this PC,
locked with Windows' own encryption (only your Windows account can unlock it), and "Sign out"
deletes it. Files go straight between this PC and the service: nothing passes through anyone else.

Everything here talks to the internet, so it runs off the window's thread."""
from __future__ import annotations

import base64
import ctypes
import hashlib
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
from typing import TYPE_CHECKING

from .cloudkeys import DROPBOX_APP_KEY, ONEDRIVE_CLIENT_ID
from .settings import LOG_DIR

if TYPE_CHECKING:
    import http.server

log = logging.getLogger("corefold")

FOLDER = LOG_DIR / "Cloud"
ACCOUNTS_FILE = FOLDER / "accounts.dat"
TIMEOUT = 30
CHUNK = 10 * 320 * 1024        # 3.2 MB pieces for big uploads (OneDrive wants multiples of 320 KB)
SMALL = 4 * 1024 * 1024        # smaller files go up in one request

# (the apps Corefold signs in as: see cloudkeys.py)
DROPBOX_PORT = 53682           # Dropbox needs the exact address registered: http://localhost:53682/

SERVICES = {"onedrive": "OneDrive", "dropbox": "Dropbox", "gdrive": "Google Drive", "icloud": "iCloud Drive"}
ONLINE = ("onedrive", "dropbox")   # the ones Corefold can sign in to by itself

# where Corefold signs in and talks to each service (a test points these at a pretend service)
MS_LOGIN = "https://login.microsoftonline.com/common/oauth2/v2.0"
MS_SCOPE = "offline_access Files.ReadWrite User.Read"
DROPBOX_AUTHORIZE = "https://www.dropbox.com/oauth2/authorize"
DROPBOX_OAUTH = "https://api.dropboxapi.com/oauth2/token"


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
    online: bool = False     # a file in a folder on this PC that is kept online only (opening it downloads it)
    hash: str = ""           # the service's own check of the file's content ("quickxor:…" or "dropbox:…")


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
    def local(self) -> str:
        """The folder the service's own app keeps on this PC, for an account found there (never saved)."""
        return self.extra.get("local", "") if isinstance(self.extra, dict) else ""

    @property
    def key(self) -> str:
        if self.local:
            return "local:" + os.path.normcase(self.local)
        return f"{self.service}:{self.email.lower()}"

    @property
    def title(self) -> str:
        if self.service not in SERVICES:  # (a folder added by hand that isn't any service's: its own name)
            return self.who
        name = SERVICES.get(self.service, self.service)
        return name if self.who in ("", name) else f"{name} · {self.who}"

    @property
    def who(self) -> str:
        """The email, or for a folder on this PC whose email isn't known, the folder's name."""
        if self.email or not self.local:
            return self.email
        p = Path(self.local)
        if not p.name:  # (a whole drive, like Google Drive's G:)
            return p.drive or str(p)
        return f"{p.name} ({p.drive})" if p.parent == p.parent.parent and p.drive else p.name


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
        data = json.dumps([a.__dict__ for a in accounts if not a.local]).encode("utf-8")
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

AGAIN = (429, 500, 502, 503, 504)   # worth one more try after a short wait


def _request(method: str, url: str, token: str = "", data=None, headers: dict | None = None,
             timeout: float = TIMEOUT, raw: bool = False, tries: int = 0):
    """One request. Reading (GET) is tried up to 3 times when the service is busy or the connection drops;
    other requests only when tries says so (sending a piece of an upload again is safe, making a folder twice
    isn't)."""
    tries = tries or (3 if method == "GET" else 1)
    for attempt in range(tries):
        try:
            return _request_once(method, url, token, data, headers, timeout, raw)
        except _Again as e:
            if attempt == tries - 1:
                raise e.error from None
            time.sleep(min(e.wait or 1.5 * (attempt + 1), 10))


class _Again(Exception):
    def __init__(self, error: CloudError, wait: float = 0):
        super().__init__(str(error))
        self.error, self.wait = error, wait


def _request_once(method, url, token, data, headers, timeout, raw):
    low = str(url).lower()
    if not low.startswith("https://") and not low.startswith(("http://127.0.0.1:", "http://localhost:")):
        # (an address a service's answer gave, for a download say, could be file:// or plain http; this PC's
        # own address is let through for the tests' pretend services)
        log.warning("cloud: refused an address that isn't https: %s", str(url)[:80])
        raise CloudError("the service is having trouble right now, try again soon")
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
        error = CloudError(_explain(e.code, detail))
        if e.code in AGAIN:
            try:
                wait = float(e.headers.get("Retry-After", 0) or 0)
            except (TypeError, ValueError):
                wait = 0
            raise _Again(error, wait) from e
        raise error from e
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise _Again(CloudError("no internet connection, or the service didn't answer")) from e
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
    if code == 404 or "not_found" in detail or "itemNotFound" in detail:
        return "it isn't there any more"
    if code == 429 or "too_many" in detail:
        return "the service asks to wait a moment, try again soon"
    if code == 409 or "nameAlreadyExists" in detail or "/conflict" in detail:
        return "something with that name is already there"
    if "malformed_path" in detail or "disallowed_name" in detail or "invalidRequest" in detail and "name" in detail:
        return "that name isn't allowed"
    if code == 403 or "accessDenied" in detail or "no_write_permission" in detail:
        return "this account doesn't allow that"
    if code >= 500:
        return "the service is having trouble right now, try again soon"
    return f"{code}: {text}".strip()


# ----------------------------------------------------------------------------- signing in

def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _listen(port: int = 0) -> http.server.HTTPServer:
    """A tiny server on this PC that catches the service sending the person back after signing in.
    (http.server is only loaded here: it costs the Cloud page half a second on Windows.)"""
    import http.server

    class _Catch(http.server.BaseHTTPRequestHandler):
        result: dict = {}

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" not in q and "error" not in q:
                self.send_response(404)
                self.end_headers()
                return
            self.server.answer = {k: v[0] for k, v in q.items()}
            from html import escape

            from .i18n import is_rtl, tr
            title = tr("You're signed in") if "code" in q else tr("Signing in was cancelled")
            page = ("<!doctype html><meta charset=utf-8><title>Corefold</title>"
                    f"<body dir='{'rtl' if is_rtl() else 'ltr'}' style='font-family:Segoe UI,Tahoma,sans-serif;"
                    "background:#111;color:#eee;display:grid;place-items:center;height:90vh'>"
                    f"<div style='text-align:center'><h2>{escape(title)}</h2>"
                    f"<p>{escape(tr('You can close this tab and go back to Corefold.'))}</p></div>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *a):
            pass

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
        url = f"{MS_LOGIN}/authorize?" + urllib.parse.urlencode({
            "client_id": ONEDRIVE_CLIENT_ID, "response_type": "code", "redirect_uri": redirect,
            "scope": MS_SCOPE, "code_challenge": challenge,
            "code_challenge_method": "S256", "state": state, "prompt": "select_account"})
    else:
        url = f"{DROPBOX_AUTHORIZE}?" + urllib.parse.urlencode({
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
        tok = _request("POST", f"{MS_LOGIN}/token",
                       data=urllib.parse.urlencode({
                           "client_id": ONEDRIVE_CLIENT_ID, "grant_type": "authorization_code",
                           "code": answer["code"], "redirect_uri": redirect, "code_verifier": verifier,
                           "scope": MS_SCOPE}),
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    else:
        tok = _request("POST", DROPBOX_OAUTH,
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
    def search(self, text: str, cancel=None) -> list[Entry]: ...

    def sign_out(self):
        """Tells the service to cancel Corefold's key too, where it can be asked to (best effort)."""


def _iso(text: str) -> float:
    if not text:
        return 0.0
    try:
        from datetime import datetime
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# where each service's files come from (written in the downloaded file's "from the internet" mark)
HOSTS = {"onedrive": "https://onedrive.live.com/", "dropbox": "https://www.dropbox.com/"}


def local_name(name: str) -> str:
    """A cloud file's name made safe to save on this PC. Names come from the service, and a shared folder
    can hold names Windows would read as a path ("..\\..\\Startup\\x.bat", "C:\\x"): those must never
    leave the folder they're saved in. Writing-direction marks go too: "invoice\u202egpj.exe" would show
    as "invoiceexe.jpg"."""
    from .core import safe_name
    name = "".join(c for c in name if not ("\u202a" <= c <= "\u202e" or "\u2066" <= c <= "\u2069"))
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    rest = safe_name(name)
    dots = len(name) - len(name.lstrip(" ."))  # (".gitignore" keeps its dot; "..", on its own, isn't a name)
    return ("." if dots and rest else "") + rest or "file"


class QuickXorHash:
    """OneDrive's check of a file's content (Microsoft's "quickXorHash"): each byte is XORed into a 160-bit
    ring, 11 bits further along for each byte, and the length is XORed into the last 8 bytes."""
    MASK = (1 << 160) - 1

    def __init__(self):
        self.h, self.shift, self.length = 0, 0, 0

    def update(self, data: bytes):
        n = len(data)
        if not n:
            return
        # the bytes that land on the same place (every 160th) are XORed together first, all at once
        rows = data + bytes(-n % 160)
        while len(rows) > 160:
            half = len(rows) // 320 * 160
            folded = (int.from_bytes(rows[:half], "little") ^ int.from_bytes(rows[half:2 * half], "little"))
            rows = folded.to_bytes(half, "little") if len(rows) == 2 * half else \
                (folded ^ int.from_bytes(rows[2 * half:], "little")).to_bytes(half, "little")
        for i in range(min(n, 160)):
            if rows[i]:
                k = (self.shift + 11 * i) % 160
                self.h ^= ((rows[i] << k) | (rows[i] >> (160 - k))) & self.MASK
        self.shift = (self.shift + 11 * (n % 160)) % 160
        self.length += n

    def digest(self) -> str:
        out = bytearray(self.h.to_bytes(20, "little"))
        for i, b in enumerate(self.length.to_bytes(8, "little")):
            out[12 + i] ^= b
        return base64.b64encode(bytes(out)).decode("ascii")


class DropboxHash:
    """Dropbox's "content_hash": SHA-256 of each 4 MB block, then SHA-256 of those."""
    BLOCK = 4 * 1024 * 1024

    def __init__(self):
        self.blocks, self.block, self.left = hashlib.sha256(), hashlib.sha256(), self.BLOCK

    def update(self, data: bytes):
        view = memoryview(data)
        while view:
            piece = view[:self.left]
            self.block.update(piece)
            self.left -= len(piece)
            view = view[len(piece):]
            if not self.left:
                self.blocks.update(self.block.digest())
                self.block, self.left = hashlib.sha256(), self.BLOCK

    def digest(self) -> str:
        if self.left != self.BLOCK:
            self.blocks.update(self.block.digest())
            self.block, self.left = hashlib.sha256(), self.BLOCK
        return self.blocks.hexdigest()


def _checker(check: str):
    kind, _, want = check.partition(":")
    if kind == "quickxor":
        return QuickXorHash(), want
    if kind == "dropbox":
        return DropboxHash(), want.lower()
    return None, ""


CUT_OFF = "the download was cut off; try again"
NOT_THE_SAME = "the downloaded file isn't the same as the one in the account; try again"


def _stream_to(resp, target: Path, size: int, progress, cancel, host: str = "", check: str = ""):
    """Saves a download. It only takes the file's place once all of it has arrived and matches what the
    service says (its size, and its own content check), so a dropped connection never leaves a cut-off file
    that looks whole. The file gets Windows' "from the internet" mark, like one saved by a browser, so
    Windows still warns before running it and Office opens it in Protected View."""
    import http.client
    from .core import mark_from_internet
    tmp = target.with_name(target.name + ".corefold-part")
    done = 0
    hasher, want = _checker(check)
    try:
        sent = int(resp.headers.get("Content-Length") or -1)
    except (AttributeError, TypeError, ValueError):
        sent = -1
    try:
        with resp, open(tmp, "wb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    raise CloudError("cancelled")
                try:
                    chunk = resp.read(256 * 1024)
                except (http.client.HTTPException, ConnectionError, TimeoutError, socket.timeout) as e:
                    raise CloudError(CUT_OFF) from e
                if not chunk:
                    break
                f.write(chunk)
                if hasher is not None:
                    hasher.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, size)
        if (size and done != size) or (sent >= 0 and done != sent):
            log.warning("cloud download cut off: %d of %d bytes (sent as %d)", done, size, sent)
            raise CloudError(CUT_OFF)
        if hasher is not None and want and hasher.digest() != want:
            log.warning("cloud download doesn't match the service's check (%s)", check.partition(":")[0])
            raise CloudError(NOT_THE_SAME)
        os.replace(tmp, target)
        mark_from_internet(target, host)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


class OneDrive(Client):
    API = "https://graph.microsoft.com/v1.0"
    SELECT = "$select=id,name,size,lastModifiedDateTime,folder,file,parentReference"

    def _refresh(self) -> dict:
        return _request("POST", f"{MS_LOGIN}/token",
                        data=urllib.parse.urlencode({"client_id": ONEDRIVE_CLIENT_ID, "grant_type": "refresh_token",
                                                     "refresh_token": self.acc.refresh, "scope": MS_SCOPE}),
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
        qx = ((d.get("file") or {}).get("hashes") or {}).get("quickXorHash", "")
        return Entry(d.get("name", ""), "folder" in d, d.get("id", ""), (parent.get("path", "") or "")
                     .split("root:", 1)[-1] + "/" + d.get("name", ""), int(d.get("size", 0) or 0),
                     _iso(d.get("lastModifiedDateTime", "")), parent.get("id", ""),
                     hash="quickxor:" + qx if qx else "")

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
        resp = self.call("GET", f"{self.API}/me/drive/items/{entry.id}/content", raw=True, timeout=120)
        _stream_to(resp, target, entry.size, progress, cancel, HOSTS["onedrive"], entry.hash)

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
                d = _request("PUT", url, data=piece, timeout=180, tries=3, headers={
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

    def search(self, text, cancel=None):
        q = urllib.parse.quote(text.replace("'", "''"))
        page = self.call("GET", f"{self.API}/me/drive/root/search(q='{q}')?{self.SELECT}&$top=200")
        return [self._entry(d) for d in page.get("value", [])]


class Dropbox(Client):
    API = "https://api.dropboxapi.com/2"
    CONTENT = "https://content.dropboxapi.com/2"

    def _refresh(self) -> dict:
        return _request("POST", DROPBOX_OAUTH,
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
                     _iso(d.get("server_modified", "")), path.rsplit("/", 1)[0].lower(),
                     hash="dropbox:" + d["content_hash"] if d.get("content_hash") else "")

    def list(self, folder):
        page = self.call("POST", f"{self.API}/files/list_folder",
                         data={"path": folder.id if folder is not None else "", "limit": 2000}, tries=3)
        out = [self._entry(d) for d in page.get("entries", [])]
        while page.get("has_more"):
            page = self.call("POST", f"{self.API}/files/list_folder/continue", data={"cursor": page["cursor"]}, tries=3)
            out += [self._entry(d) for d in page.get("entries", [])]
        return [e for e in out if e.name]

    def download(self, entry, target, progress=None, cancel=None):
        resp = self.call("POST", f"{self.CONTENT}/files/download", raw=True, timeout=120, tries=3,
                         headers={"Dropbox-API-Arg": json.dumps({"path": entry.id}, ensure_ascii=True)})
        _stream_to(resp, target, entry.size, progress, cancel, HOSTS["dropbox"], entry.hash)

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

    def sign_out(self):
        try:
            _request("POST", f"{self.API}/auth/token/revoke", self.token(), timeout=10)
        except CloudError:
            pass

    def search(self, text, cancel=None):
        d = self.call("POST", f"{self.API}/files/search_v2", data={"query": text, "options": {"max_results": 200}}, tries=3)
        out = []
        for m in d.get("matches", []):
            meta = (m.get("metadata") or {}).get("metadata") or {}
            if meta:
                out.append(self._entry(meta))
        return out


def client(acc: Account) -> Client:
    if acc.local:
        from .cloudlocal import Local
        return Local(acc)
    return OneDrive(acc) if acc.service == "onedrive" else Dropbox(acc)


# ----------------------------------------------------------------------------- whole folders

def download_tree(c: Client, entry: Entry, folder: Path, progress=None, cancel=None) -> int:
    """Saves a file, or a folder with everything in it, into folder (next to what's there: nothing is
    replaced). Returns how many files were saved."""
    from .core import unique_path
    if cancel is not None and cancel.is_set():
        raise CloudError("cancelled")
    target = unique_path(folder / local_name(entry.name))
    if not entry.folder:
        c.download(entry, target, None if progress is None else lambda d, t: progress((entry.name, d, t)), cancel)
        return 1
    target.mkdir(parents=True)
    return sum(download_tree(c, e, target, progress, cancel) for e in c.list(entry))


def upload_tree(c: Client, src: Path, folder: Entry | None, progress=None, cancel=None) -> int:
    """Uploads a file, or a folder with everything in it, into folder. Returns how many files went up."""
    if cancel is not None and cancel.is_set():
        raise CloudError("cancelled")
    if not src.is_dir():
        c.upload(src, folder, None if progress is None else lambda d, t: progress((src.name, d, t)), cancel)
        return 1
    made = c.new_folder(src.name, folder)
    try:
        inside = sorted(src.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        inside = []
    from .core import is_link  # (a junction too, not only a link: it leads out of the folder, or round in a circle)
    return sum(upload_tree(c, p, made, progress, cancel) for p in inside if not is_link(p))


def available(service: str) -> bool:
    """Corefold can sign in to this service by itself (its app id is in cloudkeys.py)."""
    return bool(ONEDRIVE_CLIENT_ID if service == "onedrive" else DROPBOX_APP_KEY if service == "dropbox" else "")
