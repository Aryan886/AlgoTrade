
# NIFTY 50 Automation Task Creator (PowerShell)
# Run this script as Administrator for sleep-resistant scheduled tasks

param(
    [switch]$Force
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "NIFTY 50 Automation Task Creator (Sleep-Resistant)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Check for admin rights
if (-NOT ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "❌ ERROR: This script requires administrator privileges" -ForegroundColor Red
    Write-Host "To run as administrator:" -ForegroundColor Yellow
    Write-Host "1. Right-click on this file (create_tasks.ps1)" -ForegroundColor Yellow
    Write-Host "2. Select 'Run with PowerShell'" -ForegroundColor Yellow
    Write-Host "3. Or run PowerShell as administrator and navigate here" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "✅ Running with administrator privileges" -ForegroundColor Green

# Get current directory
$ProjectDir = Get-Location
Write-Host "Project directory: $ProjectDir" -ForegroundColor Yellow

# Find python.exe
$PythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonPath) {
    Write-Host "❌ ERROR: Python not found in PATH" -ForegroundColor Red
    Write-Host "Please ensure Python is installed and added to PATH" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "✅ Python found at $PythonPath" -ForegroundColor Green

# Define Task Name
$TaskName = "NIFTY50_Start"

# Define script path
$ScriptPath = Join-Path $ProjectDir "run_live_data.py"

# Escape backslashes for XML
$EscapedScriptPath = $ScriptPath -replace '\', '\\'
$EscapedWorkingDir = $ProjectDir -replace '\', '\\'

# Create scheduled task XML with working directory
$TaskXML = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$PythonPath</Command>
      <Arguments>`"$EscapedScriptPath`"</Arguments>
      <WorkingDirectory>$EscapedWorkingDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

# Save to XML file
$TempXML = "$env:TEMP\nifty50_task.xml"
$TaskXML | Out-File -FilePath $TempXML -Encoding Unicode

try {
    if ($Force -or -not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
        Register-ScheduledTask -TaskName $TaskName -Xml (Get-Content $TempXML | Out-String) -Force
        Write-Host "✅ Task '$TaskName' created successfully with working directory" -ForegroundColor Green
        Write-Host "Working Directory: $ProjectDir" -ForegroundColor Yellow
    } else {
        Write-Host "⚠️ Task '$TaskName' already exists. Use -Force to overwrite." -ForegroundColor Yellow
    }
} catch {
    Write-Host "❌ Failed to create the task: $_" -ForegroundColor Red
}

Write-Host "`nManual Check Instructions:" -ForegroundColor Cyan
Write-Host "   - Open Task Scheduler" -ForegroundColor White
Write-Host "   - Navigate to 'Task Scheduler Library'" -ForegroundColor White
Write-Host "   - Right-click 'NIFTY50_Start' and check Properties > Actions" -ForegroundColor White
Write-Host "   - Verify Working Directory is set to: $ProjectDir" -ForegroundColor White

Read-Host "Press Enter to exit"
