"""A job's signals are let go on the window's thread, once its answer is in and its thread is done with them.
When the last reference went on the job's thread, Qt could still be handing the window an event for them and
read what was already freed: the test run crashed now and then, right after a job ended."""
import threading
import weakref

from corefold import work


def test_a_jobs_signals_go_on_the_windows_thread(qtbot):
    answers, gone_on = [], []

    def fn(progress):
        progress(1)
        raise ValueError("an error keeps the job's frames, and its signals through them")
    job = work.run(fn, lambda _p: None, answers.append)
    sig = job.box[0]
    weakref.finalize(sig, lambda: gone_on.append(threading.current_thread()))
    del sig
    qtbot.waitUntil(lambda: bool(answers) and not job.is_alive(), timeout=5000)
    assert isinstance(answers[0], ValueError)
    answers.clear()  # (the window let go of the answer too)
    qtbot.waitUntil(lambda: bool(gone_on), timeout=5000)
    assert gone_on == [threading.main_thread()]
    assert job.box == []


def test_many_jobs_end_without_trouble(qtbot):
    done = []
    jobs = [work.run(lambda p, i=i: i, lambda _p: None, done.append) for i in range(200)]
    qtbot.waitUntil(lambda: len(done) == 200, timeout=10000)
    assert sorted(done) == list(range(200))
    qtbot.waitUntil(lambda: all(j.box == [] for j in jobs), timeout=5000)
