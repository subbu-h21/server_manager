# Runs Server Manager in the background (no window, no browser tab) when you sign in to Windows, and again within a
# minute if it isn't running. autostart.py restarts Server Manager itself when it stops.
# Undo: Unregister-ScheduledTask -TaskName "Server Manager" -Confirm:$false
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$user = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute (Join-Path $root "venv\Scripts\pythonw.exe") `
    -Argument ('"' + (Join-Path $root "autostart.py") + '"') -WorkingDirectory $root

$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
# Every minute, from now on (only while you're signed in): while it runs, these are ignored (IgnoreNew below)
$everyMinute = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1)

# Laptop-friendly: run on battery, never time out. Normal priority (4): the servers it starts inherit it, and a
# task's default (7) is below normal.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -Priority 4 -StartWhenAvailable

$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "Server Manager" -Action $action -Trigger @($atLogon, $everyMinute) `
    -Settings $settings -Principal $principal -Description "Server Manager in the background ($root)" -Force | Out-Null
Start-ScheduledTask -TaskName "Server Manager"
"Installed scheduled task 'Server Manager' (starts at sign-in, and again within a minute if it isn't running)."
