@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch.ps1" %*
set "studioExit=%ERRORLEVEL%"
if not "%studioExit%"=="0" pause
exit /b %studioExit%
