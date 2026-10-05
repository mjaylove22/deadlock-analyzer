@echo off
rem One-time setup when running Deadlock Analyzer from the source code (a git clone or the ZIP).
rem Installs what's missing with winget (built into Windows 10 and 11): Python 3.13 and the
rem Tesseract OCR engine. Then installs the Python packages and makes a desktop shortcut.
rem Safe to run again, e.g. after "git pull" when requirements.txt has changed.
setlocal
cd /d "%~dp0"
echo.
echo === Deadlock Analyzer setup ===
echo.

where winget >nul 2>&1
if errorlevel 1 (
    echo winget isn't available on this PC. Install "App Installer" from the Microsoft Store, then run this again.
    goto :end
)

python --version >nul 2>&1
if errorlevel 1 (
    echo [1/4] Installing Python 3.13...
    winget install -e --id Python.Python.3.13 --scope user --accept-source-agreements --accept-package-agreements --override "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1"
    echo.
    echo Python is installed. Close this window and double-click setup.bat again,
    echo so Windows picks up the new Python.
    goto :end
)
echo [1/4] Python is installed.

if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    echo [2/4] Tesseract OCR is installed.
) else (
    echo [2/4] Installing Tesseract OCR. Windows may ask for permission: click Yes.
    winget install -e --id UB-Mannheim.TesseractOCR --accept-source-agreements --accept-package-agreements
)

echo [3/4] Installing the Python packages...
python -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo The packages didn't install. Check your internet connection and run this again.
    goto :end
)

echo [4/4] Making the desktop shortcut...
python make_shortcut.py

echo.
echo All done. Start the app from the "Deadlock Analyzer" icon on your desktop.
echo To update later: run "git pull" in this folder, then run setup.bat again.

:end
echo.
pause
