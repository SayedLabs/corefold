"""Rakiza Core Network: which programs are using the network right now, how fast it is, and a speed test.

Everything except the speed test is read from this PC, with the same permissions as any program
(no administrator, no driver, no service running in the background):
- the connections and the programs they belong to: Windows' own table (what `netstat -ano` shows);
- the names of the places they talk to: Windows' own list of names it looked up lately (the DNS cache),
  read without asking any server (nothing leaves the PC);
- how fast the network is going: the network adapters' own byte counters;
- how much each program used: what Windows counts itself (Settings → Network → Data usage), when Windows
  lets a program read it.

The speed test is the only part that goes online, and only when the person presses its button: it downloads
and uploads throw-away data with Cloudflare's speed test (speed.cloudflare.com, the same server their own
website uses), over a checked HTTPS connection, for a few seconds and never more than a set amount of data.
Nothing about the PC or the person is sent, and the results stay on this PC.

Nothing here imports Qt. Reading is quick but not instant, so the page calls it off the window's thread."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import ipaddress
import logging
import ntpath
import os
import re
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("corefold")

# ----------------------------------------------------------------------------- what a connection is

AF_INET, AF_INET6 = 2, 23
TCP_TABLE_OWNER_PID_ALL = 5
UDP_TABLE_OWNER_PID = 1
NO_ERROR, ERROR_INSUFFICIENT_BUFFER = 0, 122
MAX_ROWS = 20000          # a table bigger than this is cut (a PC never has that many; a broken answer might)

# MIB_TCP_STATE
TCP_STATES = {1: "closed", 2: "listening", 3: "syn_sent", 4: "syn_received", 5: "established", 6: "fin_wait1",
              7: "fin_wait2", 8: "close_wait", 9: "closing", 10: "last_ack", 11: "time_wait", 12: "delete_tcb"}
LISTENING, ESTABLISHED = "listening", "established"
# states of a connection that's still talking (the rest are opening or closing)
LIVE_STATES = {ESTABLISHED, "syn_sent", "syn_received", "close_wait"}

# where the other end is
THIS_PC, HOME, INTERNET = "this_pc", "home", "internet"


@dataclass
class Connection:
    proto: str                 # "tcp" or "udp"
    local: str                 # local address
    local_port: int
    remote: str                # "" for UDP and for listening
    remote_port: int
    state: str                 # TCP_STATES value, "listening" for UDP
    pid: int
    where: str = ""            # THIS_PC, HOME or INTERNET (the other end; for listening, who can reach it)
    host: str = ""             # the name the remote address was looked up by, if Windows remembers it


def _port(raw: int) -> int:
    """A port as Windows keeps it (network byte order in the low 16 bits) as a number."""
    return socket.ntohs(raw & 0xFFFF)


def where_is(address: str) -> str:
    """THIS_PC (loopback), HOME (this network: private, link-local, ...) or INTERNET."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return INTERNET
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return THIS_PC
    if ip.is_unspecified:
        return HOME   # (0.0.0.0 / :: only shows up for listening: everyone on the network can try it)
    if ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT:  # (a provider's own network: the internet)
            return INTERNET
        return HOME
    return INTERNET


_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def reachable_by(conn: Connection) -> str:
    """For a program listening: who can reach it. THIS_PC (only this PC), HOME (other devices on this network,
    unless the firewall says no)."""
    return THIS_PC if where_is(conn.local) == THIS_PC else HOME


_LIBS: dict[str, object] = {}
_FUNCS: dict[tuple[str, str], object] = {}
_FUNCS_LOCK = threading.Lock()


def _fn(dll: str, name: str, restype, *argtypes):
    """A Windows function with its types, set once. Rakiza Core Network loads its own copy of each library, so the
    types set here never change the same function for the rest of Rakiza Core (another tool may call it on another
    thread at the same moment), and nothing changes them while a call is running."""
    key = (dll, name)
    with _FUNCS_LOCK:
        fn = _FUNCS.get(key)
        if fn is None:
            lib = _LIBS.get(dll)
            if lib is None:
                lib = _LIBS[dll] = ctypes.WinDLL(dll)
            fn = getattr(lib, name)
            fn.restype = restype
            fn.argtypes = list(argtypes)
            _FUNCS[key] = fn
    return fn


def _table(fn_name: str, family: int, kind: int) -> bytes:
    """One of Windows' connection tables, as it answered (empty on any failure)."""
    fn = _fn("iphlpapi", fn_name, wt.DWORD, ctypes.c_void_p, ctypes.POINTER(wt.DWORD), wt.BOOL, wt.ULONG,
             ctypes.c_int, wt.ULONG)
    size = wt.DWORD(0)
    for _ in range(5):  # (it can grow between asking for the size and reading it)
        buf = ctypes.create_string_buffer(max(size.value, 4))
        size = wt.DWORD(len(buf))
        r = fn(buf, ctypes.byref(size), False, family, kind, 0)
        if r == NO_ERROR:
            return buf.raw[:size.value] if size.value <= len(buf) else buf.raw
        if r != ERROR_INSUFFICIENT_BUFFER:
            log.debug("%s(%s) answered %s", fn_name, family, r)
            return b""
        size = wt.DWORD(size.value + 16384)
    return b""


