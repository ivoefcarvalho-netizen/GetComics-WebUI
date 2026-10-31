@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo [ERRO] Python Launcher nao encontrado. Instala Python 3.12 a 3.14 em python.org.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 (pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install -r requirements-windows.txt
if errorlevel 1 (echo ERRO a instalar dependencias & pause & exit /b 1)
".venv\Scripts\python.exe" launcher_windows.py
if errorlevel 1 pause
