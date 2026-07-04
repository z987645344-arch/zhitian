@echo off
setlocal
cd /d "%~dp0"

echo Starting Zhitian backend...
if not exist ".venv\Scripts\activate.bat" (
    echo Missing .venv\Scripts\activate.bat. Please create backend virtual environment first.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python main.py
pause
