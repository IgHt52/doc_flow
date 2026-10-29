@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  ============================================
echo   DocFlow - установка на этом компьютере
echo  ============================================
echo.
if not exist "runtime\pythonw.exe" (
    echo ОШИБКА: не найдена папка runtime с Python.
    echo Убедитесь, что архив распакован ПОЛНОСТЬЮ ^(все папки на месте^).
    pause
    exit /b 1
)
if not exist data mkdir data
if not exist clients mkdir clients
if not exist backups mkdir backups
echo [1/3] Папки данных созданы: data\, clients\, backups\
rem Разблокировка файлов, скачанных из интернета (SmartScreen)
powershell -NoProfile -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File" >nul 2>&1
echo [2/3] Файлы разблокированы (Unblock-File)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_shortcut.ps1"
if errorlevel 1 (
    echo [3/3] Ярлык создать не удалось - запускайте через DocFlow.bat
) else (
    echo [3/3] Ярлык "DocFlow" создан на рабочем столе
)
echo.
echo  Готово! Запустите DocFlow ярлыком на рабочем столе
echo  или файлом DocFlow.bat в этой папке.
echo.
pause
