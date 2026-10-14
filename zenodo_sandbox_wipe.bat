@echo off
setlocal
cd /d "%~dp0"

set PYTHON_EXE=D:\Python38\python.exe

echo ============================================================
echo  Zenodo SANDBOX wipe
echo ============================================================
echo.
echo  Paste your SANDBOX Zenodo token below and press Enter.
echo  (Get one from https://sandbox.zenodo.org/account/settings/applications/tokens/new/)
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

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python not found at %PYTHON_EXE%
    pause
    exit /b 1
)

echo.
echo Running: python zenodo_sandbox_wipe.py --delete
echo.

"%PYTHON_EXE%" zenodo_sandbox_wipe.py --delete

echo.
pause
endlocal