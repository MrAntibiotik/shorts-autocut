@echo off
chcp 65001 >nul
rem ================================================================
rem  Автонарізка шортсів: встановлення та оновлення одним кліком.
rem  Просто двічі клацніть цей файл. Повторний запуск = оновлення.
rem ================================================================
set "BRANCH=main"
set "URL=https://raw.githubusercontent.com/MrAntibiotik/shorts-autocut/%BRANCH%/setup.ps1"
set "PS1=%TEMP%\shorts_setup.ps1"
if exist "%PS1%" del "%PS1%"
echo Завантажую установник...
curl.exe -fsSL -o "%PS1%" "%URL%" 2>nul
if not exist "%PS1%" powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri %URL% -OutFile %PS1%" 2>nul
if not exist "%PS1%" if exist "%~dp0setup.ps1" copy /y "%~dp0setup.ps1" "%PS1%" >nul
if not exist "%PS1%" (
  echo [ПОМИЛКА] Не вдалося завантажити установник. Перевірте інтернет і запустіть ще раз.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Branch "%BRANCH%"
echo.
pause
