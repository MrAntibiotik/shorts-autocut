@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if not exist venv\Scripts\python.exe ( echo Спершу запустіть SETUP.bat & pause & exit /b 1 )
venv\Scripts\python.exe tools\setup_speech_ai.py %*
pause
