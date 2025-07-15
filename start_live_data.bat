@echo off
echo Starting NIFTY 50 Live Data Automation...
echo.

REM Activate virtual environment if it exists
if exist "venv\Scripts\activate.bat" (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
)

REM Run the live data automation
python run_live_data.py

echo.
echo Press any key to exit...
pause >nul 