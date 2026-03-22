@echo off
cd /d %~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"

if errorlevel 1 (
	echo.
	echo Setup failed. Read the messages above, then press any key to close this window.
	pause >nul
)