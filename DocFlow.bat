@echo off
rem DocFlow - запуск приложения (без консоли)
cd /d "%~dp0"
start "" "%~dp0runtime\pythonw.exe" "%~dp0app\main.py"
