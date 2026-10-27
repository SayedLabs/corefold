"""
Archives: open, extract, test, browse and create them.

Uses the bundled 7-Zip (7z.exe + 7z.dll), which reads ZIP, RAR, 7z, ISO, CAB, TAR, GZ, XZ and more,
and writes ZIP and 7z. TAR files (and .tar.gz/.bz2/.xz/.zst) go through Python's tarfile (zstd through
zstandard), and ZIP falls back to Python's zipfile when 7-Zip isn't there (e.g. running from source).
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
from .core import ARCHIVE_EXT, NO_WINDOW, TAR_SUFFIXES, Cancelled, Ctx, Result, _cached, app_dir, bundled, \
    find_program, is_link, human_size, new_folder, norm, pass_on_zone_mark, remove_file, run_with_progress, safe_name, \
    unique_path

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
        found = find_program(name)
        if found:
            _found_7z.append(found)
            return found
    return None


def _tar_name(name: str) -> str:
    while name.startswith("./"):
        name = name[2:]
    return name.lstrip("/")


_NOT_ON_WINDOWS = re.compile(r'[<>:"|?*\\\x00-\x1f]')


def windows_safe_name(name: str) -> str:
    """A name from a Linux or Mac TAR that Windows can save: characters it refuses become "_" and an ending
    dot or space goes (like 7-Zip does). Before, one such name stopped the whole extraction."""
    return "/".join(part if part in ("", ".", "..") else _NOT_ON_WINDOWS.sub("_", part).rstrip(" .") or "_"
                    for part in name.split("/"))


def _tar_filter(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo | None:
    member = tarfile.data_filter(member, path)  # (nothing outside the folder, no odd links or devices)
    if member is not None and os.name == "nt":
        name = windows_safe_name(member.name)
        if name != member.name:
            member = member.replace(name=name, deep=False)
    return member


def is_tar(p: Path) -> bool:
    return p.name.lower().endswith(TAR_SUFFIXES)


def is_zst(p: Path) -> bool:
    """One file packed with zstd (not a TAR of several)."""
    return p.name.lower().endswith(".zst") and not is_tar(p)


class _open_tar:
    """A TAR file of any kind, .tar.zst too. Read it from start to end ("for m in tf"): a zstd one
    can't be read in any other order. .progress() is how far through the file the reading is."""

    def __init__(self, src: Path, check=None):
        self.src = src
        self.raw = None
        self.tf = None
        self.check = check  # (called at each read, so even one huge file inside can be stopped part way)
        self.file = None

    def __enter__(self) -> tarfile.TarFile:
        low = self.src.name.lower()
        self.file = open(self.src, "rb")
        fh = self.file if self.check is None else _Checked(self.file, self.check)
        try:
            if low.endswith((".zst", ".tzst")):
                import zstandard
                self.raw = self.file
                stream = zstandard.ZstdDecompressor().stream_reader(fh)
                self.tf = tarfile.open(fileobj=stream, mode="r|")
            else:
                self.tf = tarfile.open(fileobj=fh, mode="r:*")
                self.raw = self.tf.fileobj
        except BaseException:
            self.file.close()
            raise
        return self.tf

    def progress(self) -> float:
        try:
            raw = getattr(self.raw, "fileobj", None) or getattr(self.raw, "_fp", None) or self.raw  # (the file itself)
            return min(1.0, raw.tell() / max(1, self.src.stat().st_size))
        except (OSError, AttributeError, ValueError):
            return 0.0

    def __exit__(self, *exc):
        if self.tf is not None:
            self.tf.close()
        if self.file is not None:
            self.file.close()
        return False


class _Checked:
    """A file that calls check() before each read (it raises Cancelled to stop)."""

    def __init__(self, f, check):
        self._f, self._check = f, check

    def read(self, n=-1):
        self._check()
        return self._f.read(n)

    def __getattr__(self, name):
        return getattr(self._f, name)


def _unzst(src: Path, out, ctx: Ctx | None = None):
    """Unpacks one zstd file into out (an open file)."""
    import zstandard
    size = max(1, src.stat().st_size)
    with open(src, "rb") as fh, zstandard.ZstdDecompressor().stream_reader(fh) as reader:
        while True:
            if ctx is not None:
                ctx.check()
            chunk = reader.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            if ctx is not None:
                ctx.progress(min(1.0, fh.tell() / size))


def _zstd_errors() -> tuple:
    try:
        import zstandard
        return (zstandard.ZstdError,)
    except ImportError:
        return ()


class _Nowhere:
    def write(self, _data):
        pass


def _zst_inner_name(src: Path) -> str:
    return src.name[:-4] or "file"


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


def _pw(password: str, making: bool = False):
    """A password for 7-Zip. It never goes on 7-Zip's command line, where other programs, logs and security
    tools can read every program's command line: _run7z leaves it out and types it in through 7-Zip's input
    instead, as a person would when 7-Zip asks for it ("-p" with no password asks when making an archive;
    opening one asks by itself when it needs one)."""
    if not password:
        return DUMMY_PASSWORD  # (not secret: it only stops 7-Zip from waiting for one)
    return _Secret(password, making)


class _Secret:
    def __init__(self, password: str, making: bool):
        self.password, self.making = password, making

    def __repr__(self):  # (never in a log)
        return "<password>"


