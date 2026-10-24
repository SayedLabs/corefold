"""Runs a test script inside a PyInstaller build made like Corefold.exe (same packed parts), so the tests try
what people really get: frozenrun.exe <script.py> [its arguments...]"""
import runpy
import sys

if __name__ == "__main__":
    script = sys.argv[1]
    sys.argv = [script, *sys.argv[2:]]
    runpy.run_path(script, run_name="__main__")
