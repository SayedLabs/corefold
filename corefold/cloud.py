"""Corefold Cloud: Google Drive, OneDrive and Dropbox in one window, online (this file: nothing of theirs needs
to be installed) or through the folders their own apps keep on this PC (cloudlocal.py, which never goes online).

Signing in happens on Google's, Microsoft's and Dropbox's own pages, in your browser: Corefold never sees a
password. Any number of accounts can be signed in at once, several of the same service too. What it gets back is a key that only lets it reach your files; it's kept on this PC,
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

from .cloudkeys import DROPBOX_APP_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, ONEDRIVE_CLIENT_ID
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
ONLINE = ("gdrive", "onedrive", "dropbox")   # the ones Corefold can sign in to by itself

# where Corefold signs in and talks to each service (a test points these at a pretend service)
MS_LOGIN = "https://login.microsoftonline.com/common/oauth2/v2.0"
MS_SCOPE = "offline_access Files.ReadWrite User.Read"
DROPBOX_AUTHORIZE = "https://www.dropbox.com/oauth2/authorize"
DROPBOX_OAUTH = "https://api.dropboxapi.com/oauth2/token"
GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE = "https://oauth2.googleapis.com/revoke"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/drive"


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
    hash: str = ""           # the service's own check of the file's content ("quickxor:…", "dropbox:…", "md5:…")
    kind: str = ""           # Google Drive: "export:<type>:<.ext>" (a Google Doc, saved as an Office file), "web"
                             # (only opens on Google's site: a Form, a Site…), "virtual" (Shared with me, Shared
                             # drives: not a real folder), "drive" (a shared drive's top)
    own: str = ""            # a shortcut's own id (id is what it leads to): renaming or deleting it touches only
                             # the shortcut, never the file it leads to
    drive: str = ""          # Google Drive: the shared drive it's in
    web: str = ""            # its page on the service's website


@dataclass
class Account:
    service: str             # onedrive or dropbox
    email: str
    name: str = ""
    refresh: str = field(default="", repr=False)  # (the sign-in keys never show in a log or a message)
    access: str = field(default="", repr=False)
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


def _read() -> tuple[list[Account], bool]:
    """The kept accounts, and whether the file could be read (missing counts as read: there are none yet)."""
    try:
        raw = ACCOUNTS_FILE.read_bytes()
    except FileNotFoundError:
        return [], True
    except OSError:
        return [], False
    try:
        data = json.loads(unprotect(raw).decode("utf-8"))
    except (OSError, ValueError):
        return [], False
    out = []
    for d in data if isinstance(data, list) else []:
        try:
            out.append(Account(**{k: v for k, v in d.items() if k in Account.__dataclass_fields__}))
        except TypeError:
            continue
    return out, isinstance(data, list)


def _keep_unreadable():
    """The accounts file couldn't be read (Windows couldn't unlock it, or it's damaged): it's put aside before
    anything new is written, so signing in to one more account never wipes the others for good."""
    try:
        if ACCOUNTS_FILE.exists():
            os.replace(ACCOUNTS_FILE, ACCOUNTS_FILE.with_name(f"accounts-unreadable-{int(time.time())}.dat"))
            log.warning("cloud: the accounts file couldn't be read; it was put aside")
    except OSError:
        pass


def load_accounts() -> list[Account]:
    with _lock:
        return _read()[0]


def _load_for_change() -> list[Account]:
    accounts, ok = _read()
    if not ok:
        _keep_unreadable()
    return accounts


def save_accounts(accounts: list[Account]):
    with _lock:
        FOLDER.mkdir(parents=True, exist_ok=True)
        data = json.dumps([a.__dict__ for a in accounts if not a.local]).encode("utf-8")
        tmp = ACCOUNTS_FILE.with_suffix(".tmp")
        tmp.write_bytes(protect(data))
        os.replace(tmp, ACCOUNTS_FILE)


def forget_account(key: str):
    with _lock:
        save_accounts([a for a in _load_for_change() if a.key != key])


def remember_account(acc: Account):
    with _lock:
        accounts = [a for a in _load_for_change() if a.key != acc.key]
        accounts.append(acc)
        save_accounts(accounts)


def update_account(acc: Account):
    """Keeps an account's new key or details, only if it's still kept (one signed out meanwhile stays out)."""
    with _lock:
        accounts, ok = _read()
        if not ok:
            return
        if any(a.key == acc.key for a in accounts):
            save_accounts([acc if a.key == acc.key else a for a in accounts])


# ----------------------------------------------------------------------------- the internet

AGAIN = (429, 500, 502, 503, 504)   # worth one more try after a short wait
TOO_BIG_TO_EXPORT = "Google can't turn something this big into a file; download it from the Google Drive website"
WEB_ONLY = "this one only opens on the service's website"


def _request(method: str, url: str, token: str = "", data=None, headers: dict | None = None,
             timeout: float = TIMEOUT, raw: bool = False, tries: int = 0, accept: tuple = ()):
    """One request. Reading (GET) is tried up to 3 times when the service is busy or the connection drops;
    other requests only when tries says so (sending a piece of an upload again is safe, making a folder twice
    isn't). "Too many requests" is tried again whatever it was: the service didn't do it.
    accept: answers that aren't a failure here (Google's 308 "send the next piece"), returned as
    {"_status": code, "_range": what it has so far}."""
    tries = tries or (3 if method == "GET" else 1)
    attempt = 0
    while True:
        try:
            return _request_once(method, url, token, data, headers, timeout, raw, accept)
        except _Again as e:
            attempt += 1
            if attempt >= max(tries, 3 if e.busy else 1):
                raise e.error from None
            time.sleep(min(e.wait or 1.5 * attempt, 30))


class _Again(Exception):
    def __init__(self, error: CloudError, wait: float = 0, busy: bool = False):
        super().__init__(str(error))
        self.error, self.wait, self.busy = error, wait, busy  # (busy: 429, nothing was done)


def _request_once(method, url, token, data, headers, timeout, raw, accept=()):
    low = str(url).lower()
    if not low.startswith("https://") and not low.startswith(("http://127.0.0.1:", "http://localhost:")):
        # (an address a service's answer gave, for a download say, could be file:// or plain http; this PC's
        # own address is let through for the tests' pretend services)
        # (without what follows '?': a download address can carry a key there)
        log.warning("cloud: refused an address that isn't https: %s", str(url).split("?")[0][:80])
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
        if e.code in accept:
            out = {"_status": e.code, "_range": e.headers.get("Range", "") if e.headers else ""}
            e.close()
            return out
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:600]
        except Exception:
            pass
        if e.code == 401:
            raise SignInNeeded(detail or "signed out") from e
        error = CloudError(_explain(e.code, detail))
        if e.code == 403 and "ateLimitExceeded" in detail:  # (Google says "too many" this way too)
            raise _Again(error, 0, True) from e
        if e.code in AGAIN:
            try:
                wait = float(e.headers.get("Retry-After", 0) or 0)
            except (TypeError, ValueError):
                wait = 0
            raise _Again(error, wait, e.code == 429) from e
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
    if code == 507 or "insufficient_space" in detail or "quotaLimitReached" in detail or \
            "storageQuotaExceeded" in detail or "teamDriveFileLimitExceeded" in detail:
        return "there's no space left in this account"
    if "exportSizeLimitExceeded" in detail:
        return TOO_BIG_TO_EXPORT
    if "ateLimitExceeded" in detail:
        return "the service asks to wait a moment, try again soon"
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
        timeout = 5  # (a connection that never asks anything, like a browser's spare one, mustn't hold up the wait)

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" not in q and "error" not in q:
                self.send_response(404)
                self.end_headers()
                return
            answer = {k: v[0] for k, v in q.items()}
            if answer.get("state") != self.server.state:
                # (not the sign-in page's answer: any web page open in the browser can send the person here, and
                # must not end the sign-in, let alone hand Corefold a key of its own)
                self.send_response(400)
                self.end_headers()
                return
            self.server.answer = answer
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
    server.state = None
    server.timeout = 1
    return server


def sign_in(service: str, open_page, cancel: threading.Event | None = None, wait_s: float = 300) -> Account:
    """Opens the service's own sign-in page (open_page(url) opens the browser) and waits for it to
    send the person back. Returns the new account. Google and Microsoft always ask which account: signing in
    again adds another one (the same one again just gets a fresh key)."""
    if not available(service):
        if service not in ONLINE:
            raise CloudError("unknown service")
        raise CloudError(f"{SERVICES[service]} isn't set up in this copy of Corefold yet")
    if service == "dropbox":
        try:
            server = _listen(DROPBOX_PORT)
        except OSError as e:
            raise CloudError("another program is using the port Dropbox signs in through; close it and try again") \
                from e
        redirect = f"http://localhost:{DROPBOX_PORT}/"
    else:
        server = _listen(0)
        # (Google wants this PC's address written as numbers; Microsoft's registration says localhost)
        host = "127.0.0.1" if service == "gdrive" else "localhost"
        redirect = f"http://{host}:{server.server_address[1]}/"
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    server.state = state
    if service == "gdrive":
        url = f"{GOOGLE_AUTHORIZE}?" + urllib.parse.urlencode({
            "client_id": GOOGLE_CLIENT_ID, "response_type": "code", "redirect_uri": redirect,
            "scope": GOOGLE_SCOPE, "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
            # (which account, every time, so another one can be added; "consent" so Google gives a key that lasts)
            "access_type": "offline", "prompt": "select_account consent"})
    elif service == "onedrive":
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
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    asked = {"grant_type": "authorization_code", "code": answer["code"], "redirect_uri": redirect,
             "code_verifier": verifier}
    if service == "gdrive":
        tok = _request("POST", GOOGLE_TOKEN, headers=form, data=urllib.parse.urlencode(
            {**asked, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET}))
        if GOOGLE_SCOPE not in str(tok.get("scope", GOOGLE_SCOPE)).split():
            # (Google lets the person untick "see, edit and delete your files": without it nothing works)
            raise CloudError(NO_FILES_ALLOWED)
    elif service == "onedrive":
        tok = _request("POST", f"{MS_LOGIN}/token", headers=form, data=urllib.parse.urlencode(
            {**asked, "client_id": ONEDRIVE_CLIENT_ID, "scope": MS_SCOPE}))
    else:
        tok = _request("POST", DROPBOX_OAUTH, headers=form, data=urllib.parse.urlencode(
            {**asked, "client_id": DROPBOX_APP_KEY}))
    acc = Account(service, "", refresh=tok.get("refresh_token", ""), access=tok.get("access_token", ""),
                  expires=time.time() + float(tok.get("expires_in", 3600)) - 60)
    if not acc.access:
        raise CloudError("the service didn't let Corefold in")
    client(acc).fill_profile()
    if not acc.refresh:  # (Google: an account signed in to before, whose key it didn't give again)
        old = next((a for a in load_accounts() if a.key == acc.key), None)
        acc.refresh = old.refresh if old is not None else ""
    remember_account(acc)
    return acc


NO_FILES_ALLOWED = "Corefold wasn't allowed to see your files; sign in again and leave that box ticked"


# ----------------------------------------------------------------------------- the services

class Client:
    def __init__(self, account: Account):
        self.acc = account
        self.skipped: list[str] = []  # (what a whole folder's download left out: Google Forms, Sites… only open online)

    def token(self) -> str:
        acc = self.acc
        if acc.access and time.time() < acc.expires:
            return acc.access
        if not acc.refresh:
            raise SignInNeeded("signed out")
        try:
            tok = self._refresh()
        except SignInNeeded:
            raise
        except CloudError as e:
            # (only the service saying no to the key means signed out: no internet, or a busy service, doesn't)
            cause = e.__cause__
            if isinstance(cause, urllib.error.HTTPError) and cause.code in (400, 401):
                raise SignInNeeded(str(e)) from e
            raise
        acc.access = tok["access_token"]
        acc.refresh = tok.get("refresh_token") or acc.refresh
        acc.expires = time.time() + float(tok.get("expires_in", 3600)) - 60
        update_account(acc)
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

    def share_link(self, entry: Entry) -> str:
        """A link anyone who has it can open (only see, never change), made by the service."""
        raise CloudError("this account doesn't allow that")

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
HOSTS = {"onedrive": "https://onedrive.live.com/", "dropbox": "https://www.dropbox.com/",
         "gdrive": "https://drive.google.com/"}


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
    if kind == "md5":
        return _Hex(hashlib.md5(usedforsecurity=False)), want.lower()
    return None, ""


class _Hex:
    """hashlib's hash, answering digest() in hex like the services' own checks."""

    def __init__(self, h):
        self.h = h

    def update(self, data: bytes):
        self.h.update(data)

    def digest(self) -> str:
        return self.h.hexdigest()


def _same_as_sent(check: str, hasher) -> bool:
    """An upload arrived whole: what the service says it got matches what was read (True when it doesn't say)."""
    kind, _, want = check.partition(":")
    if hasher is None or not want:
        return True
    return hasher.digest() == (want if kind == "quickxor" else want.lower())


def file_name(entry: Entry) -> str:
    """The name an online file is saved under on this PC (a Google Doc gets the Office file's ending)."""
    name = entry.name
    if entry.kind.startswith("export:"):
        ext = entry.kind.rsplit(":", 1)[-1]
        if not name.lower().endswith(ext):
            name += ext
    return local_name(name)


NOT_SENT_WHOLE = "the upload didn't arrive whole, so it was taken away again; try again"


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
        os.rename(tmp, target)  # (never over something that took the name meanwhile)
        mark_from_internet(target, host)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


class OneDrive(Client):
    API = "https://graph.microsoft.com/v1.0"
    SELECT = "$select=id,name,size,lastModifiedDateTime,folder,file,parentReference,webUrl"

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
            drive = self.call("GET", f"{self.API}/me/drive?$select=quota,driveType")
            q = drive.get("quota", {})
            self.acc.used, self.acc.total = int(q.get("used", 0)), int(q.get("total", 0))
            self.acc.extra["personal"] = drive.get("driveType") == "personal"
        except CloudError:
            pass

    def _entry(self, d: dict) -> Entry:
        parent = d.get("parentReference", {}) or {}
        qx = ((d.get("file") or {}).get("hashes") or {}).get("quickXorHash", "")
        # (a OneNote notebook is a "package": it has no file of its own to download, it opens on the website)
        kind = "web" if "package" in d and "folder" not in d else ""
        return Entry(d.get("name", ""), "folder" in d, d.get("id", ""), (parent.get("path", "") or "")
                     .split("root:", 1)[-1] + "/" + d.get("name", ""), int(d.get("size", 0) or 0),
                     _iso(d.get("lastModifiedDateTime", "")), parent.get("id", ""),
                     hash="quickxor:" + qx if qx else "", web=d.get("webUrl", ""), kind=kind)

    def _arrived(self, d: dict, size: int, sent) -> Entry:
        """The uploaded file, once it's known to be all there. Only a personal OneDrive is held to the bytes sent:
        a work or school one (SharePoint) changes Office files on purpose as they arrive."""
        e = self._entry(d)
        if not self.acc.extra.get("personal") or (e.size == size and _same_as_sent(e.hash, sent)):
            return e
        log.warning("cloud upload doesn't match what was sent (onedrive: %d of %d bytes)", e.size, size)
        try:
            self.delete(e)  # (to OneDrive's recycle bin: the file on this PC is untouched)
        except CloudError:
            pass
        raise CloudError(NOT_SENT_WHOLE)

    def _where_upload_is(self, url: str) -> int:
        """How much of an upload session OneDrive has: where the next piece starts (-1: the session is gone)."""
        try:
            d = _request("GET", url, tries=3)
        except CloudError:
            return -1
        ranges = d.get("nextExpectedRanges") or []
        try:
            return int(str(ranges[0]).split("-")[0]) if ranges else -1
        except ValueError:
            return -1

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
        if entry.kind == "web":
            raise CloudError(WEB_ONLY)
        resp = self.call("GET", f"{self.API}/me/drive/items/{entry.id}/content", raw=True, timeout=120)
        _stream_to(resp, target, entry.size, progress, cancel, HOSTS["onedrive"], entry.hash)

    def upload(self, src, folder, progress=None, cancel=None):
        parent = f"items/{folder.id}" if folder is not None else "root"
        name = urllib.parse.quote(src.name)
        size = src.stat().st_size
        sent = QuickXorHash()
        if size <= SMALL:
            data = src.read_bytes()
            sent.update(data)
            d = self.call("PUT", f"{self.API}/me/drive/{parent}:/{name}:/content?@microsoft.graph.conflictBehavior=rename",
                          data=data, headers={"Content-Type": "application/octet-stream"}, timeout=120)
            if progress is not None:
                progress(size, size)
            return self._arrived(d, size, sent)
        session = self.call("POST", f"{self.API}/me/drive/{parent}:/{name}:/createUploadSession",
                            data={"item": {"@microsoft.graph.conflictBehavior": "rename"}})
        url = session["uploadUrl"]
        done = hashed = stuck = 0
        d: dict = {}
        with open(src, "rb") as f:
            while not d.get("id"):
                if cancel is not None and cancel.is_set():
                    try:
                        _request("DELETE", url)
                    except CloudError:
                        pass
                    raise CloudError("cancelled")
                f.seek(done)
                piece = f.read(min(CHUNK, size - done))
                if not piece:  # (it got shorter while it was sent: another app is changing it)
                    raise CloudError("it's open in another app, or Windows didn't allow it")
                if done + len(piece) > hashed:  # (each byte counted once, also when a piece goes again)
                    sent.update(piece[max(0, hashed - done):])
                    hashed = done + len(piece)
                end = done + len(piece) - 1
                try:
                    d = _request("PUT", url, data=piece, timeout=180, headers={
                        "Content-Length": str(len(piece)), "Content-Range": f"bytes {done}-{end}/{size}"})
                    was, done = done, end + 1
                    if not d.get("id"):
                        nxt = (d.get("nextExpectedRanges") or [""])[0]
                        if str(nxt).split("-")[0].isdigit():
                            done = int(str(nxt).split("-")[0])
                except SignInNeeded:
                    raise
                except CloudError:
                    # (cut off, or OneDrive was busy: it says which piece it wants next; a session it ended
                    # once the last piece arrived means the file is there, and is looked for by its name)
                    was, done = done, self._where_upload_is(url)
                    if done < 0:
                        raise
                    d = {}
                stuck = stuck + 1 if done <= was else 0
                if stuck >= 4 or (done >= size and not d.get("id")):
                    raise CloudError(NOT_SENT_WHOLE)
                if progress is not None:
                    progress(min(done, size), size)
        return self._arrived(d, size, sent)

    def new_folder(self, name, folder):
        parent = f"items/{folder.id}" if folder is not None else "root"
        d = self.call("POST", f"{self.API}/me/drive/{parent}/children",
                      data={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "rename"})
        return self._entry(d)

    def rename(self, entry, name):
        return self._entry(self.call("PATCH", f"{self.API}/me/drive/items/{entry.id}", data={"name": name}))

    def delete(self, entry):
        self.call("DELETE", f"{self.API}/me/drive/items/{entry.id}")

    def share_link(self, entry):
        url = f"{self.API}/me/drive/items/{entry.id}/createLink"
        try:
            d = self.call("POST", url, data={"type": "view", "scope": "anonymous"})
        except SignInNeeded:
            raise
        except CloudError:  # (a work or school account may only allow links for people in it)
            d = self.call("POST", url, data={"type": "view"})
        return str((d.get("link") or {}).get("webUrl", ""))

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
                     hash="dropbox:" + d["content_hash"] if d.get("content_hash") else "",
                     web="https://www.dropbox.com/home" + urllib.parse.quote(path) if path else "")

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
        sent = DropboxHash()  # (what was read, to check against what Dropbox says it got)
        if size <= SMALL:
            data = src.read_bytes()
            sent.update(data)
            d = self.call("POST", f"{self.CONTENT}/files/upload", data=data, timeout=120,
                          headers={"Content-Type": "application/octet-stream",
                                   "Dropbox-API-Arg": json.dumps(arg, ensure_ascii=True)})
            if progress is not None:
                progress(size, size)
            return self._arrived(self._entry({**d, ".tag": "file"}), sent)
        with open(src, "rb") as f:
            first = f.read(CHUNK)
            sent.update(first)
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
                if not piece:  # (it got shorter while it was sent: asking for more would never end)
                    raise CloudError("it's open in another app, or Windows didn't allow it")
                sent.update(piece)
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
        return self._arrived(self._entry({**d, ".tag": "file"}), sent)

    def _arrived(self, e: Entry, sent) -> Entry:
        if _same_as_sent(e.hash, sent):
            return e
        log.warning("cloud upload doesn't match what was sent (dropbox)")
        try:
            self.delete(e)  # (to Dropbox's deleted files: the file on this PC is untouched)
        except CloudError:
            pass
        raise CloudError(NOT_SENT_WHOLE)

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

    def share_link(self, entry):
        """The link it already has, or a new one (Dropbox keeps one link for each file)."""
        have = self.call("POST", f"{self.API}/sharing/list_shared_links", data={"path": entry.id, "direct_only": True})
        for link in have.get("links", []):
            if (link.get("link_permissions") or {}).get("resolved_visibility", {}).get(".tag", "public") == "public":
                return str(link.get("url", ""))
        d = self.call("POST", f"{self.API}/sharing/create_shared_link_with_settings",
                      data={"path": entry.id, "settings": {"requested_visibility": "public", "access": "viewer"}})
        return str(d.get("url", ""))

    def sign_out(self):
        try:
            acc = self.acc
            # (a fresh key is asked for but never kept: the account is being forgotten as this runs)
            token = acc.access if acc.access and time.time() < acc.expires else \
                (self._refresh().get("access_token", "") if acc.refresh else "")
            if token:
                _request("POST", f"{self.API}/auth/token/revoke", token, timeout=10)
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