def _with_password(cmd: list) -> tuple[list[str], bytes | None]:
    """The command as 7-Zip is started (no password in it), and what's typed in for 7-Zip to read."""
    out, typed = [], None
    for a in cmd:
        if isinstance(a, _Secret):
            if a.making:
                out.append("-p")
            # (twice: some 7-Zip versions ask again to be sure when making an archive; UTF-8, as -sccUTF-8 says)
            typed = ((a.password + "\n") * 2).encode("utf-8")
        else:
            out.append(a)
    if typed is not None and "-sccUTF-8" not in out:
        out.insert(2, "-sccUTF-8")
    return out, typed


def _7z_error(output: str) -> Exception:
    low = output.lower()
    if "wrong password" in low or "can not open encrypted archive" in low or "cannot open encrypted" in low:
        return NeedPassword()
    if "being used by another process" in low or "access is denied" in low:  # (not "damaged": it may be fine)
        return PermissionError(tr("permission denied (is the file open in another app?)"))
    if "crc failed" in low or "data error" in low or "unexpected end of archive" in low or "headers error" in low:
        return RuntimeError(tr("the archive is damaged or incomplete"))
    # ("Cannot open the file as [zip] archive" when the name says ZIP but what's inside isn't one)
    if re.search(r"can ?not open (the )?file as (\[[^\]]*\] )?archive|can't open as archive|is not archive", low):
        return RuntimeError(tr("the archive is damaged or isn't a real archive"))
    if "missing volume" in low:
        return RuntimeError(tr("a part of this split archive is missing"))
    if "there is not enough space" in low or "disk full" in low or "no space left on device" in low:
        return RuntimeError(tr("the disk is full"))
    lines = [ln.strip() for ln in output.splitlines() if ln.strip() and not PROGRESS_RE.fullmatch(ln.strip())]
    for ln in reversed(lines):
        if ln.lower().startswith("error") or "error:" in ln.lower():
            return RuntimeError(ln.split(":", 1)[-1].strip() or tr("7-Zip reported an error"))
    return RuntimeError(lines[-1] if lines else tr("7-Zip reported an error"))


def _progress(buf: str) -> float | None:
    found = PROGRESS_RE.findall(buf[-40:])
    return int(found[-1]) / 100 if found else None


def _too_deep(folder: Path) -> bool:
    """Windows can't start a program inside a folder whose path is past its old 260-letter limit."""
    return os.name == "nt" and len(str(folder)) >= 240


def _run7z(args: list, ctx: Ctx | None = None, cwd: Path | None = None) -> tuple[int, str]:
    exe = find_7z()
    if not exe:
        raise RuntimeError(tr("7-Zip is missing. Reinstall Corefold to fix archives"))
    cmd, typed = _with_password([exe, *args])
    if ctx is None:
        proc = subprocess.run(cmd, capture_output=True, input=typed or b"", cwd=str(cwd) if cwd else None,
                              timeout=120, creationflags=NO_WINDOW)
        return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", "replace")
    return run_with_progress(cmd, ctx, _progress, cwd=cwd, typed=typed)


# ----------------------------------------------------------------------------- ZIP name code pages

# A ZIP made before UTF-8 (Windows' own "Compressed folder", old WinZip and WinRAR) keeps its names in the
# code page of the PC that made it, without saying which. Unpacked on a PC with another one, Arabic names
# come out as rubbish. So Corefold reads the raw names and works out which code page reads them best.
# Arabic ones are mostly 720 (Arabic DOS, what Windows itself writes), some 1256 (Arabic Windows) or 864.
UTF8_CP = 65001
CODEPAGES = (720, 1256, 864)          # tried, with the PC's own two, for names that aren't UTF-8
_SAMPLE_NAMES = 3000                  # raw names read to decide (from the start of the table of contents)
_chosen_cp: dict[str, int] = {}       # archive -> code page the person picked while it's open


def _pc_codepages() -> list[int]:
    if os.name != "nt":
        return []
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        return [int(k32.GetOEMCP()), int(k32.GetACP())]
    except Exception:
        return []


def _legacy_zip_names(src: Path, limit: int = _SAMPLE_NAMES) -> list[bytes]:
    """The raw names in a ZIP's table of contents that aren't marked UTF-8 and aren't plain ASCII
    (only the first `limit` items are read, so even a ZIP of 200,000 files is quick)."""
    import struct
    try:
        size = src.stat().st_size
        with open(src, "rb") as fh:
            tail_len = min(size, 65536 + 22)
            fh.seek(size - tail_len)
            tail = fh.read(tail_len)
            at = tail.rfind(b"PK\x05\x06")
            if at < 0 or len(tail) - at < 22:
                return []
            cd_size, cd_offset = struct.unpack_from("<II", tail, at + 12)
            end = size - tail_len + at  # where the table of contents ends
            if (cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF) and at >= 20 \
                    and tail[at - 20:at - 16] == b"PK\x06\x07":  # ZIP64: its own end record says
                z64 = struct.unpack_from("<Q", tail, at - 12)[0]
                fh.seek(z64)
                rec = fh.read(56)
                if rec[:4] != b"PK\x06\x06" or len(rec) < 56:
                    return []
                cd_size = struct.unpack_from("<Q", rec, 40)[0]
                end = z64
            start = end - cd_size  # (counted from the end: right even with something stuck in front)
            if start < 0:
                return []
            fh.seek(start)
            data = fh.read(min(cd_size, 8 << 20))
    except (OSError, struct.error, ValueError):
        return []
    names, pos = [], 0
    while len(names) < limit and pos + 46 <= len(data) and data[pos:pos + 4] == b"PK\x01\x02":
        flag = struct.unpack_from("<H", data, pos + 8)[0]
        n_len, x_len, c_len = struct.unpack_from("<HHH", data, pos + 28)
        raw = data[pos + 46:pos + 46 + n_len]
        extra = data[pos + 46 + n_len:pos + 46 + n_len + x_len]
        pos += 46 + n_len + x_len + c_len
        if flag & 0x800 or raw.isascii():
            continue
        if _has_unicode_path(extra):  # (a UTF-8 copy of the name rides along: 7-Zip uses that one)
            continue
        names.append(raw)
    return names


