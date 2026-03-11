@echo off
cd /d %~dp0
powershell -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"