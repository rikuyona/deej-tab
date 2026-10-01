@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv || goto :err
)
.venv\Scripts\python -c "import serial, websockets, yaml, pycaw, comtypes, psutil, pystray, PIL, webview" 2>nul || (
  .venv\Scripts\python -m pip install --disable-pip-version-check -r requirements.txt || goto :err
)
rem Run in the system tray (no console window). Log: deej-tab.log
start "" .venv\Scripts\pythonw.exe deej_tab.py
exit /b
:err
echo.
echo Setup failed. Check the messages above.
pause
