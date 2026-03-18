@echo off
:: ─────────────────────────────────────────────────────────────────────────────
:: run_remote.bat — Start the Flare DeFi app accessible from other devices
::
:: HOW TO ACCESS FROM ANOTHER DEVICE (phone, tablet, other PC):
::   1. Run this script on your Windows machine
::   2. Find your local IP:  Settings > Network > Properties > IPv4 address
::      (e.g. 192.168.1.42)
::   3. On the other device, open browser and go to:
::      http://192.168.1.42:8501
::   4. Both devices must be on the same Wi-Fi network
::
:: FOR INTERNET ACCESS (outside your home):
::   Option A — Windows port forward:  netsh interface portproxy add ...
::   Option B — Install ngrok (ngrok.com), then run: ngrok http 8501
::   Option C — Use Tailscale (tailscale.com) for a private VPN tunnel
:: ─────────────────────────────────────────────────────────────────────────────

echo Starting Flare DeFi Model (LAN accessible on port 8501)...
echo.

:: Show local IP to make it easy to connect
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
    set LOCAL_IP=%%a
    goto :found_ip
)
:found_ip
set LOCAL_IP=%LOCAL_IP: =%
echo Your local IP address: %LOCAL_IP%
echo Access from other devices at: http://%LOCAL_IP%:8501
echo.

cd /d "%~dp0"
start "Flare DeFi Scheduler" /min python scheduler.py
timeout /t 3 /nobreak >nul
streamlit run app.py --server.address 0.0.0.0 --server.port 8501

pause
