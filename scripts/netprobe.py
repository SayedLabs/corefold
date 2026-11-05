"""Temporary probe of the Windows network APIs (removed before merge)."""
import ctypes
import ctypes.wintypes as wt
import datetime
import http.client
import socket
import ssl
import struct
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
part = sys.argv[1] if len(sys.argv) > 1 else "all"
ip = ctypes.windll.iphlpapi


def tcp_table():
    out = []
    for fam in (2, 23):
        size = wt.DWORD(0)
        ip.GetExtendedTcpTable(None, ctypes.byref(size), False, fam, 5, 0)
        buf = ctypes.create_string_buffer(size.value + 4096)
        size = wt.DWORD(len(buf))
        r = ip.GetExtendedTcpTable(buf, ctypes.byref(size), False, fam, 5, 0)
        n = struct.unpack_from("<I", buf, 0)[0]
        out.append((fam, r, n))
        if fam == 2:
            for i in range(min(n, 8)):
                st, la, lp, ra, rp, pid = struct.unpack_from("<IIIIII", buf, 4 + i * 24)
                print("  tcp4", st, socket.inet_ntoa(struct.pack("<I", la)), socket.ntohs(lp & 0xffff),
                      socket.inet_ntoa(struct.pack("<I", ra)), socket.ntohs(rp & 0xffff), pid)
    print("tcp tables", out)


def if_table():
    p = ctypes.c_void_p()
    r = ip.GetIfTable2(ctypes.byref(p))
    n = ctypes.c_ulong.from_address(p.value).value
    print("GetIfTable2", r, n, "rows; sizeof row expected 1352")
    ip.FreeMibTable(p)


def dns_cache():
    dns = ctypes.windll.dnsapi

    class ENTRY(ctypes.Structure):
        pass
    ENTRY._fields_ = [("next", ctypes.POINTER(ENTRY)), ("name", ctypes.c_wchar_p), ("type", ctypes.c_ushort),
                      ("datalen", ctypes.c_ushort), ("flags", ctypes.c_ulong)]
    head = ctypes.POINTER(ENTRY)()
    t = time.perf_counter()
    r = dns.DnsGetCacheDataTable(ctypes.byref(head))
    names = []
    e = head
    while e:
        names.append((e.contents.name, e.contents.type))
        e = e.contents.next
    print("DnsGetCacheDataTable", r, len(names), names[:10], f"{time.perf_counter() - t:.3f}s")


def winrt_usage():
    try:
        from winrt.windows.networking.connectivity import NetworkInformation, NetworkUsageStates, TriStates
        from winrt.windows.foundation import DateTime  # noqa
    except Exception as e:
        print("winrt import failed", repr(e))
        return
    import asyncio
    profs = NetworkInformation.get_connection_profiles()
    print("profiles", [p.profile_name for p in profs])
    now = datetime.datetime.now(datetime.timezone.utc)
    states = NetworkUsageStates()
    states.roaming = TriStates.DO_NOT_CARE
    states.shared = TriStates.DO_NOT_CARE

    async def go():
        for p in profs:
            for label, start in (("5min", now - datetime.timedelta(minutes=5)),
                                 ("30d", now - datetime.timedelta(days=30))):
                try:
                    t = time.perf_counter()
                    res = await p.get_attributed_network_usage_async(start, now, states)
                    items = sorted(res, key=lambda a: -(a.bytes_received + a.bytes_sent))
                    print(" ", p.profile_name, label, len(items), f"{time.perf_counter() - t:.2f}s")
                    for a in items[:8]:
                        print("    ", repr(a.attribution_id)[:90], "|", a.attribution_name, a.bytes_received, a.bytes_sent)
                except Exception as e:
                    print(" ", p.profile_name, label, "FAILED", repr(e))
    asyncio.run(go())


def speed():
    ctx = ssl.create_default_context()
    c = http.client.HTTPSConnection("speed.cloudflare.com", timeout=15, context=ctx)
    lat = []
    for i in range(10):
        t = time.perf_counter()
        c.request("GET", "/__down?bytes=0", headers={"User-Agent": "probe"})
        r = c.getresponse()
        r.read()
        el = (time.perf_counter() - t) * 1000
        st = r.getheader("Server-Timing")
        lat.append(el)
        if i < 2:
            print("  latency", round(el, 1), "ms", st, dict(r.getheaders()).keys())
    print("latency median", sorted(lat)[5])
    for size in (1_000_000, 10_000_000, 25_000_000):
        t = time.perf_counter()
        c.request("GET", f"/__down?bytes={size}", headers={"User-Agent": "probe"})
        r = c.getresponse()
        got = 0
        while True:
            b = r.read(65536)
            if not b:
                break
            got += len(b)
        el = time.perf_counter() - t
        print("  down", size, got, f"{got * 8 / el / 1e6:.0f} Mb/s")
    for size in (1_000_000, 10_000_000):
        body = b"0" * size
        t = time.perf_counter()
        c.request("POST", "/__up", body=body, headers={"User-Agent": "probe", "Content-Type": "text/plain"})
        r = c.getresponse()
        r.read()
        el = time.perf_counter() - t
        print("  up", size, r.status, r.getheader("Server-Timing"), f"{size * 8 / el / 1e6:.0f} Mb/s")


print("admin:", ctypes.windll.shell32.IsUserAnAdmin())
if part in ("all", "basic"):
    tcp_table()
    if_table()
    dns_cache()
    winrt_usage()
if part == "all":
    speed()
