@echo off
rem Запуск Shikigami: оболочка Electron сама поднимет Python-ядро
cd /d "%~dp0shell"
start "" "%~dp0shell\node_modules\electron\dist\electron.exe" .
