@echo off
rem DocFlow - запуск с консолью (для диагностики ошибок)
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0runtime\python.exe" "%~dp0app\main.py"
echo.
echo === Приложение завершилось. Если была ошибка - текст выше. ===
pause
