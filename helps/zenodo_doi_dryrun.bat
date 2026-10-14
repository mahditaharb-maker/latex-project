@echo off
setlocal
cd /d "%~dp0"

set PYTHON_EXE=D:\Python38\python.exe

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

echo ============================================================
echo  Zenodo DOI Automation - DRY RUN
echo ============================================================
echo.
echo  No changes will be made. No token required.
echo.

"%PYTHON_EXE%" zenodo_doi.py --dry-run --auto-release-stale %*

set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE%==0 (
    echo Dry run complete.
) else (
    echo Dry run finished with errors ^(exit code %EXITCODE%^).
)
echo.
pause
endlocal