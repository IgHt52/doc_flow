@echo off
rem DocFlow - восстановление из резервной копии
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  ВНИМАНИЕ: перед восстановлением полностью закройте DocFlow.
echo  Текущие база, клиенты и шаблоны будут заменены выбранной копией.
echo.
"%~dp0runtime\python.exe" "%~dp0app\backup.py" restore
echo.
pause
