"""Corefold Cloud, tried for real without a real account: a pretend OneDrive and Dropbox run on this PC (they
answer like Microsoft's and Dropbox's services do), and pretend OneDrive, Dropbox, Google Drive and iCloud folders
are set up the way their own apps leave them. Signing in (with the browser's part played by the test), keys running
out, uploads big and small, cancelling, downloads, folders, renames, deletes, search, the service being busy, and
copying from one cloud to another are all tried, and then the page is used in English (dark) and Arabic (light),
with pictures saved.

Run: python scripts/newtest_cloud.py [core|page] [folder] [language] [look]
Prints one line per check and "newtest_cloud: N problem(s)"."""
import base64
import hashlib
import http.server
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PART = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("core", "page") else "all"
ARGS = [a for a in sys.argv[1:] if a not in ("core", "page")]
OUT = Path(ARGS[0] if ARGS else ROOT / "out" / "newtest_cloud").resolve()
LANG = ARGS[1] if len(ARGS) > 1 else "en"
LOOK = ARGS[2] if len(ARGS) > 2 else "dark"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-cloudtest-"))
HOME = TMP / "home"
for name, folder in (("APPDATA", "roaming"), ("LOCALAPPDATA", "local"), ("USERPROFILE", "home"), ("HOME", "home")):
    os.environ[name] = str(TMP / folder)
    (TMP / folder).mkdir(parents=True, exist_ok=True)
for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
    os.environ.pop(name, None)
os.environ["COREFOLD_ONEDRIVE_ID"] = "test-onedrive-app"
os.environ["COREFOLD_DROPBOX_KEY"] = "test-dropbox-app"
os.environ["COREFOLD_GOOGLE_ID"] = "test-google-app"
os.environ["COREFOLD_GOOGLE_SECRET"] = "test-google-secret"
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(280, exit=True)  # stuck somewhere: say where, and stop

problems = []
ISSUED: set[str] = set()   # every sign-in key the pretend services gave out
LOGGED: list[str] = []     # everything Corefold logged while the test ran


class _Keep(logging.Handler):
    def emit(self, record):
        try:
            LOGGED.append(logging.Formatter().format(record))
        except Exception:
            pass


logging.getLogger().addHandler(_Keep(logging.DEBUG))
logging.getLogger().setLevel(logging.DEBUG)


def no_keys_logged():
    """No sign-in key the services gave out is in Corefold's log (its own file, or anything it logged)."""
    text = "\n".join(LOGGED)
    for f in TMP.rglob("*.log*"):
        try:
            text += "\n" + f.read_text("utf-8", "replace")
        except OSError:
            pass
    leaked = sorted(k for k in ISSUED if re.search(re.escape(k) + r"(?![0-9])", text))
    check(not leaked, f"no sign-in key is ever written to the log ({len(ISSUED)} given out; in the log: {leaked[:3]})")


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)
    return ok


def step(name, fn):
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc(limit=6)}")


# ----------------------------------------------------------------------------- a pretend OneDrive and Dropbox

class Store:
    """One service's files: {id: {name, parent, folder, data, modified}}. "root" is the top."""

    def __init__(self):
        self.items = {"root": {"name": "", "parent": None, "folder": True, "data": b"", "modified": time.time()}}
        self.n = 0

    def new_id(self):
        self.n += 1
        return f"i{self.n}"

    def children(self, pid):
        return [(i, d) for i, d in self.items.items() if d["parent"] == pid]

    def find(self, pid, name):
        for i, d in self.children(pid):
            if d["name"].lower() == name.lower():
                return i
        return None

    def add(self, pid, name, folder=False, data=b"", rename=True):
        base, n = name, 1
        while self.find(pid, name) is not None:
            if not rename:
                return None
            stem, dot, ext = base.rpartition(".") if not folder and "." in base else (base, "", "")
            name = f"{stem} ({n}){dot}{ext}"
            n += 1
        i = self.new_id()
        self.items[i] = {"name": name, "parent": pid, "folder": folder, "data": data, "modified": time.time()}
        return i

    def path(self, i):
        parts = []
        while i not in (None, "root"):
            parts.append(self.items[i]["name"])
            i = self.items[i]["parent"]
        return "/" + "/".join(reversed(parts)) if parts else ""

    def by_path(self, path):
        if path in ("", "/"):
            return "root"
        i = "root"
        for part in path.strip("/").split("/"):
            i = self.find(i, part)
            if i is None:
                return None
        return i

    def remove(self, i):
        for c, _ in self.children(i):
            self.remove(c)
        self.items.pop(i, None)

    def file_named(self, name):
        return next((d for d in self.items.values() if d["name"] == name and not d["folder"]), None)