def parse_tcp4(data: bytes) -> list[Connection]:
    """MIB_TCPTABLE_OWNER_PID: a count, then rows of state, local address/port, remote address/port, pid."""
    out = []
    if len(data) < 4:
        return out
    n = min(struct.unpack_from("<I", data, 0)[0], MAX_ROWS, (len(data) - 4) // 24)
    for i in range(n):
        st, la, lp, ra, rp, pid = struct.unpack_from("<IIIIII", data, 4 + i * 24)
        state = TCP_STATES.get(st, "")
        local = socket.inet_ntoa(struct.pack("<I", la))
        remote = socket.inet_ntoa(struct.pack("<I", ra)) if state != LISTENING else ""
        out.append(Connection("tcp", local, _port(lp), remote, _port(rp) if remote else 0, state, pid))
    return out


def parse_tcp6(data: bytes) -> list[Connection]:
    """MIB_TCP6TABLE_OWNER_PID: local address(16) scope(4) port(4) remote address(16) scope(4) port(4)
    state(4) pid(4) = 56 bytes a row."""
    out = []
    if len(data) < 4:
        return out
    n = min(struct.unpack_from("<I", data, 0)[0], MAX_ROWS, (len(data) - 4) // 56)
    for i in range(n):
        base = 4 + i * 56
        la = data[base:base + 16]
        lp, = struct.unpack_from("<I", data, base + 20)
        ra = data[base + 24:base + 40]
        rp, st, pid = struct.unpack_from("<III", data, base + 44)
        state = TCP_STATES.get(st, "")
        local = socket.inet_ntop(socket.AF_INET6, la)
        remote = socket.inet_ntop(socket.AF_INET6, ra) if state != LISTENING else ""
        out.append(Connection("tcp", _plain(local), _port(lp), _plain(remote), _port(rp) if remote else 0, state,
                              pid))
    return out


def parse_udp4(data: bytes) -> list[Connection]:
    """MIB_UDPTABLE_OWNER_PID: local address, local port, pid (12 bytes a row). UDP has no other end: these
    are programs that can be reached on that port."""
    out = []
    if len(data) < 4:
        return out
    n = min(struct.unpack_from("<I", data, 0)[0], MAX_ROWS, (len(data) - 4) // 12)
    for i in range(n):
        la, lp, pid = struct.unpack_from("<III", data, 4 + i * 12)
        out.append(Connection("udp", socket.inet_ntoa(struct.pack("<I", la)), _port(lp), "", 0, LISTENING, pid))
    return out


def parse_udp6(data: bytes) -> list[Connection]:
    """MIB_UDP6TABLE_OWNER_PID: local address(16) scope(4) port(4) pid(4) = 28 bytes a row."""
    out = []
    if len(data) < 4:
        return out
    n = min(struct.unpack_from("<I", data, 0)[0], MAX_ROWS, (len(data) - 4) // 28)
    for i in range(n):
        base = 4 + i * 28
        lp, pid = struct.unpack_from("<II", data, base + 20)
        local = socket.inet_ntop(socket.AF_INET6, data[base:base + 16])
        out.append(Connection("udp", _plain(local), _port(lp), "", 0, LISTENING, pid))
    return out


def _plain(address: str) -> str:
    """An IPv4 address written the IPv6 way (::ffff:1.2.3.4) as the IPv4 one."""
    if address.startswith("::ffff:") and address.count(".") == 3:
        return address[7:]
    return address


def connections() -> list[Connection]:
    """Every TCP connection and every program waiting on a TCP or UDP port, IPv4 and IPv6, with the program's
    process id. Any user can read these (it's what `netstat -ano` shows)."""
    if sys.platform != "win32":
        return []
    out: list[Connection] = []
    for fn, fam, kind, parse in (("GetExtendedTcpTable", AF_INET, TCP_TABLE_OWNER_PID_ALL, parse_tcp4),
                                 ("GetExtendedTcpTable", AF_INET6, TCP_TABLE_OWNER_PID_ALL, parse_tcp6),
                                 ("GetExtendedUdpTable", AF_INET, UDP_TABLE_OWNER_PID, parse_udp4),
                                 ("GetExtendedUdpTable", AF_INET6, UDP_TABLE_OWNER_PID, parse_udp6)):
        try:
            out.extend(parse(_table(fn, fam, kind)))
        except Exception:
            log.debug("couldn't read %s(%s)", fn, fam, exc_info=True)
    for c in out:
        c.where = reachable_by(c) if c.state == LISTENING else where_is(c.remote)
    return out


# ----------------------------------------------------------------------------- the names of the places

DNS_TYPE_A, DNS_TYPE_CNAME, DNS_TYPE_AAAA = 1, 5, 28
DNS_QUERY_NO_WIRE_QUERY = 0x10   # only Windows' own list: never asks a server
DNS_FREE_FLAT, DNS_FREE_RECORD_LIST = 0, 1
MAX_NAMES = 4000                  # (a normal cache has a few hundred)
_HOST_OK = re.compile(r"^[A-Za-z0-9._-]{1,253}$")


class _CacheEntry(ctypes.Structure):
    pass


# (DNS_CACHE_ENTRY, as ReactOS and System Informer describe it: only the first four are ever read)
_CacheEntry._fields_ = [("next", ctypes.POINTER(_CacheEntry)), ("name", ctypes.c_void_p), ("type1", ctypes.c_ushort),
                        ("type2", ctypes.c_ushort), ("flags", ctypes.c_ushort)]


class _DnsRecord(ctypes.Structure):
    _fields_ = [("next", ctypes.c_void_p), ("name", ctypes.c_wchar_p), ("type", ctypes.c_ushort),
                ("data_len", ctypes.c_ushort), ("flags", ctypes.c_uint32), ("ttl", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32), ("data", ctypes.c_ubyte * 16)]


def clean_host(name: str) -> str:
    """A host name as it's shown: plain letters, digits, dots and dashes only (anything else isn't shown)."""
    name = (name or "").strip().rstrip(".").lower()
    return name if _HOST_OK.match(name) and ".." not in name else ""


_names_lock = threading.Lock()


def cached_names() -> dict[str, str]:
    """{address: the name it was looked up by} from Windows' own DNS cache. Nothing is asked of any server:
    names Windows doesn't remember stay unknown (the address is shown instead)."""
    if sys.platform != "win32":
        return {}
    with _names_lock:  # (two at once, from two looks, corrupted the heap on freeing the list and the app died)
        return _cached_names()


def _give_back(head, free) -> None:
    """Gives Windows back the list it made. Which parts are blocks of their own (each entry? each name? the whole
    list?) isn't written down and isn't the same on every Windows: freeing each entry and each name broke the heap
    on Windows Server 2025 and the app died. So only a pointer the heap itself says is the start of a block goes
    back; anything else stays (a few kilobytes at most, read at most every few seconds)."""
    try:
        valid = _fn("kernel32", "HeapValidate", wt.BOOL, wt.HANDLE, wt.DWORD, ctypes.c_void_p)
        heap = _fn("kernel32", "GetProcessHeap", wt.HANDLE)()
    except (AttributeError, OSError):
        return
    if not heap:
        return
    found: list[int] = []
    entry, left = head, 100_000
    while entry and left:
        left -= 1
        e = entry.contents
        found.append(ctypes.cast(entry, ctypes.c_void_p).value)
        if e.name:
            found.append(e.name)
        entry = e.next
    blocks = [a for a in dict.fromkeys(found) if a and valid(heap, 0, a)]   # (all asked before any is freed)
    for a in blocks:
        free(a, DNS_FREE_FLAT)


def _cached_names() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        # (DnsGetCacheDataTable isn't in the documentation, but it's been there since Windows 2000)
        get_table = _fn("dnsapi", "DnsGetCacheDataTable", wt.BOOL, ctypes.POINTER(ctypes.POINTER(_CacheEntry)))
        query = _fn("dnsapi", "DnsQuery_W", ctypes.c_long, wt.LPCWSTR, ctypes.c_ushort, wt.DWORD, ctypes.c_void_p,
                    ctypes.POINTER(ctypes.POINTER(_DnsRecord)), ctypes.c_void_p)
        free_list = _fn("dnsapi", "DnsRecordListFree", None, ctypes.c_void_p, ctypes.c_int)
        free = _fn("dnsapi", "DnsFree", None, ctypes.c_void_p, ctypes.c_int)
    except (AttributeError, OSError):
        return out
    head = ctypes.POINTER(_CacheEntry)()
    if not get_table(ctypes.byref(head)):
        return out
    names: list[tuple[str, int]] = []
    entry = head
    try:
        while entry and len(names) < MAX_NAMES:
            e = entry.contents
            nxt = e.next
            kinds = {t for t in (e.type1, e.type2) if t in (DNS_TYPE_A, DNS_TYPE_AAAA)}
            if DNS_TYPE_CNAME in (e.type1, e.type2):   # (another name: its addresses are in the cache too)
                kinds.add(DNS_TYPE_A)
            if e.name and kinds:
                name = ctypes.wstring_at(e.name)[:300]
                names.extend((name, kind) for kind in sorted(kinds))
            entry = nxt
    finally:
        _give_back(head, free)
    for name, kind in names:
        shown = clean_host(name)
        if not shown:
            continue
        rec = ctypes.POINTER(_DnsRecord)()
        if query(name, kind, DNS_QUERY_NO_WIRE_QUERY, None, ctypes.byref(rec), None) != 0 or not rec:
            continue
        try:
            r, left = rec, 256
            while r and left:
                left -= 1
                c = r.contents
                if c.type == DNS_TYPE_A and c.data_len >= 4:
                    out.setdefault(socket.inet_ntoa(bytes(c.data[:4])), shown)
                elif c.type == DNS_TYPE_AAAA and c.data_len >= 16:
                    out.setdefault(_plain(socket.inet_ntop(socket.AF_INET6, bytes(c.data[:16]))), shown)
                r = ctypes.cast(c.next, ctypes.POINTER(_DnsRecord)) if c.next else None
        finally:
            free_list(rec, DNS_FREE_RECORD_LIST)
    return out


# ----------------------------------------------------------------------------- the programs

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYSTEM_PID = 4


@dataclass
class Program:
    """A program and what it's connected to."""
    key: str                    # its file, lower case (or "pid:123" / "name:x.exe" when Windows doesn't say)
    name: str                   # "Google Chrome"
    path: str = ""              # its file ("" for parts of Windows that don't say)
    exe: str = ""               # "chrome.exe"
    publisher: str = ""
    services: list = field(default_factory=list)   # Windows services inside it (svchost.exe)
    pids: set = field(default_factory=set)
    conns: list = field(default_factory=list)       # Connection, talking to another computer
    listening: list = field(default_factory=list)   # Connection, waiting on a port
    system: bool = False        # a part of Windows

    @property
    def internet(self) -> list[Connection]:
        return [c for c in self.conns if c.where == INTERNET]

    @property
    def open_ports(self) -> list[Connection]:
        """Ports other devices on the network can try to connect to."""
        return [c for c in self.listening if c.where == HOME]

    def places(self) -> list[str]:
        """The places it's talking to, by name when Windows remembers it, most connections first."""
        count: dict[str, int] = {}
        local: set[str] = set()
        for c in self.conns:
            if c.where == THIS_PC:
                continue
            place = c.host or c.remote
            count[place] = count.get(place, 0) + 1
            if c.where == HOME:
                local.add(place)
        return sorted(count, key=lambda p: (p in local, -count[p], p))   # (the internet's first)


def process_names() -> dict[int, str]:
    """{pid: its file's name} for every process (any user can see these)."""
    if sys.platform != "win32":
        return {}

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32), ("th32ProcessID", ctypes.c_uint32),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_uint32),
                    ("cntThreads", ctypes.c_uint32), ("th32ParentProcessID", ctypes.c_uint32),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_uint32),
                    ("szExeFile", ctypes.c_wchar * 260)]
    snapshot = _fn("kernel32", "CreateToolhelp32Snapshot", ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32)
    first = _fn("kernel32", "Process32FirstW", wt.BOOL, ctypes.c_void_p, ctypes.c_void_p)
    following = _fn("kernel32", "Process32NextW", wt.BOOL, ctypes.c_void_p, ctypes.c_void_p)
    close = _fn("kernel32", "CloseHandle", wt.BOOL, ctypes.c_void_p)
    out: dict[int, str] = {}
    snap = snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return out
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = first(snap, ctypes.byref(e))
        while ok and len(out) < MAX_ROWS:
            out[e.th32ProcessID] = e.szExeFile
            ok = following(snap, ctypes.byref(e))
    finally:
        close(snap)
    return out


