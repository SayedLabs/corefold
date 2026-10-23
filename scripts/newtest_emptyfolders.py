"""Corefold Space's empty folders, tried for real on a Windows PC: a folder tree with empty folders, a
nested tree of empty folders, a folder holding only desktop.ini, a folder with a file, a junction to an
empty folder, a hidden empty folder and a folder made just now. The finder must offer exactly the right
ones (with the "made in the last day" rule switched off, and on), and removing them must put them in
the Recycle Bin (found there by their original paths) while everything else stays. Your own folders are
looked through too, and nothing of yours is offered or removed. The window is built and lists the tree
in English (dark) and Arabic (light), and pictures of it are saved. What the test put in the Recycle Bin
is taken out again at the end.

Run: python scripts/newtest_emptyfolders.py [folder]
Prints one line per check and "newtest_emptyfolders: N problem(s)"."""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PART = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("core", "page") else "all"
ARGS = [a for a in sys.argv[1:] if a not in ("core", "page")]
OUT = Path(ARGS[0] if ARGS else ROOT / "out" / "newtest_emptyfolders").resolve()
LANG = ARGS[1] if len(ARGS) > 1 else "en"
LOOK = ARGS[2] if len(ARGS) > 2 else "dark"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="corefold-emptytest-"))
os.environ["APPDATA"] = str(TMP / "roaming")
os.environ["LOCALAPPDATA"] = str(TMP / "local")
# the folders to look through can't be in AppData (where the temp folder is): Corefold never looks there
WORK = Path(os.path.realpath(tempfile.mkdtemp(prefix="corefold-emptytest-", dir=str(Path.home()))))
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(280, exit=True)  # stuck somewhere: say where, and stop

problems = []
DAY = 86400


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)


def step(name, fn):
    try:
        fn()
    except Exception:
        check(False, f"{name}: {traceback.format_exc().splitlines()[-1]}")
        traceback.print_exc()


# ----------------------------------------------------------------------------- Windows helpers

k32 = ctypes.windll.kernel32
k32.CreateFileW.restype = ctypes.c_void_p
k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                            ctypes.c_ulong, ctypes.c_void_p]
k32.SetFileTime.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
k32.CloseHandle.argtypes = [ctypes.c_void_p]


def set_created(path: Path, ts: float):
    """Gives a folder an older "created" time (the test's folders are all made just now)."""
    h = k32.CreateFileW(str(path), 0x100, 7, None, 3, 0x02000000, None)  # write attributes, backup semantics
    if not h or h == ctypes.c_void_p(-1).value:
        raise OSError(f"can't open {path}")
    try:
        ft = ctypes.c_ulonglong(int((ts + 11644473600) * 10_000_000))
        if not k32.SetFileTime(h, ctypes.byref(ft), None, None):
            raise OSError(f"can't set the time of {path}")
    finally:
        k32.CloseHandle(h)


def hide(path: Path):
    k32.SetFileAttributesW(str(path), 0x2)


def junction(link: Path, target: Path) -> bool:
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    return r.returncode == 0 and link.exists()


def bin_records(prefix: Path) -> list[tuple[Path, Path, str]]:
    """What's in the Recycle Bin from under this folder: ($I file, $R item, original path)."""
    drive = os.path.splitdrive(str(prefix))[0] + "\\"
    base = Path(drive) / "$Recycle.Bin"
    want = os.path.normcase(str(prefix)) + os.sep
    out = []
    try:
        sids = [p for p in base.iterdir()]
    except OSError:
        return out
    for sid in sids:
        try:
            infos = [p for p in sid.iterdir() if p.name.upper().startswith("$I")]
        except OSError:
            continue
        for info in infos:
            try:
                data = info.read_bytes()
            except OSError:
                continue
            version = int.from_bytes(data[:8], "little")
            if version == 2:
                n = int.from_bytes(data[24:28], "little")
                original = data[28:28 + 2 * n].decode("utf-16-le", "replace").rstrip("\0")
            else:
                original = data[24:24 + 520].decode("utf-16-le", "replace").split("\0")[0]
            if os.path.normcase(original).startswith(want):
                out.append((info, info.with_name("$R" + info.name[2:]), original))
    return out


