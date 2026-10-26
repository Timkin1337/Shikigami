@echo off
rem Только ядро (без окон): интерфейс откроется в браузере по адресу http://127.0.0.1:7853
cd /d "%~dp0"
".venv\Scripts\python.exe" shikigami.py