def process_path(pid: int) -> str:
    """A process's file, or "" if Windows doesn't say (parts of Windows protected from programs)."""
    if sys.platform != "win32" or pid <= SYSTEM_PID:
        return ""
    open_process = _fn("kernel32", "OpenProcess", ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    image_name = _fn("kernel32", "QueryFullProcessImageNameW", wt.BOOL, ctypes.c_void_p, ctypes.c_uint32,
                     ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32))
    close = _fn("kernel32", "CloseHandle", wt.BOOL, ctypes.c_void_p)
    h = open_process(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_uint32(1024)
        return buf.value if image_name(h, 0, buf, ctypes.byref(size)) else ""
    finally:
        close(h)


class _ServiceStatusProcess(ctypes.Structure):
    _fields_ = [("dwServiceType", wt.DWORD), ("dwCurrentState", wt.DWORD), ("dwControlsAccepted", wt.DWORD),
                ("dwWin32ExitCode", wt.DWORD), ("dwServiceSpecificExitCode", wt.DWORD), ("dwCheckPoint", wt.DWORD),
                ("dwWaitHint", wt.DWORD), ("dwProcessId", wt.DWORD), ("dwServiceFlags", wt.DWORD)]


class _EnumServiceStatusProcess(ctypes.Structure):
    _fields_ = [("lpServiceName", wt.LPWSTR), ("lpDisplayName", wt.LPWSTR), ("status", _ServiceStatusProcess)]


def services_by_pid() -> dict[int, list[str]]:
    """{pid: the display names of the Windows services running in it}: what's inside each svchost.exe.
    Reading the list of services is open to every user (nothing is changed)."""
    if sys.platform != "win32":
        return {}
    open_scm = _fn("advapi32", "OpenSCManagerW", ctypes.c_void_p, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD)
    enum = _fn("advapi32", "EnumServicesStatusExW", wt.BOOL, ctypes.c_void_p, ctypes.c_int, wt.DWORD, wt.DWORD,
               ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD),
               ctypes.POINTER(wt.DWORD), wt.LPCWSTR)
    close_scm = _fn("advapi32", "CloseServiceHandle", wt.BOOL, ctypes.c_void_p)
    SC_MANAGER_ENUMERATE_SERVICE, SC_ENUM_PROCESS_INFO = 0x4, 0
    SERVICE_WIN32, SERVICE_ACTIVE = 0x30, 0x1
    out: dict[int, list[str]] = {}
    scm = open_scm(None, None, SC_MANAGER_ENUMERATE_SERVICE)
    if not scm:
        return out
    try:
        needed, count, resume = wt.DWORD(0), wt.DWORD(0), wt.DWORD(0)
        enum(scm, SC_ENUM_PROCESS_INFO, SERVICE_WIN32, SERVICE_ACTIVE, None, 0,
             ctypes.byref(needed), ctypes.byref(count), ctypes.byref(resume), None)
        if not needed.value or needed.value > 16 << 20:
            return out
        buf = ctypes.create_string_buffer(needed.value + 4096)
        resume = wt.DWORD(0)
        if not enum(scm, SC_ENUM_PROCESS_INFO, SERVICE_WIN32, SERVICE_ACTIVE, buf, len(buf),
                    ctypes.byref(needed), ctypes.byref(count), ctypes.byref(resume), None):
            return out
        rows = ctypes.cast(buf, ctypes.POINTER(_EnumServiceStatusProcess))
        for i in range(min(count.value, 5000)):
            row = rows[i]
            pid = row.status.dwProcessId
            if pid and row.lpDisplayName:
                out.setdefault(pid, []).append(row.lpDisplayName[:80])
    finally:
        close_scm(scm)
    return out


