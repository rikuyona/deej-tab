@echo off
rem Debug: run without the tray icon and show the log in this console
cd /d "%~dp0"
.venv\Scripts\python deej_tab.py --no-tray
pause
