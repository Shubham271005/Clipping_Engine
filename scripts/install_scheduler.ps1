# Registers two Windows Task Scheduler jobs for the current user:
#   ClipEngine-Publish   : every 15 minutes, publishes clips whose slot has arrived
#   ClipEngine-Analytics : daily at 09:00, fetches metrics and updates learned weights
# Remove with: Unregister-ScheduledTask -TaskName ClipEngine-Publish,ClipEngine-Analytics -Confirm:$false

$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$main = Join-Path $root "main.py"

$publish = New-ScheduledTaskAction -Execute $py -Argument "`"$main`" publish-due" -WorkingDirectory $root
$every15 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "ClipEngine-Publish" -Action $publish -Trigger $every15 -Settings $settings -Force | Out-Null

$analytics = New-ScheduledTaskAction -Execute $py -Argument "`"$main`" analytics fetch" -WorkingDirectory $root
$daily = New-ScheduledTaskTrigger -Daily -At 9am
Register-ScheduledTask -TaskName "ClipEngine-Analytics" -Action $analytics -Trigger $daily -Settings $settings -Force | Out-Null

Write-Host "Registered ClipEngine-Publish (every 15 min) and ClipEngine-Analytics (daily 09:00)."
Write-Host "Note: the PC must be on (and logged in) for uploads to run."
