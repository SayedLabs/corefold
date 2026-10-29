"""A job's files are measured in the job's own thread, not as it starts: a drive that's slow to answer (or a file
being scanned) held the window 1 s when a job was started."""
import threading
from unittest import mock

from corefold import app as A
from corefold.converters import Result


def test_the_files_are_measured_in_the_jobs_thread(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(b"x" * 300)
    b.write_bytes(b"x" * 200)
    window = threading.get_ident()
    measured_in = []
    real = A.size_of

    def size_of(p):
        measured_in.append(threading.get_ident())
        return real(p)
    job, done = {"bytes_in": 0}, []
    signals = A.JobSignals()
    signals.finished.connect(lambda *args: done.append(args), type=A.Qt.ConnectionType.DirectConnection)
    work = A.Job(1, mock.Mock(), [a, b], mock.Mock(), signals, threading.Event(), {}, job)
    with mock.patch.object(A, "size_of", size_of), mock.patch.object(A, "run_job", return_value=(Result(), [])):
        t = threading.Thread(target=work.run)
        t.start()
        t.join(10)
    assert job["bytes_in"] == 500 and done
    assert measured_in and window not in measured_in

    cancel = threading.Event()  # (cancelled while it waited: nothing is measured)
    cancel.set()
    job = {"bytes_in": 0}
    with mock.patch.object(A, "size_of", side_effect=AssertionError("measured")):
        A.Job(2, mock.Mock(), [a], mock.Mock(), signals, cancel, {}, job).run()
    assert job["bytes_in"] == 0
