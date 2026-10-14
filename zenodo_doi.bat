@echo off
setlocal
cd /d "%~dp0"

set PYTHON_EXE=D:\Python38\python.exe

echo ============================================================
echo  Zenodo DOI Automation
echo ============================================================
echo.
echo  Paste your Zenodo token below and press Enter.
echo  (Token is not echoed to screen.)
echo.

rem ---- Hidden token prompt via PowerShell ----
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

if not exist "zenodo_doi.py" (
    echo [ERROR] zenodo_doi.py not found next to this .bat file.
    pause
    exit /b 1
)

echo.
echo Running: python zenodo_doi.py --auto-release-stale %*
echo.

rem ---- Pass-through args appended AFTER --auto-release-stale, so user
rem ---- flags still win (e.g. --sandbox, --only NAME, --dry-run).
"%PYTHON_EXE%" zenodo_doi.py --auto-release-stale %*

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