class GoogleDrive(Client):
    """Google Drive, online: My Drive, what's shared with you, and shared drives. Google lets a folder hold two
    things with the same name; Corefold doesn't make that happen (a new name gets " (1)", like OneDrive and
    Dropbox do), so nothing can be mistaken for something else. Deleting moves to Google Drive's bin, never for
    good."""
    API = "https://www.googleapis.com/drive/v3"
    UPLOAD = "https://www.googleapis.com/upload/drive/v3"
    FOLDER_TYPE = "application/vnd.google-apps.folder"
    SHORTCUT = "application/vnd.google-apps.shortcut"
    FIELDS = "id,name,mimeType,size,modifiedTime,parents,md5Checksum,driveId,shortcutDetails,webViewLink"
    ALL = "supportsAllDrives=true&includeItemsFromAllDrives=true"
    # Google's own files have no bytes to download: each is turned into the file its kind is usually kept as
    EXPORT = {
        "application/vnd.google-apps.document":
            ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
        "application/vnd.google-apps.spreadsheet":
            ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
        "application/vnd.google-apps.presentation":
            ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
        "application/vnd.google-apps.drawing": ("image/png", ".png"),
        "application/vnd.google-apps.script": ("application/vnd.google-apps.script+json", ".json"),
        "application/vnd.google-apps.jam": ("application/pdf", ".pdf"),
    }
    SHARED_WITH_ME, SHARED_DRIVES = "sharedWithMe", "sharedDrives"

    def __init__(self, account: Account):
        super().__init__(account)
        self._names: dict[str, set[str]] = {}  # (the names in a folder, while uploading many things into it)

    def _refresh(self) -> dict:
        return _request("POST", GOOGLE_TOKEN,
                        data=urllib.parse.urlencode({"client_id": GOOGLE_CLIENT_ID,
                                                     "client_secret": GOOGLE_CLIENT_SECRET,
                                                     "grant_type": "refresh_token", "refresh_token": self.acc.refresh}),
                        headers={"Content-Type": "application/x-www-form-urlencoded"})

    def fill_profile(self):
        me = self.call("GET", f"{self.API}/about?fields=user(displayName,emailAddress),storageQuota(limit,usage)")
        user = me.get("user") or {}
        self.acc.email = user.get("emailAddress") or self.acc.email or "Google Drive"
        self.acc.name = user.get("displayName", "")
        q = me.get("storageQuota") or {}
        self.acc.used, self.acc.total = int(q.get("usage") or 0), int(q.get("limit") or 0)  # (no limit: unlimited)

    @staticmethod
    def _q(text: str) -> str:
        """Text inside a search's quotes."""
        return text.replace("\\", "\\\\").replace("'", "\\'")

    def _entry(self, d: dict, drive: str = "") -> Entry:
        kind_of = d.get("mimeType", "")
        own = ""
        target = d.get("id", "")
        if kind_of == self.SHORTCUT:  # (it's shown as what it leads to, but only the shortcut is ever changed)
            details = d.get("shortcutDetails") or {}
            own, target = target, details.get("targetId", "")
            kind_of = details.get("targetMimeType", "")
        folder = kind_of == self.FOLDER_TYPE
        kind = ""
        if not folder and kind_of.startswith("application/vnd.google-apps."):
            export = self.EXPORT.get(kind_of)
            kind = f"export:{export[0]}:{export[1]}" if export else "web"
        md5 = d.get("md5Checksum", "")
        return Entry(d.get("name", ""), folder, target, "/" + d.get("name", ""), int(d.get("size") or 0),
                     _iso(d.get("modifiedTime", "")), (d.get("parents") or [""])[0],
                     hash="md5:" + md5 if md5 and not own else "", kind=kind, own=own,
                     drive=d.get("driveId", "") or drive, web=d.get("webViewLink", ""))

    def _files(self, q: str, drive: str = "", every: bool = True, extra: str = "") -> list[Entry]:
        where = f"&corpora=drive&driveId={urllib.parse.quote(drive)}" if drive else ""
        url = (f"{self.API}/files?q={urllib.parse.quote(q)}&fields=nextPageToken,files({self.FIELDS})"
               f"&pageSize=1000&{self.ALL}{where}{extra}")
        out, token = [], ""
        while True:
            page = self.call("GET", url + (f"&pageToken={urllib.parse.quote(token)}" if token else ""))
            out += [self._entry(d, drive) for d in page.get("files", [])]
            token = page.get("nextPageToken", "")
            if not token or not every:
                return out

    def _drives(self) -> list[Entry]:
        out, token = [], ""
        while True:
            page = self.call("GET", f"{self.API}/drives?pageSize=100&fields=nextPageToken,drives(id,name)"
                             + (f"&pageToken={urllib.parse.quote(token)}" if token else ""))
            out += [Entry(d.get("name", ""), True, d.get("id", ""), "/" + d.get("name", ""), kind="drive",
                          drive=d.get("id", "")) for d in page.get("drives", [])]
            token = page.get("nextPageToken", "")
            if not token:
                return out

    def list(self, folder):
        from .i18n import tr
        if folder is None:
            out = self._files("'root' in parents and trashed=false")
            out.append(Entry(tr("Shared with me"), True, self.SHARED_WITH_ME, "/", kind="virtual"))
            try:
                if self._drives():
                    out.append(Entry(tr("Shared drives"), True, self.SHARED_DRIVES, "/", kind="virtual"))
            except CloudError:  # (a personal account may not be allowed to ask: there are none then)
                pass
            return out
        if folder.id == self.SHARED_WITH_ME and folder.kind == "virtual":
            return self._files("sharedWithMe and trashed=false")
        if folder.id == self.SHARED_DRIVES and folder.kind == "virtual":
            return self._drives()
        return self._files(f"'{self._q(folder.id)}' in parents and trashed=false", folder.drive)

    def _parent(self, folder: Entry | None) -> str:
        if folder is not None and folder.kind == "virtual":
            raise CloudError("this account doesn't allow that")
        return folder.id if folder is not None else "root"

    def _free_name(self, parent: str, name: str, drive: str = "") -> str:
        """name, or "name (1).ext"… when something in that folder already has it."""
        names = self._names.get(parent)
        if names is None:
            names = {e.name.casefold() for e in self._files(f"'{self._q(parent)}' in parents and trashed=false",
                                                            drive)}
            self._names[parent] = names
        stem, dot, ext = name.rpartition(".")
        if not stem:
            stem, dot, ext = name, "", ""
        cand, n = name, 1
        while cand.casefold() in names:
            cand = f"{stem} ({n}){dot}{ext}"
            n += 1
        names.add(cand.casefold())
        return cand

    def download(self, entry, target, progress=None, cancel=None):
        if entry.kind == "web" or entry.kind == "virtual" or entry.kind == "drive":
            raise CloudError(WEB_ONLY)
        fid = urllib.parse.quote(entry.id)
        if entry.kind.startswith("export:"):
            mime = entry.kind.split(":")[1]
            resp = self.call("GET", f"{self.API}/files/{fid}/export?mimeType={urllib.parse.quote(mime)}",
                             raw=True, timeout=120)
            _stream_to(resp, target, 0, progress, cancel, HOSTS["gdrive"])
            return
        resp = self.call("GET", f"{self.API}/files/{fid}?alt=media&supportsAllDrives=true", raw=True, timeout=120)
        _stream_to(resp, target, entry.size, progress, cancel, HOSTS["gdrive"], entry.hash)

    def upload(self, src, folder, progress=None, cancel=None):
        parent = self._parent(folder)
        drive = folder.drive if folder is not None else ""
        size = src.stat().st_size
        meta = {"name": self._free_name(parent, src.name, drive), "parents": [parent]}
        sent = _Hex(hashlib.md5(usedforsecurity=False))
        ask = f"&supportsAllDrives=true&fields={self.FIELDS}"
        if size <= SMALL:
            data = src.read_bytes()
            sent.update(data)
            bound = "corefold" + secrets.token_hex(12)
            body = (f"--{bound}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
                    f"--{bound}\r\nContent-Type: application/octet-stream\r\n\r\n").encode("utf-8") + data + \
                f"\r\n--{bound}--\r\n".encode("ascii")
            d = self.call("POST", f"{self.UPLOAD}/files?uploadType=multipart{ask}", data=body, timeout=120,
                          headers={"Content-Type": f"multipart/related; boundary={bound}"})
            if progress is not None:
                progress(size, size)
            return self._arrived(self._entry(d), size, sent)
        # bigger: a resumable upload, in pieces Google takes (multiples of 256 KB); a piece that doesn't get there
        # is sent again from where Google says it got to
        resp = self.call("POST", f"{self.UPLOAD}/files?uploadType=resumable{ask}", data=meta, raw=True,
                         headers={"X-Upload-Content-Length": str(size),
                                  "X-Upload-Content-Type": "application/octet-stream"})
        with resp:
            url = resp.headers.get("Location", "")
        if not url:
            raise CloudError("the service is having trouble right now, try again soon")
        piece_size = max(256 * 1024, CHUNK // (256 * 1024) * 256 * 1024)
        done = hashed = stuck = 0
        d: dict = {}
        with open(src, "rb") as f:
            while not d.get("id"):
                if cancel is not None and cancel.is_set():
                    try:
                        _request("DELETE", url, accept=(499,))
                    except CloudError:
                        pass
                    raise CloudError("cancelled")
                f.seek(done)
                piece = f.read(min(piece_size, size - done))
                if not piece and done < size:  # (it got shorter while it was sent: another app is changing it)
                    raise CloudError("it's open in another app, or Windows didn't allow it")
                if done + len(piece) > hashed:  # (each byte counted once, also when a piece goes again)
                    sent.update(piece[max(0, hashed - done):])
                    hashed = done + len(piece)
                try:
                    d = _request("PUT", url, data=piece, timeout=180, accept=(308,), headers={
                        "Content-Length": str(len(piece)),
                        "Content-Range": f"bytes {done}-{done + len(piece) - 1}/{size}" if piece else f"bytes */{size}"})
                except SignInNeeded:
                    raise
                except CloudError:
                    d = self._where_upload_is(url, size)  # (cut off, or Google was busy: ask how far it got)
                if d.get("_status") == 308:
                    was = done
                    done = _range_end(d.get("_range", "")) + 1  # (the next piece starts where Google got to)
                    stuck = stuck + 1 if done <= was else 0
                    if stuck >= 4 or done > size:
                        raise CloudError(NOT_SENT_WHOLE)
                elif not d.get("id"):
                    raise CloudError(NOT_SENT_WHOLE)
                else:
                    done = size
                if progress is not None:
                    progress(done, size)
        return self._arrived(self._entry(d), size, sent)

    def _where_upload_is(self, url: str, size: int) -> dict:
        """How much of a resumable upload Google has (308 with what it has), or the file once it's all there."""
        return _request("PUT", url, data=b"", accept=(308,), tries=3,
                        headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"})

    def _arrived(self, e: Entry, size: int, sent) -> Entry:
        if e.size == size and _same_as_sent(e.hash, sent):
            return e
        log.warning("cloud upload doesn't match what was sent (gdrive: %d of %d bytes)", e.size, size)
        try:
            self.delete(e)  # (to Google Drive's bin: the file on this PC is untouched)
        except CloudError:
            pass
        raise CloudError(NOT_SENT_WHOLE)

    def new_folder(self, name, folder):
        parent = self._parent(folder)
        meta = {"name": self._free_name(parent, name, folder.drive if folder is not None else ""),
                "mimeType": self.FOLDER_TYPE, "parents": [parent]}
        d = self.call("POST", f"{self.API}/files?supportsAllDrives=true&fields={self.FIELDS}", data=meta)
        return self._entry(d)

    def rename(self, entry, name):
        if entry.kind in ("virtual", "drive"):
            raise CloudError("this account doesn't allow that")
        mine = entry.own or entry.id
        if entry.parent:
            there = self._files(f"'{self._q(entry.parent)}' in parents and name='{self._q(name)}' and trashed=false",
                                entry.drive, every=False)
            if any((e.own or e.id) != mine for e in there):
                raise CloudError("something with that name is already there")
        d = self.call("PATCH", f"{self.API}/files/{urllib.parse.quote(mine)}?supportsAllDrives=true"
                      f"&fields={self.FIELDS}", data={"name": name})
        return self._entry(d, entry.drive)

    def delete(self, entry):
        """To Google Drive's bin (Google keeps it there for 30 days), never deleted for good. A shortcut: only
        the shortcut goes, never the file it leads to."""
        if entry.kind in ("virtual", "drive"):
            raise CloudError("this account doesn't allow that")
        self.call("PATCH", f"{self.API}/files/{urllib.parse.quote(entry.own or entry.id)}?supportsAllDrives=true"
                  "&fields=id,trashed", data={"trashed": True})

    def share_link(self, entry):
        """Anyone with the link can see it (it isn't listed or searchable). A shortcut shares what it leads to."""
        if entry.kind in ("virtual", "drive"):
            raise CloudError("this account doesn't allow that")
        fid = urllib.parse.quote(entry.id)
        self.call("POST", f"{self.API}/files/{fid}/permissions?supportsAllDrives=true&fields=id",
                  data={"role": "reader", "type": "anyone", "allowFileDiscovery": False})
        d = self.call("GET", f"{self.API}/files/{fid}?supportsAllDrives=true&fields=webViewLink")
        return str(d.get("webViewLink") or entry.web)

    def search(self, text, cancel=None):
        return self._files(f"name contains '{self._q(text)}' and trashed=false", every=False,
                           extra="&corpora=allDrives")[:200]

    def sign_out(self):
        try:
            if self.acc.refresh:  # (taking back the key that lasts takes back Corefold's permission for good)
                _request("POST", GOOGLE_REVOKE, data=urllib.parse.urlencode({"token": self.acc.refresh}),
                         headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=10)
        except CloudError:
            pass


def _range_end(header: str) -> int:
    """Google's "Range: bytes=0-1048575": the last byte it has (-1: none yet)."""
    try:
        return int(header.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return -1


def client(acc: Account) -> Client:
    if acc.local:
        from .cloudlocal import Local
        return Local(acc)
    return {"onedrive": OneDrive, "gdrive": GoogleDrive}.get(acc.service, Dropbox)(acc)


# ----------------------------------------------------------------------------- whole folders

def _key(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def download_tree(c: Client, entry: Entry, folder: Path, progress=None, cancel=None, _made: set | None = None) -> int:
    """Saves a file, or a folder with everything in it, into folder (next to what's there: nothing is
    replaced). Returns how many files were saved."""
    from .core import unique_path
    if cancel is not None and cancel.is_set():
        raise CloudError("cancelled")
    if not entry.folder and entry.kind == "web":  # (a Google Form, a Site…: there's no file to save)
        c.skipped.append(entry.name)
        return 0
    target = unique_path(folder / (file_name(entry) if not entry.folder else local_name(entry.name)))
    if not entry.folder:
        c.download(entry, target, None if progress is None else lambda d, t: progress((entry.name, d, t)), cancel)
        return 1
    # what's in it is read before the new folder is made, and new folders are never gone into: a folder of
    # this PC saved into itself would otherwise be copied into its copy, over and over until it crashed. An
    # online folder is gone into once: a Google Drive shortcut to a folder it's in would lead round in a circle
    made = set() if _made is None else _made
    made.add("id:" + entry.id)
    inside = [e for e in c.list(entry) if _key(e.id) not in made and "id:" + e.id not in made]
    target.mkdir(parents=True)
    made.add(_key(target))
    return sum(download_tree(c, e, target, progress, cancel, made) for e in inside)


def upload_tree(c: Client, src: Path, folder: Entry | None, progress=None, cancel=None,
                _made: set | None = None) -> int:
    """Uploads a file, or a folder with everything in it, into folder. Returns how many files went up."""
    if cancel is not None and cancel.is_set():
        raise CloudError("cancelled")
    if not src.is_dir():
        c.upload(src, folder, None if progress is None else lambda d, t: progress((src.name, d, t)), cancel)
        return 1
    made = set() if _made is None else _made
    try:  # (read before the new folder is made: see download_tree)
        inside = sorted(src.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        inside = []
    new = c.new_folder(src.name, folder)
    made.add(_key(new.id))  # (for a folder on this PC its id is its path)
    from .core import is_link  # (a junction too, not only a link: it leads out of the folder, or round in a circle)
    return sum(upload_tree(c, p, new, progress, cancel, made) for p in inside
               if not is_link(p) and _key(p) not in made)


def copy_tree(src: Client, entry: Entry, dst: Client, into: Entry | None, progress=None, cancel=None,
              _made: set | None = None, _temp: Path | None = None) -> int:
    """Copies a file, or a folder with everything in it, from one account into a folder of another. Files go
    one at a time through a temporary copy on this PC that's deleted as soon as it's up (both ends checked to
    match, as for any download and upload), so only room for the biggest single file is ever needed here, never
    for the whole folder. Returns how many files were copied."""
    import shutil
    import tempfile
    if _temp is None:
        temp = Path(tempfile.mkdtemp(prefix="corefold-cloud-copy-"))
        try:
            return copy_tree(src, entry, dst, into, progress, cancel, _made, temp)
        finally:
            shutil.rmtree(temp, ignore_errors=True)
    if cancel is not None and cancel.is_set():
        raise CloudError("cancelled")
    if not entry.folder:
        if entry.kind == "web":  # (a Google Form, a Site…: there's no file to copy)
            src.skipped.append(entry.name)
            return 0
        local = _temp / file_name(entry)  # (the name it gets in the other account)
        try:
            src.download(entry, local, None if progress is None else lambda d, t: progress((entry.name, d / 2, t)),
                         cancel)
            dst.upload(local, into, None if progress is None else
                       lambda d, t: progress((entry.name, (t + d) / 2, t)), cancel)
        finally:
            local.unlink(missing_ok=True)
        return 1
    made = set() if _made is None else _made
    made.add("id:" + entry.id)
    inside = [e for e in src.list(entry) if "id:" + e.id not in made]  # (read first: see download_tree)
    new = dst.new_folder(local_name(entry.name), into)
    made.add("id:" + new.id)
    return sum(copy_tree(src, e, dst, new, progress, cancel, made, _temp) for e in inside)


def available(service: str) -> bool:
    """Corefold can sign in to this service by itself (its app id is in cloudkeys.py)."""
    return bool({"gdrive": GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, "onedrive": ONEDRIVE_CLIENT_ID,
                 "dropbox": DROPBOX_APP_KEY}.get(service))
