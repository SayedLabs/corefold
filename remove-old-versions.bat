@echo off
title Satsuma clean-up
rem Removes old Satsuma copies (first version, old project folders, old zip downloads).
rem Asks before deleting. Never touches the installed app or your settings.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\cleanup-old.ps1"
pause
