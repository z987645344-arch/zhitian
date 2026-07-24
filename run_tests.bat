@echo off
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo venv missing. Create it and install requirements first: .venv\Scripts\python.exe -m pip install -r requirements.txt 1>&2
    exit /b 1
)

"%PY%" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 10) else 1)"
if errorlevel 1 (
    echo Python 3.10 venv is required. The current interpreter version does not match. 1>&2
    exit /b 1
)

set "HAS_MARKER="
for %%A in (%*) do (
    if /I "%%~A"=="-m" set "HAS_MARKER=1"
)

if defined HAS_MARKER (
    "%PY%" -m pytest %*
) else (
    "%PY%" -m pytest -m "not integration" %*
)

exit /b %ERRORLEVEL%