def empty_bin_of(prefix: Path):
    """Takes what this test put in the Recycle Bin out again (only that)."""
    for info, item, _ in bin_records(prefix):
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            elif item.exists():
                item.unlink()
            info.unlink()
        except OSError:
            pass
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # (Explorer's bin icon updates)
    except Exception:
        pass


def build(base: Path) -> Path:
    """The test tree. Returns its root."""
    root = base / "tree"
    for d in ("empty", r"nested\a\b\c", r"nested\d", "only ini", r"with file\empty inside", "has link",
              r"has hidden\hidden", "made now"):
        (root / d).mkdir(parents=True)
    (root / "only ini" / "desktop.ini").write_text("[.ShellClassInfo]\r\n")
    (root / "with file" / "note.txt").write_text("keep me")
    target = base / "outside empty"
    target.mkdir()
    check(junction(root / "has link" / "link", target), "a junction to an empty folder was made")
    hide(root / "has hidden" / "hidden")
    old = time.time() - 3 * DAY
    for p in [root, *[p for p in root.rglob("*") if p.is_dir() and not p.is_junction()], target]:
        if p.name != "made now":
            set_created(p, old)
    return root


EXPECT_ALL = {"empty", "nested", "only ini", r"with file\empty inside", "made now"}
EXPECT_OLD = EXPECT_ALL - {"made now"}


def names(found, root: Path) -> set[str]:
    return {os.path.relpath(i.path, root) for i in found.items}


# ----------------------------------------------------------------------------- the finder, no window

