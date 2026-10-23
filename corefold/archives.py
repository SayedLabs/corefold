"""
Archives: open, extract, test, browse and create them.

Uses the bundled 7-Zip (7z.exe + 7z.dll), which reads ZIP, RAR, 7z, ISO, CAB, TAR, GZ, XZ and more,
and writes ZIP and 7z. TAR files (and .tar.gz/.bz2/.xz) go through Python's tarfile, and ZIP falls
back to Python's zipfile when 7-Zip isn't there (e.g. running from source).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .i18n import tr
from .core import ARCHIVE_EXT, NO_WINDOW, TAR_SUFFIXES, Cancelled, Ctx, Result, _cached, app_dir, bundled, is_link, \
    norm, pass_on_zone_mark, remove_file, run_with_progress, safe_name, unique_path

log = logging.getLogger("corefold")
DUMMY_PASSWORD = "-pcorefold-no-password"  # never let 7-Zip stop and ask for one
PROGRESS_RE = re.compile(r"(\d{1,3})%")


class NeedPassword(Exception):
    """The archive is encrypted and the password is missing or wrong."""


@dataclass
class Entry:
    path: str         # inside the archive, with "/" separators
    size: int = 0
    packed: int = 0
    modified: str = ""
    is_dir: bool = False
    encrypted: bool = False


# ----------------------------------------------------------------------------- helpers

_found_7z: list[str] = []


def find_7z() -> str | None:
    if _found_7z and os.path.exists(_found_7z[0]):  # (asked for every menu and wheel entry: looked up once)
        return _found_7z[0]
    _found_7z.clear()
    exe = "7z.exe" if os.name == "nt" else "7z"
    for cand in (bundled("7zip", exe), app_dir() / "7zip" / exe, app_dir() / "vendor" / "7zip" / exe):
        if cand and Path(cand).exists():
            _found_7z.append(str(cand))
            return str(cand)
    for name in ("7z", "7zz", "7za"):
        found = shutil.which(name)
        if found:
            _found_7z.append(found)
            return found
    return None


def _tar_name(name: str) -> str:
    while name.startswith("./"):
        name = name[2:]
    return name.lstrip("/")


def is_tar(p: Path) -> bool:
    return p.name.lower().endswith(TAR_SUFFIXES)


def is_extra_part(p: Path) -> bool:
    """Second, third ... part of a split archive (only the first part should be opened)."""
    name = p.name.lower()
    m = re.search(r"\.(\d{3})$", name)
    if m and int(m.group(1)) != 1:
        return True
    m = re.search(r"\.part(\d+)\.rar$", name)
    return bool(m and int(m.group(1)) != 1)


def archive_stem(p: Path) -> str:
    name = p.name
    low = name.lower()
    for suf in sorted(TAR_SUFFIXES, key=len, reverse=True):
        if low.endswith(suf):
            return name[: -len(suf)]
    name = re.sub(r"\.part\d+\.rar$", "", name, flags=re.I)
    name = re.sub(r"\.(7z|zip|rar)\.\d{3}$", "", name, flags=re.I)
    name = re.sub(r"\.\d{3}$", "", name)
    return Path(name).stem if Path(name).suffix else name


def _listfile(names: list[str]) -> Path:
    """A temporary list of names for 7-Zip (@listfile). The file is closed before 7-Zip reads it:
    on Windows a file that is still open can't be deleted afterwards."""
    fd, name = tempfile.mkstemp(suffix=".txt", prefix="corefold-list-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(names))
    return Path(name)


def _pw(password: str) -> str:
    return f"-p{password}" if password else DUMMY_PASSWORD


def _7z_error(output: str) -> Exception:
    low = output.lower()
    if "wrong password" in low or "can not open encrypted archive" in low or "cannot open encrypted" in low:
        return NeedPassword()
    if "can not open the file as archive" in low or "cannot open the file as archive" in low:
        return RuntimeError(tr("not an archive Corefold can open"))
    if "crc failed" in low or "data error" in low or "unexpected end of archive" in low:
        return RuntimeError(tr("the archive is damaged or incomplete"))
    if "missing volume" in low:
        return RuntimeError(tr("a part of this split archive is missing"))
    if "there is not enough space" in low or "disk full" in low:
        return RuntimeError(tr("the disk is full"))
    lines = [ln.strip() for ln in output.splitlines() if ln.strip() and not PROGRESS_RE.fullmatch(ln.strip())]
    for ln in reversed(lines):
        if ln.lower().startswith("error") or "error:" in ln.lower():
            return RuntimeError(ln.split(":", 1)[-1].strip() or tr("7-Zip reported an error"))
    return RuntimeError(lines[-1] if lines else tr("7-Zip reported an error"))


def _progress(buf: str) -> float | None:
    found = PROGRESS_RE.findall(buf[-40:])
    return int(found[-1]) / 100 if found else None


def _run7z(args: list[str], ctx: Ctx | None = None, cwd: Path | None = None) -> tuple[int, str]:
    exe = find_7z()
    if not exe:
        raise RuntimeError(tr("7-Zip is missing. Reinstall Corefold to fix archives"))
    cmd = [exe, *args]
    if ctx is None:
        proc = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, cwd=str(cwd) if cwd else None,
                              timeout=120, creationflags=NO_WINDOW)
        return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", "replace")
    return run_with_progress(cmd, ctx, _progress, cwd=cwd)


