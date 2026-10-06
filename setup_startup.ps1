# setup_startup.ps1 - shortcut to moderator bot in per-user Startup folder.
# Runs at every Windows logon, no admin needed, no console window (pythonw).
# Remove: delete AutoposterModerator.lnk from shell:startup
$startup = [Environment]::GetFolderPath('Startup')
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $startup 'AutoposterModerator.lnk'))
$lnk.TargetPath = 'C:\Users\HP\AppData\Roaming\kimi-desktop\daimon-share\daimon\runtime\python\.venv\Scripts\pythonw.exe'
$lnk.Arguments = '"C:\Users\HP\Documents\Kimi\Workspaces\Куда пойти в москве\autoposter\main.py" moderate'
$lnk.WorkingDirectory = 'C:\Users\HP\Documents\Kimi\Workspaces\Куда пойти в москве\autoposter'
$lnk.Save()
Write-Output "Shortcut created: $startup\AutoposterModerator.lnk"
