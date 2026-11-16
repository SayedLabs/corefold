"""Writes the text of a release page: what's new in a version, in English and then Arabic, from the notes
kept in assets/release-notes (without their "date:" line). Used by release.yml for the draft release it
puts in the public repository.

    python scripts/release_body.py 2.3.0 dist/release-body.md
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDER = ROOT / "assets" / "release-notes"


def text(name):
    lines = (FOLDER / name).read_text(encoding="utf-8-sig").splitlines()
    while lines and (not lines[0].strip() or lines[0].lower().startswith("date:")):
        lines.pop(0)
    return "\n".join(lines).strip()


def body(version):
    version = version.lstrip("vV")
    parts = [text(f"{version}.md")]
    if (FOLDER / f"{version}.ar.md").is_file():
        parts.append(text(f"{version}.ar.md"))
    return "\n\n---\n\n".join(parts) + "\n"


if __name__ == "__main__":
    out = Path(sys.argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body(sys.argv[1]), encoding="utf-8")
    print(f"release page text for {sys.argv[1]}: {out}")
