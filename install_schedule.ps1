# Creates (or replaces) a daily Windows task that runs refresh.py.   Run in PowerShell:
#     powershell -ExecutionPolicy Bypass -File install_schedule.ps1                 # every day at 07:30
#     powershell -ExecutionPolicy Bypass -File install_schedule.ps1 -Time 09:00     # other time
#     powershell -ExecutionPolicy Bypass -File install_schedule.ps1 -Remove         # delete the task
# If a run fails (for example the internet is not up yet right after the PC wakes), it is tried again every 20 minutes, up to 6 times.
# It runs as YOU (no admin needed) and only while you are logged in. If the PC was off at the scheduled time it runs as
# soon as the PC is on again. Output goes to data\refresh.log.
param(
    [string]$Time = "07:30",
    [switch]$Remove
)
$taskName = "GEW Data Refresh"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Task '$taskName' removed."
    return
}

$py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source          # pythonw = no console window
if (-not $py) { $py = (Get-Command python.exe).Source }
$action   = New-ScheduledTaskAction -Execute $py -Argument "`"$root\refresh.py`"" -WorkingDirectory $root
$trigger  = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 45) -Priority 4 -RestartCount 6 -RestartInterval (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Downloads the GEW sheets and rebuilds the revenue database (refresh.py)" -Force | Out-Null
Write-Host "Task '$taskName' will run every day at $Time using $py"
Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo | Format-List TaskName, NextRunTime
