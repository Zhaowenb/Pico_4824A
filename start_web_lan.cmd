@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_web_lan.ps1"
set "launcher_exit=%errorlevel%"
echo.
if "%launcher_exit%"=="0" (
    echo Backend process has exited. Review the output above.
) else (
    echo Launcher exited with code %launcher_exit%. Review the error above.
)
echo Press any key to close this window.
pause >nul
exit /b %launcher_exit%
