@echo off
setlocal
title PiShock Standalone Flipper Bridge
cd /d "%~dp0"
set "BRIDGE_EXIT=1"
if not exist "%~dp0.venv\Scripts\python.exe" goto find_system_python
"%~dp0.venv\Scripts\python.exe" -B -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto local_python
:find_system_python
py -3 -B -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto py_launcher
python -B -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto system_python
echo Python 3.10 or newer is needed. See the first-time setup in README.md.
echo Install Python, then install host/requirements.txt before starting the bridge.
goto finished
:local_python
"%~dp0.venv\Scripts\python.exe" -B -u "%~dp0host\standalone.py" %*
set "BRIDGE_EXIT=%ERRORLEVEL%"
goto finished
:py_launcher
py -3 -B -u "%~dp0host\standalone.py" %*
set "BRIDGE_EXIT=%ERRORLEVEL%"
goto finished
:system_python
python -B -u "%~dp0host\standalone.py" %*
set "BRIDGE_EXIT=%ERRORLEVEL%"
:finished
if /I "%~1"=="--help" goto exit_launcher
if /I "%~1"=="-h" goto exit_launcher
echo.
pause
:exit_launcher
exit /b %BRIDGE_EXIT%