_PROGRAM_NAMES: dict[str, tuple[str, str]] = {}


def program_name(path: str) -> tuple[str, str]:
    """(its own name, its publisher) from the file's details ("Google Chrome", "Google LLC"), kept."""
    if path not in _PROGRAM_NAMES:
        from .startup import _program_name
        try:
            _PROGRAM_NAMES[path] = _program_name(path)
        except Exception:
            _PROGRAM_NAMES[path] = ("", "")
    return _PROGRAM_NAMES[path]


def _windows_part(path: str) -> bool:
    windir = os.environ.get("SystemRoot", r"C:\Windows").lower().rstrip("\\") + "\\"
    return path.lower().startswith(windir)


def programs(conns: list[Connection] | None = None, names: dict[str, str] | None = None) -> list[Program]:
    """The programs that have connections or wait on a port, each with them (a program with several
    processes, like a browser, is one program)."""
    if conns is None:
        conns = connections()
    if names is None:
        names = cached_names()
    for c in conns:
        if c.remote and not c.host:
            c.host = names.get(c.remote, "")
    exes = process_names()
    services = services_by_pid() if any(exes.get(c.pid, "").lower() == "svchost.exe" for c in conns) else {}
    paths: dict[int, str] = {}
    found: dict[str, Program] = {}
    for c in conns:
        if c.state not in LIVE_STATES and c.state != LISTENING:
            continue   # (opening or closing: gone in a moment)
        pid = c.pid
        if pid not in paths:
            paths[pid] = process_path(pid)
        path = paths[pid]
        exe = ntpath.basename(path) if path else exes.get(pid, "")
        svc = services.get(pid, []) if exe.lower() == "svchost.exe" else []
        if pid == SYSTEM_PID:
            key = "system"
        elif svc:
            key = "svc:" + "|".join(sorted(svc)).lower()   # (each group of services is its own thing)
        elif path:
            key = path.lower()
        elif exe:
            key = "name:" + exe.lower()
        else:
            key = f"pid:{pid}"
        p = found.get(key)
        if p is None:
            if pid == SYSTEM_PID:
                name, publisher, system = "Windows", "Microsoft Corporation", True
            else:
                name, publisher = program_name(path) if path else ("", "")
                # (in Windows' folder, or a part of Windows that doesn't say where it is; a program's signature is
                # still checked and shown, so something posing as part of Windows in that folder is told apart)
                system = (not path and exe.lower() in _SYSTEM_EXES) or (bool(path) and _windows_part(path))
                if svc:
                    name = svc[0] if len(svc) == 1 else name
            p = found[key] = Program(key, name or (exe[:-4] if exe.lower().endswith(".exe") else exe) or
                                     f"#{pid}", path, exe, publisher, svc, system=system)
        p.pids.add(pid)
        (p.listening if c.state == LISTENING else p.conns).append(c)
    return sorted(found.values(), key=lambda p: (-len(p.internet), -len(p.conns), p.name.lower()))


