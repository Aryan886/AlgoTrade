@echo off
echo ========================================
echo NIFTY 50 Automation Task Creator
echo ========================================

REM Check if running as administrator
net session >nul 2>&1
if %errorLevel% == 0 (
    echo ✅ Running with administrator privileges
) else (
    echo ❌ ERROR: This script requires administrator privileges
    echo.
    echo To run as administrator:
    echo 1. Right-click on this file (create_tasks.bat)
    echo 2. Select "Run as administrator"
    echo 3. Or run Command Prompt as administrator and navigate here
    echo.
    pause
    exit /b 1
)

REM Get current directory
set PROJECT_DIR=%cd%
echo Project directory: %PROJECT_DIR%

REM Get Python path
for /f "delims=" %%i in ('where python') do set PYTHON_PATH=%%i
echo Python path: %PYTHON_PATH%

REM Check if Python was found
if "%PYTHON_PATH%"=="" (
    echo ❌ ERROR: Python not found in PATH
    echo Please ensure Python is installed and added to PATH
    pause
    exit /b 1
)

REM Check if the Python script exists
if not exist "%PROJECT_DIR%\run_live_data.py" (
    echo ❌ ERROR: run_live_data.py not found in %PROJECT_DIR%
    echo Please ensure the script exists before creating tasks
    pause
    exit /b 1
)

echo.
echo Creating scheduled tasks...

REM Delete existing tasks if they exist
echo Cleaning up existing tasks...
schtasks /delete /tn "NIFTY50_Start" /f >nul 2>&1
schtasks /delete /tn "NIFTY50_Stop" /f >nul 2>&1

REM Create start task
echo Creating start task...
schtasks /create /tn "NIFTY50_Start" /tr "\"%PYTHON_PATH%\" \"%PROJECT_DIR%\\run_live_data.py\"" /sc daily /st 09:15 /ru "%USERNAME%" /f
if %errorLevel% == 0 (
    echo ✅ Start task created successfully
) else (
    echo ❌ Failed to create start task (Error code: %errorLevel%)
    echo This might be due to:
    echo - Insufficient permissions
    echo - Invalid user account
    echo - Path issues
)

REM Create stop task  
echo Creating stop task...
schtasks /create /tn "NIFTY50_Stop" /tr "taskkill /f /im python.exe /fi \"WINDOWTITLE eq run_live_data.py*\"" /sc daily /st 15:30 /ru "%USERNAME%" /f
if %errorLevel% == 0 (
    echo ✅ Stop task created successfully
) else (
    echo ❌ Failed to create stop task (Error code: %errorLevel%)
)

echo.
echo ========================================
echo Verification Steps:
echo ========================================
echo 1. Check Task Scheduler:
echo    - Press Win+R, type "taskschd.msc", press Enter
echo    - Look for "NIFTY50_Start" and "NIFTY50_Stop" tasks
echo.
echo 2. Or use command line:
echo    schtasks /query /tn "NIFTY50*"
echo.
echo 3. To manually test:
echo    schtasks /run /tn "NIFTY50_Start"
echo.
echo 4. To delete tasks if needed:
echo    schtasks /delete /tn "NIFTY50_Start" /f
echo    schtasks /delete /tn "NIFTY50_Stop" /f
echo ========================================
pause 