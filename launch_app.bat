@echo off
setlocal
title Miscoshorts AI Launcher
cd /d %~dp0
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"
exit /b %errorlevel%