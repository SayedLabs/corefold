"""Corefold Network's page: the programs and their places, what other devices can reach, Windows' counts, and the
speed test's results in plain words. Everything on the page is read from this PC (the speed test is faked)."""
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QHBoxLayout, QLabel

from corefold import network, settings as settings_mod, tools


def _prog(key, name, conns, listening=(), path="", system=False):
    p = network.Program(key, name, path=path, exe=name.lower() + ".exe", system=system)
    for c in conns:
        c.where = network.where_is(c.remote)
    for c in listening:
        c.where = network.reachable_by(c)
    p.conns, p.listening = list(conns), list(listening)
    return p


def _tcp(remote, port, host=""):
    return network.Connection("tcp", "10.0.0.2", 50000, remote, port, "established", 10, host=host)


PROGRAMS = [
    _prog("c:\\apps\\browser.exe", "Browser", [_tcp("142.250.1.1", 443, "www.example.com"),
                                               _tcp("142.250.1.2", 443, "video.example.com"),
                                               _tcp("192.168.1.1", 80)]),
    _prog("c:\\apps\\printer.exe", "Printer helper", [_tcp("192.168.1.30", 9100)]),
    _prog("system", "Windows", [], [network.Connection("tcp", "0.0.0.0", 445, "", 0, "listening", 4)], system=True),
    _prog("c:\\apps\\local.exe", "Local only", [], [network.Connection("tcp", "127.0.0.1", 9000, "", 0, "listening",
                                                                           5)]),
]


@pytest.fixture
def page(qtbot, monkeypatch):
    from corefold import app as A
    from corefold import networkpage
    monkeypatch.setattr(networkpage, "look", lambda: [p for p in PROGRAMS])
    monkeypatch.setattr(network, "adapters", lambda: [network.Adapter(3, "Wi-Fi", "Intel Wi-Fi 6", "wifi", 10, 10,
                                                                      300_000_000, 300_000_000, True)])
    monkeypatch.setattr(network, "usage", lambda days: network.UsageReport(
        [network.Usage("Browser", "c:\\apps\\browser.exe", 3 << 30, 200 << 20)], 4 << 30, 300 << 20))
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
    s.network_tests = []
    s.network_test_ok = False
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    p = networkpage.NetworkPage(c, QHBoxLayout())
    qtbot.addWidget(p)
    p.resize(1100, 800)
    p.show()
    yield p
    p.timer.stop()
    from shiboken6 import delete
    c.tray.hide()
    for timer in c.findChildren(QTimer):
        timer.stop()
    delete(c)


def _texts(widget) -> str:
    return "\n".join(lab.text() for lab in widget.findChildren(QLabel) if lab.isVisibleTo(widget))


def test_the_tool_is_ready_in_computer():
    assert tools.state("network") == tools.AVAILABLE
    assert "network" in tools.SECTION_OF and tools.SECTION_OF["network"].id == "computer"


def test_programs_on_the_internet_and_on_this_network(page, qtbot):
    page.ensure_loaded()
    qtbot.waitUntil(lambda: "c:\\apps\\browser.exe" in page.rows, timeout=5000)
    browser = page.rows["c:\\apps\\browser.exe"]
    assert browser.group == "internet"
    assert "www.example.com" in browser.row.description.text()
    assert page.rows["c:\\apps\\printer.exe"].group == "home"
    assert "system" not in page.rows and "c:\\apps\\local.exe" not in page.rows  # (only waiting, not connected)
    qtbot.mouseClick(browser, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.LeftButton)
    assert browser.more_text.isVisibleTo(browser) and "video.example.com" in browser.more_text.text()
    # (a second look keeps the same rows: nothing flickers)
    page._looked(list(PROGRAMS))
    assert page.rows["c:\\apps\\browser.exe"] is browser


def test_only_ports_other_devices_can_reach_are_listed(page, qtbot):
    page.show_tab("open")
    qtbot.waitUntil(lambda: "system" in page.open_rows, timeout=5000)
    assert "c:\\apps\\local.exe" not in page.open_rows   # (only this PC can reach it)
    row = page.open_rows["system"]
    assert "445" in row.row.description.text() and row.warn_pill.isVisibleTo(row)


def test_new_programs_are_marked_after_the_first_look(page, qtbot, monkeypatch):
    from corefold import networkpage
    page._looked(list(PROGRAMS))   # (the first look: nothing is new)
    assert not page.rows["c:\\apps\\browser.exe"].new_pill.isVisibleTo(page.rows["c:\\apps\\browser.exe"])
    fresh = _prog("c:\\apps\\fresh.exe", "Fresh", [_tcp("8.8.8.8", 443)])
    page._looked(list(PROGRAMS) + [fresh])
    assert page.rows["c:\\apps\\fresh.exe"].new_pill.isVisibleTo(page.rows["c:\\apps\\fresh.exe"])
    assert "c:\\apps\\fresh.exe" in networkpage.read_seen()


def test_windows_counts(page, qtbot):
    page.show_tab("usage")
    qtbot.waitUntil(lambda: page.usage_group.lay.count() == 1, timeout=5000)
    assert page.usage_sum.isVisibleTo(page) and not page.usage_open.isVisibleTo(page)
    page._usage_read(network.UsageReport(None, 1 << 30, 1 << 20))   # (Windows doesn't say who used it)
    assert page.usage_open.isVisibleTo(page) and page.usage_sum.isVisibleTo(page)


def test_the_speed_test_asks_first_and_explains_the_result(page, qtbot, monkeypatch):
    from corefold import sheet
    asked = []

    def run(box):
        asked.append(box.text())
        return 0   # (Cancel)
    monkeypatch.setattr(sheet, "run", run)
    monkeypatch.setattr(network, "metered", lambda: False)
    page.start_test()
    assert asked and "speed.cloudflare.com" in asked[0]
    assert page.test_job is None   # (cancelled: nothing went online)
    result = network.SpeedResult(down=150e6, up=20e6, latency=14, jitter=3, loaded_down=180, loaded_up=40,
                                 used=300 << 20, server="Dubai", when=1.0)
    page._tested(result)
    text = _texts(page.speed)
    assert "Dubai" in text and "150 Mb/s" in text and "Smart Queue" in text   # (busy line: lags)
    assert page.c.settings.network_tests[-1]["down"] == 150e6
    page._tested(network.Stopped())
    assert "stopped" in page.speed.status.text()


def test_rates_read_like_internet_plans():
    from corefold.networkpage import rate_text
    assert rate_text(950e6) == "950 Mb/s" and rate_text(12.4e6) == "12.4 Mb/s" and rate_text(2.5e9) == "2.50 Gb/s"
    assert rate_text(800e3) == "800 kb/s"


def test_the_speed_is_read_off_the_windows_thread(page, qtbot, monkeypatch):
    import threading
    read_on = []

    def adapters():
        read_on.append(threading.current_thread() is threading.main_thread())
        return [network.Adapter(3, "Wi-Fi", "Intel Wi-Fi 6", "wifi", 10 + 1000 * len(read_on), 10, 300_000_000,
                                300_000_000, True)]
    monkeypatch.setattr(network, "adapters", adapters)
    if page.meter_job is not None:   # (the reading started when the page showed)
        qtbot.waitUntil(lambda: not page.meter_job.is_alive(), timeout=5000)
    page.link.setText("")
    page._tick()
    qtbot.waitUntil(lambda: bool(read_on) and "Wi-Fi" in page.link.text(), timeout=5000)
    assert not any(read_on)   # (never on the window's thread)