class Fake:
    def __init__(self):
        self.ms, self.dbx = Store(), Store()
        self.codes = {}            # code -> (challenge, client id, redirect)
        self.access = {}           # token -> service
        self.refresh = {}          # refresh token -> service
        self.n = 0
        self.fail = []             # [(service, path part, code, times)]: answer with an error first
        self.seen = []             # (method, path, had Authorization)
        self.uploads = {}
        self.cancelled_uploads = 0
        self.revoked = 0
        self.who = {}              # Microsoft key -> email (several Microsoft accounts at once)
        self.next_who = "sam@example.com"
        self.ms_drop_piece = 0     # upload pieces whose answer never comes back
        self.ms_spoil = 0          # uploads whose bytes arrive changed
        self.ms_kind = "personal"
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        server.daemon_threads = True
        self.server = server
        self.base = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def token(self, service):
        self.n += 1
        a, r = f"{service}-A{self.n}", f"{service}-R{self.n}"
        self.access[a], self.refresh[r] = service, service
        ISSUED.update((a, r))
        return {"access_token": a, "refresh_token": r, "expires_in": 3600, "token_type": "bearer"}

    def handler(self):
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def send(self, code, data=None, headers=None):
                raw = b"" if data is None else data if isinstance(data, bytes) else json.dumps(data).encode()
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if data is not None and not isinstance(data, bytes):
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def authed(self, service):
                auth = self.headers.get("Authorization", "")
                ok = auth.startswith("Bearer ") and fake.access.get(auth[7:]) == service
                if not ok:
                    self.send(401, {"error": {"code": "InvalidAuthenticationToken", "message": "expired"}})
                return ok

            def injected(self, service):
                for k, (svc, part, code, times) in enumerate(fake.fail):
                    if svc == service and part in self.path and times > 0:
                        fake.fail[k] = (svc, part, code, times - 1)
                        detail = {507: {"error": {"code": "quotaLimitReached", "message": "full"}},
                                  409: {"error": {"code": "nameAlreadyExists", "message": "exists"}}}.get(
                            code, {"error": {"code": "busy", "message": "busy"}})
                        self.send(code, detail, {"Retry-After": "0"} if code in (429, 503) else None)
                        return True
                return False

            def do_GET(self):
                self.route("GET")

            def do_POST(self):
                self.route("POST")

            def do_PUT(self):
                self.route("PUT")

            def do_PATCH(self):
                self.route("PATCH")

            def do_DELETE(self):
                self.route("DELETE")

            def route(self, method):
                fake.seen.append((method, self.path, "Authorization" in self.headers))
                data = self.body()
                try:
                    if self.path.startswith("/ms/"):
                        return self.ms_login(data)
                    if self.path.startswith("/graph/"):
                        if self.injected("ms"):
                            return
                        return self.graph(method, data)
                    if self.path.startswith("/blob/"):
                        d = fake.ms.items.get(self.path[6:])
                        return self.send(200, d["data"], {"Content-Type": "application/octet-stream"})
                    if self.path.startswith("/upload/"):
                        return self.graph_upload(method, data)
                    if self.path.startswith("/dbx/oauth2/token"):
                        return self.dbx_login(data)
                    if self.path.startswith("/dbx/"):
                        if self.injected("dbx"):
                            return
                        return self.dropbox(data)
                    self.send(404, {"error": "no such page"})
                except Exception:
                    traceback.print_exc()
                    self.send(500, {"error": "pretend service broke"})

            # -- Microsoft
            def ms_login(self, data):
                q = dict(urllib.parse.parse_qsl(data.decode()))
                if q.get("grant_type") == "authorization_code":
                    challenge, cid, redirect = fake.codes.pop(q.get("code"), (None, None, None))
                    proof = base64.urlsafe_b64encode(hashlib.sha256(q.get("code_verifier", "").encode()).digest()) \
                        .rstrip(b"=").decode()
                    if challenge is None or proof != challenge or cid != q.get("client_id") or \
                            redirect != q.get("redirect_uri"):
                        return self.send(400, {"error": "invalid_grant", "error_description": "bad code"})
                    tok = fake.token("ms")
                    fake.who[tok["access_token"]] = fake.who[tok["refresh_token"]] = fake.next_who
                    return self.send(200, tok)
                old = q.get("refresh_token")
                if fake.refresh.pop(old, None) == "ms":
                    tok = fake.token("ms")
                    email = fake.who.get(old, "sam@example.com")
                    fake.who[tok["access_token"]] = fake.who[tok["refresh_token"]] = email
                    return self.send(200, tok)
                self.send(400, {"error": "invalid_grant", "error_description": "refresh token revoked"})

            def item(self, i):
                s = fake.ms
                d = s.items[i]
                parent = d["parent"]
                out = {"id": i, "name": d["name"], "size": len(d["data"]),
                       "lastModifiedDateTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(d["modified"])),
                       "parentReference": {"id": parent or "", "path": "/drive/root:" + s.path(parent)
                                           if parent else ""}}
                out["folder" if d["folder"] else "file"] = {}
                if not d["folder"]:
                    from corefold.cloud import QuickXorHash
                    h = QuickXorHash()
                    h.update(d["data"])
                    out["file"] = {"hashes": {"quickXorHash": h.digest()}}
                if d.get("package"):
                    out.pop("file")
                    out["package"] = {"type": "oneNote"}
                return out

            @staticmethod
            def spoiled(data):
                if fake.ms_spoil and data:
                    fake.ms_spoil -= 1
                    return data[:-1] + bytes([data[-1] ^ 1])
                return data

            def graph(self, method, data):
                if not self.authed("ms"):
                    return
                s = fake.ms
                u = urllib.parse.urlparse(self.path)
                p = urllib.parse.unquote(u.path)[len("/graph/v1.0"):]
                q = dict(urllib.parse.parse_qsl(u.query))
                if p == "/me":
                    email = fake.who.get(self.headers["Authorization"][7:], "sam@example.com")
                    return self.send(200, {"displayName": "Sam Tester", "mail": email})
                if p == "/me/drive":
                    return self.send(200, {"quota": {"used": 1500000, "total": 5000000000},
                                           "driveType": fake.ms_kind})
                if p.startswith("/me/drive/root/search(q='"):
                    text = p[len("/me/drive/root/search(q='"):-2].replace("''", "'").lower()
                    return self.send(200, {"value": [self.item(i) for i, d in s.items.items()
                                                     if i != "root" and text in d["name"].lower()]})
                rest = p[len("/me/drive/"):]
                if rest.startswith("root"):
                    pid, rest = "root", rest[4:]
                else:
                    m = re.match(r"items/([^/:]+)(.*)", rest)
                    pid, rest = m.group(1), m.group(2)
                if pid not in s.items:
                    return self.send(404, {"error": {"code": "itemNotFound", "message": "gone"}})
                if rest.startswith(":/"):  # (by name inside a folder)
                    name, _, what = rest[2:].partition(":/")
                    if what == "content" and method == "PUT":
                        i = s.add(pid, name, data=self.spoiled(data))
                        return self.send(201, self.item(i))
                    if what == "createUploadSession":
                        sid = f"s{len(fake.uploads) + 1}"
                        fake.uploads[sid] = {"parent": pid, "name": name, "data": b""}
                        return self.send(200, {"uploadUrl": f"{fake.base}/upload/{sid}"})
                if rest == "/children" and method == "GET":
                    kids = [self.item(i) for i, _ in s.children(pid)]
                    start = int(q.get("$skiptoken", 0))
                    page = {"value": kids[start:start + 3]}
                    if start + 3 < len(kids):  # (in pages of three, so following the next page is tried)
                        page["@odata.nextLink"] = (f"{fake.base}/graph/v1.0/me/drive/"
                                                   f"{'root' if pid == 'root' else 'items/' + pid}/children?"
                                                   f"$skiptoken={start + 3}")
                    return self.send(200, page)
                if rest == "/children" and method == "POST":
                    body = json.loads(data)
                    i = s.add(pid, body["name"], folder=True)
                    return self.send(201, self.item(i))
                if rest == "/content" and method == "GET":
                    return self.send(302, b"", {"Location": f"{fake.base}/blob/{pid}"})
                if rest == "" and method == "PATCH":
                    name = json.loads(data)["name"]
                    other = s.find(s.items[pid]["parent"], name)
                    if other is not None and other != pid:
                        return self.send(409, {"error": {"code": "nameAlreadyExists", "message": "exists"}})
                    s.items[pid]["name"] = name
                    return self.send(200, self.item(pid))
                if rest == "" and method == "DELETE":
                    s.remove(pid)
                    return self.send(204)
                self.send(400, {"error": {"code": "invalidRequest", "message": p}})

            def graph_upload(self, method, data):
                sid = self.path.split("/")[2]
                up = fake.uploads.get(sid)
                if "Authorization" in self.headers:  # (Microsoft says: never send the key here)
                    return self.send(400, {"error": {"code": "invalidRequest", "message": "no auth here"}})
                if up is None:
                    return self.send(404, {"error": {"code": "itemNotFound", "message": "no such upload"}})
                if method == "DELETE":
                    fake.cancelled_uploads += 1
                    fake.uploads.pop(sid, None)
                    return self.send(204)
                if method == "GET":  # (how far it got)
                    return self.send(200, {"nextExpectedRanges": [f"{len(up['data'])}-"]})
                start, end_total = self.headers["Content-Range"][6:].split("-")
                end, total = end_total.split("/")
                if int(start) != len(up["data"]) or int(end) - int(start) + 1 != len(data):
                    return self.send(416, {"error": {"code": "invalidRange", "message": "wrong piece"}})
                up["data"] += data
                if fake.ms_drop_piece and len(up["data"]) < int(total):
                    fake.ms_drop_piece -= 1
                    self.close_connection = True
                    self.connection.close()
                    return
                if len(up["data"]) < int(total):
                    return self.send(202, {"nextExpectedRanges": [f"{len(up['data'])}-"]})
                fake.uploads.pop(sid, None)
                i = fake.ms.add(up["parent"], up["name"], data=self.spoiled(up["data"]))
                return self.send(201, self.item(i))

            # -- Dropbox
            def dbx_login(self, data):
                q = dict(urllib.parse.parse_qsl(data.decode()))
                if q.get("grant_type") == "authorization_code":
                    challenge, cid, redirect = fake.codes.pop(q.get("code"), (None, None, None))
                    proof = base64.urlsafe_b64encode(hashlib.sha256(q.get("code_verifier", "").encode()).digest()) \
                        .rstrip(b"=").decode()
                    if challenge is None or proof != challenge or cid != q.get("client_id") or \
                            redirect != q.get("redirect_uri"):
                        return self.send(400, {"error": "invalid_grant", "error_description": "bad code"})
                    return self.send(200, fake.token("dbx"))
                if fake.refresh.get(q.get("refresh_token")) == "dbx":  # (Dropbox keeps the same refresh key)
                    t = fake.token("dbx")
                    t.pop("refresh_token")
                    return self.send(200, t)
                self.send(400, {"error": "invalid_grant", "error_description": "refresh token is invalid"})

            def meta(self, i):
                s = fake.dbx
                d = s.items[i]
                path = s.path(i)
                out = {".tag": "folder" if d["folder"] else "file", "name": d["name"], "path_display": path,
                       "path_lower": path.lower(), "id": "id:" + i}
                if not d["folder"]:
                    out.update(size=len(d["data"]),
                               server_modified=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(d["modified"])))
                return out

            def dropbox(self, data):
                if not self.authed("dbx"):
                    return
                s = fake.dbx
                p = self.path.split("/dbx/", 1)[1]
                arg = json.loads(self.headers.get("Dropbox-API-Arg") or "null")
                body = json.loads(data) if data and self.headers.get("Content-Type") == "application/json" else None
                if p == "api/2/auth/token/revoke":
                    fake.revoked += 1
                    fake.access.pop(self.headers["Authorization"][7:], None)
                    return self.send(200, None)
                if p == "api/2/users/get_current_account":
                    return self.send(200, {"email": "sam@dropbox.test", "name": {"display_name": "Sam Box"}})
                if p == "api/2/users/get_space_usage":
                    return self.send(200, {"used": 2500000, "allocation": {".tag": "individual",
                                                                            "allocated": 2000000000}})
                if p == "api/2/files/list_folder" or p == "api/2/files/list_folder/continue":
                    path, start = (body["path"], 0) if p.endswith("list_folder") else json.loads(body["cursor"])
                    pid = s.by_path(path)
                    if pid is None:
                        return self.send(409, {"error_summary": "path/not_found/.."})
                    kids = [self.meta(i) for i, _ in s.children(pid)]
                    more = start + 3 < len(kids)
                    return self.send(200, {"entries": kids[start:start + 3], "has_more": more,
                                           "cursor": json.dumps([path, start + 3])})
                if p == "content/2/files/download":
                    i = s.by_path(arg["path"])
                    if i is None:
                        return self.send(409, {"error_summary": "path/not_found/"})
                    return self.send(200, s.items[i]["data"], {"Content-Type": "application/octet-stream",
                                                               "Dropbox-API-Result": json.dumps(self.meta(i))})
                if p == "content/2/files/upload":
                    return self.dbx_commit(arg, data)
                if p == "content/2/files/upload_session/start":
                    sid = f"d{len(fake.uploads) + 1}"
                    fake.uploads[sid] = {"data": data}
                    return self.send(200, {"session_id": sid})
                if p in ("content/2/files/upload_session/append_v2", "content/2/files/upload_session/finish"):
                    cur = arg["cursor"]
                    up = fake.uploads[cur["session_id"]]
                    if cur["offset"] != len(up["data"]):
                        return self.send(409, {"error_summary": "lookup_failed/incorrect_offset/"})
                    up["data"] += data
                    if p.endswith("finish"):
                        return self.dbx_commit(arg["commit"], up["data"])
                    return self.send(200, None)
                if p == "api/2/files/create_folder_v2":
                    parent, _, name = body["path"].rpartition("/")
                    pid = s.by_path(parent)
                    i = s.add(pid, name, folder=True)
                    return self.send(200, {"metadata": self.meta(i)})
                if p == "api/2/files/move_v2":
                    i = s.by_path(body["from_path"])
                    parent, _, name = body["to_path"].rpartition("/")
                    other = s.find(s.items[i]["parent"], name)
                    if other is not None and other != i:
                        return self.send(409, {"error_summary": "to/conflict/file/.."})
                    s.items[i]["name"] = name
                    return self.send(200, {"metadata": self.meta(i)})
                if p == "api/2/files/delete_v2":
                    i = s.by_path(body["path"])
                    if i is None:
                        return self.send(409, {"error_summary": "path_lookup/not_found/"})
                    m = self.meta(i)
                    s.remove(i)
                    return self.send(200, {"metadata": m})
                if p == "api/2/files/search_v2":
                    text = body["query"].lower()
                    return self.send(200, {"matches": [{"metadata": {".tag": "metadata", "metadata": self.meta(i)}}
                                                       for i, d in s.items.items()
                                                       if i != "root" and text in d["name"].lower()]})
                self.send(400, {"error_summary": "unknown/" + p})

            def dbx_commit(self, arg, data):
                s = fake.dbx
                parent, _, name = arg["path"].rpartition("/")
                pid = s.by_path(parent)
                if pid is None:
                    pid = "root"
                i = s.add(pid, name, data=data, rename=arg.get("autorename", False))
                if i is None:
                    return self.send(409, {"error_summary": "path/conflict/file/"})
                m = self.meta(i)
                m.pop(".tag")
                return self.send(200, m)
        return H


def point_at(fake, cloud):
    cloud.MS_LOGIN = fake.base + "/ms"
    cloud.DROPBOX_AUTHORIZE = fake.base + "/dbx/authorize"
    cloud.DROPBOX_OAUTH = fake.base + "/dbx/oauth2/token"
    cloud.OneDrive.API = fake.base + "/graph/v1.0"
    cloud.Dropbox.API = fake.base + "/dbx/api/2"
    cloud.Dropbox.CONTENT = fake.base + "/dbx/content/2"


