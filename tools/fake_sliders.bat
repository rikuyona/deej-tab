@echo off
cd /d "%~dp0"
start "" ..\app\.venv\Scripts\pythonw.exe fake_sliders.py