# ----------------------------------------------------------------------------- listing

def _parse_slt(out: str) -> list[Entry]:
    """7-Zip's detailed listing: one block of "Key = value" lines per item, blocks split by an empty line.
    (Written for speed: archives can hold 100,000 items.)"""
    body = out.split("\n----------", 1)[1] if "\n----------" in out else out
    body = body.replace("\r\n", "\n")
    entries = []
    for block in body.split("\n\n"):
        if "Path = " not in block:
            continue
        cur = dict(line.split(" = ", 1) for line in block.split("\n") if " = " in line)
        path = cur.get("Path")
        if not path:
            continue
        size, packed, attrs = cur.get("Size", ""), cur.get("Packed Size", ""), cur.get("Attributes", "")
        entries.append(Entry(
            path=path.replace("\\", "/"),
            size=int(size) if size.isdigit() else 0,
            packed=int(packed) if packed.isdigit() else 0,
            modified=cur.get("Modified", ""),
            is_dir=cur.get("Folder") == "+" or attrs[:1] == "D",
            encrypted=cur.get("Encrypted") == "+"))
    return entries


def list_entries(src: Path, password: str = "") -> list[Entry]:
    if is_tar(src):
        try:
            with tarfile.open(src, "r:*") as tf:
                return [Entry(_tar_name(m.name), m.size, 0, _tar_time(m.mtime), m.isdir())
                        for m in tf.getmembers() if _tar_name(m.name)]
        except (tarfile.TarError, OSError, EOFError) as e:
            raise RuntimeError(tr("the archive is damaged or isn't a TAR file")) from e
    if find_7z():
        code, out = _run7z(["l", "-slt", "-sccUTF-8", _pw(password), str(src)])
        if code not in (0, 1):
            raise _7z_error(out)
        entries = _parse_slt(out)
        # 7-Zip also prints the archive itself as the first block when there's no "----------" marker
        return [e for e in entries if e.path != str(src).replace("\\", "/")]
    if src.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(src) as zf:
                return [Entry(i.filename.rstrip("/"), i.file_size, i.compress_size,
                              "%04d-%02d-%02d %02d:%02d" % i.date_time[:5], i.is_dir(), bool(i.flag_bits & 1))
                        for i in zf.infolist()]
        except zipfile.BadZipFile as e:
            raise RuntimeError(tr("the ZIP file is damaged")) from e
    raise RuntimeError(tr("7-Zip is missing. Reinstall Corefold to fix archives"))


def _tar_time(ts: float) -> str:
    import time
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except (OverflowError, OSError, ValueError):
        return ""


def is_encrypted(src: Path) -> bool:
    def work():
        if is_tar(src):
            return False
        try:
            return any(e.encrypted for e in list_entries(src))
        except NeedPassword:
            return True
        except Exception:
            return False
    return _cached("encrypted", src, work)


