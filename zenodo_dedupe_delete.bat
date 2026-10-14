@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set PYTHON_EXE=D:\Python38\python.exe

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python not found at %PYTHON_EXE%
    pause
    exit /b 1
)
if not exist "zenodo_dedupe.py" (
    echo [ERROR] zenodo_dedupe.py not found.
    pause
    exit /b 1
)
if not exist "zenodo_delete_one.py" (
    echo [ERROR] zenodo_delete_one.py not found.
    pause
    exit /b 1
)

echo ============================================================
echo  Zenodo Duplicate Cleanup - ONE BY ONE
echo ============================================================
echo.
echo  Paste your Zenodo token below and press Enter.
echo.

set "ZENODO_TOKEN="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "$s = Read-Host -AsSecureString 'Token'; $b = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($b)"`) do set "ZENODO_TOKEN=%%p"

if "%ZENODO_TOKEN%"=="" (
    echo [ERROR] No token entered. Aborting.
    pause
    exit /b 1
)

:refresh
echo.
echo ============================================================
echo  Current duplicates
echo ============================================================
echo.
"%PYTHON_EXE%" zenodo_dedupe.py --list
echo.
echo  Type an ID to delete it.  Commands:  Q = quit    R = refresh
echo.

:loop
set "ID="
set /p "ID=ID: "

if /i "!ID!"=="q" goto :done
if /i "!ID!"=="r" goto :refresh
if "!ID!"=="" goto :loop

choice /c YN /n /m "Delete Zenodo record !ID!? [Y/N] "
if errorlevel 2 (
    echo Skipped.
    goto :loop
)

echo.
echo Deleting !ID! ...
"%PYTHON_EXE%" zenodo_delete_one.py !ID!
echo.
goto :loop

:done
echo.
echo Done.  Verify with: zenodo_dedupe.bat
echo.
pause
endlocal