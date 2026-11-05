"""Rakiza Core Uninstaller: watching an install. A look at the usual places apps put things (their folders, shortcuts,
registry keys, services, startup entries) before an app is installed and once it's done; what's new is kept as
that app's trace. When the app is uninstalled later, everything in its trace that's still there is offered with
its leftovers, even what isn't named after it. No Qt here.

Only the top of each place is compared (C:\\Program Files\\<Maker>\\<App>, Software\\<Maker>\\<App>…), so a look
takes a second or two, and what Windows itself changes deep inside its own folders meanwhile isn't mistaken
for the app's."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

log = logging.getLogger("corefold")

TRACES = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Rakiza Core" / "Install traces"
MAX_ITEMS = 2000                  # (a trace bigger than this was something else than one install)
# a watch left on longer than this (started, then forgotten for a day) saw other things happen too: what in its
# trace isn't named after the app is then only offered as "check first", never ticked
LONG_WATCH = 6 * 3600
# names Windows and other programs keep changing on their own: never part of an app's trace
NOISE = {"temp", "tmp", "microsoft", "packages", "crashdumps", "corefold", "package cache", "d3dscache", "windows",
         "connecteddevicesplatform", "comms", "history", "inetcache", "fontcache", "nvidia", "nvidia corporation",
         "amd", "intel", "google", "mozilla", "classes", "policies", "wow6432node", "clients", "softwaredistribution",
         "regid.1991-06.com.microsoft", "ssh", "usoshared", "usoprivate", "microsoft onedrive", "onedrive"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def folder_places() -> list[tuple]:
    """(folder, how deep to look[, only shortcuts]) for each place apps put their files and shortcuts. On the
    desktops only shortcuts count: a document or a folder saved there meanwhile is the person's own, never the
    app's (it would be offered for removal with the app one day)."""
    home = _env("USERPROFILE", str(Path.home()))
    roaming, local = _env("APPDATA"), _env("LOCALAPPDATA")
    pd, public = _env("ProgramData", r"C:\ProgramData"), _env("PUBLIC", r"C:\Users\Public")
    out = [(_env("ProgramFiles", r"C:\Program Files"), 2), (_env("ProgramFiles(x86)", r"C:\Program Files (x86)"), 2),
           (pd, 2), (os.path.join(pd, r"Microsoft\Windows\Start Menu\Programs"), 2),
           (os.path.join(public, "Desktop"), 1, True), (os.path.join(home, "Desktop"), 1, True)]
    if roaming:
        out += [(roaming, 2), (os.path.join(roaming, r"Microsoft\Windows\Start Menu\Programs"), 2)]
    if local:
        out += [(local, 2), (os.path.join(local, "Programs"), 2)]
    seen, kept = set(), []
    for place in out:
        k = os.path.normcase(place[0])
        if place[0] and k not in seen:
            seen.add(k)
            kept.append(place)
    return kept


def _list_folders(root: str, depth: int, out: set, only_shortcuts: bool = False):
    try:
        items = list(os.scandir(root))
    except OSError:
        return
    for d in items:
        try:
            if d.name.lower() in NOISE or d.is_symlink():
                continue
            if only_shortcuts:
                if d.name.lower().endswith((".lnk", ".url")) and d.is_file(follow_symlinks=False):
                    out.add("file|" + d.path)
            elif d.is_dir(follow_symlinks=False):
                out.add("folder|" + d.path)
                if depth > 1:
                    _list_folders(d.path, depth - 1, out)
            elif d.name.lower().endswith((".lnk", ".url", ".exe")) or depth == 1:
                out.add("file|" + d.path)
        except OSError:
            continue


REG_PLACES = [("HKLM", "Software", (64, 32), 2), ("HKCU", "Software", (0,), 2),
              ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Uninstall", (64, 32), 1),
              ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall", (0,), 1),
              ("HKLM", r"SYSTEM\CurrentControlSet\Services", (0,), 1)]
