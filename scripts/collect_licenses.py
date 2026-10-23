"""Copies the license files of every Python package in a Corefold build into one folder.

Used by scripts/build.ps1 (run with the build's own Python): python scripts/collect_licenses.py <folder>
Each package gets <folder>/<name>-<version>/ with its LICENSE, COPYING, NOTICE and AUTHORS files, so
the copyright notices that MIT, BSD and Apache ask us to pass on ship next to Corefold.exe.
"""
import re
import shutil
import sys
from importlib import metadata
from pathlib import Path

# only used to build, not shipped
SKIP = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile",
        "pywin32-ctypes", "packaging"}
LICENSE_NAME = re.compile(r"^(licen[cs]e|copying|notice|authors|copyright)", re.I)

out = Path(sys.argv[1] if len(sys.argv) > 1 else "licenses-python")
out.mkdir(parents=True, exist_ok=True)
missing = []
for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
    name = dist.metadata["Name"] or ""
    if not name or name.lower() in SKIP:
        continue
    found = [f for f in (dist.files or []) if LICENSE_NAME.match(Path(str(f)).name)
             or "licenses" in Path(str(f)).parts[:-1]]
    if not found:
        missing.append(name)
        continue
    dest = out / f"{name}-{dist.version}"
    dest.mkdir(exist_ok=True)
    for f in found:
        src = Path(dist.locate_file(f))
        if src.is_file():
            target = dest / Path(str(f)).name
            if target.exists():  # (two files of the same name, e.g. a bundled library's own LICENSE)
                target = dest / "_".join(Path(str(f)).parts[-2:])
            shutil.copyfile(src, target)
for name in missing:
    print(f"no license file in the package of {name}: see THIRD_PARTY_NOTICES.md", flush=True)
