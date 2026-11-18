@echo off
rem One-click start: creates the virtualenv on first run, then opens the UI.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Setting up Python environment...
  python -m venv .venv || goto :error
  .venv\Scripts\python.exe -m pip install -q -r requirements.txt || goto :error
)
.venv\Scripts\python.exe ytct.py serve %*
goto :eof
:error
echo Setup failed. Make sure Python 3.10+ is installed and on PATH.
pause
