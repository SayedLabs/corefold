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
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(280, exit=True)  # stuck somewhere: say where, and stop

problems = []


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
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        server.daemon_threads = True
        self.server = server
        self.base = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def token(self, service):
        self.n += 1
        a, r = f"{service}-A{self.n}", f"{service}-R{self.n}"
        self.access[a], self.refresh[r] = service, service
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
                    return self.send(200, fake.token("ms"))
                if fake.refresh.pop(q.get("refresh_token"), None) == "ms":
                    return self.send(200, fake.token("ms"))
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
                return out

            def graph(self, method, data):
                if not self.authed("ms"):
                    return
                s = fake.ms
                u = urllib.parse.urlparse(self.path)
                p = urllib.parse.unquote(u.path)[len("/graph/v1.0"):]
                q = dict(urllib.parse.parse_qsl(u.query))
                if p == "/me":
                    return self.send(200, {"displayName": "Sam Tester", "mail": "sam@example.com"})
                if p == "/me/drive":
                    return self.send(200, {"quota": {"used": 1500000, "total": 5000000000}})
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
                        i = s.add(pid, name, data=data)
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
                if method == "DELETE":
                    fake.cancelled_uploads += 1
                    fake.uploads.pop(sid, None)
                    return self.send(204)
                start, end_total = self.headers["Content-Range"][6:].split("-")
                end, total = end_total.split("/")
                if int(start) != len(up["data"]) or int(end) - int(start) + 1 != len(data):
                    return self.send(416, {"error": {"code": "invalidRange", "message": "wrong piece"}})
                up["data"] += data
                if len(up["data"]) < int(total):
                    return self.send(202, {"nextExpectedRanges": [f"{len(up['data'])}-"]})
                i = fake.ms.add(up["parent"], up["name"], data=up["data"])
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


def browser(fake, how="yes"):
    """Plays the browser: checks the sign-in address, then sends the person back like the service would."""
    def open_page(url):
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
                "wrong": {"code": code, "state": "someone-else"}}[how]

        def go():
            time.sleep(0.3)
            with urllib.request.urlopen(q["redirect_uri"] + "?" + urllib.parse.urlencode(back), timeout=10) as r:
                page = r.read().decode("utf-8")
            check("Corefold" in page, "the browser shows a page saying it can be closed")
        threading.Thread(target=go, daemon=True).start()
    return open_page


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
        for how, want in (("no", "signing in was cancelled"), ("wrong", "the answer from the sign-in page didn't "
                                                                            "match; try again")):
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
    fake.server.shutdown()


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

    def uploads():
        src = TMP / "to-upload"
        make_files(src, "upload")
        page.upload([src])
        page.upload([src / "notes.txt"])
        check(len(page.queue) == 1, "a second upload waits its turn")
        check(until(lambda: not page._working() and not page.queue, 15), "both finish")
        check(same_tree(src, od / "to-upload") and (od / "notes (2).txt").exists(),
              f"they're in the folder ({page.status.text()})")
        check(until(lambda: "to-upload" in names(), 5), "and listed")
    step("uploads", uploads)

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
    cleanup()
    print(f"newtest_cloud{'' if PART == 'all' else ' ' + PART}: {len(problems)} problem(s)", flush=True)
    for p in problems:
        print("  - " + p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