def core():
    from corefold import emptyfolders as ef
    from corefold import space

    root = build(WORK / "core")

    def finds():
        t0 = time.time()
        f = ef.find([root], new_seconds=0)
        got = names(f, root)
        check(got == EXPECT_ALL, f"with the age rule off: exactly {sorted(EXPECT_ALL)} ({sorted(got)}, "
                                 f"{time.time() - t0:.2f} s, {f.looked} folders)")
        by = {os.path.relpath(i.path, root): i for i in f.items}
        check(by.get("nested") is not None and by["nested"].inside == 4,
              f"the nested tree is one item with 4 folders inside ({by.get('nested')})")
        check(by.get("only ini") is not None and by["only ini"].leftovers == ("desktop.ini",),
              "the folder with only desktop.ini counts as empty, and says so")
        check(by.get("empty") is not None and by["empty"].created < time.time() - 2 * DAY,
              "its created date is the folder's")
        check(f.new_left == 0 and not f.capped and not f.cancelled, "nothing left out, nothing cut short")
        f = ef.find([root])
        got = names(f, root)
        check(got == EXPECT_OLD, f"with the age rule on: {sorted(EXPECT_OLD)} ({sorted(got)})")
        check(f.new_left == 1, f"and the folder made now is counted as left out ({f.new_left})")
        check(not any("link" in n or "hidden" in n for n in got) and "has link" not in got
              and "has hidden" not in got, "junctions, hidden folders and the folders holding them aren't offered")
        check(os.path.relpath(root, root) not in got, "the folder looked in isn't offered itself")
    step("finding", finds)

    def edges():
        lone = WORK / "lone"
        lone.mkdir()
        set_created(lone, time.time() - 3 * DAY)
        check(ef.find([lone], new_seconds=0).items == [], "an empty folder that is itself the one looked in isn't offered")
        kept = WORK / "kept"
        (kept / "Documents").mkdir(parents=True)
        f = ef.find([kept], new_seconds=0, keep=[kept / "Documents"])
        check(f.items == [], "a user folder (Documents) isn't offered even when it's empty")
        (TMP / "local" / "Thing" / "empty").mkdir(parents=True)
        f = ef.find([TMP / "local" / "Thing"], new_seconds=0)
        check(f.refused and not f.items, "a folder inside AppData isn't looked in")
        win = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        check(ef.off_limits(win / "Temp") and ef.off_limits(Path(os.environ["ProgramFiles"]) / "x"),
              "Windows and Program Files are off limits")
        f = ef.find([root], new_seconds=0, limit=4)
        check(f.capped, "the folder limit stops it and says so")
        f2 = ef.find([root], new_seconds=0)
        check(all(i.path in {x.path for x in f2.items} for i in f.items), "what's found before the limit is right")
        import threading
        stop = threading.Event()
        stop.set()
        check(ef.find([root], stop).cancelled, "Stop stops it")
    step("edges", edges)

    def removes():
        before = space.recycle_bin_size()[1]
        f = ef.find([root])
        # something put in one of them since: it must stay
        sneaky = WORK / "core" / "sneaky"
        sneaky.mkdir()
        set_created(sneaky, time.time() - 3 * DAY)
        f2 = ef.find([WORK / "core"])
        late = [i for i in f2.items if i.path == str(sneaky)]
        (sneaky / "new.txt").write_text("x")
        gone = ef.remove(f.items + late)
        check(len(gone) == len(EXPECT_OLD), f"{len(EXPECT_OLD)} went ({len(gone)})")
        check(sneaky.exists() and (sneaky / "new.txt").exists(), "a folder that got a file since isn't removed")
        for n in EXPECT_OLD:
            check(not (root / n).exists(), f"{n} is gone")
        after = space.recycle_bin_size()[1]
        check(after >= before + len(EXPECT_OLD), f"the Recycle Bin has {after - before} more items")
        records = {os.path.relpath(o, root) for _, _, o in bin_records(root)}
        check(EXPECT_OLD <= records, f"they're in the Recycle Bin by their own paths ({sorted(records)})")
        check((root / "with file" / "note.txt").read_text() == "keep me", "the folder with a file is untouched")
        check((root / "has link" / "link").is_junction() and (WORK / "core" / "outside empty").is_dir(),
              "the junction and where it leads are untouched")
        check((root / "has hidden" / "hidden").is_dir(), "the hidden folder is untouched")
        check((root / "made now").is_dir(), "the folder made now is untouched")
        check(root.is_dir(), "the folder looked in is untouched")
    step("removing", removes)

    def yours():
        folders = ef.user_folders()
        print(f"your folders: {[str(p) for p in folders]}", flush=True)
        check(any(p.name == "Documents" or "Documents" in str(p) for p in folders) or not folders,
              "Documents is among your folders")
        t0 = time.time()
        f = ef.find(folders)
        took = time.time() - t0
        keys = {os.path.normcase(str(p)) for p in folders}
        check(not any(os.path.normcase(i.path) in keys for i in f.items),
              f"none of your folders is offered itself ({len(f.items)} empty folders, {f.looked} looked at, "
              f"{took:.1f} s)")
        check(not any("\\appdata\\" in os.path.normcase(i.path) + "\\" for i in f.items), "nothing in AppData")
    step("your own folders (nothing removed)", yours)


# ----------------------------------------------------------------------------- the window

