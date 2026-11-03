@echo off
setlocal
cd /d "%~dp0"
if exist ".venv-workbench\Scripts\python.exe" (
  ".venv-workbench\Scripts\python.exe" -B -m servo_workbench %*
) else (
py -3 -B -m servo_workbench %*
)
if errorlevel 1 pause
