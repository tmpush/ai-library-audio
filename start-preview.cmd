@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m client.preview --with-samples
) else (
  uv run python -m client.preview --with-samples
)
if errorlevel 1 pause
