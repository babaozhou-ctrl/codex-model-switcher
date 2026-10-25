@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo  Codex Model Switcher - Installer
echo ============================================================
echo.
echo  This is the INSTALLER. It only does two things:
echo    1. Copy files into %%USERPROFILE%%\.codex\profiles\
echo    2. Create a shortcut on your Desktop
echo.
echo  NO input is needed here - including the API Key.
echo  When it finishes, DOUBLE-CLICK the desktop shortcut to
echo  open the GUI; that is where you enter your API Key.
echo.
echo ------------------------------------------------------------
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
