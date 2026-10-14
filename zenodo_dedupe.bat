@echo off
setlocal
cd /d "%~dp0"

set PYTHON_EXE=D:\Python38\python.exe

echo ============================================================
echo  Zenodo Duplicate Cleanup
echo ============================================================
echo.
echo  Paste your Zenodo token below and press Enter.
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

if not exist "zenodo_dedupe.py" (
    echo [ERROR] zenodo_dedupe.py not found next to this .bat file.
    pause
    exit /b 1
)

rem ---- Default to --list when no args are given (safe, no deletion) ----
if "%~1"=="" (
    "%PYTHON_EXE%" zenodo_dedupe.py --list
) else (
    "%PYTHON_EXE%" zenodo_dedupe.py %*
)

set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE%==0 (
    echo Finished successfully.
) else (
    echo Finished with errors ^(exit code %EXITCODE%^).
)
echo.
pause
endlocal