def page_part():
    from corefold import settings as st
    from corefold import tools as all_tools

    s = st.Settings()
    s.first_run, s.tour_done, s.check_updates = False, True, False
    s.language, s.appearance = LANG, LOOK
    s.known_tools = sorted(t.id for t in all_tools.ready())
    s.tools_off = []
    s.quick_told = True
    s.save()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QPushButton

    qapp = QApplication(sys.argv)
    from corefold import i18n
    i18n.set_language(LANG)
    if i18n.is_rtl():
        qapp.setLayoutDirection(Qt.RightToLeft)
    from corefold import app as A

    def until(cond, seconds=10.0):
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False

    c = A.Controller(qapp, QIcon(str(ROOT / "assets" / "icon.png")))
    said = []
    c.say = lambda title, detail, done=False: said.append((title, detail))
    root = build(WORK / f"page-{LANG}")
    check("emptyview" not in sys.modules and "corefold.emptyview" not in sys.modules,
          "nothing of the empty folders window is loaded before it's asked for")

    def lists():
        c.window.show()
        c.window.resize(1100, 820)
        c.window.open_page("Space")
        check(until(lambda: c.window.space_page is not None, 5), "the Space page opens")
        page = c.window.space_page
        page.show_section("dupes")
        btn = next((b for b in page.findChildren(QPushButton) if b.text() == i18n.tr("Find empty folders…")), None)
        check(btn is not None, "Space's Duplicates section has \"Find empty folders…\"")
        until(lambda: False, 0.4)
        name = f"space-dupes-{LANG}-{LOOK}.png"
        check(c.window.grab().save(str(OUT / name)), f"picture saved: {name}")
        btn.click()
        from corefold.emptyview import EmptyFoldersDialog
        dlg = getattr(page, "_empty_dialog", None)
        check(isinstance(dlg, EmptyFoldersDialog) and dlg.isVisible(), "the window opens")
        check(until(lambda: not dlg.busy(), 120), "looking through your folders finishes")
        dlg.start([root], new_seconds=0)
        check(until(lambda: not dlg.busy(), 20), "looking through the test folder finishes")
        rows = {os.path.relpath(dlg.tree.topLevelItem(i).data(0, Qt.UserRole).path, root)
                for i in range(dlg.tree.topLevelItemCount())}
        check(rows == EXPECT_ALL, f"it lists exactly the empty folders ({sorted(rows)})")
        ticked = dlg.ticked()
        check(len(ticked) == len(EXPECT_ALL), "all of them are ticked")
        check(dlg.remove_btn.isEnabled() and dlg.tree.isVisible(), "Move to the Recycle Bin can be pressed")
        dlg.tree.setCurrentItem(dlg.tree.topLevelItem(0))
        check(dlg.show_btn.isEnabled(), "Show in folder can be pressed for the chosen one")
        dlg.resize(820, 600)
        until(lambda: False, 0.5)
        name = f"emptyfolders-{LANG}-{LOOK}.png"
        check(dlg.grab().save(str(OUT / name)), f"picture saved: {name}")
        if LANG == "en":
            made_now = next(i for i in range(dlg.tree.topLevelItemCount())
                            if dlg.tree.topLevelItem(i).data(0, Qt.UserRole).path.endswith("made now"))
            dlg.tree.topLevelItem(made_now).setCheckState(0, Qt.Unchecked)
            dlg.remove()
            check(until(lambda: not dlg.busy(), 20), "removing finishes")
            check(said and said[-1][0] == i18n.tr("{count} empty folders removed", count="4")
                  and "Recycle Bin" in said[-1][1], f"a message says how many, and where they are ({said})")
            check(dlg.tree.topLevelItemCount() == 1 and (root / "made now").is_dir(),
                  "the unticked one stays, in the list and on the disk")
            name = f"emptyfolders-{LANG}-{LOOK}-removed.png"
            check(dlg.grab().save(str(OUT / name)), f"picture saved: {name}")
        dlg.close()
        until(lambda: False, 0.3)
    step("the window", lists)
    c.quit()


def cleanup():
    try:
        empty_bin_of(WORK)
        check(not bin_records(WORK), "what the test put in the Recycle Bin is gone again")
    except Exception:
        traceback.print_exc()
    for link in WORK.rglob("link"):
        try:
            os.rmdir(link)  # (the junction itself, not what it leads to)
        except OSError:
            pass
    shutil.rmtree(WORK, ignore_errors=True)
    shutil.rmtree(TMP, ignore_errors=True)


def main():
    try:
        if PART == "all":
            step("the finder", core)
            for lang, look in (("en", "dark"), ("ar", "light")):
                r = subprocess.run([sys.executable, __file__, "page", str(OUT), lang, look], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=170)
                print(f"---- the window in {lang} ({look})", flush=True)
                print(r.stdout.rstrip(), flush=True)
                if r.stderr.strip():
                    print(r.stderr.rstrip()[-3000:], flush=True)
                for line in r.stdout.splitlines():
                    if line.startswith("FAIL"):
                        problems.append(f"{lang}: {line[6:]}")
                check(r.returncode in (0, 1), f"the {lang} window part ran to its end (exit {r.returncode})")
        elif PART == "core":
            step("the finder", core)
        else:
            step("the window", page_part)
    finally:
        cleanup()
    print(f"newtest_emptyfolders{'' if PART == 'all' else ' ' + PART}: {len(problems)} problem(s)", flush=True)
    for p in problems:
        print("  - " + p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
