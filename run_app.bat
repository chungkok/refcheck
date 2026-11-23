@echo off
REM Start RefCheck in your browser (Windows).
REM First run: creates a private Python environment in .venv with the tested versions.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install -q --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -q -r requirements-lock.txt || goto :error
)
if "%REFCHECK_DEPLOYMENT%"=="" set REFCHECK_DEPLOYMENT=local
".venv\Scripts\python.exe" -m streamlit run app.py --server.address=127.0.0.1
goto :eof
:error
echo Installation failed. Is Python 3.10 or newer installed and on the PATH?
pause