def browser(fake, how="yes", who="sam@example.com"):
    """Plays the browser: checks the sign-in address, then sends the person back like the service would (as who,
    for Microsoft)."""
    def open_page(url):
        fake.next_who = who
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        check(q.get("code_challenge_method") == "S256" and len(q.get("code_challenge", "")) >= 43,
              "sign-in asks with a PKCE challenge (no secret)")
        check(q.get("redirect_uri", "").startswith("http://localhost:"), f"answer comes back to this PC "
                                                                          f"({q.get('redirect_uri')})")
        if how == "nothing":
            return
        code = f"code-{time.time()}"
        fake.codes[code] = (q["code_challenge"], q["client_id"], q["redirect_uri"])
        back = {"yes": {"code": code, "state": q["state"]}, "no": {"error": "access_denied", "state": q["state"]},
                "wrong": {"error": "access_denied", "state": q["state"]}}[how]

        def go():
            time.sleep(0.3)
            if how == "wrong":  # (another web page sends the person back first, with its own code: it's ignored)
                stray = {"code": code, "state": "someone-else"}
                try:
                    urllib.request.urlopen(q["redirect_uri"] + "?" + urllib.parse.urlencode(stray), timeout=10)
                    check(False, "an answer that isn't the sign-in page's is refused")
                except urllib.error.HTTPError as e:
                    check(e.code == 400, f"an answer that isn't the sign-in page's is refused ({e.code})")
            with urllib.request.urlopen(q["redirect_uri"] + "?" + urllib.parse.urlencode(back), timeout=10) as r:
                page = r.read().decode("utf-8")
            check("Corefold" in page, "the browser shows a page saying it can be closed")
        threading.Thread(target=go, daemon=True).start()
    return open_page


# ----------------------------------------------------------------------------- a pretend Google Drive

