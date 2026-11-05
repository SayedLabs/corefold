"""Rakiza Core Network: reading Windows' connection tables, the names Windows remembers, the adapters' counters,
and the speed test (against a server on this PC: the tests never go online)."""
import ctypes
import http.server
import json
import socket
import struct
import sys
import threading
import time

import pytest

from corefold import network

WINDOWS = sys.platform == "win32"


# ----------------------------------------------------------------------------- the tables, as Windows writes them

def _tcp4_row(state, local, lport, remote, rport, pid):
    ip = lambda a: struct.unpack("<I", socket.inet_aton(a))[0]  # noqa: E731
    return struct.pack("<IIIIII", state, ip(local), socket.htons(lport), ip(remote), socket.htons(rport), pid)


def test_tcp4_table_is_read_with_ports_and_programs():
    rows = [_tcp4_row(5, "192.168.1.5", 50123, "142.250.1.1", 443, 1234),
            _tcp4_row(2, "0.0.0.0", 445, "0.0.0.0", 0, 4),
            _tcp4_row(5, "127.0.0.1", 6000, "127.0.0.1", 6001, 99)]
    data = struct.pack("<I", len(rows)) + b"".join(rows)
    conns = network.parse_tcp4(data)
    assert [(c.local, c.local_port, c.remote, c.remote_port, c.state, c.pid) for c in conns] == [
        ("192.168.1.5", 50123, "142.250.1.1", 443, "established", 1234),
        ("0.0.0.0", 445, "", 0, "listening", 4),
        ("127.0.0.1", 6000, "127.0.0.1", 6001, "established", 99)]


def test_a_table_that_says_it_has_more_rows_than_it_holds_is_cut():
    data = struct.pack("<I", 10_000_000) + _tcp4_row(5, "10.0.0.2", 1, "8.8.8.8", 53, 7)
    assert len(network.parse_tcp4(data)) == 1
    assert network.parse_tcp4(b"") == [] and network.parse_tcp4(b"\x01\x00") == []
    assert network.parse_udp6(struct.pack("<I", 5)) == []


def test_tcp6_and_udp_tables():
    la = socket.inet_pton(socket.AF_INET6, "::ffff:10.0.0.2")
    ra = socket.inet_pton(socket.AF_INET6, "2606:4700::1")
    row = la + struct.pack("<II", 0, socket.htons(51000)) + ra + struct.pack("<II", 0, socket.htons(443)) + \
        struct.pack("<II", 5, 321)
    conns = network.parse_tcp6(struct.pack("<I", 1) + row)
    assert (conns[0].local, conns[0].local_port, conns[0].remote, conns[0].remote_port, conns[0].pid) == \
        ("10.0.0.2", 51000, "2606:4700::1", 443, 321)
    udp = struct.pack("<I", 1) + struct.pack("<III", struct.unpack("<I", socket.inet_aton("0.0.0.0"))[0],
                                             socket.htons(5353), 77)
    u = network.parse_udp4(udp)[0]
    assert (u.proto, u.local_port, u.state, u.pid) == ("udp", 5353, "listening", 77)


def test_where_the_other_end_is():
    assert network.where_is("127.0.0.1") == network.THIS_PC
    assert network.where_is("::1") == network.THIS_PC
    assert network.where_is("192.168.1.20") == network.HOME
    assert network.where_is("fe80::1%12") == network.HOME
    assert network.where_is("10.1.2.3") == network.HOME
    assert network.where_is("100.70.1.1") == network.INTERNET   # (a provider's shared addresses)
    assert network.where_is("142.250.1.1") == network.INTERNET
    assert network.where_is("::ffff:8.8.8.8") == network.INTERNET
    assert network.where_is("not an address") == network.INTERNET
    listening_all = network.Connection("tcp", "0.0.0.0", 445, "", 0, "listening", 4)
    listening_here = network.Connection("tcp", "127.0.0.1", 9000, "", 0, "listening", 4)
    assert network.reachable_by(listening_all) == network.HOME
    assert network.reachable_by(listening_here) == network.THIS_PC


def test_host_names_from_outside_are_shown_only_when_plain():
    assert network.clean_host("WWW.Example.com.") == "www.example.com"
    for bad in ("evil<b>.com", "a b.com", "x" * 300, "..", "\\\\server\\share", "name\u202e.com", ""):
        assert network.clean_host(bad) == "", bad


