"""The Uninstaller follows only programs its uninstaller started: a program that keeps an old parent id
(Windows gives ended processes' ids out again) is never waited for, shown or stopped."""
from corefold import uninstall as un

BORN = {1000: 500, 2000: 900, 3000: 100}  # handle: when its process started (100 ns steps)


class FakeKernel:
    def GetProcessId(self, h):
        return h // 100

    def OpenProcess(self, access, inherit, pid):
        return pid * 100

    def GetProcessTimes(self, h, created, *_):
        created._obj.value = BORN.get(h, 0)
        return 1

    def WaitForSingleObject(self, h, ms):
        return 0x102  # still running

    def CloseHandle(self, h):
        self.closed.append(h)
        return 1


def test_old_program_with_a_reused_parent_id_isnt_followed(monkeypatch):
    k = FakeKernel()
    k.closed = []
    monkeypatch.setattr(un, "_dll", lambda name: k)
    # the uninstaller is process 10 (started at 500); process 20 is its copy (started at 900); process 30
    # started at 100, by an earlier process that also had the id 10
    monkeypatch.setattr(un, "processes", lambda: [(10, 1, "uninst.exe"), (20, 10, "au_.exe"),
                                                  (30, 10, "notepad.exe")])
    procs = un.Processes(1000)
    procs.refresh()
    assert sorted(procs.alive()) == [10, 20]
    assert 3000 in k.closed  # its handle isn't kept


def test_a_child_whose_start_time_is_unknown_is_still_followed(monkeypatch):
    k = FakeKernel()
    k.closed = []
    monkeypatch.setattr(un, "_dll", lambda name: k)
    monkeypatch.setattr(un, "processes", lambda: [(10, 1, "uninst.exe"), (40, 10, "helper.exe")])
    procs = un.Processes(1000)
    procs.refresh()
    assert sorted(procs.alive()) == [10, 40]
