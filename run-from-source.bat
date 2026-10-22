@echo off
rem For testing changes without building. Needs Python.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if not exist "assets\icon.png" ".venv\Scripts\python.exe" scripts\make_icon.py
start "" ".venv\Scripts\pythonw.exe" main.py