def test_programs_group_processes_and_name_places(monkeypatch):
    conns = [network.Connection("tcp", "10.0.0.2", 1, "142.250.1.1", 443, "established", 10),
             network.Connection("tcp", "10.0.0.2", 2, "142.250.1.1", 443, "established", 11),
             network.Connection("tcp", "10.0.0.2", 3, "192.168.1.1", 80, "established", 11),
             network.Connection("tcp", "10.0.0.2", 4, "1.2.3.4", 443, "time_wait", 12),
             network.Connection("tcp", "0.0.0.0", 445, "", 0, "listening", 4)]
    for c in conns:
        c.where = network.reachable_by(c) if c.state == "listening" else network.where_is(c.remote)
    monkeypatch.setattr(network, "process_names", lambda: {10: "chrome.exe", 11: "chrome.exe", 12: "old.exe",
                                                           4: "System"})
    monkeypatch.setattr(network, "process_path", lambda pid: r"C:\Apps\Chrome\chrome.exe" if pid in (10, 11) else "")
    monkeypatch.setattr(network, "program_name", lambda path: ("Google Chrome", "Google LLC"))
    found = network.programs(conns, {"142.250.1.1": "www.google.com"})
    chrome = next(p for p in found if p.exe == "chrome.exe")
    assert chrome.name == "Google Chrome" and chrome.pids == {10, 11}
    assert chrome.places() == ["www.google.com", "192.168.1.1"]
    assert len(chrome.internet) == 2
    assert not any(p.exe == "old.exe" for p in found)   # (only closing: gone in a moment)
    windows = next(p for p in found if p.key == "system")
    assert windows.system and [c.local_port for c in windows.open_ports] == [445]


def test_the_speed_counters():
    m = network.Meter()
    a = network.Adapter(3, "Wi-Fi", "Intel", "wifi", 1_000_000, 500_000, 866_000_000, 866_000_000, True)
    assert m.read(100.0, [a])[:2] == (0.0, 0.0)
    b = network.Adapter(3, "Wi-Fi", "Intel", "wifi", 1_250_000, 550_000, 866_000_000, 866_000_000, True)
    down, up, _ = m.read(102.0, [b])
    assert (down, up) == (125_000.0, 25_000.0)
    reset = network.Adapter(3, "Wi-Fi", "Intel", "wifi", 10, 10, 0, 0, True)   # (counters went back)
    assert m.read(103.0, [reset])[:2] == (0.0, 0.0)


@pytest.mark.skipif(not WINDOWS, reason="Windows' own tables")
def test_windows_tables_can_be_read_without_being_an_administrator():
    assert ctypes.sizeof(network._MibIfRow2) == 1352
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    try:
        conns = network.connections()
        mine = [c for c in conns if c.local_port == port and c.state == "listening"]
        assert mine and mine[0].pid == __import__("os").getpid() and mine[0].where == network.THIS_PC
        assert any(c.remote_port == port and c.state == "established" for c in conns)
        assert isinstance(network.cached_names(), dict)
        assert isinstance(network.adapters(), list)
        assert isinstance(network.services_by_pid(), dict)
        progs = network.programs()
        me = [p for p in progs if __import__("os").getpid() in p.pids]
        assert me and me[0].listening
        assert isinstance(network.usage(1), network.UsageReport)   # (Windows may or may not share its counts)
        assert network.metered() in (True, False, None)
    finally:
        client.close()
        server.close()


@pytest.mark.skipif(not WINDOWS, reason="Windows' own tables")
def test_reading_from_several_threads_at_once_is_safe():
    """The page reads the speed and the programs on two threads, while other tools use the same Windows calls."""
    import threading
    failed = []

    def read(fn):
        try:
            for _ in range(5):
                fn()
        except Exception as e:   # (a crash would end the test run itself)
            failed.append(repr(e))
    jobs = [threading.Thread(target=read, args=(fn,)) for fn in
            (network.programs, network.adapters, network.cached_names, network.services_by_pid,
             network.process_names, network.programs, network.adapters)]
    for t in jobs:
        t.start()
    for t in jobs:
        t.join(120)
    assert not failed


# ----------------------------------------------------------------------------- the speed test, on this PC