_SYSTEM_EXES = {"system", "svchost.exe", "lsass.exe", "services.exe", "wininit.exe", "smss.exe", "csrss.exe"}


# ----------------------------------------------------------------------------- what a port is for

# well-known ports in plain words (English, shown through tr())
PORTS = {20: "File transfer (FTP)", 21: "File transfer (FTP)", 22: "Remote command line (SSH)", 23: "Telnet",
         25: "Sending email", 53: "Looking up names (DNS)", 67: "Getting an address (DHCP)",
         68: "Getting an address (DHCP)", 80: "Web (not encrypted)", 110: "Email (POP3)", 123: "Clock (time)",
         135: "Windows (RPC)", 137: "File sharing (NetBIOS)", 138: "File sharing (NetBIOS)",
         139: "File sharing (NetBIOS)", 143: "Email (IMAP)", 443: "Web (secure)", 445: "File sharing (SMB)",
         465: "Sending email (secure)", 554: "Video streams (RTSP)", 587: "Sending email", 993: "Email (secure IMAP)",
         995: "Email (secure POP3)", 1433: "Database (SQL Server)", 1900: "Finding devices (UPnP)",
         3306: "Database (MySQL)", 3389: "Remote Desktop", 3478: "Calls (STUN)", 3479: "Calls (STUN)",
         3480: "Calls (STUN)", 3481: "Calls (STUN)", 5040: "Windows (device discovery)",
         5353: "Finding devices (mDNS)", 5355: "Finding devices (LLMNR)", 5357: "Finding devices (WSD)",
         5432: "Database (PostgreSQL)", 5900: "Remote control (VNC)", 7680: "Windows Update sharing",
         8080: "Web (other port)", 8443: "Web (secure, other port)"}
# ports someone on the same network shouldn't be able to reach unless they're meant to (told apart in the list)
RISKY_PORTS = {21, 22, 23, 445, 139, 3389, 5900, 1433, 3306, 5432}


def port_name(port: int) -> str:
    return PORTS.get(port, "")


# ----------------------------------------------------------------------------- how fast it's going

IF_TYPE_ETHERNET, IF_TYPE_WIFI, IF_TYPE_LOOPBACK, IF_TYPE_TUNNEL, IF_TYPE_PPP, IF_TYPE_MOBILE = 6, 71, 24, 131, 23, 243
IF_OPER_UP = 1
MEDIA_CONNECTED = 1


class _MibIfRow2(ctypes.Structure):
    _fields_ = [("InterfaceLuid", ctypes.c_uint64), ("InterfaceIndex", ctypes.c_uint32),
                ("InterfaceGuid", ctypes.c_ubyte * 16), ("Alias", ctypes.c_wchar * 257),
                ("Description", ctypes.c_wchar * 257), ("PhysicalAddressLength", ctypes.c_uint32),
                ("PhysicalAddress", ctypes.c_ubyte * 32), ("PermanentPhysicalAddress", ctypes.c_ubyte * 32),
                ("Mtu", ctypes.c_uint32), ("Type", ctypes.c_uint32), ("TunnelType", ctypes.c_int),
                ("MediaType", ctypes.c_int), ("PhysicalMediumType", ctypes.c_int), ("AccessType", ctypes.c_int),
                ("DirectionType", ctypes.c_int), ("Flags", ctypes.c_ubyte), ("OperStatus", ctypes.c_int),
                ("AdminStatus", ctypes.c_int), ("MediaConnectState", ctypes.c_int),
                ("NetworkGuid", ctypes.c_ubyte * 16), ("ConnectionType", ctypes.c_int),
                ("TransmitLinkSpeed", ctypes.c_uint64), ("ReceiveLinkSpeed", ctypes.c_uint64),
                ("InOctets", ctypes.c_uint64), ("InUcastPkts", ctypes.c_uint64), ("InNUcastPkts", ctypes.c_uint64),
                ("InDiscards", ctypes.c_uint64), ("InErrors", ctypes.c_uint64), ("InUnknownProtos", ctypes.c_uint64),
                ("InUcastOctets", ctypes.c_uint64), ("InMulticastOctets", ctypes.c_uint64),
                ("InBroadcastOctets", ctypes.c_uint64), ("OutOctets", ctypes.c_uint64),
                ("OutUcastPkts", ctypes.c_uint64), ("OutNUcastPkts", ctypes.c_uint64),
                ("OutDiscards", ctypes.c_uint64), ("OutErrors", ctypes.c_uint64), ("OutUcastOctets", ctypes.c_uint64),
                ("OutMulticastOctets", ctypes.c_uint64), ("OutBroadcastOctets", ctypes.c_uint64),
                ("OutQLen", ctypes.c_uint64)]


@dataclass
class Adapter:
    index: int
    name: str            # "Wi-Fi"
    description: str     # "Intel(R) Wi-Fi 6 AX201 160MHz"
    kind: str            # "wifi", "ethernet", "mobile" or "other"
    received: int        # bytes, since Windows started (or the adapter came on)
    sent: int
    link_down: int       # bits a second the link itself can carry (what Windows shows as "Speed")
    link_up: int
    main: bool = False   # the one the internet goes through


def _kind(if_type: int) -> str:
    return {IF_TYPE_WIFI: "wifi", IF_TYPE_ETHERNET: "ethernet", IF_TYPE_MOBILE: "mobile"}.get(if_type, "other")


def main_adapter_index() -> int:
    """The adapter Windows would send internet traffic through (read from this PC's own routes; nothing is sent)."""
    if sys.platform != "win32":
        return 0
    idx = wt.DWORD(0)
    fn = _fn("iphlpapi", "GetBestInterface", wt.DWORD, ctypes.c_uint32, ctypes.POINTER(wt.DWORD))
    # (any public address will do: Windows looks at its routes, it doesn't contact it)
    target = struct.unpack("<I", socket.inet_aton("1.1.1.1"))[0]
    return idx.value if fn(target, ctypes.byref(idx)) == NO_ERROR else 0


