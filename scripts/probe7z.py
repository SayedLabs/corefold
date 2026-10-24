"""Temporary: how 7-Zip takes a password from stdin (delete before merge)."""
import os, subprocess, sys, tempfile
from pathlib import Path
exe = sys.argv[1] if len(sys.argv) > 1 else r"C:\Program Files\7-Zip\7z.exe"
d = Path(tempfile.mkdtemp())
(d / "a.txt").write_text("hello " * 1000)
print(subprocess.run([exe], capture_output=True).stdout.decode()[:200])
def run(args, stdin=b"", show=True):
    p = subprocess.run([exe, *args], input=stdin, capture_output=True, timeout=60)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    if show:
        print("$", args, "stdin", stdin, "->", p.returncode)
        print("   ", out.strip().replace("\n", "\n    ")[-600:])
    return p.returncode, out
for pw in ["secret1", "كلمة سر", "Pässwörd€"]:
    for fmt in ["zip", "7z"]:
        for enc in ["utf-8", "mbcs-oem"]:
            arc = d / f"t-{fmt}-{abs(hash(pw+enc))}.{fmt}"
            # made the old way (password on the command line): the known-good archive
            run(["a", f"-t{fmt}", f"-p{pw}", "-sccUTF-8", str(arc), str(d / "a.txt")], show=False)
            data = pw.encode("utf-8") if enc == "utf-8" else pw.encode("oem", "replace")
            print(f"== {fmt} pw={pw!r} stdin={enc}")
            run(["t", "-p", "-sccUTF-8", str(arc)], data + b"\n")
            run(["t", "-sccUTF-8", str(arc)], data + b"\n")
            # made with the password from stdin, then tested with the command-line password
            arc2 = d / f"s-{fmt}-{abs(hash(pw+enc))}.{fmt}"
            run(["a", f"-t{fmt}", "-p", "-sccUTF-8", str(arc2), str(d / "a.txt")], data + b"\n" + data + b"\n")
            run(["t", f"-p{pw}", "-sccUTF-8", str(arc2)])
# not encrypted, -p given: does it wait / read?
plain = d / "plain.zip"
run(["a", "-tzip", str(plain), str(d / "a.txt")], show=False)
run(["t", "-p", "-sccUTF-8", str(plain)], b"")
run(["l", "-slt", "-p", "-sccUTF-8", str(plain)], b"")
# wrong password from stdin, and none at all (stdin closed)
arc = d / "w.7z"
run(["a", "-t7z", "-pabc", "-mhe=on", str(arc), str(d / "a.txt")], show=False)
run(["l", "-slt", "-p", "-sccUTF-8", str(arc)], b"nope\n")
run(["l", "-slt", "-p", "-sccUTF-8", str(arc)], b"")
run(["l", "-slt", "-p", "-sccUTF-8", str(arc)], b"abc\n")
run(["e", "-so", "-p", "-sccUTF-8", str(arc), "a.txt"], b"abc\n")
run(["x", "-p", "-y", f"-o{d/'out'}", "-bsp1", "-bse1", "-sccUTF-8", str(arc)], b"abc\n")