def _has_unicode_path(extra: bytes) -> bool:
    import struct
    pos = 0
    while pos + 4 <= len(extra):
        tag, n = struct.unpack_from("<HH", extra, pos)
        if tag == 0x7075:
            return True
        pos += 4 + n
    return False


def _cp_score(names: list[bytes], cp: int) -> float | None:
    """How well code page cp reads these names: letters of one script score, symbols, box lines and
    letters of a second script count against it. None: it can't read them at all."""
    import unicodedata
    enc = "utf-8" if cp == UTF8_CP else f"cp{cp}"
    score, scripts = 0.0, {}
    for raw in names:
        try:
            text = unicodedata.normalize("NFKC", raw.decode(enc))
        except (UnicodeDecodeError, LookupError):
            return None
        for ch in text:
            if ch.isascii():
                continue
            if ch.isalpha():
                try:
                    script = unicodedata.name(ch).split(" ", 1)[0]
                except ValueError:
                    script = "?"
                scripts[script] = scripts.get(script, 0) + 1
            elif unicodedata.category(ch)[0] in "MN":  # (Arabic marks and digits)
                score += 0.5
            else:
                score -= 3
    if scripts:
        main = max(scripts.values())
        score += main - 2 * (sum(scripts.values()) - main)
    return score


def detect_zip_codepage(src: Path) -> int | None:
    """The code page an old ZIP's names are in (UTF8_CP for UTF-8 without the mark), or None when every
    name already says it's UTF-8 or is plain ASCII."""
    names = _legacy_zip_names(src)
    if not names:
        return None
    if _cp_score(names, UTF8_CP) is not None:  # (rubbish almost never reads as UTF-8 by chance)
        return UTF8_CP
    pc = _pc_codepages()
    order = list(dict.fromkeys([*pc[:1], *CODEPAGES, *pc[1:]]))  # ties go to the PC's own DOS one
    best, best_score = None, None
    for cp in order:
        got = _cp_score(names, cp)
        if got is not None and (best_score is None or got > best_score):
            best, best_score = cp, got
    return best


def _key(src: Path) -> str:
    return os.path.normcase(os.path.abspath(src))


def is_zip(src: Path) -> bool:
    return src.suffix.lower() in (".zip", ".zipx") and not is_tar(src)


def zip_codepage(src: Path) -> int | None:
    """The code page used for this ZIP's names: the one picked in the archive window, else the one
    worked out (None: nothing to do)."""
    if not is_zip(src):
        return None
    chosen = picked_zip_codepage(src)
    if chosen is not None:
        return chosen or None  # (0: "as the PC reads it")
    return found_zip_codepage(src)


def found_zip_codepage(src: Path) -> int | None:
    return _cached("codepage", src, lambda: detect_zip_codepage(src))


def picked_zip_codepage(src: Path) -> int | None:
    return _chosen_cp.get(_key(src))


def set_zip_codepage(src: Path, cp: int | None):
    """Picked in the archive window. None goes back to working it out; 0 reads names as the PC does."""
    if cp is None:
        _chosen_cp.pop(_key(src), None)
    else:
        _chosen_cp[_key(src)] = cp


def has_legacy_names(src: Path) -> bool:
    return is_zip(src) and _cached("legacy-names", src, lambda: bool(_legacy_zip_names(src, 1)))


def _cp(src: Path) -> list[str]:
    """7-Zip's switch for the names' code page, if this ZIP needs one."""
    cp = zip_codepage(src)
    return [f"-mcp={cp}"] if cp else []


def _open_zip(src: Path) -> zipfile.ZipFile:
    """Python's own ZIP reader (no 7-Zip), reading the names in the right code page."""
    cp = zip_codepage(src)
    if cp:
        return zipfile.ZipFile(src, metadata_encoding="utf-8" if cp == UTF8_CP else f"cp{cp}")
    return zipfile.ZipFile(src)


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


_listed: dict = {}  # the last few lists, by (file, its size and time, password, code page): unpacking reads it again


def list_entries(src: Path, password: str = "") -> list[Entry]:
    try:
        st = src.stat()
        key = (str(src), st.st_size, st.st_mtime_ns, password, tuple(_cp(src)))
    except OSError:
        key = None
    if key is not None and key in _listed:
        return list(_listed[key])
    entries = _list_entries(src, password)
    if key is not None:
        if len(_listed) >= 4:
            _listed.pop(next(iter(_listed)))
        _listed[key] = entries
    return list(entries)


