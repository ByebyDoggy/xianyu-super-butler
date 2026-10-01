@echo off
cd /d %~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_service.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_service.ps1"
pause
