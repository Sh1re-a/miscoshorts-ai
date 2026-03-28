@echo off
setlocal
title Miscoshorts AI Launcher
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d %~dp0
where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo PowerShell could not be found on this machine.
  echo Install or restore Windows PowerShell, then run this launcher again.
  pause
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"
exit /b %errorlevel%
