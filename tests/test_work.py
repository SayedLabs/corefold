"""work.run: slow things run on their own thread, their answer comes back on the window's thread, and the
window's thread never waits for Windows to start a thread (that held it up to 3 s after a big sort)."""
import threading

from PySide6.QtCore import QObject

from corefold import work


class Page(QObject):
    """Stands in for a page: its methods get the answers (on the window's thread, as on a real page)."""

    def __init__(self):
        super().__init__()
        self.steps, self.answers, self.where = [], [], []

    def step(self, n):
        self.steps.append(n)

    def answer(self, result):
        self.answers.append(result)
        self.where.append(threading.current_thread() is threading.main_thread())


def test_a_job_runs_on_its_own_thread_and_answers_on_the_windows_thread(qtbot):
    page, seen = Page(), []

    def fn(progress):
        seen.append(threading.current_thread().name)
        progress(1)
        return 42
    job = work.run(fn, page.step, page.answer, name="corefold-test-job")
    qtbot.waitUntil(lambda: page.answers == [42], timeout=5000)
    assert seen == ["corefold-test-job"] and page.steps == [1] and page.where == [True]
    job.join(5)
    assert not job.is_alive()


def test_an_error_is_the_answer_and_busy_ends(qtbot):
    page = Page()

    def fn(_progress):
        raise OSError("disk gone")
    job = work.run(fn, page.step, page.answer, busy=True)
    qtbot.waitUntil(lambda: bool(page.answers), timeout=5000)
    assert isinstance(page.answers[0], OSError) and not work.busy()
    job.join(5)
    assert not job.is_alive()


def test_the_windows_thread_never_starts_the_jobs_thread(qtbot, monkeypatch):
    page = Page()
    first = work.run(lambda _p: None, page.step, page.answer)  # (the starter is there from the first job on)
    first.join(5)
    starters = []
    real = threading.Thread.start

    def start(self):
        starters.append(threading.current_thread().name)
        real(self)
    monkeypatch.setattr(threading.Thread, "start", start)
    job = work.run(lambda _p: 1, page.step, page.answer, name="corefold-test-late")
    qtbot.waitUntil(lambda: page.answers[-1:] == [1], timeout=5000)
    job.join(5)
    assert starters == ["corefold-starter"]


def test_background_work_is_started_by_the_starter_too(monkeypatch):
    first = threading.Event()
    work.background(first.set)  # (the starter is there from then on)
    assert first.wait(5)
    ran = threading.Event()
    starters, names = [], []
    real = threading.Thread.start

    def start(self):
        starters.append(threading.current_thread().name)
        real(self)
    monkeypatch.setattr(threading.Thread, "start", start)

    def fn():
        names.append(threading.current_thread().name)
        ran.set()
    work.background(fn, "corefold-test-sound")
    assert ran.wait(5)
    assert starters == ["corefold-starter"] and names == ["corefold-test-sound"]
