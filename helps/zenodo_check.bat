@echo off
setlocal
cd /d "%~dp0"

set PYTHON_EXE=D:\Python38\python.exe

echo ============================================================
echo  Zenodo Token Checker
echo ============================================================
echo.
echo  Paste the token you want to test below and press Enter.
echo  (Token is not echoed to screen.)
echo.

set "ZENODO_TOKEN="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "$s = Read-Host -AsSecureString 'Token'; $b = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($b)"`) do set "ZENODO_TOKEN=%%p"

if "%ZENODO_TOKEN%"=="" (
    echo.
    echo [ERROR] No token entered. Aborting.
    pause
    exit /b 1
)

"%PYTHON_EXE%" zenodo_check.py

echo.
pause
endlocal