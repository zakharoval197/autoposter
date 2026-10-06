# setup_autostart.ps1 - register moderator bot autostart at Windows logon.
# Run:     powershell -ExecutionPolicy Bypass -File setup_autostart.ps1
# Remove:  Unregister-ScheduledTask -TaskName 'AutoposterModerator'
$python = 'C:\Users\HP\AppData\Roaming\kimi-desktop\daimon-share\daimon\runtime\python\.venv\Scripts\pythonw.exe'
$workdir = $PSScriptRoot
$action = New-ScheduledTaskAction -Execute $python -Argument 'main.py moderate' -WorkingDirectory $workdir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'AutoposterModerator' -Action $action -Trigger $trigger -Settings $settings -Description 'Afisha moderator bot (publish/skip buttons)' -Force | Select-Object TaskName, State | Format-Table
