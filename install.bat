@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo  Codex Model Switcher - Installer
echo ============================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 install.py %*
    goto done
)

where python >nul 2>nul
if %errorlevel%==0 (
    python install.py %*
    goto done
)

echo [X] Python not found.
echo     Install Python 3.8+ from https://www.python.org/downloads/
echo     and make sure "Add python.exe to PATH" is checked.
echo.

:done
echo.
pause
