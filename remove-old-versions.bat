@echo off
title Corefold clean-up
rem Removes old copies from when Corefold was called Satsuma (first version, old project folders, old zip downloads).
rem Asks before deleting. Never touches the installed app or your settings.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\cleanup-old.ps1"
pause