def _list_entries(src: Path, password: str = "") -> list[Entry]:
    if is_tar(src):
        try:
            with _open_tar(src) as tf:
                return [Entry(_tar_name(m.name), m.size, 0, _tar_time(m.mtime), m.isdir())
                        for m in tf if _tar_name(m.name)]
        except (tarfile.TarError, OSError, EOFError, *_zstd_errors()) as e:
            raise RuntimeError(tr("the archive is damaged or isn't a TAR file")) from e
    if is_zst(src):
        try:
            import zstandard
            with open(src, "rb") as fh:
                size = zstandard.frame_content_size(fh.read(18))
        except (OSError, *_zstd_errors()) as e:
            raise RuntimeError(tr("the archive is damaged or incomplete")) from e
        return [Entry(_zst_inner_name(src), max(0, size), src.stat().st_size, _tar_time(src.stat().st_mtime))]
    if find_7z():
        code, out = _run7z(["l", "-slt", "-sccUTF-8", *_cp(src), _pw(password), str(src)])
        if code not in (0, 1):
            raise _7z_error(out)
        entries = _parse_slt(out)
        # 7-Zip also prints the archive itself as the first block when there's no "----------" marker
        return [e for e in entries if e.path != str(src).replace("\\", "/")]
    if src.suffix.lower() == ".zip":
        try:
            with _open_zip(src) as zf:
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
        if is_tar(src) or is_zst(src):
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
            code, _ = _run7z(["t", str(src), "-sccUTF-8", "-scsUTF-8", *_cp(src), _pw(password), f"@{listfile}"])
        finally:
            listfile.unlink(missing_ok=True)
        return code == 0
    try:
        with _open_zip(src) as zf:
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
    if is_tar(files[0]) or is_zst(files[0]):
        return None
    if not find_7z() and files[0].suffix.lower() != ".zip":
        return tr("Needs 7-Zip (reinstall Corefold)")
    return None


# ----------------------------------------------------------------------------- extracting

class _SpaceGuard:
    """Watches the disk while 7-Zip unpacks: stops it before the disk's last RESCUE_RESERVE is used, or
    once it has written more than `budget` (a damaged archive being rescued). 7-Zip itself only stops when
    the disk is completely full, and Windows and other programs then fail too."""

    EVERY = 0.25  # seconds between looks

    def __init__(self, ctx: Ctx, folder: Path, budget: int | None = None, rescuing: bool = True):
        import threading
        self.outer, self.folder, self.budget, self.rescuing = ctx, folder, budget, rescuing
        self.inner = Ctx(ctx.options, ctx.progress, threading.Event(), ctx.params, ctx.index, ctx.total)
        self.tripped = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True, name="corefold-space-guard")

    def _free(self) -> int | None:
        try:
            return shutil.disk_usage(self.folder).free
        except OSError:
            return None

    def _watch(self):
        start = self._free()
        while not self._stop.wait(self.EVERY):
            if self.outer.cancelled:
                self.inner.cancel_event.set()
                return
            free = self._free()
            if free is None or start is None:
                continue
            if self.budget is not None and start - free > self.budget:
                self.tripped = "budget"
            elif free < RESCUE_RESERVE and start >= RESCUE_RESERVE:
                self.tripped = "full"
            if self.tripped:
                self.inner.cancel_event.set()
                return

    def __enter__(self) -> Ctx:
        self._thread.start()
        return self.inner

    def __exit__(self, kind, exc, tb):
        self._stop.set()
        self._thread.join(2)
        if kind is not None and issubclass(kind, Cancelled) and self.tripped:
            if self.tripped == "budget" and self.rescuing:
                raise RuntimeError(tr("this archive unpacks to far more than its size or the free space allows, "
                                      "so rescuing it was stopped")) from None
            if self.tripped == "budget":
                raise RuntimeError(tr("this archive unpacked to far more than it says it holds (an archive bomb), "
                                      "so unpacking was stopped")) from None
            raise RuntimeError(tr("the disk is nearly full, so unpacking was stopped before it filled it")) from None
        return False


# An "archive bomb" is a small archive that unpacks to far more than the disk holds (a few KB to many GB or
# more, sometimes archives inside archives). Before unpacking, what the archive says it holds is checked against
# the free space (keeping the disk's last RESCUE_RESERVE free); while unpacking, the disk is watched, so an
# archive that holds more than it says (or one whose size isn't known in advance, like a .tar.gz) is stopped
# too. What was unpacked so far is deleted (extract() unpacks into a hidden folder first).
BOMB_RATIO = 1000            # unpacking to over a thousand times its size, more than fits: called a bomb
_LIE_SLACK = 256 * 1024 ** 2  # what other programs may write meanwhile, before "more than it says" counts
_CLUSTER, _ENTRY = 4096, 1024


