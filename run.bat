@echo off
title Flare DeFi Model

echo ============================================================
echo   Flare DeFi Model — Starting Up
echo ============================================================
echo.

REM Check Python
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python not found. Please install Python 3.10+
    pause
    exit /b 1
)

REM Install / check dependencies
echo Checking dependencies...
pip install -r requirements.txt --quiet --disable-pip-version-check

echo.
echo Starting scheduler in background (scans at 6am + 6pm)...
start "DeFi Scheduler" /min cmd /c "python scheduler.py >> data\scheduler.log 2>&1"

echo Starting Streamlit dashboard...
echo.
echo Your browser will open automatically.
echo To stop everything, close both windows.
echo.
python -m streamlit run app.py --server.port 8501 --server.headless false

pause
