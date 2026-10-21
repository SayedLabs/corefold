@echo off
title Building Satsuma
cd /d "%~dp0"
set VER=%~1
if "%VER%"=="" set VER=1.5.0

echo Building Satsuma %VER%. The first time takes a few minutes...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build.ps1" -Version %VER%
if errorlevel 1 (
  echo.
  echo The build stopped because of an error. Take a screenshot of this window and ask for help.
  pause
  exit /b 1
)

set "SETUP=%~dp0dist\release\Satsuma-Setup-%VER%.exe"
if not exist "%SETUP%" (
  echo.
  echo The app was built but the installer wasn't. Your files are in dist\release.
  explorer "%~dp0dist\release"
  pause
  exit /b 1
)

echo.
echo ============================================================
echo   Installer ready:  dist\release\Satsuma-Setup-%VER%.exe
echo   Share that file (or the portable zip next to it).
echo ============================================================
echo.
choice /c YN /m "Install Satsuma on this PC now"
if errorlevel 2 (
  explorer /select,"%SETUP%"
) else (
  start "" "%SETUP%"
)
