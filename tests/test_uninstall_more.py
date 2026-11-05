"""Corefold Uninstaller: extensions a program forces into a browser (found in the registry, removed only through
their own entry), and watching an install (what it put in is offered when the app is uninstalled)."""
import os
from pathlib import Path

import pytest

from corefold import extensions, installtrack
from corefold import uninstall as un

ID = "abcdefghijklmnopabcdefghijklmnop"
ID2 = "ponmlkjihgfedcbaponmlkjihgfedcba"


def _fake_registry(data: dict):
    """data: {(hive, view, key): {"values": {...}, "subkeys": [...]}}"""
    def values(hive, key, view):
        return dict(data.get((hive, view, key.lower()), {}).get("values", {}))

    def subkeys(hive, key, view):
        return list(data.get((hive, view, key.lower()), {}).get("subkeys", []))
    return values, subkeys


def test_finds_policy_and_registry_forced_extensions():
    reg = {
        ("HKLM", 64, r"software\policies\google\chrome\extensioninstallforcelist"):
            {"values": {"1": f"{ID};https://evil.example/update.xml"}},
        ("HKCU", 0, r"software\microsoft\edge\extensions"): {"subkeys": [ID2, "not-an-id"]},
        ("HKCU", 0, rf"software\microsoft\edge\extensions\{ID2}"): {"values": {"path": r"C:\Bad\ext.crx"}},
    }
    found = extensions.find_forced(_fake_registry(reg))
    by = {(f.browser, f.id): f for f in found}
    assert set(by) == {("Google Chrome", ID), ("Microsoft Edge", ID2)}
    chrome = by[("Google Chrome", ID)]
    assert chrome.kind == "policy" and chrome.needs_admin and chrome.value == "1" and chrome.view == 64
    edge = by[("Microsoft Edge", ID2)]
    assert edge.kind == "external" and not edge.needs_admin and edge.by == r"C:\Bad\ext.crx"


def test_only_forced_entries_may_be_removed():
    ok = extensions.forced_ok
    assert ok({"hive": "HKLM", "key": r"Software\Policies\Google\Chrome\ExtensionInstallForcelist", "value": "1"})
    assert ok({"hive": "HKCU", "key": rf"Software\Google\Chrome\Extensions\{ID}", "value": ""})
    assert not ok({"hive": "HKLM", "key": r"Software\Policies\Google\Chrome", "value": "1"})
    assert not ok({"hive": "HKCU", "key": r"Software\Google\Chrome\Extensions", "value": ""})  # (all of them)
    assert not ok({"hive": "HKCU", "key": r"Software\Google\Chrome\Extensions\..\..\Run", "value": ""})
    assert not ok({"hive": "HKLM", "key": r"Software\Microsoft\Windows\CurrentVersion\Run", "value": "x"})
    assert not ok({"hive": "HKCR", "key": rf"Software\Google\Chrome\Extensions\{ID}", "value": ""})
    assert not ok({"hive": "HKCU", "key": r"Software\Google\Chrome\Extensions\short", "value": ""})


def test_administrator_entries_go_through_the_helper(monkeypatch):
    removed = []
    monkeypatch.setattr(extensions, "remove_entry", lambda item: removed.append(item["key"]) or True)
    asked = []
    items = [extensions.Forced("Google Chrome", ID, "HKLM", r"Software\Policies\Google\Chrome\ExtensionInstallForcelist",
                               "1", 64, ID),
             extensions.Forced("Microsoft Edge", ID2, "HKCU", rf"Software\Microsoft\Edge\Extensions\{ID2}")]
    done, failed = extensions.remove_forced(items, lambda payload: asked.append(payload) or {"done": 1})
    assert (done, failed) == (2, 0)
    assert removed == [rf"Software\Microsoft\Edge\Extensions\{ID2}"]
    assert asked[0]["items"][0]["hive"] == "HKLM"
    done, failed = extensions.remove_forced(items[:1], lambda payload: None)  # (said no to the administrator)
    assert (done, failed) == (0, 1)


def test_helper_knows_the_new_kinds():
    assert "--browser-ext" in un.HELPER_KINDS
    main = (Path(__file__).parent.parent / "main.py").read_text("utf-8")
    assert '"--browser-ext"' in main


# ----------------------------------------------------------------------------- watching an install

@pytest.fixture
def places(tmp_path, monkeypatch):
    monkeypatch.setattr(installtrack, "TRACES", tmp_path / "traces")
    pf = tmp_path / "Program Files"
    (pf / "Old App").mkdir(parents=True)
    menu = tmp_path / "Start Menu"
    menu.mkdir()
    reg = {("HKLM", 64, "software"): {"subkeys": ["OldVendor"]}}
    folders = [(str(pf), 2), (str(menu), 2)]
    return pf, menu, reg, folders


def _kw(reg, folders):
    values, subkeys = _fake_registry(reg)
    return {"subkeys": subkeys, "values": values, "folders": folders}