def adapters() -> list[Adapter]:
    """The PC's own network adapters that are connected (Wi-Fi, cable, mobile), with their byte counters.
    Virtual ones (VPN tunnels, virtual machines' switches, loopback) are left out, so nothing is counted twice."""
    if sys.platform != "win32":
        return []
    get_table = _fn("iphlpapi", "GetIfTable2", wt.DWORD, ctypes.POINTER(ctypes.c_void_p))
    free_table = _fn("iphlpapi", "FreeMibTable", None, ctypes.c_void_p)
    table = ctypes.c_void_p()
    if get_table(ctypes.byref(table)) != NO_ERROR or not table:
        return []
    out = []
    main = main_adapter_index()
    try:
        n = min(ctypes.c_uint32.from_address(table.value).value, 1000)
        rows = ctypes.cast(table.value + 8, ctypes.POINTER(_MibIfRow2))  # (the rows start 8 bytes in)
        for i in range(n):
            r = rows[i]
            hardware = bool(r.Flags & 0x1)
            filtered = bool(r.Flags & 0x2)
            if r.Type in (IF_TYPE_LOOPBACK, IF_TYPE_TUNNEL) or filtered:
                continue
            if r.OperStatus != IF_OPER_UP or r.MediaConnectState != MEDIA_CONNECTED:
                continue
            if not hardware and r.InterfaceIndex != main:   # (a VPN or a virtual switch: counted where it goes)
                continue
            out.append(Adapter(r.InterfaceIndex, r.Alias[:80], r.Description[:120], _kind(r.Type), r.InOctets,
                               r.OutOctets, r.ReceiveLinkSpeed, r.TransmitLinkSpeed, r.InterfaceIndex == main))
    finally:
        free_table(table)
    return out


class Meter:
    """How fast the PC is receiving and sending right now, from the adapters' counters read now and then."""

    def __init__(self):
        self.last: dict[int, tuple[int, int]] = {}
        self.at = 0.0

    def read(self, now: float | None = None, found: list[Adapter] | None = None) -> tuple[float, float, list[Adapter]]:
        """(bytes a second received, bytes a second sent, the adapters) since the last read (0, 0 the first time)."""
        now = time.monotonic() if now is None else now
        found = adapters() if found is None else found
        down = up = 0.0
        span = now - self.at
        if self.at and span > 0.05:
            for a in found:
                before = self.last.get(a.index)
                if before is None:
                    continue
                got, put = a.received - before[0], a.sent - before[1]
                if got >= 0:     # (a counter that went back: the adapter was reset; skipped once)
                    down += got / span
                if put >= 0:
                    up += put / span
        self.last = {a.index: (a.received, a.sent) for a in found}
        self.at = now
        return down, up, found


# ----------------------------------------------------------------------------- what each program used

@dataclass
class Usage:
    name: str           # what Windows calls it ("Microsoft Edge", or a program's file)
    id: str             # Windows' own id for it (a file path, or a Store app's family name)
    received: int
    sent: int

    @property
    def total(self) -> int:
        return self.received + self.sent


@dataclass
class UsageReport:
    apps: list | None = None     # Usage for each program, biggest first (None: Windows doesn't share that)
    received: int | None = None  # all together, on every network (None: Windows doesn't share that either)
    sent: int | None = None


def usage(days: float) -> UsageReport:
    """How much was received and sent over the last days, on every network, as Windows counted it (the same
    numbers as Settings → Network → Data usage): for each program when Windows lets other programs read that,
    otherwise all together, otherwise nothing."""
    report = UsageReport()
    if sys.platform != "win32":
        return report
    try:
        import asyncio
        import datetime
        from winrt.windows.networking.connectivity import (DataUsageGranularity, NetworkInformation,
                                                           NetworkUsageStates, TriStates)
    except Exception:
        log.debug("no Windows data usage here", exc_info=True)
        return report
    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(days=days)
    states = NetworkUsageStates(TriStates.DO_NOT_CARE, TriStates.DO_NOT_CARE)
    apps: dict[str, Usage] = {}

    async def read():
        got_apps = got_total = False
        received = sent = 0
        for prof in list(NetworkInformation.get_connection_profiles())[:30]:
            try:
                found = await prof.get_attributed_network_usage_async(start, end, states)
                got_apps = True
            except Exception:
                found = []
            for a in list(found)[:5000]:
                key = (a.attribution_id or a.attribution_name or "").lower()
                if not key:
                    continue
                u = apps.get(key)
                if u is None:
                    u = apps[key] = Usage(str(a.attribution_name or "")[:120], str(a.attribution_id or "")[:400], 0, 0)
                u.received += max(0, int(a.bytes_received))
                u.sent += max(0, int(a.bytes_sent))
            try:
                for part in list(await prof.get_network_usage_async(start, end, DataUsageGranularity.TOTAL,
                                                                    states))[:1000]:
                    received += max(0, int(part.bytes_received))
                    sent += max(0, int(part.bytes_sent))
                got_total = True
            except Exception:
                log.debug("no data usage for a network", exc_info=True)
        if got_apps:
            report.apps = sorted((u for u in apps.values() if u.total), key=lambda u: -u.total)
        if got_total:
            report.received, report.sent = received, sent
        elif report.apps is not None:
            report.received = sum(u.received for u in report.apps)
            report.sent = sum(u.sent for u in report.apps)

    try:
        asyncio.run(read())
    except Exception:
        log.debug("couldn't read the data usage", exc_info=True)
    return report


def metered() -> bool | None:
    """The internet connection is metered (a mobile plan, or set as metered in Windows), None if not known."""
    if sys.platform != "win32":
        return None
    try:
        from winrt.windows.networking.connectivity import NetworkCostType, NetworkInformation
        prof = NetworkInformation.get_internet_connection_profile()
        if prof is None:
            return None
        cost = prof.get_connection_cost()
        return cost.network_cost_type in (NetworkCostType.FIXED, NetworkCostType.VARIABLE) or bool(cost.roaming)
    except Exception:
        log.debug("couldn't tell if the connection is metered", exc_info=True)
        return None


