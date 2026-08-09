@echo off
rem [警告] 仅供本机旧.venv后端调试；绕开Compose并直接读写本机data/。
rem [警告] 严禁用于MVP验收，监听:8000可能被Flutter误连。
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
