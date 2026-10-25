"""The release gate's check of the notes kept with Corefold (assets/release-notes): every file reads, has its
day and some words; and the version being released has its notes, in English and Arabic, under the same
version Corefold is built as.

    python scripts/release_notes_check.py            every file
    python scripts/release_notes_check.py v1.11.0    and that version's
Prints one line per problem, then "release_notes_check: N problem(s)".
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from corefold import releasenotes  # noqa: E402

problems = []
folder = ROOT / "assets" / "release-notes"
for f in sorted(folder.glob("*.md")):
    m = re.fullmatch(r"(\d{1,4}(?:\.\d{1,6}){1,3})(?:\.([a-z]{2}))?\.md", f.name)
    if not m:
        problems.append(f"{f.name}: not named <version>.md or <version>.<language>.md")
        continue
    version, lang = m.groups()
    notes = releasenotes.kept(version, lang or "en")
    if notes is None or notes.empty():
        problems.append(f"{f.name}: no changes in it")
    elif not lang and not notes.date:
        problems.append(f"{f.name}: no 'date: YYYY-MM-DD' line at the top")
    if lang and not (folder / f"{version}.md").is_file():
        problems.append(f"{f.name}: there's no English {version}.md beside it")

if len(sys.argv) > 1:
    version = sys.argv[1].lstrip("vV")
    if re.fullmatch(r"\d{1,4}(\.\d{1,6}){1,3}", version):  # (a test build like 0.0.12-dev has no notes)
        for name in (f"{version}.md", f"{version}.ar.md"):
            if not (folder / name).is_file():
                problems.append(f"{name} is missing: write what's new in {version} before releasing it")

for p in problems:
    print("PROBLEM " + p)
print(f"release_notes_check: {len(problems)} problem(s)")
sys.exit(1 if problems else 0)