def password_works(src: Path, password: str) -> bool:
    """True if the password opens the archive: tried on its smallest locked file only, so it's quick
    even for a big archive."""
    try:
        entries = list_entries(src, password)
    except NeedPassword:
        return False  # the names are locked too (7z): this password didn't open them
    locked = [e for e in entries if e.encrypted and not e.is_dir]
    if not locked:
        return True
    smallest = min(locked, key=lambda e: e.size)
    if find_7z():
        listfile = _listfile([smallest.path])
        try:
            code, _ = _run7z(["t", str(src), "-sccUTF-8", "-scsUTF-8", _pw(password), f"@{listfile}"])
        finally:
            listfile.unlink(missing_ok=True)
        return code == 0
    try:
        with zipfile.ZipFile(src) as zf:
            zf.setpassword(password.encode())
            with zf.open(smallest.path) as f:
                while f.read(1 << 20):
                    pass
        return True
    except (RuntimeError, zipfile.BadZipFile, KeyError, OSError):
        return False


def top_level(entries: list[Entry]) -> set[str]:
    return {e.path.strip("/").split("/", 1)[0] for e in entries if e.path.strip("/")}


def check_readable(files: list[Path]) -> str | None:
    if is_tar(files[0]):
        return None
    if not find_7z() and files[0].suffix.lower() != ".zip":
        return tr("Needs 7-Zip (reinstall Corefold)")
    return None


# ----------------------------------------------------------------------------- extracting

def _extract_into(src: Path, target: Path, ctx: Ctx, password: str, members: list[str] | None = None):
    if is_tar(src):
        with tarfile.open(src, "r:*") as tf:
            items = tf.getmembers()
            if members:
                wanted = set(members)
                items = [m for m in items if _tar_name(m.name) in wanted]
            for i, m in enumerate(items):
                ctx.check()
                tf.extract(m, target, filter="data")
                ctx.progress((i + 1) / max(1, len(items)))
        pass_on_zone_mark([src], [target])
        return
    if find_7z():
        # -snz: files from a downloaded archive keep its "from the internet" mark (Windows then still
        # warns before running them); -spd: names are names, not wildcards
        args = ["x", str(src), f"-o{target}", "-y", "-bsp1", "-bse1", "-bb0", "-sccUTF-8", "-snz", "-spd",
                _pw(password)]
        listfile = None
        if members:
            listfile = _listfile([m.replace("/", os.sep) for m in members])
            args += ["-scsUTF-8", f"@{listfile}"]
        try:
            code, out = _run7z(args, ctx)
        finally:
            if listfile:
                listfile.unlink(missing_ok=True)
        if code not in (0, 1):
            raise _7z_error(out)
        return
    wanted = set(members or [])
    with zipfile.ZipFile(src) as zf:
        infos = [i for i in zf.infolist() if not wanted or i.filename.rstrip("/") in wanted]
        for i, info in enumerate(infos):
            ctx.check()
            try:
                zf.extract(info, target, pwd=password.encode() if password else None)
            except RuntimeError as e:
                if "password" in str(e).lower() or "encrypted" in str(e).lower():
                    raise NeedPassword() from e
                raise
            ctx.progress((i + 1) / max(1, len(infos)))
    pass_on_zone_mark([src], [target])


def extract(src: Path, ctx: Ctx, mode: str = "smart") -> Result:
    """mode "smart": straight into the folder if the archive holds a single item, otherwise into a
    new folder named after it. mode "folder": always into a new folder. ctx.params may hold
    "password", "dest" (a folder to extract into) and "members" (only these paths).

    Everything is first unpacked into a hidden folder next to the destination and only moved into
    place when it worked, so a wrong password or a damaged archive never leaves half files behind,
    and nothing that's already there gets overwritten."""
    password = ctx.params.get("password", "")
    members = ctx.params.get("members")
    base = Path(ctx.params["dest"]) if ctx.params.get("dest") else ctx.folder_for(src)
    base.mkdir(parents=True, exist_ok=True)
    # safe_name: a name like "photos." would lose its dot on Windows and the new folder couldn't be found
    name = safe_name(ctx.options.rename_base) or safe_name(archive_stem(src)) or "Extracted"

    _tidy_old_staging(base)
    staging = Path(tempfile.mkdtemp(prefix=".corefold-unpacking-", dir=base))
    try:
        _hide(staging)
        _extract_into(src, staging, ctx, password, members)
        items = sorted(staging.iterdir())
        if not items:
            raise RuntimeError(tr("the archive is empty"))
        if members or (mode == "smart" and len(items) == 1):
            moved = [_move_unique(item, base) for item in items]
            result = moved[0] if len(moved) == 1 else base
            created = moved
        else:
            result = unique_path(base / name)
            staging.rename(result)
            staging = None
            _hide(result, False)
            created = [result]
        note = ""
        if ctx.params.get("nested"):  # archives inside: unpacked too (only in what was just made)
            count = unpack_nested(created, ctx)
            if count:
                note = tr("Also unpacked {count} archives that were inside", count=count)
        return Result([result], note=note)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


