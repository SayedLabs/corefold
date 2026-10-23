"""Temporary proof for the scan tool's review (not part of the app): the administrator command line built
by scan._mpcmdrun (branch of the scan tool) runs a command hidden in a quarantined file's name."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from corefold.uninstall import shell_run, wait_process  # noqa: E402

pwned = Path(os.environ.get("SystemDrive", "C:") + "\\corefold-pwned")
if pwned.exists():
    pwned.rmdir()
exe = os.path.join(os.environ["SystemRoot"], "System32", "hostname.exe")  # stands in for MpCmdRun.exe
# a file name Windows allows: no spaces, so scan._mpcmdrun leaves it unquoted
args = ["-Restore", "-FilePath", r"C:\Users\Public\Downloads\invoice&md;" + str(pwned) + "&.exe"]
fd, out = tempfile.mkstemp(prefix="corefold-defender-", suffix=".txt")
os.close(fd)
quoted = " ".join(f'"{a}"' if " " in a or not a else a for a in args)        # copied from scan._mpcmdrun
cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
handle = shell_run(cmd, f'/c ""{exe}" {quoted} > "{out}" 2>&1"', admin=False, show=0)  # (admin=True there)
wait_process(handle, timeout_s=30)
print("scan._mpcmdrun way: the name ran a command:", pwned.exists())
if pwned.exists():
    pwned.rmdir()
always = " ".join(f'"{a}"' for a in args)                                        # every argument quoted
handle = shell_run(cmd, f'/c ""{exe}" {always} > "{out}" 2>&1"', admin=False, show=0)
wait_process(handle, timeout_s=30)
print("every argument quoted: the name ran a command:", pwned.exists())
