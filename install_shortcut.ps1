# Creates a desktop shortcut for DocFlow (ASCII only for PS 5.1 compatibility)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath('Desktop')
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut((Join-Path $desktop 'DocFlow.lnk'))
$sc.TargetPath = (Join-Path $root 'runtime\pythonw.exe')
$sc.Arguments = '"' + (Join-Path $root 'app\main.py') + '"'
$sc.WorkingDirectory = $root
$sc.IconLocation = (Join-Path $root 'runtime\pythonw.exe') + ',0'
$sc.Description = 'DocFlow - document management'
$sc.Save()
Write-Host ('Shortcut created: ' + (Join-Path $desktop 'DocFlow.lnk'))