_STAGING = re.compile(r"^\.corefold-unpacking-[a-z0-9_]{8}$")


def _tidy_old_staging(folder: Path):
    """A hidden unpacking folder left behind when the PC was switched off mid-way (older than a day)."""
    import time
    try:
        for p in folder.iterdir():
            if _STAGING.match(p.name) and p.is_dir() and not is_link(p) and p.stat().st_mtime < time.time() - 86400:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def _move_unique(item: Path, folder: Path) -> Path:
    target = unique_path(folder / item.name)
    shutil.move(str(item), str(target))
    return target


def _hide(path: Path, hidden: bool = True):
    """Hides the unpacking folder while it fills up, and shows it again when it's done.
    (FILE_ATTRIBUTE_NORMAL clears "hidden"; the "directory" flag can't be set this way, so using it
    could leave the finished folder invisible.)"""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02 if hidden else 0x80)  # HIDDEN / NORMAL
        except Exception:
            pass


def test_archive(src: Path, ctx: Ctx) -> Result:
    password = ctx.params.get("password", "")
    if is_tar(src):
        with tarfile.open(src, "r:*") as tf:
            members = tf.getmembers()
            for i, m in enumerate(members):
                ctx.check()
                if m.isfile():
                    f = tf.extractfile(m)
                    while f and f.read(1 << 20):
                        pass
                ctx.progress((i + 1) / max(1, len(members)))
        return Result(note=tr("No problems found in {name}", name=src.name))
    if find_7z():
        code, out = _run7z(["t", str(src), "-bsp1", "-bse1", "-sccUTF-8", _pw(password)], ctx)
        if code not in (0, 1):
            raise _7z_error(out)
        return Result(note=tr("No problems found in {name}", name=src.name))
    with zipfile.ZipFile(src) as zf:
        if password:
            zf.setpassword(password.encode())
        try:
            bad = zf.testzip()
        except RuntimeError as e:
            raise NeedPassword() from e
    if bad:
        raise RuntimeError(tr("damaged file inside: {name}", name=bad))
    return Result(note=tr("No problems found in {name}", name=src.name))


# ----------------------------------------------------------------------------- creating

LEVELS = {"fast": 1, "normal": 5, "max": 9}


def _pack_name(items: list[Path], ctx: Ctx) -> str:
    if ctx.options.rename_base:
        return safe_name(ctx.options.rename_base)
    if len(items) == 1:
        p = items[0]
        name = p.name if p.is_dir() else p.stem
    else:
        name = items[0].parent.name
    return safe_name(name) or "Archive"  # a whole drive (D:\) has no name


