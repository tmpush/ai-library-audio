@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m client.preview --stop
) else (
  uv run python -m client.preview --stop
)
