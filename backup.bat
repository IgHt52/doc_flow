@echo off
rem DocFlow - ручное/планируемое резервное копирование
rem Запуск: backup.bat (с паузой) или backup.bat silent (для планировщика)
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ts = Get-Date -Format 'yyyy-MM-dd_HHmm';" ^
  "$dst = Join-Path '%~dp0backups' $ts;" ^
  "New-Item -ItemType Directory -Force -Path $dst | Out-Null;" ^
  "Copy-Item '%~dp0data' $dst -Recurse -Force -ErrorAction SilentlyContinue;" ^
  "Copy-Item '%~dp0clients' $dst -Recurse -Force -ErrorAction SilentlyContinue;" ^
  "Copy-Item '%~dp0app\templates' $dst -Recurse -Force -ErrorAction SilentlyContinue;" ^
  "Write-Host ('Backup created: ' + $dst)"
rem Ротация: удалить бэкапы старше 30 дней
powershell -NoProfile -Command ^
  "Get-ChildItem '%~dp0backups' -Directory | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item -Recurse -Force"
if /i not "%~1"=="silent" pause