def pack_into(items: list[Path], out: Path, ctx: Ctx, fmt: str = "zip", level: str = "normal",
              password: str = "", split_mb: float = 0) -> list[Path]:
    """Creates out (a .zip or .7z) from the items. Returns the file(s) written."""
    try:
        parent = Path(os.path.commonpath([str(p.parent.resolve()) for p in items]))
    except ValueError as e:  # different drives
        raise RuntimeError(tr("pick files that are on the same drive")) from e
    rel = [os.path.relpath(p.resolve(), parent) for p in items]
    if find_7z():
        args = ["a", f"-t{fmt}", f"-mx={LEVELS.get(level, 5)}", "-bsp1", "-bse1", "-bb0", "-sccUTF-8", "-ssw", "-y",
                "-spd"]
        if password:
            args.append(f"-p{password}")
            args.append("-mhe=on" if fmt == "7z" else "-mem=AES256")
        if split_mb and split_mb > 0:
            args.append(f"-v{int(split_mb * 1024)}k")
        listfile = _listfile(rel)

        def remove_made():
            # remove only what this run made: the archive and its numbered parts (.001, .002 …),
            # never other files that just start with the same name
            remove_file(out)
            for part in out.parent.glob(glob_escape(out.name) + ".[0-9][0-9][0-9]"):
                remove_file(part)
        try:
            code, output = _run7z([*args, "-scsUTF-8", str(out), f"@{listfile}"], ctx, cwd=parent)
        except Cancelled:  # stopped halfway: no half-made archive is left behind
            remove_made()
            raise
        finally:
            listfile.unlink(missing_ok=True)
        if code not in (0, 1):
            remove_made()
            raise _7z_error(output)
        if split_mb:
            return sorted(out.parent.glob(glob_escape(out.name) + ".[0-9][0-9][0-9]"))
        return [out]
    # no 7-Zip: plain ZIP through Python
    if fmt != "zip" or password or split_mb:
        raise RuntimeError(tr("needs 7-Zip (reinstall Corefold)"))
    files: list[tuple[Path, str]] = []
    for p, r in zip(items, rel):
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                files.append((f, os.path.join(r, os.path.relpath(f, p))))
        else:
            files.append((p, r))
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=LEVELS.get(level, 5)) as zf:
            for i, (f, arc) in enumerate(files):
                ctx.check()
                zf.write(f, arc)
                ctx.progress((i + 1) / max(1, len(files)))
    except BaseException:
        remove_file(out)
        raise
    return [out]


