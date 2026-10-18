# Makes the portal (server.py) start by itself every time you log in to Windows, and keeps it running (restarts it if it stops).
#     powershell -ExecutionPolicy Bypass -File install_server_autostart.ps1            # install and start now
#     powershell -ExecutionPolicy Bypass -File install_server_autostart.ps1 -Remove    # remove the auto-start
# Runs as YOU (no admin needed), without a window. Open the portal at http://localhost:8000 . Log: data\server.log
param([switch]$Remove)
$taskName = "GEW Portal Server"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Remove) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Task '$taskName' removed."
    return
}

$py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source          # pythonw = no console window
if (-not $py) { $py = (Get-Command python.exe).Source }
$action   = New-ScheduledTaskAction -Execute $py -Argument "`"$root\server.py`"" -WorkingDirectory $root
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew `
            -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -Priority 4
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Starts the GEW portal (server.py) at http://localhost:8000 when you log in" -Force | Out-Null
Write-Host "Task '$taskName' will start the portal every time you log in (using $py)."
