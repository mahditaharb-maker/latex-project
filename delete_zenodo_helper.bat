@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo  Zenodo Manual Delete Helper
echo ============================================================
echo.
echo  For each record, this will:
echo    1. Open the Zenodo edit page in your browser
echo    2. Wait while you click Edit -^> Delete -^> Confirm
echo    3. Continue to the next record when you press a key
echo.
echo  Note: Zenodo's web UI allows self-service deletion only
echo        within 30 days of publication.
echo.

rem ---- Collect IDs: from args, or from delete_list.txt ----
set "IDS="
if "%~1"=="" (
    if exist "delete_list.txt" (
        echo Reading IDs from delete_list.txt...
        for /f "usebackq tokens=*" %%L in ("delete_list.txt") do (
            set "LINE=%%L"
            for /f "tokens=* delims= " %%A in ("!LINE!") do set "LINE=%%A"
            if not "!LINE!"=="" (
                if not "!LINE:~0,1!"=="#" (
                    set "IDS=!IDS! !LINE!"
                )
            )
        )
    ) else (
        echo [ERROR] no IDs given and delete_list.txt not found.
        echo.
        echo Usage:
        echo   delete_zenodo_helper.bat 23375703 23375207 23375196 ...
        echo.
        echo   or create a file named delete_list.txt in this folder
        echo   containing one Zenodo ID per line.
        echo.
        pause
        exit /b 1
    )
) else (
    set "IDS=%*"
)

rem ---- Count IDs ----
set /a COUNT=0
for %%I in (%IDS%) do set /a COUNT+=1

if %COUNT%==0 (
    echo [ERROR] no IDs to process.
    pause
    exit /b 1
)

echo Processing %COUNT% record^(s^)...
echo.

set /a N=0
for %%I in (%IDS%) do (
    set /a N+=1
    echo ------------------------------------------------------------
    echo  [!N!/%COUNT%!]  Opening:  https://zenodo.org/deposit/%%I
    echo ------------------------------------------------------------
    start "" "https://zenodo.org/deposit/%%I"
    echo.
    echo    In the browser window that just opened:
    echo      1. Click "Edit" (top right)
    echo      2. Scroll to the bottom
    echo      3. Click "Delete"
    echo      4. Confirm in the popup
    echo.
    echo    Then come back here and press any key for the next one.
    echo.
    pause >nul
)

echo.
echo ============================================================
echo  All done - %COUNT% record^(s^) opened.
echo ============================================================
echo.
echo  Verify with:  zenodo_dedupe.bat
echo.
pause
endlocal