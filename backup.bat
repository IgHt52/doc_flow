@echo off
rem DocFlow - ручное/планируемое резервное копирование
rem SQLite-бэкап выполняется безопасно даже при открытом приложении.
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0runtime\python.exe" "%~dp0app\backup.py" create
if errorlevel 1 echo ОШИБКА: резервная копия не создана.
if /i not "%~1"=="silent" pause