def glob_escape(name: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", name)


def pack(items: list[Path], ctx: Ctx, fmt: str | None = None) -> Result:
    p = ctx.params
    fmt = fmt or p.get("format", "zip")
    out = ctx.out_path(items[0], f"{_pack_name(items, ctx)}.{fmt}")
    outs = pack_into(items, out, ctx, fmt, p.get("level", "normal"), p.get("password", ""),
                     float(p.get("split_mb") or 0))
    note = ""
    if len(outs) > 1:
        note = tr("Saved in {count} parts ({name} …)", count=len(outs), name=outs[0].name)
    if p.get("password"):
        note = (note + " · " if note else "") + tr("Protected with your password")
    return Result(outs, note=note)


def convert_archive(src: Path, ctx: Ctx, fmt: str) -> Result:
    """Re-packs an archive as ZIP or 7z (e.g. RAR → ZIP)."""
    with tempfile.TemporaryDirectory(prefix="corefold-", ignore_cleanup_errors=True) as tmp:
        inner = Ctx(ctx.options, lambda f: ctx.progress(0.5 * f), ctx.cancel_event, {"password": ctx.params.get("password", "")})
        _extract_into(src, Path(tmp), inner, ctx.params.get("password", ""))
        items = sorted(Path(tmp).iterdir())
        if not items:
            raise RuntimeError(tr("the archive is empty"))
        out = ctx.out_path(src, f"{safe_name(ctx.options.rename_base) or safe_name(archive_stem(src)) or 'Archive'}.{fmt}")
        packer = Ctx(ctx.options, lambda f: ctx.progress(0.5 + 0.5 * f), ctx.cancel_event)
        # a password-protected archive stays protected, with the same password, in its new format
        password = ctx.params.get("password", "")
        outs = pack_into(items, out, packer, fmt, "normal", password=password)
    return Result(outs, note=tr("Protected with your password") if password else "")


def check_needs_7z(files: list[Path]) -> str | None:
    return None if find_7z() else tr("Needs 7-Zip (reinstall Corefold)")


# ----------------------------------------------------------------------------- editing (ZIP / 7z)

def can_edit(src: Path) -> bool:
    return bool(find_7z()) and src.suffix.lower() in (".zip", ".7z") and not is_tar(src)


def add_files(src: Path, items: list[Path], ctx: Ctx) -> Result:
    """Adds files/folders to an existing ZIP or 7z archive (at its top level)."""
    if not can_edit(src):
        raise RuntimeError(tr("only ZIP and 7z archives can be changed"))
    password = ctx.params.get("password", "")
    for parent in {p.parent.resolve() for p in items}:
        group = [p for p in items if p.parent.resolve() == parent]
        listfile = _listfile([p.name for p in group])
        try:
            args = ["a", str(src.resolve()), "-bsp1", "-bse1", "-bb0", "-sccUTF-8", "-scsUTF-8", "-ssw", "-y", "-spd"]
            if password:
                args.append(f"-p{password}")
            code, out = _run7z([*args, f"@{listfile}"], ctx, cwd=parent)
        finally:
            listfile.unlink(missing_ok=True)
        if code not in (0, 1):
            raise _7z_error(out)
    return Result([src], note=tr("Added {count} items to {name}", count=len(items), name=src.name))


def delete_entries(src: Path, members: list[str], ctx: Ctx) -> Result:
    if not can_edit(src):
        raise RuntimeError(tr("only ZIP and 7z archives can be changed"))
    listfile = _listfile([m.replace("/", os.sep) for m in members])
    try:
        code, out = _run7z(["d", str(src), "-bsp1", "-bse1", "-sccUTF-8", "-scsUTF-8", "-y", "-spd",
                            _pw(ctx.params.get("password", "")), f"@{listfile}"], ctx)
    finally:
        listfile.unlink(missing_ok=True)
    if code not in (0, 1):
        raise _7z_error(out)
    return Result([src], note=tr("Removed {count} items from {name}", count=len(members), name=src.name))


def extract_for_opening(src: Path, member: str, ctx: Ctx) -> Result:
    """Extracts one file to a temporary folder so it can be opened with its app."""
    target = Path(tempfile.mkdtemp(prefix="corefold-open-"))
    inner = Ctx(ctx.options, ctx._progress, ctx.cancel_event, dict(ctx.params, dest=str(target), members=[member]))
    extract(src, inner)
    out = target / member.replace("/", os.sep)
    try:
        inside = out.resolve().is_relative_to(target.resolve())
    except OSError:
        inside = False
    if not inside or not out.is_file():
        raise RuntimeError(tr("couldn't take that file out of the archive"))
    return Result([out])


# ----------------------------------------------------------------------------- archives inside archives

NESTED_DEPTH = 3     # an archive in an archive in an archive, and no deeper
NESTED_MAX = 200


def is_archive_file(p: Path) -> bool:
    try:
        return p.is_file() and not is_link(p) and (norm(p.suffix) in ARCHIVE_EXT or is_tar(p)) \
            and not is_extra_part(p)
    except OSError:
        return False


def unpack_nested(roots: list[Path], ctx: Ctx) -> int:
    """Unpacks archives found in roots (files and folders Corefold just made), each into a folder
    named after it, and removes that inner copy once it's unpacked. Returns how many."""
    from dataclasses import replace
    options = replace(ctx.options, rename_base="")
    done = 0
    queue = [(r, 1) for r in roots]
    while queue:
        root, depth = queue.pop(0)
        try:
            found = [root] if root.is_file() else [p for p in sorted(root.rglob("*")) if not is_link(p)]
        except OSError:
            continue
        for f in found:
            if done >= NESTED_MAX:
                return done
            if not is_archive_file(f):
                continue
            ctx.check()
            inner = Ctx(options, None, ctx.cancel_event, {"dest": str(f.parent)})
            try:
                made = extract(f, inner, mode="folder").outputs[0]
            except NeedPassword:
                continue
            except Exception:
                log.info("couldn't unpack %s inside the archive", f.name, exc_info=True)
                continue
            try:
                f.unlink()
            except OSError:
                pass
            done += 1
            if depth < NESTED_DEPTH:
                queue.append((made, depth + 1))
    return done


def holds_archives(entries: list[Entry]) -> bool:
    return any(not e.is_dir and is_archive_file_name(e.path) for e in entries)


def is_archive_file_name(name: str) -> bool:
    base = name.rstrip("/").rpartition("/")[2]
    stem, dot, ext = base.rpartition(".")
    if not dot or not stem:
        return False
    return norm("." + ext) in ARCHIVE_EXT and not is_extra_part(Path(base))


# ----------------------------------------------------------------------------- changing a file inside

UPDATE_ALL = "-up1q1r2x2y2z2w2"  # replace the file even if the archive's copy looks newer


def update_member(src: Path, member: str, new_file: Path, ctx: Ctx) -> Result:
    """Puts an edited copy of a file back into the archive, at the same place inside it."""
    if not can_edit(src):
        raise RuntimeError(tr("only ZIP and 7z archives can be changed"))
    parts = [x for x in member.replace("\\", "/").strip("/").split("/") if x]
    if not parts or any(x in (".", "..") or ":" in x for x in parts):
        raise RuntimeError(tr("couldn't put that file back into the archive"))
    rel = os.sep.join(parts)
    stage = Path(tempfile.mkdtemp(prefix="corefold-"))
    listfile = None
    try:
        dest = stage / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(new_file, dest)
        listfile = _listfile([rel])
        args = ["u", str(src.resolve()), UPDATE_ALL, "-bsp1", "-bse1", "-bb0", "-sccUTF-8", "-scsUTF-8", "-y", "-spd"]
        password = ctx.params.get("password", "")
        if password:
            args.append(f"-p{password}")
        code, out = _run7z([*args, f"@{listfile}"], ctx, cwd=stage)
    finally:
        if listfile:
            listfile.unlink(missing_ok=True)
        shutil.rmtree(stage, ignore_errors=True)
    if code not in (0, 1):
        raise _7z_error(out)
    return Result([src], note=tr("Saved {name} back into {archive}", name=parts[-1], archive=src.name))


# ----------------------------------------------------------------------------- rescuing damaged archives

_DAMAGED_LINE = re.compile(r"^ERROR:\s*(?:[^:]*?)\s*:\s*(.+?)\s*$")


def _scan_zip(src: Path, target: Path, ctx: Ctx) -> tuple[int, int]:
    """Reads a ZIP file by its pieces (each file's own header), for ZIPs whose table of contents at the
    end is missing or broken. Writes every file that checks out; returns (good, damaged)."""
    import mmap
    import struct
    import zlib
    good = bad = 0
    root = target.resolve()
    with open(src, "rb") as fh:
        try:
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:  # empty file
            return 0, 0
        with mm:
            size = len(mm)
            pos = 0
            while True:
                ctx.check()
                i = mm.find(b"PK\x03\x04", pos)
                if i < 0 or i + 30 > size:
                    break
                ctx.progress(i / max(1, size))
                (_sig, _ver, flag, method, _t, _d, crc, csize, usize, nlen, xlen) = \
                    struct.unpack("<IHHHHHIIIHH", mm[i:i + 30])
                raw = mm[i + 30:i + 30 + nlen]
                name = raw.decode("utf-8" if flag & 0x800 else "cp437", "replace")
                start = i + 30 + nlen + xlen
                pos = i + 4
                parts = [x for x in name.replace("\\", "/").split("/") if x not in ("", ".")]
                if not parts or ".." in parts or any(":" in x for x in parts) or start > size:
                    continue
                out = target.joinpath(*parts)
                try:
                    if not out.resolve().is_relative_to(root):
                        continue
                except OSError:
                    continue
                if name.endswith("/"):
                    try:
                        out.mkdir(parents=True, exist_ok=True)
                    except OSError:  # a file of that name came first
                        pass
                    continue
                if flag & 1 or method not in (0, 8) or csize == 0xFFFFFFFF:  # locked, or a kind not read here
                    bad += 1
                    continue
                known = not (flag & 8)  # otherwise the sizes come after the data
                check = 0
                ok = True
                tmp = out.with_name(out.name + ".part")
                try:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with open(tmp, "wb") as w:
                        if method == 0:
                            if not known:
                                ok = False
                            else:
                                data = mm[start:start + csize]
                                ok = len(data) == csize
                                w.write(data)
                                check = zlib.crc32(data)
                                end = start + csize
                        else:
                            d = zlib.decompressobj(-15)
                            p = start
                            limit = start + csize if known else size
                            try:
                                while p < limit and not d.eof:
                                    chunk = mm[p:min(limit, p + (1 << 20))]
                                    p += len(chunk)
                                    piece = d.decompress(chunk)
                                    check = zlib.crc32(piece, check)
                                    w.write(piece)
                                    ctx.check()
                                ok = d.eof
                            except zlib.error:  # damaged data
                                ok = False
                            end = p - len(d.unused_data)
                            if not known and ok and end + 16 <= size:  # the data descriptor holds the check
                                tail = mm[end:end + 16]
                                crc = struct.unpack("<I", tail[4:8] if tail[:4] == b"PK\x07\x08" else tail[:4])[0]
                    if ok and check == crc:
                        os.replace(tmp, out)
                        good += 1
                        pos = max(pos, end)
                    else:
                        bad += 1
                except OSError as e:  # a name that clashes with a folder, an odd name, a full disk
                    if getattr(e, "errno", None) == 28:  # ENOSPC: no point going on
                        raise
                    bad += 1
                finally:
                    try:
                        if tmp.exists():
                            tmp.unlink()
                    except OSError:
                        pass
    return good, bad


def repair_archive(src: Path, ctx: Ctx) -> Result:
    """Takes out everything that can still be read from a damaged archive and puts it in a new,
    healthy ZIP next to it ("name (repaired).zip"). The damaged archive is left as it was."""
    password = ctx.params.get("password", "")
    base = ctx.folder_for(src)  # next to it, or the folder chosen for results (a CD or a locked folder: elsewhere)
    base.mkdir(parents=True, exist_ok=True)
    _tidy_old_staging(base)
    stage = Path(tempfile.mkdtemp(prefix=".corefold-unpacking-", dir=base))
    try:
        _hide(stage)
        damaged = 0
        if find_7z():
            code, out = _run7z(["x", str(src), f"-o{stage}", "-y", "-bsp1", "-bse1", "-bb0", "-sccUTF-8", "-snz",
                                "-spd", _pw(password)], ctx)
            if code not in (0, 1):
                err = _7z_error(out)
                if isinstance(err, NeedPassword):
                    raise err
            for line in out.splitlines():  # files 7-Zip found broken: left out of the new ZIP
                m = _DAMAGED_LINE.match(line.strip())
                if m:
                    broken = stage / m.group(1).replace("/", os.sep)
                    try:
                        if broken.is_file() and broken.resolve().is_relative_to(stage.resolve()):
                            broken.unlink()
                            damaged += 1
                    except OSError:
                        pass
        rescued = [p for p in stage.rglob("*") if p.is_file()]
        if not rescued and src.suffix.lower() in (".zip", ".zipx"):  # read it piece by piece instead
            shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir()
            _hide(stage)
            _good, damaged = _scan_zip(src, stage, ctx)
            rescued = [p for p in stage.rglob("*") if p.is_file()]
        if not rescued:
            raise RuntimeError(tr("nothing in this archive could be rescued"))
        out_zip = unique_path(base / f"{safe_name(archive_stem(src)) or 'Archive'} (repaired).zip")
        outs = pack_into(sorted(stage.iterdir()), out_zip, ctx, "zip", password=password)
        pass_on_zone_mark([src], outs)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    note = tr("Rescued {count} files", count=len(rescued))
    if damaged:
        note += " · " + tr("{count} damaged files couldn't be saved", count=damaged)
    return Result(outs, note=note)


# ----------------------------------------------------------------------------- dragging files out

def extract_for_drag(src: Path, members: list[str], password: str) -> Path:
    """Takes these files out into a new temporary folder (for dragging them into Explorer)."""
    from .core import Options
    target = Path(tempfile.mkdtemp(prefix="corefold-drag-"))
    _extract_into(src, target, Ctx(Options()), password, members)
    return target


def remove_drag_folder(folder: Path):
    """Removes a temporary folder made by extract_for_drag (and nothing else)."""
    import tempfile as _tf
    try:
        if folder.name.startswith("corefold-drag-") and folder.parent.resolve() == Path(_tf.gettempdir()).resolve() \
                and folder.is_dir() and not is_link(folder):
            shutil.rmtree(folder, ignore_errors=True)
    except OSError:
        pass
