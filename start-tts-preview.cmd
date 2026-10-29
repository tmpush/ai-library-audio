@echo off
cd /d "%~dp0"
if not exist ".venv-tts\Scripts\python.exe" (
  echo TTS environment is missing. See README.md.
  pause
  exit /b 1
)
".venv-tts\Scripts\python.exe" -X utf8 -m client.preview --with-samples
if errorlevel 1 pause