class _FakeSpeed(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    got = 0

    def log_message(self, *_a):
        pass

    def do_GET(self):
        size = int(self.path.split("bytes=", 1)[1])
        self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.send_header("Server-Timing", "cfSpeedEdge;dur=1, cfSpeedWorker;dur=2, cfL4;desc=\"?rtt=5\"")
        self.send_header("colo", "DXB")
        self.send_header("city", "Dubai<script>")
        self.end_headers()
        chunk = b"0" * 65536
        while size > 0:
            piece = chunk[:min(size, len(chunk))]
            try:
                self.wfile.write(piece)
            except OSError:
                return
            size -= len(piece)

    def do_POST(self):
        left = int(self.headers["Content-Length"])
        while left > 0:
            block = self.rfile.read(min(left, 65536))
            if not block:
                return
            left -= len(block)
            type(self).got += len(block)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture
def fake_speed(monkeypatch):
    import http.client
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeSpeed)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    monkeypatch.setattr(network, "_connection",
                        lambda timeout=network.TIMEOUT_S: http.client.HTTPConnection("127.0.0.1", port,
                                                                                    timeout=timeout))
    monkeypatch.setattr(network, "WARMUP_S", 0.2)
    yield
    server.shutdown()


def test_a_speed_test_measures_both_ways_and_stays_within_its_limits(fake_speed, monkeypatch):
    monkeypatch.setattr(network, "MAX_DOWN", 30_000_000)
    monkeypatch.setattr(network, "MAX_UP", 20_000_000)
    phases = []
    r = network.SpeedTest(phase_s=1.0).run(lambda phase, value: phases.append(phase))
    assert r.down > 0 and r.up > 0 and r.latency > 0
    assert {"ping", "down", "up"} <= set(phases)
    assert r.server == "Dubaiscript"   # (only plain letters are kept)
    assert r.used <= 30_000_000 + 20_000_000
    assert network.SpeedResult.from_dict(json.loads(json.dumps(r.as_dict()))) == r


def test_a_short_test_on_a_metered_connection(fake_speed):
    r = network.SpeedTest(small=True, phase_s=3.0).run()
    assert r.small and 0 < r.used <= 2 * (network.SMALL_DOWN << 20)


def test_stopping_a_speed_test(fake_speed):
    t = network.SpeedTest(phase_s=5.0)
    threading.Timer(0.6, t.stop).start()
    start = time.monotonic()
    with pytest.raises(network.Stopped):
        t.run()
    assert time.monotonic() - start < 4


def test_the_speed_test_only_ever_connects_to_cloudflare():
    conn = network._connection()
    try:
        assert conn.host == "speed.cloudflare.com" and conn.port == 443
        assert conn._context.check_hostname and conn._context.verify_mode.name == "CERT_REQUIRED"
    finally:
        conn.close()


def test_server_time_and_figures():
    assert network.server_time("cfSpeedEdge;dur=4, cfSpeedWorker;dur=34, cfL4;desc=\"?rtt=5007&dur=99\"") == 38
    assert network.server_time(None) == 0 and network.server_time("garbage") == 0
    assert network.server_time("x;dur=99999999") == 10_000
    assert network.median([3, 1, 2]) == 2 and network.median([]) == 0
    assert network.jitter_of([10, 12, 10, 14]) == pytest.approx(8 / 3)
    assert network.rate([(0.0, 0), (1.0, 1_000_000), (2.0, 3_000_000)], 1.0) == 16_000_000


def test_what_the_numbers_mean():
    fast = network.SpeedResult(down=300e6, up=50e6, latency=12, jitter=2, loaded_down=30, loaded_up=40)
    assert set(network.verdicts(fast).values()) == {"good"}
    slow = network.SpeedResult(down=3e6, up=0.5e6, latency=200, jitter=40, loaded_down=900, loaded_up=900)
    v = network.verdicts(slow)
    assert v["video_4k"] == "poor" and v["call"] == "poor" and v["game"] == "poor"
    wifi_bound = network.SpeedResult(down=200e6)
    assert network.link_limits(wifi_bound, 300_000_000, "wifi")
    assert not network.link_limits(wifi_bound, 2_400_000_000, "wifi")
    assert network.download_time(network.SpeedResult(down=80e6), 10 ** 9) == 100


def test_broken_saved_results_are_dropped():
    assert network.SpeedResult.from_dict({"down": "x"}) is None
    assert network.SpeedResult.from_dict({}) is None