def _declared_size(src: Path, password: str, members: list[str] | None) -> int | None:
    """About what the archive's files take on the disk, unpacked, by what it says they are (None: not known
    without unpacking it all)."""
    if is_tar(src):  # (a .tar.gz or .tar.zst can only be listed by unpacking it)
        return None
    try:
        entries = list_entries(src, password)
    except Exception:  # (a locked list of names, a damaged archive: unpacking says what's wrong)
        return None
    if is_zst(src) and entries and entries[0].size <= 0:
        return None
    if members:
        wanted = [m.strip("/") for m in members]
        entries = [e for e in entries if any(e.path == w or e.path.startswith(w + "/") for w in wanted)]
    # on the disk: each file takes whole clusters (4 KB), and every file and folder an entry of its own in
    # the disk's table (1 KB): 200,000 small files of 100 bytes need some 1 GB, not 20 MB
    return sum((0 if e.is_dir else -(-e.size // _CLUSTER) * _CLUSTER) + _ENTRY for e in entries)


def check_room(src: Path, target: Path, declared: int | None):
    """Refuses to start unpacking what can't fit on the disk (leaving its last RESCUE_RESERVE free)."""
    if declared is None:
        return
    try:
        free = shutil.disk_usage(target).free
    except OSError:
        return
    if declared + RESCUE_RESERVE <= free:
        return
    try:
        packed = max(1, src.stat().st_size)
    except OSError:
        packed = 1
    if declared > BOMB_RATIO * packed:
        raise RuntimeError(tr("this archive says it unpacks to {size} from just {packed}: that's an archive bomb, "
                              "so it wasn't unpacked", size=human_size(declared), packed=human_size(packed)))
    raise RuntimeError(tr("unpacking this needs {size}, and the disk only has {free} free",
                          size=human_size(declared), free=human_size(max(0, free - RESCUE_RESERVE))))


def _extract_into(src: Path, target: Path, ctx: Ctx, password: str, members: list[str] | None = None):
    declared = _declared_size(src, password, members)
    check_room(src, target, declared)
    budget = None if declared is None else declared + max(_LIE_SLACK, declared // 10)
    with _SpaceGuard(ctx, target, budget, rescuing=False) as guarded:
        _unpack(src, target, guarded, password, members)


def _unpack(src: Path, target: Path, ctx: Ctx, password: str, members: list[str] | None = None):
    if is_tar(src):
        wanted = set(members or [])
        opened = _open_tar(src, ctx.check)
        with opened as tf:
            for m in tf:  # (start to end, the only way a .tar.zst can be read)
                ctx.check()
                if not wanted or _tar_name(m.name) in wanted:
                    tf.extract(m, target, filter=_tar_filter)
                ctx.progress(opened.progress())
        pass_on_zone_mark([src], [target])
        return
    if is_zst(src):
        with open(target / _zst_inner_name(src), "wb") as out:
            _unzst(src, out, ctx)
        pass_on_zone_mark([src], [target])
        return
    if find_7z():
        # -snz: files from a downloaded archive keep its "from the internet" mark (Windows then still
        # warns before running them); -spd: names are names, not wildcards; -aou: two files in it with the same
        # name (or names only the case tells apart, one name to Windows) are both kept, the second as "name_1"
        args = ["x", str(src), f"-o{target}", "-y", "-aou", "-bsp1", "-bse1", "-bb0", "-sccUTF-8", "-snz", "-spd",
                *_cp(src), _pw(password)]
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
    with _open_zip(src) as zf:
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
    staging = new_folder(base, ".corefold-unpacking-")
    try:
        _hide(staging)
        _extract_into(src, staging, ctx, password, members)
        dropped = _drop_links(staging)
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
        if dropped:
            note = (note + " · " if note else "") + tr("Left out {count} links that pointed elsewhere", count=dropped)
        return Result([result], note=note)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _drop_links(folder: Path) -> int:
    """Links that came out of an archive (a symbolic link or junction can point anywhere on the PC) are
    taken out before anything is moved into place: only the link goes, never what it points at."""
    from .core import is_link
    dropped = 0
    for root, dirs, files in os.walk(folder):  # (os.walk doesn't go into links)
        for name in dirs + files:
            path = Path(root) / name
            if is_link(path):
                try:
                    try:
                        os.unlink(path)
                    except OSError:
                        os.rmdir(path)  # (a junction: rmdir takes the link away, not the folder it points at)
                    dropped += 1
                except OSError:
                    log.warning("couldn't take a link out of what was unpacked: %s", path)
                    raise RuntimeError(tr("the archive holds a link that couldn't be taken out")) from None
        dirs[:] = [d for d in dirs if not is_link(Path(root) / d)]
    return dropped


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


_PART_NUMBER = re.compile(r"\.\d{3}$")
_RAR_PART = re.compile(r"^(.*)\.part\d+\.rar$", re.IGNORECASE)


def archive_parts(src: Path) -> list[Path]:
    """The archive and the other files it's split into: name.7z.001, .002 …; name.part1.rar, .part2.rar …;
    name.zip with name.z01 …; name.rar with name.r00 …. Never other files that just start the same."""
    folder, name = src.parent, src.name
    found = {src}
    if _PART_NUMBER.search(name):
        found |= set(folder.glob(glob_escape(name[:-4]) + ".[0-9][0-9][0-9]"))
    elif m := _RAR_PART.match(name):
        found |= {p for p in folder.glob(glob_escape(m.group(1)) + ".part*.rar")
                  if (n := _RAR_PART.match(p.name)) and n.group(1).lower() == m.group(1).lower()}
    elif src.suffix.lower() in (".zip", ".rar"):
        letter = src.suffix.lower()[1]
        found |= set(folder.glob(glob_escape(src.stem) + f".{letter}[0-9][0-9]"))
    return sorted(p for p in found if p.is_file())


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
    if is_tar(src) or is_zst(src):
        try:
            if is_zst(src):
                _unzst(src, _Nowhere(), ctx)
            else:
                opened = _open_tar(src)
                with opened as tf:
                    for m in tf:
                        ctx.check()
                        if m.isfile():
                            f = tf.extractfile(m)
                            while f and f.read(1 << 20):
                                pass
                        ctx.progress(opened.progress())
        except (tarfile.TarError, EOFError, *_zstd_errors()) as e:
            raise RuntimeError(tr("the archive is damaged or incomplete")) from e
        return Result(note=tr("No problems found in {name}", name=src.name))
    if find_7z():
        code, out = _run7z(["t", str(src), "-bsp1", "-bse1", "-sccUTF-8", *_cp(src), _pw(password)], ctx)
        if code not in (0, 1):
            raise _7z_error(out)
        return Result(note=tr("No problems found in {name}", name=src.name))
    with _open_zip(src) as zf:
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
# TAR files (for Linux and Mac), made by Corefold itself: their packing level for fast, normal and max
TAR_FORMATS = {"tar.gz": (1, 6, 9), "tar.xz": (1, 6, 9), "tar.zst": (1, 6, 19)}
# files that are packed already: squeezing them again takes time and saves next to nothing
PACKED_ALREADY = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".avif", ".gif", ".mp4", ".mov", ".mkv",
                  ".webm", ".m4v", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".zip", ".7z", ".rar", ".gz",
                  ".xz", ".zst", ".bz2", ".docx", ".xlsx", ".pptx", ".epub"}


def packed_already(items: list[Path]) -> bool:
    """Only files that are packed already (photos, videos, music, archives): they're stored as they are."""
    return bool(items) and all(p.is_file() and p.suffix.lower() in PACKED_ALREADY for p in items)


def _pack_tar(items: list[Path], rel: list[str], out: Path, ctx: Ctx, fmt: str, level: str):
    fast, normal, best = TAR_FORMATS[fmt]
    n = {"fast": fast, "max": best}.get(level, normal)
    files: list[tuple[Path, str]] = []
    for p, r in zip(items, rel):
        files.append((p, r))
        if p.is_dir() and not is_link(p):
            for f in sorted(p.rglob("*")):
                files.append((f, os.path.join(r, os.path.relpath(f, p))))
    total = max(1, sum(f.stat().st_size for f, _ in files if f.is_file() and not f.is_symlink()))
    done = 0
    raw = zst = None
    try:
        if fmt == "tar.zst":
            import zstandard
            raw = open(out, "wb")
            zst = zstandard.ZstdCompressor(level=n, threads=-1).stream_writer(raw)
            tf = tarfile.open(fileobj=zst, mode="w|", format=tarfile.PAX_FORMAT)
        elif fmt == "tar.xz":
            tf = tarfile.open(out, "w:xz", preset=n, format=tarfile.PAX_FORMAT)
        else:
            tf = tarfile.open(out, "w:gz", compresslevel=n, format=tarfile.PAX_FORMAT)
        with tf:
            for f, arc in files:
                ctx.check()
                tf.add(f, arc.replace(os.sep, "/"), recursive=False)
                if f.is_file():
                    done += f.stat().st_size
                    ctx.progress(done / total)
        if zst is not None:
            zst.close()  # (closes the file too)
    except BaseException:
        if raw is not None:
            raw.close()
        out.unlink(missing_ok=True)
        raise


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
    """Creates out (a .zip, .7z, .tar.gz, .tar.xz or .tar.zst) from the items. Returns the file(s) written."""
    try:
        parent = Path(os.path.commonpath([str(p.parent.resolve()) for p in items]))
    except ValueError as e:  # different drives
        raise RuntimeError(tr("pick files that are on the same drive")) from e
    # (the item's own name in its folder: resolving the item itself would follow a shortcut folder (junction)
    # to wherever it points, even another drive)
    rel = [os.path.relpath(p.parent.resolve() / p.name, parent) for p in items]
    if fmt in TAR_FORMATS:  # (no password or parts: TAR has neither)
        _pack_tar(items, rel, out, ctx, fmt, level)
        return [out]
    # (as Bandizip does: photos and videos in a ZIP are just stored, many times faster and hardly bigger;
    # "max" still squeezes everything)
    mx = 0 if level == "normal" and packed_already(items) else LEVELS.get(level, 5)
    if find_7z():
        args = ["a", f"-t{fmt}", f"-mx={mx}", "-bsp1", "-bse1", "-bb0", "-sccUTF-8", "-ssw", "-y",
                "-spd"]
        if fmt == "zip":  # (names always marked UTF-8: they read right on every PC and in every program)
            args.append("-mcu=on")
        if password:
            args.append(_pw(password, making=True))
            args.append("-mhe=on" if fmt == "7z" else "-mem=AES256")
        if split_mb and split_mb > 0:
            args.append(f"-v{int(split_mb * 1024)}k")
        cwd = parent
        if _too_deep(parent) and not any(os.sep in r or "/" in r for r in rel):
            # (Windows can't start 7-Zip inside it; given whole paths, 7-Zip still keeps only the names)
            rel, cwd = [str(parent / r) for r in rel], None
        listfile = _listfile(rel)
        # numbered parts already there (an older split archive of the same name) are never removed or listed
        before = set(_parts_of(out))

        def remove_made():
            # remove only what this run made: the archive and its numbered parts (.001, .002 …),
            # never other files that just start with the same name
            remove_file(out)
            for part in _parts_of(out):
                if part not in before:
                    remove_file(part)
        try:
            code, output = _run7z([*args, "-scsUTF-8", str(out), f"@{listfile}"], ctx, cwd=cwd)
        except Cancelled:  # stopped halfway: no half-made archive is left behind
            remove_made()
            raise
        finally:
            listfile.unlink(missing_ok=True)
        if code == 1:  # (only a warning to 7-Zip: a file it couldn't read was left out of the archive)
            remove_made()
            low = output.lower()
            if "cannot find" in low or "can not find" in low:
                raise FileNotFoundError(tr("file not found"))
            raise PermissionError(tr("permission denied (is the file open in another app?)"))
        if code != 0:
            remove_made()
            raise _7z_error(output)
        if split_mb:
            return sorted(p for p in _parts_of(out) if p not in before)
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
        how = zipfile.ZIP_STORED if mx == 0 else zipfile.ZIP_DEFLATED
        with zipfile.ZipFile(out, "w", how, compresslevel=None if mx == 0 else mx) as zf:
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


def _parts_of(out: Path) -> list[Path]:
    """The numbered parts of a split archive: name.zip.001, name.zip.002 …"""
    return list(out.parent.glob(glob_escape(out.name) + ".[0-9][0-9][0-9]"))


def pack(items: list[Path], ctx: Ctx, fmt: str | None = None) -> Result:
    p = ctx.params
    fmt = fmt or p.get("format", "zip")
    name = _pack_name(items, ctx)
    out = ctx.out_path(items[0], f"{name}.{fmt}")
    n = 2
    while _parts_of(out):  # (the parts of an older split archive with this name: a new name, never mixed in)
        out = ctx.out_path(items[0], f"{name} ({n}).{fmt}")
        n += 1
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
        deep = _too_deep(parent)  # (as in pack_into)
        listfile = _listfile([str(parent / p.name) if deep else p.name for p in group])
        try:
            args = ["a", str(src.resolve()), "-bsp1", "-bse1", "-bb0", "-sccUTF-8", "-scsUTF-8", "-ssw", "-y", "-spd",
                    *_cp(src)]
            if password:
                args.append(_pw(password, making=True))
            code, out = _run7z([*args, f"@{listfile}"], ctx, cwd=None if deep else parent)
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
        code, out = _run7z(["d", str(src), "-bsp1", "-bse1", "-sccUTF-8", "-scsUTF-8", "-y", "-spd", *_cp(src),
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


PREVIEW_MAX = 40 << 20  # files bigger than this (unpacked) get no preview


def read_member(src: Path, member: str, password: str = "", limit: int = PREVIEW_MAX, cancel=None) -> bytes:
    """The start (up to limit bytes) of one file inside the archive, without writing it anywhere.
    cancel (a threading.Event) stops it early: another file was picked meanwhile."""
    from .core import Cancelled
    if is_tar(src):
        with _open_tar(src) as tf:
            for m in tf:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                if m.isfile() and _tar_name(m.name) == member:
                    f = tf.extractfile(m)
                    return f.read(limit) if f else b""
        raise RuntimeError(tr("couldn't take that file out of the archive"))
    if is_zst(src):
        import zstandard
        with open(src, "rb") as fh, zstandard.ZstdDecompressor().stream_reader(fh) as reader:
            return reader.read(limit)
    exe = find_7z()
    if exe:
        listfile = _listfile([member.replace("/", os.sep)])
        try:
            cmd, typed = _with_password([exe, "e", "-so", "-sccUTF-8", "-scsUTF-8", "-spd", *_cp(src), _pw(password), str(src),
                                         f"@{listfile}"])
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    stdin=subprocess.PIPE if typed else subprocess.DEVNULL, creationflags=NO_WINDOW)
            if typed:
                try:
                    proc.stdin.write(typed)
                    proc.stdin.close()
                except OSError:
                    pass
            if cancel is not None:  # (a big solid archive can take a while to reach the file)
                def watch():
                    while proc.poll() is None:
                        if cancel.wait(0.2):
                            proc.kill()
                            return
                import threading
                threading.Thread(target=watch, daemon=True).start()
            try:
                data = proc.stdout.read(limit)
            finally:
                proc.kill()  # (enough read: the rest isn't needed)
                _out, err = proc.communicate(timeout=10)
        finally:
            listfile.unlink(missing_ok=True)
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if not data and proc.returncode not in (0, 1):  # (nothing read: 7-Zip had finished by itself)
            raise _7z_error(err.decode("utf-8", "replace"))
        return data
    with _open_zip(src) as zf:
        try:
            with zf.open(member, pwd=password.encode() if password else None) as f:
                return f.read(limit)
        except RuntimeError as e:
            raise NeedPassword() from e


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
        args = ["u", str(src.resolve()), UPDATE_ALL, "-bsp1", "-bse1", "-bb0", "-sccUTF-8", "-scsUTF-8", "-y", "-spd",
                *_cp(src)]
        password = ctx.params.get("password", "")
        if password:
            args.append(_pw(password, making=True))
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


# A damaged ZIP is read piece by piece with no table of contents to trust, so what it unpacks to is capped:
# never more than RESCUE_RATIO times its own size (at least RESCUE_MIN), and never into the last
# RESCUE_RESERVE of the disk. A "ZIP bomb" (a few KB that unpack to many GB) then stops early instead of
# filling the disk or the memory. Each piece is unpacked a few MB at a time, never all at once.
RESCUE_RATIO = 200
RESCUE_MIN = 2 * 1024 ** 3
RESCUE_RESERVE = 1024 ** 3
_RESCUE_STEP = 4 * 1024 * 1024


def _rescue_budget(src_size: int, target: Path) -> int:
    try:
        free = shutil.disk_usage(target).free - RESCUE_RESERVE
    except OSError:
        free = RESCUE_MIN
    return max(0, min(free, max(RESCUE_MIN, RESCUE_RATIO * src_size)))


class _TooBig(Exception):
    pass


def _scan_zip(src: Path, target: Path, ctx: Ctx, budget: int | None = None) -> tuple[int, int, bool]:
    """Reads a ZIP file by its pieces (each file's own header), for ZIPs whose table of contents at the
    end is missing or broken. Writes every file that checks out; returns (good, damaged, stopped: it would
    have unpacked to more than the budget)."""
    import mmap
    import struct
    import zlib
    good = bad = 0
    root = target.resolve()
    with open(src, "rb") as fh:
        try:
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:  # empty file
            return 0, 0, False
        with mm:
            size = len(mm)
            left = _rescue_budget(size, target) if budget is None else budget  # (for every piece, kept or not)
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
                if flag & 1 or method not in (0, 8) or 0xFFFFFFFF in (csize, usize):  # locked, or a kind not read here
                    bad += 1
                    continue
                known = not (flag & 8)  # otherwise the sizes come after the data
                if known and usize > left:
                    return good, bad + 1, True
                cap = usize if known else left  # (more than it says it holds: damaged, or made to harm)
                check = 0
                ok = True
                made = 0
                tmp = out.with_name(out.name + ".part")
                try:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with open(tmp, "wb") as w:
                        if method == 0:
                            if not known or start + csize > size:
                                ok = False
                            else:
                                for p in range(start, start + csize, _RESCUE_STEP):
                                    data = mm[p:min(start + csize, p + _RESCUE_STEP)]
                                    w.write(data)
                                    check = zlib.crc32(data, check)
                                    ctx.check()
                                made = csize
                                left -= csize
                                end = start + csize
                        else:
                            d = zlib.decompressobj(-15)
                            p = start
                            limit = start + csize if known else size
                            pending = b""
                            try:
                                while not d.eof:
                                    if not pending:
                                        if p >= limit:
                                            break
                                        pending = mm[p:min(limit, p + (1 << 20))]
                                        p += len(pending)
                                    piece = d.decompress(pending, _RESCUE_STEP)
                                    pending = d.unconsumed_tail
                                    made += len(piece)
                                    left -= len(piece)
                                    if left < 0:
                                        raise _TooBig()
                                    if made > cap:
                                        ok = False
                                        break
                                    check = zlib.crc32(piece, check)
                                    w.write(piece)
                                    ctx.check()
                                ok = ok and d.eof
                            except zlib.error:  # damaged data
                                ok = False
                            end = p - len(pending) - len(d.unused_data)
                            if not known and ok and end + 16 <= size:  # the data descriptor holds the check
                                tail = mm[end:end + 16]
                                crc = struct.unpack("<I", tail[4:8] if tail[:4] == b"PK\x07\x08" else tail[:4])[0]
                    if ok and check == crc and (not known or made == usize):
                        os.replace(tmp, out)
                        good += 1
                        pos = max(pos, end)
                    else:
                        bad += 1
                except _TooBig:
                    return good, bad + 1, True
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
    return good, bad, False


def repair_archive(src: Path, ctx: Ctx) -> Result:
    """Takes out everything that can still be read from a damaged archive and puts it in a new,
    healthy ZIP next to it ("name (repaired).zip"). The damaged archive is left as it was."""
    password = ctx.params.get("password", "")
    base = ctx.folder_for(src)  # next to it, or the folder chosen for results (a CD or a locked folder: elsewhere)
    base.mkdir(parents=True, exist_ok=True)
    _tidy_old_staging(base)
    stage = new_folder(base, ".corefold-unpacking-")
    try:
        _hide(stage)
        damaged = 0
        if find_7z():
            with _SpaceGuard(ctx, stage, _rescue_budget(src.stat().st_size, stage)) as guarded:
                code, out = _run7z(["x", str(src), f"-o{stage}", "-y", "-aou", "-bsp1", "-bse1", "-bb0", "-sccUTF-8",
                                    "-snz", "-spd", *_cp(src), _pw(password)], guarded)
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
        stopped = False
        if not rescued and src.suffix.lower() in (".zip", ".zipx"):  # read it piece by piece instead
            shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir()
            _hide(stage)
            _good, damaged, stopped = _scan_zip(src, stage, ctx)
            rescued = [p for p in stage.rglob("*") if p.is_file()]
        if not rescued:
            if stopped:
                raise RuntimeError(tr("this archive unpacks to far more than its size or the free space allows, "
                                      "so rescuing it was stopped"))
            raise RuntimeError(tr("nothing in this archive could be rescued"))
        out_zip = unique_path(base / f"{safe_name(archive_stem(src)) or 'Archive'} (repaired).zip")
        outs = pack_into(sorted(stage.iterdir()), out_zip, ctx, "zip", password=password)
        pass_on_zone_mark([src], outs)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    note = tr("Rescued {count} files", count=len(rescued))
    if stopped:
        note += " · " + tr("Stopped early: the rest would unpack to far more than the archive's size")
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