def test_watching_an_install_keeps_only_what_it_put_in(places):
    pf, menu, reg, folders = places
    installtrack.start(**_kw(reg, folders))
    assert installtrack.waiting()
    # the install: its folder (with many files inside), a shortcut under another name, keys, an uninstall entry
    (pf / "Acme Tools" / "bin").mkdir(parents=True)
    (pf / "Acme Tools" / "bin" / "a.dll").write_bytes(b"x")
    (menu / "Launcher.lnk").write_bytes(b"lnk")
    reg[("HKLM", 64, "software")] = {"subkeys": ["OldVendor", "AcmeCorp"]}
    reg[("HKLM", 64, r"software\acmecorp")] = {"subkeys": ["Helper"]}
    reg[("HKLM", 64, r"software\microsoft\windows\currentversion\uninstall")] = {"subkeys": ["AcmeTools"]}
    reg[("HKCU", 0, r"software\microsoft\windows\currentversion\run")] = {"values": {"AcmeHelper": "x.exe"}}
    trace = installtrack.finish(lambda hive, view, key: "Acme Tools" if key == "AcmeTools" else "",
                                **_kw(reg, folders))
    items = trace["items"]
    assert f"folder|{pf / 'Acme Tools'}" in items
    assert not any("bin" in i for i in items)  # (inside the new folder: covered by it)
    assert f"file|{menu / 'Launcher.lnk'}" in items
    assert "key|HKLM|64|Software\\AcmeCorp" in items and not any("AcmeCorp\\Helper" in i for i in items)
    assert "value|HKCU|0|Software\\Microsoft\\Windows\\CurrentVersion\\Run|AcmeHelper" in items
    assert trace["apps"] == ["Acme Tools"] and trace["entries"] == [["HKLM", 64, "AcmeTools"]]
    assert not any("Old" in i for i in items)
    assert not installtrack.waiting() and len(installtrack.traces()) == 1


def test_the_trace_is_offered_when_the_app_goes(places, monkeypatch):
    pf, menu, reg, folders = places
    installtrack.start(**_kw(reg, folders))
    (pf / "Acme Tools").mkdir()
    (menu / "Launcher.lnk").write_bytes(b"lnk")
    reg[("HKLM", 64, r"software\microsoft\windows\currentversion\uninstall")] = {"subkeys": ["AcmeTools"]}
    reg[("HKLM", 64, "software")] = {"subkeys": ["OldVendor", "AcmeCorp"]}
    installtrack.finish(lambda *a: "Acme Tools", **_kw(reg, folders))
    monkeypatch.setattr(un, "key_exists", lambda hive, path, view: path == "Software\\AcmeCorp")
    app = un.App("x", "Acme Tools", "AcmeCorp", key="AcmeTools", hive="HKLM", view=64,
                 location=str(pf / "Acme Tools"))
    got = {(i.kind, i.path, i.level) for i in installtrack.leftovers_for(app)}
    assert ("folder", str(pf / "Acme Tools"), 3) in got
    assert ("shortcut", str(menu / "Launcher.lnk"), 2) in got  # (another name: most likely, not surely)
    assert ("key", "HKLM\\Software\\AcmeCorp", 2) in got  # (the maker's key may hold its other apps)
    assert all(i.reason == "tracked" for i in installtrack.leftovers_for(app))
    other = un.App("y", "Something Else", key="Other", hive="HKLM", view=64)
    assert installtrack.leftovers_for(other) == []
    (menu / "Launcher.lnk").unlink()  # (gone already: not offered)
    assert not any(i.kind == "shortcut" for i in installtrack.leftovers_for(app))


def test_nothing_waiting_gives_nothing(places):
    assert installtrack.finish() is None
    installtrack.cancel()
    assert "tracked" in un.REASONS


def test_watch_dialog_builds(qapp, places):
    from PySide6.QtGui import QIcon
    from corefold.appspage import WatchInstallDialog
    dlg = WatchInstallDialog(QIcon())
    assert dlg.b_start.isVisibleTo(dlg) and not dlg.b_done.isVisibleTo(dlg)
    dlg.deleteLater()


@pytest.mark.skipif(os.name != "nt", reason="Windows' registry")
def test_real_snapshot_on_windows():
    snap = installtrack.snapshot()
    assert any(s.startswith("key|HKLM|") for s in snap) and any(s.startswith("folder|") for s in snap)
    assert not any("\\microsoft|" in s.lower() or s.lower().endswith("\\microsoft") for s in snap)


@pytest.mark.skipif(os.name != "nt", reason="Windows' registry")
def test_real_forced_extension_entry_is_found_and_removed():
    import winreg
    key = rf"Software\Google\Chrome\Extensions\{ID}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        winreg.SetValueEx(k, "update_url", 0, winreg.REG_SZ, "https://example.invalid/update.xml")
    try:
        found = [f for f in extensions.find_forced() if f.id == ID and f.hive == "HKCU"]
        assert len(found) == 1 and found[0].kind == "external"
        assert extensions.remove_forced(found) == (1, 0)
        with pytest.raises(OSError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, key)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome"):
            pass  # (only that extension's own key went)
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass


def test_extensions_dialog_shows_forced_ones(qapp, monkeypatch):
    from PySide6.QtGui import QIcon
    from corefold.appspage import ExtensionsDialog
    ext = extensions.Extension("Google Chrome", "chrome.exe", "", "Default", ID, "Coupons Helper")
    monkeypatch.setattr(extensions, "find", lambda: [ext])
    monkeypatch.setattr(extensions, "find_forced", lambda: [
        extensions.Forced("Google Chrome", ID, "HKLM", r"Software\Policies\Google\Chrome\ExtensionInstallForcelist",
                          "1", 64, ID),
        extensions.Forced("Microsoft Edge", ID2, "HKCU", rf"Software\Microsoft\Edge\Extensions\{ID2}")])
    dlg = ExtensionsDialog(QIcon())
    assert dlg.tree.topLevelItemCount() == 2
    texts = [dlg.tree.topLevelItem(i).text(0) for i in range(2)]
    assert all("(forced by a program)" in t for t in texts)
    dlg.tree.topLevelItem(0).setSelected(True)
    assert dlg.b_unforce.isEnabled() and not dlg.b_remove.isEnabled()
    dlg.deleteLater()
