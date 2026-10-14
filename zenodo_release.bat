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

rem ---- No argument: show current status as reference ----
if "%~1"=="" (
    echo ============================================================
    echo  Zenodo release helper
    echo ============================================================
    echo.
    echo  Usage:  zenodo_release.bat PAPER-NAME
    echo.
    echo  Publishes a new Zenodo version for a paper whose files have
    echo  changed since its DOI was minted.
    echo.
    echo  Current status:
    echo.
    "%PYTHON_EXE%" zenodo_doi.py --status
    echo.
    pause
    exit /b 1
)

set "PAPER=%~1"

echo ============================================================
echo  Zenodo release - %PAPER%
echo ============================================================
echo.

rem ---- Verify the paper is actually stale ----
"%PYTHON_EXE%" zenodo_doi.py --verify-stale "%PAPER%"
set VERIFY=%ERRORLEVEL%
echo.

if not "%VERIFY%"=="0" (
    if "%VERIFY%"=="1" (
        echo Nothing to release. This paper has not changed since publish.
    ) else if "%VERIFY%"=="2" (
        echo This paper is not tracked yet.
        echo Run zenodo_doi.bat first to mint its initial DOI.
    ) else (
        echo Cannot verify paper state.
    )
    echo.
    pause
    exit /b 1
)

rem ---- Confirm ----
choice /c YN /n /m "Publish a new version to production Zenodo? [Y/N] "
if errorlevel 2 (
    echo.
    echo Cancelled.
    pause
    exit /b 0
)

echo.
echo Paste your Zenodo production token below and press Enter.
echo (Token is not echoed to screen.)
echo.

set "ZENODO_TOKEN="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "$s = Read-Host -AsSecureString 'Token'; $b = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($b)"`) do set "ZENODO_TOKEN=%%p"

if "%ZENODO_TOKEN%"=="" (
    echo.
    echo [ERROR] No token entered. Aborting.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo.

"%PYTHON_EXE%" zenodo_doi.py --new-version --only "%PAPER%"

set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE%==0 (
    echo Release completed successfully.
) else (
    echo Release failed ^(exit code %EXITCODE%^).
)

echo.
echo Next steps:
echo   1. Review the new DOI above.
echo   2. Run sync_repos.bat to commit the updated state file and
echo      CITATION.cff to GitHub.
echo.

pause
endlocal