# ----------------------------------------------------------------------------- the speed test

SPEED_HOST = "speed.cloudflare.com"   # Cloudflare's speed test (the only place Rakiza Core Network ever connects to)
DOWN_PATH = "/__down?bytes={size}"
UP_PATH = "/__up"
STREAMS = 4                  # connections at once, so one connection's limits don't hide a fast line
PHASE_S = 7.0                # how long each direction is measured
WARMUP_S = 1.5               # the first part of each direction isn't counted (connections speed up at first)
CHUNK = 25_000_000           # bytes asked for in one download request
UP_CHUNK = 8_000_000         # bytes sent in one upload request
MAX_DOWN = 1_500_000_000     # never more than this is downloaded in one test
MAX_UP = 500_000_000         # never more than this is uploaded in one test
PINGS = 15                   # latency measurements before loading the line
TIMEOUT_S = 10
SMALL_DOWN = 4               # on a metered connection: MB a direction (a few MB, enough for a rough number)
MAX_TESTS = 20              # results kept
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"   # (a plain one: nothing about the PC or the app)
_TIMING = re.compile(r"dur=([0-9]+(?:\.[0-9]+)?)")


class Stopped(Exception):
    """The person stopped the test."""


@dataclass
class SpeedResult:
    down: float = 0.0            # bits a second
    up: float = 0.0
    latency: float = 0.0         # ms, idle
    jitter: float = 0.0          # ms
    loaded_down: float = 0.0     # ms of latency while downloading
    loaded_up: float = 0.0       # ms of latency while uploading
    used: int = 0                # bytes downloaded and uploaded, all together
    server: str = ""             # where Cloudflare's server was ("DXB", "Dubai")
    when: float = 0.0
    small: bool = False          # a short test (metered connection)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("down", "up", "latency", "jitter", "loaded_down", "loaded_up", "used",
                                               "server", "when", "small")}

    @classmethod
    def from_dict(cls, d: dict) -> "SpeedResult | None":
        try:
            r = cls(**{k: float(d[k]) for k in ("down", "up", "latency", "jitter", "loaded_down", "loaded_up")})
            r.used, r.when = int(d.get("used", 0)), float(d.get("when", 0))
            r.server, r.small = str(d.get("server", ""))[:40], bool(d.get("small", False))
            return r
        except (KeyError, TypeError, ValueError):
            return None


def server_time(header: str | None) -> float:
    """Milliseconds the server itself spent on a request, from its Server-Timing header (0 if it doesn't say)."""
    if not header:
        return 0.0
    total = 0.0
    for part in header.split(",")[:8]:
        if "cfl4" in part.lower():
            continue   # (a note about the connection, not time spent)
        m = _TIMING.search(part)
        if m:
            total += min(float(m.group(1)), 10_000.0)
    return total


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def jitter_of(values: list[float]) -> float:
    """The average change from one measurement to the next."""
    if len(values) < 2:
        return 0.0
    return sum(abs(b - a) for a, b in zip(values, values[1:])) / (len(values) - 1)


def _connection(timeout: float = TIMEOUT_S):
    """A connection to Cloudflare's speed test only, with its certificate checked (Windows' own trusted
    certificates), never anywhere else (http.client doesn't follow redirects)."""
    import http.client
    import ssl
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return http.client.HTTPSConnection(SPEED_HOST, 443, timeout=timeout, context=ctx)