RUN_PLACES = [("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Run", (64, 32)),
              ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run", (0,))]


def _list_keys(hive, path, view, depth, out: set, subkeys):
    for sub in subkeys(hive, path, view):
        if sub.lower() in NOISE:
            continue
        full = f"{path}\\{sub}"
        out.add(f"key|{hive}|{view}|{full}")
        if depth > 1:
            _list_keys(hive, full, view, depth - 1, out, subkeys)


def snapshot(subkeys=None, values=None, folders: list | None = None) -> set[str]:
    """What's in the usual places right now, as "kind|..." lines."""
    registry = os.name == "nt" or subkeys is not None
    if subkeys is None or values is None:
        from . import uninstall as un
        subkeys, values = un._subkeys, un._values
    out: set[str] = set()
    for folder, depth, *only in folders if folders is not None else folder_places():
        _list_folders(folder, depth, out, bool(only and only[0]))
    if registry:
        for hive, path, views, depth in REG_PLACES:
            for view in views:
                _list_keys(hive, path, view, depth, out, subkeys)
        for hive, path, views in RUN_PLACES:
            for view in views:
                for name in values(hive, path, view):
                    if name:
                        out.add(f"value|{hive}|{view}|{path}|{name}")
    return out


def start(**kw) -> Path:
    """The look before installing, kept on disk (Rakiza Core may be closed and opened again meanwhile)."""
    TRACES.mkdir(parents=True, exist_ok=True)
    before = TRACES / "before.json"
    tmp = before.with_suffix(".tmp")
    tmp.write_text(json.dumps({"time": time.time(), "items": sorted(snapshot(**kw))}), "utf-8")
    os.replace(tmp, before)
    return before


def waiting() -> float:
    """When the look before an install was taken (0 when none is waiting)."""
    try:
        return float(json.loads((TRACES / "before.json").read_text("utf-8")).get("time", 0))
    except (OSError, ValueError, AttributeError):
        return 0.0


def _uninstall_entry(line: str) -> tuple[str, int, str] | None:
    kind, *rest = line.split("|")
    if kind == "key" and len(rest) == 3 and rest[2].lower().startswith(
            r"software\microsoft\windows\currentversion\uninstall" + "\\"):
        return rest[0], int(rest[1]), rest[2].rsplit("\\", 1)[1]
    return None


def finish(name_of=None, **kw) -> dict | None:
    """The look once the install is done: what's new is kept as a trace, named after the app whose uninstall entry
    appeared (name_of(hive, view, key) -> its name). Returns the trace, or None when nothing was waiting."""
    before_file = TRACES / "before.json"
    try:
        kept = json.loads(before_file.read_text("utf-8"))
        before, began = set(kept.get("items", [])), float(kept.get("time", 0) or 0)
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    new = sorted(snapshot(**kw) - before)
    # what's inside a new folder or key is covered by it
    tops = [ln.partition("|")[2].lower() + ("\\" if ln.startswith("key|") else os.sep) for ln in new
            if ln.startswith(("folder|", "key|"))]
    new = [ln for ln in new if not any(ln.partition("|")[2].lower().startswith(t) for t in tops)]
    entries = [e for e in map(_uninstall_entry, new) if e]
    names = []
    if name_of is not None:
        for hive, view, key in entries:
            try:
                n = name_of(hive, view, key)
            except Exception:
                n = ""
            if n:
                names.append(n)
    trace = {"id": uuid.uuid4().hex[:10], "time": time.time(), "watched": max(0.0, time.time() - began),
             "apps": names,
             "entries": [list(e) for e in entries], "items": new[:MAX_ITEMS]}
    if new:
        TRACES.mkdir(parents=True, exist_ok=True)
        (TRACES / f"{trace['id']}.json").write_text(json.dumps(trace, ensure_ascii=False), "utf-8")
    before_file.unlink(missing_ok=True)
    return trace


def cancel():
    (TRACES / "before.json").unlink(missing_ok=True)


def traces() -> list[dict]:
    out = []
    try:
        files = sorted(TRACES.glob("*.json"))
    except OSError:
        return out
    for f in files:
        if f.name == "before.json" or not re.fullmatch(r"[0-9a-f]{10}\.json", f.name):
            continue
        try:
            t = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(t, dict) and isinstance(t.get("items"), list):
            out.append(t)
    return out


def trace_for(app) -> dict | None:
    """The trace of this app's install, if Rakiza Core watched it: matched by its uninstall entry, else its name."""
    for t in traces():
        for e in t.get("entries", []):
            if isinstance(e, list) and len(e) == 3 and e[2].lower() == (app.key or "").lower() and \
                    e[0] == app.hive and (app.hive != "HKLM" or int(e[1]) in (0, app.view)):
                return t
    for t in traces():
        if any(str(n).lower() == app.name.lower() for n in t.get("apps", [])):
            return t
    return None


def leftovers_for(app) -> list:
    """What the trace of this app's install says it put in, as leftovers (only what's still there). How sure
    each one is: surely its own when its name is the app's or it's in its folder, most likely otherwise
    (something else could have been installed at the same time)."""
    from . import uninstall as un
    t = trace_for(app)
    if t is None:
        return []
    keys = app.names()
    try:
        long = float(t.get("watched", 0) or 0) > LONG_WATCH
    except (TypeError, ValueError):
        long = True
    likely = 1 if long else 2  # (what isn't named after the app: most likely its, or only maybe after a long watch)
    out = []
    for line in t.get("items", []):
        kind, *rest = str(line).split("|")
        if kind in ("folder", "file") and rest:
            path = rest[0]
            if not os.path.lexists(path):
                continue
            named = un.clean_name(os.path.basename(path)) in keys or \
                (app.location and un._inside(path, app.location))
            shortcut = path.lower().endswith((".lnk", ".url"))
            out.append(un.Leftover("shortcut" if shortcut else kind, path, "tracked", level=3 if named else likely))
        elif kind == "key" and len(rest) == 3:
            hive, view, path = rest[0], int(rest[1]), rest[2]
            if path.upper().startswith("SYSTEM\\"):
                continue  # (a service: offered the usual way, by the program it runs)
            if not un.key_exists(hive, path, view):
                continue
            named = un.clean_name(path.rsplit("\\", 1)[-1]) in keys
            out.append(un.Leftover("key", f"{hive}\\{path}", "tracked", level=3 if named else likely,
                                   view=view if hive == "HKLM" else 0))
        elif kind == "value" and len(rest) == 4:
            hive, view, path, name = rest[0], int(rest[1]), rest[2], rest[3]
            values = un._values(hive, path, view)
            if name not in values:
                continue
            # (another app that started with Windows since the watch began isn't this one's: only one named after
            # the app, or starting a program in its folder, is surely its)
            exe, _ = un.split_command(str(values.get(name) or ""))
            named = un.clean_name(name) in keys or bool(app.location and exe and os.path.isabs(exe)
                                                        and un._inside(exe, app.location))
            out.append(un.Leftover("value", f"{hive}\\{path}", "tracked", level=3 if named else likely, value=name,
                                   view=view if hive == "HKLM" else 0))
    return out


def forget(trace_id: str):
    if re.fullmatch(r"[0-9a-f]{10}", trace_id or ""):
        (TRACES / f"{trace_id}.json").unlink(missing_ok=True)
