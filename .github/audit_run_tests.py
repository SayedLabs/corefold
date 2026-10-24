"""TEMPORARY (audit run only, deleted before the PR): runs each test with a time limit and shows its exit code,
the same two on main too, to see if a non-zero exit was already there."""
import subprocess
import sys

RUNS = [("main", "organizetest", ["out/organizetest", "en", "dark"]),
        ("branch", "organizetest", ["out/organizetest", "en", "dark"]),
        ("main", "newtest_basics", []), ("branch", "newtest_basics", []),
        ("branch", "check_translations", []), ("branch", "featuretest", []), ("branch", "edgetest", []),
        ("branch", "newtest_cloud", []), ("branch", "newtest_emptyfolders", []), ("branch", "newtest_unlock", [])]
fails = []
for where, test, args in RUNS:
    cwd = r"..\main" if where == "main" else "."
    try:
        r = subprocess.run([sys.executable, f"scripts/{test}.py", *args], cwd=cwd, timeout=600, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        code, out = r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        code, out = "TIMEOUT", e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
    hexed = f" (0x{code & 0xFFFFFFFF:08X})" if isinstance(code, int) else ""
    print(f"== {where} {test}: exit {code}{hexed}")
    for line in [ln for ln in out.splitlines() if "PROBLEM" in ln or "Traceback" in ln or "Error" in ln][-12:]:
        print("   " + line[:200])
    print("   last: " + " | ".join(out.splitlines()[-3:])[:300], flush=True)
    if where == "branch" and code != 0:
        fails.append(test)
print(f"FAILED {fails}" if fails else "all branch tests exit 0")
sys.exit(1 if fails else 0)
