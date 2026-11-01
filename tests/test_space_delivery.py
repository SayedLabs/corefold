"""Corefold Space: the update copies Windows keeps for other PCs (Delivery Optimization)."""
import subprocess

from corefold import space
from corefold import uninstall as un


def test_delivery_size_is_what_windows_says(monkeypatch):
    scripts = []
    monkeypatch.setattr(un, "powershell", lambda script, **k: scripts.append(script) or "2034602224\r\n")
    assert space.delivery_size() == 2034602224
    assert "Get-DeliveryOptimizationPerfSnap" in scripts[0]
    for said in ("", "not a number", "-5"):
        monkeypatch.setattr(un, "powershell", lambda script, said=said, **k: said)
        assert space.delivery_size() == 0

    def fails(script, **k):
        raise subprocess.TimeoutExpired("powershell", 30)
    monkeypatch.setattr(un, "powershell", fails)
    assert space.delivery_size() == 0


def _only_delivery(monkeypatch, tmp_path, size):
    monkeypatch.setattr(space.os, "name", "nt")
    monkeypatch.setattr(space, "admin_path", lambda key: tmp_path / "missing" if key != "delivery" else tmp_path)
    monkeypatch.setattr(space, "delivery_size", lambda: size)
    return [j for j in space._admin_junk() if j.key == "delivery"]


def test_offered_unticked_for_an_administrator_from_50_mb(monkeypatch, tmp_path):
    found = _only_delivery(monkeypatch, tmp_path, 2 << 30)
    assert len(found) == 1
    j = found[0]
    assert j.size == 2 << 30 and j.admin and not j.ticked and not j.to_bin
    assert _only_delivery(monkeypatch, tmp_path, space.DELIVERY_MIN - 1) == []
    assert _only_delivery(monkeypatch, tmp_path, 0) == []


def test_clean_asks_windows_and_touches_no_file_itself(monkeypatch, tmp_path):
    cache = tmp_path / "Cache"
    cache.mkdir()
    (cache / "piece").write_bytes(b"x" * 4096)
    scripts = []

    def windows_cleans(script, **k):
        scripts.append(script)
        (cache / "piece").unlink()
        return ""
    monkeypatch.setattr(space, "admin_path", lambda key: cache if key == "delivery" else None)
    monkeypatch.setattr(un, "powershell", windows_cleans)
    monkeypatch.setattr(space, "_remove", lambda p: (_ for _ in ()).throw(AssertionError("removed by hand")))
    assert space.clean_as_admin(["delivery"]) == 4096
    assert scripts == ["Delete-DeliveryOptimizationCache -Force -ErrorAction Stop"]
    assert "delivery" in space.ADMIN_KEYS                     # (the administrator copy only takes these)
