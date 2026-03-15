@echo off
title Run DeFi Scan Now
echo Running a full DeFi scan right now...
echo This will take about 30 seconds.
echo.
python scheduler.py --now
echo.
echo Scan complete. Refresh the Streamlit dashboard to see results.
pause