class SpeedTest:
    """One speed test. run() measures latency, then download, then upload, calling progress(phase, value)
    on its own thread: phase "ping" (ms), "down" / "up" (bits a second so far). stop() ends it soon after."""

    def __init__(self, small: bool = False, phase_s: float = PHASE_S):
        self.small = small
        self.phase_s = phase_s
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.used = 0
        self.server = ""

    def stop(self):
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _check(self):
        if self._stop.is_set():
            raise Stopped()

    def _count(self, n: int):
        with self._lock:
            self.used += n

    # -- latency

    def _ping(self, conn) -> float:
        """One small request: the time it took, less the time the server spent on it, in ms."""
        t = time.perf_counter()
        conn.request("GET", DOWN_PATH.format(size=0), headers={"User-Agent": USER_AGENT})
        resp = conn.getresponse()
        resp.read(1024)
        took = (time.perf_counter() - t) * 1000
        if resp.status != 200:
            raise OSError(f"speed test answered {resp.status}")
        if not self.server:
            self.server = _place(resp.getheader("colo"), resp.getheader("city"))
        return max(0.1, took - server_time(resp.getheader("Server-Timing")))

    def latency(self, progress) -> list[float]:
        conn = _connection()
        try:
            self._ping(conn)   # (the first one includes making the connection: not counted)
            out = []
            for _ in range(PINGS):
                self._check()
                out.append(self._ping(conn))
                progress("ping", median(out))
            return out
        finally:
            conn.close()

    # -- loading the line, with latency measured alongside

    def _measure(self, direction: str, progress) -> tuple[float, float]:
        """Downloads or uploads on STREAMS connections at once for phase_s seconds (or a few MB on a metered
        connection). Returns (bits a second, median latency while loaded in ms)."""
        cap = (SMALL_DOWN << 20) if self.small else (MAX_DOWN if direction == "down" else MAX_UP)
        moved = [0]
        moved_lock = threading.Lock()
        done = threading.Event()
        errors: list[BaseException] = []
        start = time.perf_counter()
        deadline = start + self.phase_s
        samples: list[tuple[float, int]] = [(start, 0)]

        def add(n: int):
            with moved_lock:
                moved[0] += n
                if moved[0] >= cap:
                    done.set()
            self._count(n)

        def finished() -> bool:
            return done.is_set() or self._stop.is_set() or time.perf_counter() >= deadline

        asked = [0]   # bytes asked for so far: never more than the cap, so no more than that can ever arrive

        def ask(size: int) -> int:
            with moved_lock:
                size = min(size, cap - asked[0])
                if size > 0:
                    asked[0] += size
                return size

        def download():
            conn = _connection()
            try:
                while not finished():
                    size = ask(CHUNK)
                    if size <= 0:
                        return
                    conn.request("GET", DOWN_PATH.format(size=size), headers={"User-Agent": USER_AGENT})
                    resp = conn.getresponse()
                    if resp.status != 200:
                        raise OSError(f"speed test answered {resp.status}")
                    left = size
                    while left > 0 and not finished():
                        block = resp.read(min(65536, left))
                        if not block:
                            break
                        left -= len(block)
                        add(len(block))
                    if left > 0:   # (stopped half-way: this connection can't be used again)
                        break
            finally:
                conn.close()

        block = os.urandom(65536)   # (data that can't be squeezed smaller on the way, so it's a fair test)

        def upload():
            conn = _connection()
            try:
                while not finished():
                    size = ask(UP_CHUNK)
                    if size <= 0:
                        return
                    conn.putrequest("POST", UP_PATH)
                    conn.putheader("User-Agent", USER_AGENT)
                    conn.putheader("Content-Type", "application/octet-stream")
                    conn.putheader("Content-Length", str(size))
                    conn.endheaders()
                    left = size
                    while left > 0:
                        if finished():
                            return   # (half-sent: the connection is closed and the rest never goes)
                        piece = block if left >= len(block) else block[:left]
                        conn.send(piece)
                        left -= len(piece)
                        add(len(piece))
                    resp = conn.getresponse()
                    resp.read(1024)
                    if resp.status != 200:
                        raise OSError(f"speed test answered {resp.status}")
            finally:
                conn.close()

        def worker(fn):
            try:
                fn()
            except Exception as e:  # (shown as one problem: the test couldn't finish)
                errors.append(e)
                done.set()

        loaded: list[float] = []

        def pinger():
            conn = None
            try:
                conn = _connection()
                while not finished():
                    loaded.append(self._ping(conn))
                    done.wait(0.4)
            except Exception:
                log.debug("a latency check under load failed", exc_info=True)
            finally:
                if conn is not None:
                    conn.close()

        threads = [threading.Thread(target=worker, args=(download if direction == "down" else upload,),
                                    daemon=True, name=f"corefold-speed-{direction}") for _ in range(STREAMS)]
        threads.append(threading.Thread(target=pinger, daemon=True, name="corefold-speed-ping"))
        for t in threads:
            t.start()
        while not finished():
            time.sleep(0.2)
            now = time.perf_counter()
            with moved_lock:
                samples.append((now, moved[0]))
            progress(direction, rate(samples, start + WARMUP_S))
        last = time.perf_counter() + TIMEOUT_S   # (all of them together: a stalled line never holds the test longer)
        for t in threads:
            t.join(max(0.0, last - time.perf_counter()))
        self._check()
        with moved_lock:
            samples.append((time.perf_counter(), moved[0]))
        if errors and moved[0] == 0:
            raise errors[0]
        return rate(samples, start + WARMUP_S), median(loaded)

    def run(self, progress=lambda phase, value: None) -> SpeedResult:
        result = SpeedResult(when=time.time(), small=self.small)
        pings = self.latency(progress)
        result.latency, result.jitter = median(pings), jitter_of(pings)
        self._check()
        result.down, result.loaded_down = self._measure("down", progress)
        self._check()
        result.up, result.loaded_up = self._measure("up", progress)
        result.used, result.server = self.used, self.server
        return result


def rate(samples: list[tuple[float, int]], after: float) -> float:
    """Bits a second from (time, bytes moved so far) samples, counting only from `after` on (or from the start
    if the test was too short for that)."""
    if len(samples) < 2:
        return 0.0
    final = samples[-1][1]
    end = next(i for i, s in enumerate(samples) if s[1] == final)   # (the time spent waiting after the last byte
    samples = samples[:max(end, 1) + 1]                              # isn't counted)
    later = [s for s in samples if s[0] >= after]
    if len(later) < 2 or later[0][1] == later[-1][1]:
        later = samples
    (t0, b0), (t1, b1) = later[0], later[-1]
    if t1 - t0 <= 0:
        return 0.0
    return max(0.0, (b1 - b0) * 8 / (t1 - t0))


def _place(colo: str | None, city: str | None) -> str:
    """Where Cloudflare's server is, as it says ("Dubai"), only plain letters (it's shown on the page)."""
    for value in (city, colo):
        text = re.sub(r"[^A-Za-z .'-]", "", value or "").strip()[:40]
        if text:
            return text
    return ""


# ----------------------------------------------------------------------------- what the numbers mean

# what a connection needs for each thing (bits a second down, up, ms latency); widely used guidance
NEEDS = {"browse": (5e6, 1e6, 150), "video_hd": (8e6, 0, 0), "video_4k": (25e6, 0, 0),
         "call": (4e6, 3e6, 150), "game": (10e6, 3e6, 60)}


def verdicts(r: SpeedResult) -> dict[str, str]:
    """For each everyday use: "good", "ok" or "poor"."""
    out = {}
    worst_latency = max(r.latency, r.loaded_down, r.loaded_up)
    for use, (down, up, latency) in NEEDS.items():
        if r.down < down * 0.6 or (up and r.up < up * 0.6) or (latency and r.latency > latency * 1.5):
            out[use] = "poor"
        elif r.down < down or (up and r.up < up) or (latency and worst_latency > latency * 1.5):
            out[use] = "ok"
        else:
            out[use] = "good"
    if r.jitter > 30 and out.get("call") == "good":
        out["call"] = "ok"
    if r.jitter > 20 and out.get("game") == "good":
        out["game"] = "ok"
    return out


def link_limits(r: SpeedResult, link_bits: int, kind: str) -> bool:
    """The Wi-Fi (or cable) link itself is about as fast as the result: it's what holds the speed back. Wi-Fi
    rarely carries more than about 60% of its link speed."""
    if not link_bits or not r.down:
        return False
    usable = link_bits * (0.6 if kind == "wifi" else 0.9)
    return r.down >= usable * 0.8


def download_time(r: SpeedResult, size_bytes: int) -> float:
    """Seconds to download a file of this size at the measured speed."""
    return size_bytes * 8 / r.down if r.down > 0 else 0.0