class GFake:
    """Google Drive for several Google accounts at once: each has its own files. {id: {name, parents, mime, data,
    modified, trashed, drive, target}}. A trashed file is kept (Google's bin), so it can be checked nothing was
    deleted for good."""
    FOLDER = "application/vnd.google-apps.folder"

    def __init__(self):
        self.files = {}           # email -> {id: item}
        self.drives = {}          # email -> [(drive id, name)]
        self.codes = {}           # code -> (challenge, client id, redirect, email)
        self.access = {}          # token -> email
        self.refresh = {}         # refresh token -> email
        self.revoked = 0
        self.sessions = {}
        self.n = 0
        self.page_size = 3        # (in pages of three, so following the next page is tried)
        self.short_piece = 0      # resumable pieces that only half arrive (Google says how far it got)
        self.drop_piece = 0       # resumable pieces whose answer never comes back
        self.spoil_upload = 0     # uploads whose bytes arrive changed
        self.fail = []            # [(path part, code, reason, times)]
        self.no_scope = False     # the person unticked "see, edit and delete your files"
        self.seen = []
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        server.daemon_threads = True
        self.server = server
        self.base = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def new_id(self):
        self.n += 1
        return f"g{self.n}"

    def account(self, email):
        if email not in self.files:
            self.files[email] = {"root": {"name": "My Drive", "parents": [], "mime": self.FOLDER, "data": b"",
                                          "modified": time.time(), "trashed": False, "drive": "", "target": None}}
            self.drives[email] = []
        return self.files[email]

    def add(self, email, parent, name, data=b"", mime="application/octet-stream", drive="", target=None):
        i = self.new_id()
        self.account(email)[i] = {"name": name, "parents": [parent], "mime": mime, "data": data,
                                  "modified": time.time(), "trashed": False, "drive": drive, "target": target}
        return i

    def named(self, email, name):
        return [(i, d) for i, d in self.account(email).items() if d["name"] == name]

    def token(self, email, refresh=True):
        self.n += 1
        a = f"gA{self.n}"
        self.access[a] = email
        ISSUED.add(a)
        out = {"access_token": a, "expires_in": 3599, "token_type": "Bearer",
               "scope": "openid" if self.no_scope else "https://www.googleapis.com/auth/drive"}
        if refresh:
            r = f"gR{self.n}"
            self.refresh[r] = email
            ISSUED.add(r)
            out["refresh_token"] = r
        return out

    def handler(self):
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def send(self, code, data=None, headers=None):
                raw = b"" if data is None else data if isinstance(data, bytes) else json.dumps(data).encode()
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if data is not None and not isinstance(data, bytes):
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def error(self, code, reason, message=""):
                self.send(code, {"error": {"code": code, "message": message or reason,
                                           "errors": [{"reason": reason, "message": message or reason}]}})

            def who(self):
                auth = self.headers.get("Authorization", "")
                email = fake.access.get(auth[7:]) if auth.startswith("Bearer ") else None
                if email is None:
                    self.error(401, "authError", "Invalid Credentials")
                return email

            def do_GET(self):
                self.route("GET")

            def do_POST(self):
                self.route("POST")

            def do_PUT(self):
                self.route("PUT")

            def do_PATCH(self):
                self.route("PATCH")

            def do_DELETE(self):
                self.route("DELETE")

            def route(self, method):
                fake.seen.append((method, self.path, "Authorization" in self.headers))
                data = self.body()
                try:
                    u = urllib.parse.urlparse(self.path)
                    q = dict(urllib.parse.parse_qsl(u.query))
                    for k, (part, code, reason, times) in enumerate(fake.fail):
                        if part in self.path and times > 0:
                            fake.fail[k] = (part, code, reason, times - 1)
                            return self.error(code, reason)
                    if u.path == "/g/token":
                        return self.login(data)
                    if u.path == "/g/revoke":
                        token = dict(urllib.parse.parse_qsl(data.decode())).get("token")
                        if fake.refresh.pop(token, None) is None:
                            return self.send(400, {"error": "invalid_token"})
                        fake.revoked += 1
                        return self.send(200, {})
                    if u.path.startswith("/g/session/"):
                        return self.session(method, u.path.rsplit("/", 1)[1], data)
                    email = self.who()
                    if email is None:
                        return
                    files = fake.account(email)
                    p = u.path
                    if p == "/g/drive/v3/about":
                        return self.send(200, {"user": {"displayName": email.split("@")[0].title(),
                                                        "emailAddress": email},
                                               "storageQuota": {"limit": "16106127360", "usage": "3221225472"}})
                    if p == "/g/drive/v3/drives":
                        return self.send(200, {"drives": [{"id": i, "name": n} for i, n in fake.drives[email]]})
                    if p == "/g/drive/v3/files" and method == "GET":
                        return self.listing(email, files, q)
                    if p == "/g/drive/v3/files" and method == "POST":
                        meta = json.loads(data)
                        i = fake.add(email, meta["parents"][0], meta["name"], mime=meta.get("mimeType", ""),
                                     drive=self.drive_of(files, meta["parents"][0]))
                        return self.send(200, self.meta(files, i))
                    m = re.match(r"/g/drive/v3/files/([^/]+)(/export)?$", p)
                    if m:
                        i = urllib.parse.unquote(m.group(1))
                        if i not in files:
                            return self.error(404, "notFound", f"File not found: {i}.")
                        d = files[i]
                        if m.group(2):
                            raw = f"exported {d['name']} as {q.get('mimeType')}".encode()
                            return self.send(200, raw, {"Content-Type": q.get("mimeType", "")})
                        if method == "GET" and q.get("alt") == "media":
                            if d["mime"].startswith("application/vnd.google-apps."):
                                return self.error(403, "fileNotDownloadable", "Only files with binary content")
                            return self.send(200, d["data"], {"Content-Type": "application/octet-stream"})
                        if method == "PATCH":
                            change = json.loads(data)
                            if "name" in change:
                                d["name"] = change["name"]
                            if change.get("trashed"):
                                d["trashed"] = True
                            return self.send(200, self.meta(files, i))
                    if p == "/g/upload/drive/v3/files" and method == "POST":
                        if q.get("uploadType") == "multipart":
                            bound = self.headers["Content-Type"].split("boundary=")[1]
                            parts = data.split(b"--" + bound.encode())
                            meta = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0])
                            content = parts[2].split(b"\r\n\r\n", 1)[1][:-2]
                            return self.created(email, files, meta, content)
                        sid = f"s{len(fake.sessions) + 1}"
                        fake.sessions[sid] = {"email": email, "meta": json.loads(data), "data": b"",
                                              "size": int(self.headers["X-Upload-Content-Length"])}
                        return self.send(200, {}, {"Location": f"{fake.base}/g/session/{sid}"})
                    self.error(400, "badRequest", p)
                except Exception:
                    traceback.print_exc()
                    self.error(500, "backendError")

            def login(self, data):
                q = dict(urllib.parse.parse_qsl(data.decode()))
                if q.get("client_secret") != "test-google-secret" or q.get("client_id") != "test-google-app":
                    return self.send(401, {"error": "invalid_client"})
                if q.get("grant_type") == "authorization_code":
                    challenge, cid, redirect, email = fake.codes.pop(q.get("code"), (None,) * 4)
                    proof = base64.urlsafe_b64encode(hashlib.sha256(q.get("code_verifier", "").encode()).digest()) \
                        .rstrip(b"=").decode()
                    if challenge is None or proof != challenge or redirect != q.get("redirect_uri"):
                        return self.send(400, {"error": "invalid_grant", "error_description": "Bad Request"})
                    return self.send(200, fake.token(email))
                email = fake.refresh.get(q.get("refresh_token"))
                if email is None:
                    return self.send(400, {"error": "invalid_grant", "error_description": "Token has been expired "
                                                                                          "or revoked."})
                return self.send(200, fake.token(email, refresh=False))  # (Google keeps the same refresh key)

            @staticmethod
            def drive_of(files, i):
                return files[i]["drive"] if i in files else (i if i.startswith("drive") else "")

            def meta(self, files, i):
                d = files[i]
                out = {"id": i, "name": d["name"], "mimeType": d["mime"], "parents": d["parents"],
                       "modifiedTime": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(d["modified"])),
                       "webViewLink": f"https://drive.google.com/file/d/{i}/view"}
                if d["drive"]:
                    out["driveId"] = d["drive"]
                if not d["mime"].startswith("application/vnd.google-apps."):
                    out["size"] = str(len(d["data"]))
                    out["md5Checksum"] = hashlib.md5(d["data"]).hexdigest()
                if d["target"]:
                    t = files[d["target"]]
                    out["shortcutDetails"] = {"targetId": d["target"], "targetMimeType": t["mime"]}
                return out

            def listing(self, email, files, q):
                text = q.get("q", "")
                live = {i: d for i, d in files.items() if not d["trashed"] and i != "root"}
                m = re.fullmatch(r"'(.+?)' in parents and (?:name='(.*)' and )?trashed=false", text)
                if m:
                    parent = m.group(1).replace("\\'", "'")
                    known = parent in files and not files[parent]["trashed"] or \
                        any(parent == d for d, _ in fake.drives[email])
                    if not known:
                        return self.error(404, "notFound", f"File not found: {parent}.")
                    hits = [i for i, d in live.items() if parent in d["parents"] and
                            (m.group(2) is None or d["name"] == m.group(2).replace("\\'", "'"))]
                elif text == "sharedWithMe and trashed=false":
                    hits = [i for i, d in live.items() if d["parents"] == ["shared"]]
                else:
                    m = re.fullmatch(r"name contains '(.*)' and trashed=false", text)
                    if not m:
                        return self.error(400, "invalid", "Invalid Value")
                    want = m.group(1).replace("\\'", "'").lower()
                    hits = [i for i, d in live.items() if want in d["name"].lower()]
                start = int(q.get("pageToken", 0))
                page = {"files": [self.meta(files, i) for i in hits[start:start + fake.page_size]]}
                if start + fake.page_size < len(hits):
                    page["nextPageToken"] = str(start + fake.page_size)
                return self.send(200, page)

            def created(self, email, files, meta, content):
                if fake.spoil_upload:
                    fake.spoil_upload -= 1
                    content = content[:-1] + bytes([content[-1] ^ 1]) if content else b"x"
                parent = meta["parents"][0]
                i = fake.add(email, parent, meta["name"], data=content, drive=self.drive_of(files, parent))
                return self.send(200, self.meta(files, i))

            def session(self, method, sid, data):
                up = fake.sessions.get(sid)
                if up is None:
                    return self.error(404, "notFound", "No such upload")
                if "Authorization" in self.headers:  # (the upload's own address is all it needs)
                    return self.error(400, "badRequest", "no auth here")
                if method == "DELETE":
                    fake.sessions.pop(sid)
                    return self.send(499, {})
                rng = self.headers["Content-Range"][6:]
                span, total = rng.split("/")
                if span != "*":
                    a, b = (int(x) for x in span.split("-"))
                    if a > len(up["data"]) or b - a + 1 != len(data):
                        return self.error(400, "badRequest", "wrong piece")
                    data = data[len(up["data"]) - a:]  # (what Google already has isn't kept twice)
                    if fake.short_piece and len(data) > 1:
                        fake.short_piece -= 1
                        data = data[:len(data) // 2]
                    up["data"] += data
                    if fake.drop_piece:
                        fake.drop_piece -= 1
                        self.close_connection = True
                        self.connection.close()
                        return
                if len(up["data"]) < up["size"]:
                    head = {"Range": f"bytes=0-{len(up['data']) - 1}"} if up["data"] else {}
                    return self.send(308, b"", head)
                files = fake.account(up["email"])
                return self.created(up["email"], files, up["meta"], up["data"])
        return H


def g_browser(fake, email, how="yes"):
    """Plays the browser for Google: checks the sign-in address, and picks this account on Google's page."""
    def open_page(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        check(q.get("code_challenge_method") == "S256" and len(q.get("code_challenge", "")) >= 43,
              "Google: sign-in asks with a PKCE challenge")
        check(q.get("redirect_uri", "").startswith("http://127.0.0.1:"), f"Google: the answer comes back to this PC "
                                                                         f"({q.get('redirect_uri')})")
        check("select_account" in q.get("prompt", "") and q.get("access_type") == "offline",
              "Google asks which account each time, and for a key that lasts")
        check("client_secret" not in q, "the sign-in page address carries no secret")
        code = f"gcode-{time.time()}"
        fake.codes[code] = (q["code_challenge"], q["client_id"], q["redirect_uri"], email)
        back = {"yes": {"code": code, "state": q["state"]}, "no": {"error": "access_denied", "state": q["state"]}}[how]

        def go():
            time.sleep(0.3)
            with urllib.request.urlopen(q["redirect_uri"] + "?" + urllib.parse.urlencode(back), timeout=10) as r:
                r.read()
        threading.Thread(target=go, daemon=True).start()
    return open_page


def g_point_at(fake, cloud):
    cloud.GOOGLE_AUTHORIZE = fake.base + "/g/authorize"
    cloud.GOOGLE_TOKEN = fake.base + "/g/token"
    cloud.GOOGLE_REVOKE = fake.base + "/g/revoke"
    cloud.GoogleDrive.API = fake.base + "/g/drive/v3"
    cloud.GoogleDrive.UPLOAD = fake.base + "/g/upload/drive/v3"


# ----------------------------------------------------------------------------- folders like the services' apps leave

def make_files(root: Path, label: str):
    (root / "Photos" / "2024").mkdir(parents=True, exist_ok=True)
    (root / "Photos" / "2024" / "beach.jpg").write_bytes(b"\xff\xd8" + os.urandom(3000))
    (root / f"{label} report.docx").write_bytes(os.urandom(5000))
    (root / "notes.txt").write_text("hello from " + label)
    (root / "desktop.ini").write_text("[.ShellClassInfo]")  # (never shown)
    (root / ".dropbox").write_text("{}")


def fake_pc_folders():
    od = HOME / "OneDrive"
    make_files(od, "OneDrive")
    os.environ["OneDriveConsumer"] = str(od)
    dbx = HOME / "Dropbox"
    make_files(dbx, "Dropbox")
    (TMP / "local" / "Dropbox").mkdir(parents=True, exist_ok=True)
    (TMP / "local" / "Dropbox" / "info.json").write_text(json.dumps({"personal": {
        "path": str(dbx), "host": 1, "is_team": False, "subscription_type": "Basic"}}))
    (TMP / "local" / "Google" / "DriveFS").mkdir(parents=True, exist_ok=True)
    make_files(HOME / "My Drive", "Google")
    make_files(HOME / "iCloudDrive", "iCloud")
    return od, dbx


def same_tree(a: Path, b: Path) -> bool:
    def files(root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    return files(a) == files(b)


def microsoft(cloud, fake, accounts):
    """OneDrive: two Microsoft accounts at once, and the ways an upload could quietly not arrive whole."""
    def two():
        acc = cloud.sign_in("onedrive", browser(fake, who="work@example.com"), wait_s=20)
        kept = sorted(a.key for a in cloud.load_accounts() if a.service == "onedrive")
        check(acc.email == "work@example.com" and kept == ["onedrive:sam@example.com", "onedrive:work@example.com"],
              f"two Microsoft accounts are kept at once ({kept})")
        check(acc.extra.get("personal") is True, "a personal OneDrive is known as one")
        accounts["onedrive-work"] = acc
    step("OneDrive: two accounts", two)
    acc = accounts.get("onedrive-work")
    if acc is None:
        return
    c = cloud.client(acc)

    def uploads():
        big = TMP / "ms-big.bin"
        big.write_bytes(os.urandom(300 * 1024))
        fake.ms_drop_piece = 2
        e = c.upload(big, None)
        stored = fake.ms.items.get(e.id)
        check(stored is not None and stored["data"] == big.read_bytes(),
              f"a big upload whose answers get lost still arrives whole ({len(stored['data']) if stored else 0})")
        small = TMP / "ms-small.txt"
        small.write_text("exactly this")
        fake.ms_spoil = 1
        try:
            c.upload(small, None)
            check(False, "a spoiled OneDrive upload should fail")
        except cloud.CloudError as err:
            check(str(err) == cloud.NOT_SENT_WHOLE, f"a OneDrive upload that arrives changed is caught ({err})")
        check(fake.ms.file_named("ms-small.txt") is None, "and the spoiled copy is taken away")
        fake.ms_spoil = 1
        try:
            c.upload(big, None)
            check(False, "a spoiled big OneDrive upload should fail")
        except cloud.CloudError as err:
            check(str(err) == cloud.NOT_SENT_WHOLE, f"a big one that arrives changed too ({err})")
        acc.extra["personal"] = False  # (work or school: SharePoint changes Office files as they arrive)
        fake.ms_spoil = 1
        e = c.upload(small, None)
        check(e.name.startswith("ms-small"), "a work or school OneDrive isn't held to the bytes sent")
        acc.extra["personal"] = True
    step("OneDrive: uploads", uploads)

    def notebook():
        i = fake.ms.add("root", "Class notes", data=b"")
        fake.ms.items[i]["package"] = True
        e = next(x for x in c.list(None) if x.name == "Class notes")
        check(e.kind == "web" and not e.folder, "a OneNote notebook is known as one that opens on the website")
        box = TMP / "ms-notebook"
        box.mkdir()
        c.skipped.clear()
        n = cloud.download_tree(c, e, box)
        check(n == 0 and c.skipped == ["Class notes"] and not any(box.iterdir()),
              "downloading it is said, not a broken file")
    step("OneDrive: OneNote notebooks", notebook)

def google(cloud):
    """Google Drive online: two Google accounts at once, everything a file manager does, and the ways files could be
    lost (a shortcut that leads round in a circle, deleting through a shortcut, an upload cut off or spoiled)."""
    g = GFake()
    g_point_at(g, cloud)
    accs = {}

    def sign_in_two():
        for email in ("sam@gmail.com", "work.sam@gmail.com"):
            acc = cloud.sign_in("gdrive", g_browser(g, email), wait_s=20)
            check(acc.email == email and acc.service == "gdrive", f"Google: signed in as {acc.email}")
            check(acc.total == 16106127360 and acc.used == 3221225472, f"Google: space is known ({acc.used} of "
                                                                       f"{acc.total})")
            accs[email] = acc
        kept = sorted(a.key for a in cloud.load_accounts() if a.service == "gdrive")
        check(kept == ["gdrive:sam@gmail.com", "gdrive:work.sam@gmail.com"], f"two Google accounts are kept at once "
                                                                             f"({kept})")
        again = cloud.sign_in("gdrive", g_browser(g, "sam@gmail.com"), wait_s=20)
        kept = [a.key for a in cloud.load_accounts() if a.service == "gdrive"]
        check(len(kept) == 2 and again.refresh, "signing in to the same one again doesn't add it twice")
        accs["sam@gmail.com"] = again
        g.no_scope = True
        try:
            cloud.sign_in("gdrive", g_browser(g, "third@gmail.com"), wait_s=20)
            check(False, "Google: signing in without leave to see the files should fail")
        except cloud.CloudError as e:
            check(str(e) == cloud.NO_FILES_ALLOWED, f"Google: unticking the files box says so ({e})")
        g.no_scope = False
        check(len([a for a in cloud.load_accounts() if a.service == "gdrive"]) == 2, "and isn't kept")
    step("Google: two accounts", sign_in_two)
    if len(accs) < 2:
        return

    def real(entries):
        return [e for e in entries if e.kind != "virtual"]

    def files():
        acc = accs["sam@gmail.com"]
        c = cloud.client(acc)
        top = c.list(None)
        check(real(top) == [] and [e.kind for e in top] == ["virtual"], f"Google: a new account is empty, with "
                                                                         f"Shared with me ({[e.name for e in top]})")
        other = cloud.client(accs["work.sam@gmail.com"])
        g.add("work.sam@gmail.com", "root", "work only.txt", b"work")
        check([e.name for e in real(other.list(None))] == ["work only.txt"] and real(c.list(None)) == [],
              "Google: each account shows only its own files")
        src = TMP / "up-gdrive"
        make_files(src, "gdrive")
        (src / "big.bin").write_bytes(os.urandom(700 * 1024))   # (resumable, in 256 KB pieces)
        (src / "empty.txt").write_bytes(b"")
        seen = []
        n = cloud.upload_tree(c, src, None, lambda p: seen.append(p))
        check(n == 7, f"Google: a whole folder goes up ({n} files)")
        check(any(t == 700 * 1024 and 0 < d < t for _, d, t in seen), "Google: a big file shows progress in pieces")
        folder = next(e for e in real(c.list(None)) if e.folder)
        names = sorted(e.name for e in c.list(folder))
        check({"Photos", "big.bin", "notes.txt", "empty.txt"} <= set(names), f"Google: listed in pages ({names})")
        back = TMP / "down-gdrive"
        back.mkdir()
        n = cloud.download_tree(c, folder, back)
        check(n == 7 and same_tree(src, back / folder.name), f"Google: the folder comes back the same ({n})")
        again = cloud.upload_tree(c, src / "notes.txt", folder)
        names2 = sorted(e.name for e in c.list(folder))
        check(again == 1 and "notes (1).txt" in names2 and names2.count("notes.txt") == 1,
              f"Google: the same name again is kept beside it, never two of one name ({names2})")
        made = c.new_folder("Photos", folder)
        check(made.folder and made.name == "Photos (1)", f"Google: a new folder with a name that's there ({made.name})")
        r = c.rename(next(e for e in c.list(folder) if e.name == "notes (1).txt"), "renamed.txt")
        check(r.name == "renamed.txt", "Google: renamed")
        try:
            c.rename(r, "notes.txt")
            check(False, "Google: renaming onto another file should fail")
        except cloud.CloudError as e:
            check(str(e) == "something with that name is already there", f"Google: a name already there: {e}")
        c.delete(r)
        gone = g.named("sam@gmail.com", "renamed.txt")
        check("renamed.txt" not in [e.name for e in c.list(folder)] and gone and gone[0][1]["trashed"],
              "Google: deleting moves it to Google Drive's bin (not deleted for good)")
        found = c.search("beach")
        check([e.name for e in found] == ["beach.jpg"], f"Google: search finds it ({[e.name for e in found]})")
        found = c.search("it's")
        check(found == [], "Google: a ' in a search is fine")
        try:
            c.list(cloud.Entry("gone", True, "g999999"))
            check(False, "Google: a deleted folder should fail")
        except cloud.CloudError as e:
            check(str(e) == "it isn't there any more", f"Google: a folder deleted elsewhere: {e}")
        if os.name == "nt":
            zone = back / folder.name / "notes.txt:Zone.Identifier"
            check(zone.exists() and "drive.google.com" in zone.read_text(),
                  "Google: a downloaded file has Windows' \"from the internet\" mark")
    step("Google: files", files)

    def googles_own():
        email = "sam@gmail.com"
        c = cloud.client(accs[email])
        box = g.add(email, "root", "Mixed", mime=g.FOLDER)
        g.add(email, box, "Budget", mime="application/vnd.google-apps.spreadsheet")
        g.add(email, box, "Survey", mime="application/vnd.google-apps.form")
        g.add(email, box, "photo.jpg", b"\xff\xd8 photo")
        g.add(email, box, "Back to the top", mime="application/vnd.google-apps.shortcut", target=box)
        keep = g.add(email, "root", "Precious.txt", b"must never be lost")
        g.add(email, box, "Precious shortcut", mime="application/vnd.google-apps.shortcut", target=keep)
        entry = next(e for e in c.list(None) if e.name == "Mixed")
        inside = {e.name: e for e in c.list(entry)}
        check(inside["Budget"].kind.endswith(".xlsx") and inside["Survey"].kind == "web",
              f"Google Docs are known ({[(n, e.kind) for n, e in inside.items()]})")
        check(inside["Back to the top"].folder and inside["Back to the top"].own,
              "a shortcut to a folder shows as that folder")
        back = TMP / "down-google-own"
        back.mkdir()
        c.skipped.clear()
        t = time.monotonic()
        n = cloud.download_tree(c, entry, back)
        got = sorted(p.name for p in (back / "Mixed").iterdir())
        check(time.monotonic() - t < 20 and "Budget.xlsx" in got and "photo.jpg" in got,
              f"a folder with a shortcut to itself is saved once, not round and round ({n}: {got})")
        check((back / "Mixed" / "Budget.xlsx").read_bytes().startswith(b"exported Budget"),
              "a Google Sheet is saved as an Excel file")
        check(c.skipped == ["Survey"], f"a Google Form is left out, and said ({c.skipped})")
        c.delete(inside["Precious shortcut"])
        check(not g.account(email)[keep]["trashed"] and any(d["trashed"] for _, d in g.named(email,
                                                                                               "Precious shortcut")),
              "deleting a shortcut takes only the shortcut, never the file it leads to")
        c.rename(inside["Back to the top"], "Shortcut")
        check(g.account(email)[box]["name"] == "Mixed", "renaming a shortcut doesn't rename the folder it leads to")
        shared = next(e for e in c.list(None) if e.kind == "virtual")
        g.add(email, "shared", "From a friend.pdf", b"%PDF-1.4 friend")
        check([e.name for e in c.list(shared)] == ["From a friend.pdf"], "Shared with me shows what's shared")
        for what, fn in (("deleted", lambda: c.delete(shared)), ("renamed", lambda: c.rename(shared, "x")),
                         ("uploaded into", lambda: c.upload(TMP / "up-gdrive" / "notes.txt", shared))):
            try:
                fn()
                check(False, f"Shared with me can't be {what}")
            except cloud.CloudError:
                check(True, f"Shared with me can't be {what}")
        g.drives[email].append(("drive1", "Team"))
        top = c.list(None)
        check([e.name for e in top if e.kind == "virtual"] == ["Shared with me", "Shared drives"],
              "Shared drives show when there are any")
        drives = c.list(next(e for e in top if e.name == "Shared drives"))
        check([(e.name, e.kind) for e in drives] == [("Team", "drive")], "with each shared drive")
        up = c.upload(TMP / "up-gdrive" / "notes.txt", drives[0])
        check(up.drive == "drive1" and [e.name for e in c.list(drives[0])] == ["notes.txt"],
              "a file goes into a shared drive")
    step("Google: its own kinds of files, shortcuts, shared", googles_own)

    def uploads_that_go_wrong():
        email = "work.sam@gmail.com"
        c = cloud.client(accs[email])
        big = TMP / "g-big.bin"
        big.write_bytes(os.urandom(900 * 1024))
        for what in ("short_piece", "drop_piece"):
            setattr(g, what, 2)
            e = c.upload(big, None)
            stored = next(d for i, d in g.account(email).items() if i == e.id)
            check(stored["data"] == big.read_bytes(), f"a big upload where {what.replace('_', ' ')} still arrives "
                                                      f"whole ({len(stored['data'])} of {big.stat().st_size})")
        g.spoil_upload = 1
        small = TMP / "g-small.txt"
        small.write_text("exactly this")
        try:
            c.upload(small, None)
            check(False, "a spoiled upload should fail")
        except cloud.CloudError as e:
            check(str(e) == cloud.NOT_SENT_WHOLE, f"an upload that arrives changed is caught ({e})")
        spoiled = g.named(email, "g-small.txt")
        check(spoiled and all(d["trashed"] for _, d in spoiled), "and the spoiled copy is put in the bin")
        stop = threading.Event()
        try:
            c.upload(big, None, lambda d, t: stop.set() if d else None, stop)
            check(False, "Google: cancelling should stop the upload")
        except cloud.CloudError as e:
            check(str(e) == "cancelled", f"Google: Cancel stops a big upload ({e})")
        g.fail.append(("/upload/drive/v3/files", 403, "storageQuotaExceeded", 1))
        try:
            c.upload(small, None)
            check(False, "a full Google account should fail")
        except cloud.CloudError as e:
            check(str(e) == "there's no space left in this account", f"a full Google account says so ({e})")
        g.fail.append(("/drive/v3/files?", 403, "userRateLimitExceeded", 1))
        t = time.monotonic()
        c.list(None)
        check(time.monotonic() - t < 8, "Google's \"too many requests\" is asked again and answers")
        g.fail.append(("/export", 403, "exportSizeLimitExceeded", 1))
        sheet = g.add(email, "root", "Huge sheet", mime="application/vnd.google-apps.spreadsheet")
        e = next(x for x in c.list(None) if x.id == sheet)
        try:
            c.download(e, TMP / "huge.xlsx")
            check(False, "a too big Google Sheet should fail")
        except cloud.CloudError as err:
            check(str(err) == cloud.TOO_BIG_TO_EXPORT and not (TMP / "huge.xlsx").exists(),
                  f"a Google Sheet too big to turn into a file says so ({err})")
        leaked = [p for m, p, auth in g.seen if auth and p.startswith("/g/session/")]
        check(not leaked, "the key is never sent to Google's upload addresses")
    step("Google: uploads that go wrong", uploads_that_go_wrong)

    def keys():
        acc = accs["work.sam@gmail.com"]
        c = cloud.client(acc)
        old = acc.access
        g.access.pop(old, None)
        c.list(None)
        check(acc.access != old and acc.refresh, "Google: a key that ran out is renewed (with the app's secret)")
        before = g.revoked
        c.sign_out()
        cloud.forget_account(acc.key)
        check(g.revoked == before + 1 and acc.key not in [a.key for a in cloud.load_accounts()],
              "Google: signing out takes the key back at Google too, and Corefold forgets it")
        check("gdrive:sam@gmail.com" in [a.key for a in cloud.load_accounts()], "the other Google account stays")
        try:
            cloud.client(acc).list(None)
        except cloud.SignInNeeded:
            check(True, "a taken back Google key asks to sign in again")
    step("Google: keys", keys)

    def unreadable_accounts_file():
        good = cloud.ACCOUNTS_FILE.read_bytes()
        cloud.ACCOUNTS_FILE.write_bytes(b"\x00garbage")
        acc = cloud.sign_in("gdrive", g_browser(g, "new@gmail.com"), wait_s=20)
        aside = list(cloud.FOLDER.glob("accounts-unreadable-*.dat"))
        check(aside and aside[0].read_bytes() == b"\x00garbage" and [a.key for a in cloud.load_accounts()] ==
              [acc.key], "an accounts file that can't be read is put aside, never written over")
        for a in aside:
            a.unlink()
        cloud.ACCOUNTS_FILE.write_bytes(good)
    step("Google: an unreadable accounts file", unreadable_accounts_file)
    g.server.shutdown()


# ----------------------------------------------------------------------------- the parts

def core():
    from corefold import cloud, cloudlocal
    fake = Fake()
    point_at(fake, cloud)
    cloud.SMALL, cloud.CHUNK = 64 * 1024, 40 * 1024   # (big uploads in pieces, without big files)
    accounts = {}

    def sign_in(service):
        acc = cloud.sign_in(service, browser(fake), wait_s=20)
        check(acc.email in ("sam@example.com", "sam@dropbox.test"), f"{service}: signed in as {acc.email}")
        check(acc.total > 0 and acc.used > 0, f"{service}: space used is known ({acc.used} of {acc.total})")
        accounts[service] = acc
    step("sign in to OneDrive", lambda: sign_in("onedrive"))
    step("sign in to Dropbox", lambda: sign_in("dropbox"))

    def kept():
        saved = cloud.load_accounts()
        check(sorted(a.key for a in saved) == sorted(a.key for a in accounts.values()),
              f"both accounts are kept ({[a.key for a in saved]})")
        raw = cloud.ACCOUNTS_FILE.read_bytes()
        if os.name == "nt":
            plain = [a.refresh.encode() for a in accounts.values()] + [a.access.encode() for a in accounts.values()]
            check(not any(k in raw for k in plain) and b"sam@" not in raw,
                  "the keys are locked on disk (no readable key or email)")
            check(cloud.unprotect(raw).startswith(b"["), "and Windows unlocks them again")
    step("keys kept", kept)

    def refused():
        # ("wrong": a stray answer first doesn't end the wait; the real one after it still counts)
        for how, want in (("no", "signing in was cancelled"), ("wrong", "signing in was cancelled")):
            try:
                cloud.sign_in("onedrive", browser(fake, how), wait_s=20)
                check(False, f"sign-in {how}: should fail")
            except cloud.CloudError as e:
                check(str(e) == want, f"sign-in {how}: {e}")
        stop = threading.Event()
        threading.Timer(1.5, stop.set).start()
        t = time.monotonic()
        try:
            cloud.sign_in("dropbox", browser(fake, "nothing"), stop, wait_s=60)
        except cloud.CloudError as e:
            check(str(e) == "cancelled" and time.monotonic() - t < 5, f"Cancel stops waiting for the browser ({e})")
    step("sign-in refused or cancelled", refused)

    def each(service):
        acc = accounts[service]
        c = cloud.client(acc)
        top = c.list(None)
        check(top == [], f"{service}: a new account is empty")
        src = TMP / f"up-{service}"
        make_files(src, service)
        (src / "big.bin").write_bytes(os.urandom(300 * 1024))   # (several pieces)
        (src / "empty.txt").write_bytes(b"")
        seen = []
        n = cloud.upload_tree(c, src, None, lambda p: seen.append(p))
        check(n == 7, f"{service}: a whole folder goes up ({n} files)")
        check(any(t == 300 * 1024 and d < t for _, d, t in seen), f"{service}: a big file shows progress in pieces")
        folder = next(e for e in c.list(None) if e.folder)
        inside = c.list(folder)
        names = sorted(e.name for e in inside)
        check({"Photos", "big.bin", "notes.txt"} <= set(names), f"{service}: listed in pages, all there ({names})")
        big = next(e for e in inside if e.name == "big.bin")
        check(big.size == 300 * 1024, f"{service}: sizes come back ({big.size})")
        back = TMP / f"down-{service}"
        back.mkdir()
        n = cloud.download_tree(c, folder, back)
        check(n == 7 and same_tree(src, back / folder.name), f"{service}: the folder comes back the same ({n})")
        if os.name == "nt":
            zone = back / folder.name / "notes.txt:Zone.Identifier"
            check(zone.exists() and "ZoneId=3" in zone.read_text(),
                  f"{service}: a downloaded file has Windows' \"from the internet\" mark")
        again = cloud.upload_tree(c, src / "notes.txt", folder)
        names2 = sorted(e.name for e in c.list(folder))
        check(again == 1 and "notes (1).txt" in names2, f"{service}: the same name again is kept beside it")
        made = c.new_folder("New stuff", folder)
        check(made.folder and made.name == "New stuff", f"{service}: a new folder")
        r = c.rename(next(e for e in c.list(folder) if e.name == "notes (1).txt"), "renamed.txt")
        check(r.name == "renamed.txt", f"{service}: renamed")
        try:
            c.rename(r, "notes.txt")
            check(False, f"{service}: renaming onto another file should fail")
        except cloud.CloudError as e:
            check(str(e) == "something with that name is already there", f"{service}: a name already there: {e}")
        c.delete(r)
        check("renamed.txt" not in [e.name for e in c.list(folder)], f"{service}: deleted")
        found = c.search("beach")
        check([e.name for e in found] == ["beach.jpg"], f"{service}: search finds it ({[e.name for e in found]})")
        try:
            c.list(cloud.Entry("gone", True, "i999999" if service == "onedrive" else "/gone/away"))
            check(False, f"{service}: a deleted folder should fail")
        except cloud.CloudError as e:
            check(str(e) == "it isn't there any more", f"{service}: a folder deleted elsewhere: {e}")
    step("OneDrive files", lambda: each("onedrive"))
    step("Dropbox files", lambda: each("dropbox"))

    def keys_run_out():
        for service, acc in accounts.items():
            c = cloud.client(acc)
            old = acc.access
            fake.access.pop(old, None)           # the service forgot it early
            c.list(None)
            check(acc.access != old, f"{service}: a key that ran out early is renewed by itself")
            check(cloud.load_accounts()[[a.key for a in cloud.load_accounts()].index(acc.key)].access == acc.access,
                  f"{service}: and the new one is kept")
            acc.expires = 0                       # and one that ran out on time
            c.list(None)
            check(acc.expires > time.time(), f"{service}: one that ran out on time too")
        acc = accounts["onedrive"]
        fake.access.pop(acc.access, None)
        fake.refresh.pop(acc.refresh, None)       # signed out on the service's side
        try:
            cloud.client(acc).list(None)
            check(False, "a taken back key should ask to sign in again")
        except cloud.SignInNeeded:
            check(True, "a key taken back on the service asks to sign in again")
    step("keys running out", keys_run_out)

    def troubles():
        c = cloud.client(accounts["dropbox"])
        fake.fail.append(("dbx", "list_folder", 503, 1))
        t = time.monotonic()
        c.list(None)
        check(time.monotonic() - t < 8, "a busy service is asked again and answers")
        fake.fail.append(("dbx", "files/upload", 507, 1))
        f = TMP / "full.txt"
        f.write_text("x")
        try:
            c.upload(f, None)
            check(False, "a full account should fail")
        except cloud.CloudError as e:
            check(str(e) == "there's no space left in this account", f"a full account says so: {e}")
        fake.fail.append(("dbx", "list_folder", 429, 5))
        try:
            c.list(None)
            check(False, "still busy should fail")
        except cloud.CloudError as e:
            check(str(e) == "the service asks to wait a moment, try again soon", f"still busy says so: {e}")
        fake.fail.clear()
        big = TMP / "huge.bin"
        big.write_bytes(os.urandom(400 * 1024))
        stop = threading.Event()
        for service in ("dropbox",):
            try:
                cloud.client(accounts[service]).upload(big, None, lambda d, t: stop.set() if d else None, stop)
                check(False, f"{service}: cancelling should stop the upload")
            except cloud.CloudError as e:
                check(str(e) == "cancelled", f"{service}: Cancel stops a big upload ({e})")
        old = cloud.OneDrive.API
        cloud.OneDrive.API = "http://127.0.0.1:9/nothing"
        try:
            accounts["onedrive"].expires = time.time() + 999
            accounts["onedrive"].access = "x"
            cloud.client(accounts["onedrive"]).list(None)
        except cloud.CloudError as e:
            check(str(e) == "no internet connection, or the service didn't answer", f"no internet says so: {e}")
        cloud.OneDrive.API = old
    step("troubles", troubles)

    def no_auth_elsewhere():
        leaked = [p for m, p, auth in fake.seen if auth and (p.startswith("/blob/") or p.startswith("/upload/"))]
        check(not leaked, f"the key is never sent to download or upload addresses ({leaked[:3]})")
    step("the key stays with the service", no_auth_elsewhere)

    def safe_names():
        for bad in ("..\\..\\Startup\\x.bat", "C:\\x.bat", "../../x", "CON", "a<b>.txt", ""):
            n = cloud.local_name(bad)
            check("/" not in n and "\\" not in n and ":" not in n and n not in ("", "..", "CON"),
                  f"a cloud file named {bad!r} is saved as {n!r}")
    step("names from the cloud", safe_names)

    def sign_out():
        acc = accounts["dropbox"]
        acc.expires = time.time() + 999
        before = fake.revoked
        cloud.client(acc).sign_out()
        check(fake.revoked == before + 1, "signing out of Dropbox takes back the key there too")
        cloud.forget_account(acc.key)
        check(acc.key not in [a.key for a in cloud.load_accounts()], "and Corefold forgets it")
    step("sign out", sign_out)

    # -- folders on this PC
    od, dbx = fake_pc_folders()

    def finding():
        found = cloudlocal.find()
        kinds = sorted(a.service for a in found)
        check(kinds == ["dropbox", "gdrive", "icloud", "onedrive"], f"all four folders are found ({kinds})")
        check(all(a.local and a.key.startswith("local:") for a in found), "they're folders on this PC")
        cloud.save_accounts(cloud.load_accounts() + found)
        check(not any(a.local for a in cloud.load_accounts()), "they're never saved with the signed-in accounts")
        os.environ["OneDrive"] = str(od)  # (Windows sets both: found once)
        check(len(cloudlocal.find()) == 4, "the same folder twice is listed once")
    step("finding the folders on this PC", finding)

    def local_files():
        acc = next(a for a in cloudlocal.find() if a.service == "onedrive")
        c = cloud.client(acc)
        top = sorted(e.name for e in c.list(None))
        check(top == ["OneDrive report.docx", "Photos", "notes.txt"], f"hidden files are left out ({top})")
        src = TMP / "up-local"
        make_files(src, "local")
        n = cloud.upload_tree(c, src, None)
        check(n == 5 and same_tree(src, od / "up-local"), f"a folder goes in ({n} files)")
        again = c.upload(src / "notes.txt", None)
        check(again.name == "notes (2).txt", f"the same name again is kept beside it ({again.name})")
        made = c.new_folder("Work", None)
        check((od / "Work").is_dir() and made.path == "/Work", f"a new folder ({made.path})")
        for bad in ("a/b", "x:y", "CON", "end.", ""):
            try:
                c.new_folder(bad, None)
                check(False, f"{bad!r} should not be allowed")
            except cloud.CloudError as e:
                check(str(e) == "that name isn't allowed", f"the name {bad!r} isn't allowed")
        r = c.rename(again, "kept.txt")
        check((od / "kept.txt").exists() and r.name == "kept.txt", "renamed")
        try:
            c.rename(r, "notes.txt")
            check(False, "renaming onto another file should fail")
        except cloud.CloudError as e:
            check(str(e) == "something with that name is already there", f"a name already there: {e}")
        c.delete(r)
        check(not (od / "kept.txt").exists(), "deleted (to the Recycle Bin)")
        found = sorted(e.path for e in c.search("BEACH"))
        check(found == ["/Photos/2024/beach.jpg", "/up-local/Photos/2024/beach.jpg"], f"search, any case ({found})")
        back = TMP / "down-local"
        back.mkdir()
        check(cloud.download_tree(c, next(e for e in c.list(None) if e.name == "Photos"), back) == 1,
              "a folder can be saved somewhere else")
        stop = threading.Event()
        big = TMP / "big-local.bin"
        big.write_bytes(os.urandom(3 * 1024 * 1024))
        try:
            c.upload(big, None, lambda d, t: stop.set(), stop)
            check(False, "cancelling should stop the copy")
        except cloud.CloudError as e:
            check(str(e) == "cancelled" and not (od / "big-local.bin").exists(), "Cancel leaves no half file")
    step("working in a folder on this PC", local_files)

    def across():
        here = next(a for a in cloudlocal.find() if a.service == "dropbox")
        online = accounts["onedrive"]
        fake.refresh[online.refresh] = "ms"
        online.expires = 0
        n = cloud.upload_tree(cloud.client(online), dbx / "Photos", None)
        check(n == 1 and fake.ms.file_named("beach.jpg") is not None, "a folder on this PC goes to an online account")
        tmp = TMP / "via"
        tmp.mkdir()
        entry = next(e for e in cloud.client(online).list(None) if e.name == "Photos")
        cloud.download_tree(cloud.client(online), entry, tmp)
        cloud.upload_tree(cloud.client(here), tmp / "Photos", None)
        check((dbx / "Photos (2)" / "2024" / "beach.jpg").exists(), "and from an online account to a folder here")
    step("from one cloud to another", across)
    microsoft(cloud, fake, accounts)
    fake.server.shutdown()
    google(cloud)


def page_part():
    from corefold import settings as st
    from corefold import tools as all_tools

    od, dbx = fake_pc_folders()
    s = st.Settings()
    s.first_run, s.tour_done, s.check_updates = False, True, False
    s.language, s.appearance = LANG, LOOK
    s.known_tools = sorted(t.id for t in all_tools.ready())
    s.tools_off = []
    s.quick_told = True
    s.save()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    qapp = QApplication(sys.argv)
    from corefold import i18n
    i18n.set_language(LANG)
    if i18n.is_rtl():
        qapp.setLayoutDirection(Qt.RightToLeft)
    from corefold import app as A
    from corefold import cloud, cloudpage

    fake = Fake()
    point_at(fake, cloud)
    acc = cloud.Account("onedrive", "sam@example.com", "Sam Tester")
    tok = fake.token("ms")
    acc.access, acc.refresh, acc.expires = tok["access_token"], tok["refresh_token"], time.time() + 3000
    acc.used, acc.total = 1500000, 5000000000
    cloud.remember_account(acc)
    fake.ms.add("root", "Online notes.txt", data=b"from the pretend OneDrive")
    fake.ms.add("root", "Shared", folder=True)

    def until(cond, seconds=10.0):
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False

    c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
    check(all_tools.is_on(c.settings, "cloud"), "Corefold Cloud is ready (no longer Coming soon) and in use")
    c.show_window("Cloud")
    w = c.window
    w.resize(1180, 780)
    check(until(lambda: getattr(w, "cloud_page", None) is not None, 5), "the Cloud page opens")
    page = w.cloud_page
    check(until(lambda: page.found is not None and len(page.accounts) == 5, 10),
          f"it finds the four folders on this PC, and the signed-in account ({len(page.accounts)})")
    services = [a.service for a in page.accounts]
    check(services == ["onedrive", "dropbox", "gdrive", "icloud", "onedrive"] and page.accounts[-1].local == "",
          f"folders on this PC first, then online accounts ({services})")

    def names():
        return sorted(page.files.topLevelItem(i).data(0, Qt.UserRole).name for i in range(page.files.topLevelItemCount()))
    check(until(lambda: page.files.topLevelItemCount() == 3, 5), f"OneDrive's folder is shown ({names()})")
    check("desktop.ini" not in names(), "without Windows' hidden files")
    check(page.explorer_btn.isVisible() and not page.add_btn.isHidden(),
          "a folder on this PC can be opened in File Explorer; online accounts can be signed in to")
    until(lambda: False, 0.5)
    shot = f"cloud-{LANG}-{LOOK}.png"
    check(w.grab().save(str(OUT / shot)), f"picture saved: {shot}")

    def open_folder():
        item = next(page.files.topLevelItem(i) for i in range(page.files.topLevelItemCount())
                    if page.files.topLevelItem(i).data(0, Qt.UserRole).name == "Photos")
        page._double_click(item, 0)
        check(until(lambda: names() == ["2024"], 5) and len(page.trail) == 1, f"a folder opens ({names()})")
        check(w.go_back() and until(lambda: len(page.trail) == 0 and len(names()) == 3, 5),
              "Back goes up again")
    step("folders", open_folder)

    def shown():
        return [page.files.topLevelItem(i).data(0, Qt.UserRole) for i in range(page.files.topLevelItemCount())]

    def browsing():
        from PySide6.QtWidgets import QPushButton
        page._double_click(next(page.files.topLevelItem(i) for i in range(page.files.topLevelItemCount())
                                if page.files.topLevelItem(i).data(0, Qt.UserRole).name == "Photos"), 0)
        until(lambda: names() == ["2024"], 5)
        page._double_click(page.files.topLevelItem(0), 0)
        check(until(lambda: len(page.trail) == 2, 5), "a folder in a folder opens")
        until(lambda: False, 0.1)
        crumbs = [b for b in page.where.findChildren(QPushButton) if b.isVisible()]
        texts = [b.text() for b in crumbs]
        check(len(crumbs) == 3 and "Photos" in texts[1] and "2024" in texts[2],
              f"the path at the top has a button for each folder ({texts})")
        crumbs[0].click()
        check(until(lambda: len(page.trail) == 0 and len(names()) == 3, 5), "clicking the cloud's name goes to its top")
        # sorting by a column: folders stay first
        page.files.sortByColumn(1, Qt.DescendingOrder)
        got = shown()
        files = [e for e in got if not e.folder]
        check(got[0].folder and [e.size for e in files] == sorted((e.size for e in files), reverse=True),
              f"sorting by size, biggest first, folders first ({[e.name for e in got]})")
        page.files.sortByColumn(0, Qt.DescendingOrder)
        got = shown()
        check(got[0].folder and [e.name.casefold() for e in got if not e.folder] ==
              sorted((e.name.casefold() for e in got if not e.folder), reverse=True), "sorting by name, Z to A")
        page.files.sortByColumn(0, Qt.AscendingOrder)
        # preview: the Viewer's Quick look on the picked file, without opening it
        first = next(i for i in range(page.files.topLevelItemCount())
                     if not page.files.topLevelItem(i).data(0, Qt.UserRole).folder)
        page.files.setCurrentItem(page.files.topLevelItem(first))
        page.preview_current()
        check(until(lambda: page.peeker is not None and page.peeker.isVisible(), 5) and
              str(page.peeker.current()) == page.files.topLevelItem(first).data(0, Qt.UserRole).id,
              "Preview shows the picked file in Quick look")
        page.peeker.close_peek(back=False)
    step("browsing", browsing)

    def uploads():
        src = TMP / "to-upload"
        make_files(src, "upload")
        page.upload([src])
        page.upload([src / "notes.txt"])
        check(len(page.queue) == 1, "a second upload waits its turn")
        check(until(lambda: not page._working() and not page.queue, 15), "both finish")
        until(lambda: all(t.state == "done" for t in page.transfers), 5)
        states = [t.state for t in page.transfers]
        check(page.transfers_btn.isVisible() and states.count("done") >= 2 and page.transfer_list.count() >= 2,
              f"both are in the list of transfers, done ({states})")
        page._toggle_transfers()
        until(lambda: False, 0.2)
        check(page.transfer_list.isVisible(), "the list of transfers opens")
        shot = f"cloud-transfers-{LANG}-{LOOK}.png"
        check(w.grab().save(str(OUT / shot)), f"picture saved: {shot}")
        page._clear_finished()
        check(not page.transfers and not page.transfers_btn.isVisible(), "the finished ones can be cleared")
        check(same_tree(src, od / "to-upload") and (od / "notes (2).txt").exists(),
              f"they're in the folder ({page.status.text()})")
        check(until(lambda: "to-upload" in names(), 5), "and listed")
    step("uploads", uploads)

    def adding():
        page._fill_add_menu()
        texts = [a.text() for a in page.add_menu.actions()]
        check(page.add_btn.isVisible() and any("…" in t for t in texts),
              f"Add a cloud is always there, with adding a folder ({texts})")
        check(until(lambda: all(a.extra.get("measured") for a in page.accounts), 15),
              "every account's space is looked at")
        od_acc = page.accounts[0]
        check(cloudpage.space_text(od_acc) != "" and od_acc.used > 0, f"a folder says how much it uses "
                                                                      f"({cloudpage.space_text(od_acc)})")
        online = page.accounts[-1]
        uses = cloudpage.tr("Uses {size}", size=cloudpage.human_size(online.used))
        check(cloudpage.space_text(online) != uses and online.total > online.used,
              f"an online account says how much is free ({cloudpage.space_text(online)})")
        extra = TMP / "home" / "Synced stuff"
        make_files(extra, "extra")
        from unittest import mock as _m
        with _m.patch.object(cloudpage.QFileDialog, "getExistingDirectory", return_value=str(extra)):
            page.pick_folder()
        added = [a for a in page.accounts if a.local and Path(a.local) == extra]
        check(len(added) == 1 and added[0].extra.get("added") and page.account is added[0],
              f"a folder can be added by hand, and is opened ({page.status.text()})")
        check(until(lambda: "notes.txt" in names(), 5), f"its files are shown ({names()})")
        page.look_for_folders()
        check(until(lambda: not page.looking and any(a.local and Path(a.local) == extra for a in page.accounts), 10),
              "it's still there after looking again")
        until(lambda: False, 0.3)
        shot = f"cloud-added-{LANG}-{LOOK}.png"
        check(w.grab().save(str(OUT / shot)), f"picture saved: {shot}")
        page.remove_folder(added[0])
        check(not any(a.local and Path(a.local) == extra for a in page.accounts) and extra.exists(),
              "Remove from the list takes it off (its files stay)")
        page.account_list.setCurrentRow(0)
    step("adding a folder", adding)

    def other_accounts():
        online = page.accounts[-1]
        local_dbx = page.accounts[1]
        entries = [e for e in cloud.client(page.accounts[0]).list(None) if e.name == "Photos"]
        page.copy_to(page.accounts[0], entries, online, None)
        check(until(lambda: not page._working(), 15), f"copying to an online account finishes ({page.status.text()})")
        check(fake.ms.file_named("beach.jpg") is not None, "the folder is in the pretend OneDrive")
        page.account_list.setCurrentRow(4)
        check(until(lambda: "Online notes.txt" in names(), 8), f"the online account is shown ({names()})")
        check(page.space.text() != "", f"with its space ({page.space.text()})")
        e = next(e for e in cloud.client(online).list(None) if e.name == "Online notes.txt")
        page.copy_to(online, [e], local_dbx, None)
        check(until(lambda: not page._working() and (dbx / "Online notes.txt").exists(), 15),
              "an online file goes to a folder on this PC")
        until(lambda: False, 0.4)
        shot = f"cloud-online-{LANG}-{LOOK}.png"
        check(w.grab().save(str(OUT / shot)), f"picture saved: {shot}")
    step("other accounts", other_accounts)

    def searching():
        page.search.setText("beach")
        page.search_all()
        check(until(lambda: page.results is not None and len(page.results) >= 6, 15),
              f"search looks in every cloud ({len(page.results or [])} found)")
        kinds = {a.service for a, _ in page.results}
        check(kinds == {"onedrive", "dropbox", "gdrive", "icloud"}, f"in all of them ({sorted(kinds)})")
        until(lambda: False, 0.3)
        shot = f"cloud-search-{LANG}-{LOOK}.png"
        check(w.grab().save(str(OUT / shot)), f"picture saved: {shot}")
        check(w.go_back() and until(lambda: page.results is None, 5), "Back leaves the search")
    step("search", searching)

    def signed_out():
        online = page.accounts[-1]
        fake.access.pop(online.access, None)
        fake.refresh.pop(online.refresh, None)
        page.account_list.setCurrentRow(4)
        page.refresh()
        check(until(lambda: page.sign_again_btn.isVisible(), 8), f"a taken back key offers Sign in again "
                                                                 f"({page.status.text()})")
    step("signed out elsewhere", signed_out)

    def words():
        for err in ("that name isn't allowed", "it's open in another app, or Windows didn't allow it",
                    "there's no space left in this account"):
            text = cloudpage._ui_error(cloud.CloudError(err))
            check(bool(text) and (LANG == "en" or text != err), f"said in {LANG}: {text}")
    step("words", words)

    def google_page():
        from unittest import mock as _m
        g = GFake()
        g_point_at(g, cloud)
        for email in ("sam@gmail.com", "work.sam@gmail.com"):
            a = cloud.Account("gdrive", email, email.split("@")[0].title())
            tok = g.token(email)
            a.access, a.refresh, a.expires = tok["access_token"], tok["refresh_token"], time.time() + 3000
            a.used, a.total = 3221225472, 16106127360
            cloud.remember_account(a)
        g.page_size = 100
        g.add("sam@gmail.com", "root", "Report", mime="application/vnd.google-apps.document")
        g.add("sam@gmail.com", "root", "Survey", mime="application/vnd.google-apps.form")
        g.add("sam@gmail.com", "root", "Trips", mime=g.FOLDER)
        g.add("sam@gmail.com", "root", "a.txt", b"hello")
        g.add("work.sam@gmail.com", "root", "Work plan.txt", b"plan")
        page.load_accounts()
        rows = [i for i, a in enumerate(page.accounts) if a.service == "gdrive" and not a.local]
        check(len(rows) == 2 and len({page.accounts[i].email for i in rows}) == 2,
              f"two Google accounts are listed at once ({[page.accounts[i].email for i in rows]})")
        page._fill_add_menu()
        texts = [x.text() for x in page.add_menu.actions()]
        want = cloudpage.tr("Add another {service} account", service="Google Drive")
        check(want in texts, f"Add a cloud offers another Google account ({texts})")
        page.account_list.setCurrentRow(rows[0])
        check(until(lambda: {"Report", "Trips", "a.txt"} <= set(names()), 8), f"a Google account's files ({names()})")
        check(cloudpage.tr("Shared with me") in names(), "with Shared with me")
        until(lambda: False, 0.4)
        shot = f"cloud-google-{LANG}-{LOOK}.png"
        check(w.grab().save(str(OUT / shot)), f"picture saved: {shot}")

        def item(name):
            return next(page.files.topLevelItem(i) for i in range(page.files.topLevelItemCount())
                        if page.files.topLevelItem(i).data(0, Qt.UserRole).name == name)
        opened = []
        with _m.patch.object(cloudpage.QDesktopServices, "openUrl", lambda u: opened.append(u.toString())):
            page._double_click(item("Report"), 0)
            until(lambda: False, 0.3)
        check(opened and "drive.google.com" in opened[0] and not page._working(),
              f"a Google Doc opens in the browser, nothing is downloaded ({opened})")
        acc = page.accounts[rows[0]]
        menus = [[x.text() for x in m.actions() if not x.isSeparator()] for m in (
            page.file_menu(acc, [item("a.txt").data(0, Qt.UserRole)]),
            page.file_menu(acc, [item(cloudpage.tr("Shared with me")).data(0, Qt.UserRole)]),
            page.account_menu(acc))]
        delete = cloudpage.tr("Delete") + "\tDel"
        check(len(menus) == 3 and delete in menus[0] and any("\tF2" in t for t in menus[0]),
              f"a file's menu has its keys ({menus[0] if menus else ''})")
        check(len(menus) > 1 and delete not in menus[1] and not any("\tF2" in t for t in menus[1]),
              f"Shared with me can't be deleted or renamed ({menus[1] if len(menus) > 1 else ''})")
        check(len(menus) > 2 and cloudpage.tr("Sign out") in menus[2] and
              cloudpage.tr("Open {service} in the browser", service="Google Drive") in menus[2],
              f"an online account's menu ({menus[2] if len(menus) > 2 else ''})")
        asked = []
        with _m.patch.object(cloudpage.QMessageBox, "exec", lambda self: asked.append(1)):
            page.files.clearSelection()
            item(cloudpage.tr("Shared with me")).setSelected(True)
            page._with_picked(page.delete)
        check(not asked, "the Delete key on Shared with me does nothing")
        page._double_click(item(cloudpage.tr("Shared with me")), 0)
        check(until(lambda: len(page.trail) == 1, 5) and not page.upload_btn.isEnabled() and
              not page.folder_btn.isEnabled(), "nothing can be uploaded into Shared with me")
        page.account_list.setCurrentRow(rows[1])
        check(until(lambda: "Work plan.txt" in names(), 8) and "a.txt" not in names(),
              f"the other Google account shows its own files ({names()})")
        g.server.shutdown()
    step("Google Drive on the page", google_page)
    fake.server.shutdown()
    c.quit()


def cleanup():
    shutil.rmtree(TMP, ignore_errors=True)


def main():
    if PART == "all":
        step("core", core)
        for lang, look in (("en", "dark"), ("ar", "light")):
            r = subprocess.run([sys.executable, __file__, "page", str(OUT), lang, look], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=200)
            print(f"---- the page in {lang} ({look})", flush=True)
            print(r.stdout.rstrip(), flush=True)
            if r.stderr.strip():
                print(r.stderr.rstrip()[-3000:], flush=True)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL"):
                    problems.append(f"{lang}: {line[6:]}")
            check(r.returncode in (0, 1), f"the {lang} page part ran to its end (exit {r.returncode})")
    elif PART == "core":
        step("core", core)
    else:
        step("the page", page_part)
    no_keys_logged()
    cleanup()
    print(f"newtest_cloud{'' if PART == 'all' else ' ' + PART}: {len(problems)} problem(s)", flush=True)
    for p in problems:
        print("  - " + p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
