"""Looks through the app's code for a widget that's shown before it has a parent: a child widget made
with no parent (QLabel(), QPushButton("…")), then setVisible(True) / show(), and only after that put in a
layout. Until it's in a layout under a window, Qt shows it as a window of its own, for a moment: the small
windows that flash open. (Give it its parent when it's made, or show it after it's laid out.)

Run: python scripts/flashscan.py      Prints each one found and "flashscan: N problem(s)"."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLACES = (".addWidget", ".insertWidget", ".setWidget", ".addRow", ".insertRow", ".setParent", ".setCornerWidget",
          ".addTab", ".insertTab", ".setCellWidget", ".setItemWidget", ".addPermanentWidget", ".setTitleBarWidget")


def calls_in_order(fn):
    return sorted((n for n in ast.walk(fn) if isinstance(n, (ast.Assign, ast.Call))),
                  key=lambda n: (n.lineno, n.col_offset))


def scan(path: Path) -> list[str]:
    found = []
    tree = ast.parse(path.read_text("utf-8"))
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        made = {}       # name -> line, for widgets made here with no parent
        shown = {}      # name -> line where it was shown while it still had none
        placed = set()
        for n in calls_in_order(fn):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and len(n.targets) == 1:
                name = ast.unparse(n.targets[0])
                call = n.value
                has_parent = any(k.arg == "parent" for k in call.keywords) or \
                    any(isinstance(a, ast.Name) and a.id in ("self", "parent") for a in call.args)
                made.pop(name, None)
                shown.pop(name, None)
                placed.discard(name)
                if not has_parent:
                    made[name] = n.lineno
                continue
            if not isinstance(n, ast.Call):
                continue
            func = n.func
            if not isinstance(func, ast.Attribute):
                continue
            text = ast.unparse(func)
            if text.endswith(PLACES):
                for a in n.args:
                    a = ast.unparse(a)
                    if a in shown and a not in placed:
                        found.append(f"{path.relative_to(ROOT)}:{shown[a]} {a} is shown before it's laid out "
                                     f"(line {n.lineno}): it flashes as a window of its own")
                    placed.add(a)
                continue
            obj = ast.unparse(func.value)
            if obj in made and obj not in placed and obj not in shown:
                arg = ast.unparse(n.args[0]) if n.args else ""
                if (func.attr == "show" or (func.attr == "setVisible" and arg != "False")
                        or (func.attr == "setHidden" and arg != "True")):
                    shown[obj] = n.lineno
    return found


problems = []
for p in sorted((ROOT / "corefold").rglob("*.py")):
    problems += scan(p)
for line in problems:
    print("FAIL  " + line)
print(f"flashscan: {len(problems)} problem(s)")
sys.exit(1 if problems else 0)
