@echo off
setlocal enabledelayedexpansion

:: ============================================
:: Directory + File Tree Generator
:: Unicode box-drawing tree (├── └── │)
:: Windows 7 Safe (run "chcp 65001" first)
:: ============================================

if "%~1"=="" (
    set "TARGET_DIR=%CD%"
) else (
    set "TARGET_DIR=%~1"
)
set "OUTPUT_FILE=%~2"
if "%OUTPUT_FILE%"=="" set "OUTPUT_FILE=directory_tree.txt"

if "%TARGET_DIR:~-1%"=="\" (
    if not "%TARGET_DIR:~1,1%"==":" set "TARGET_DIR=%TARGET_DIR:~0,-1%"
)
if not exist "%TARGET_DIR%\" (
    echo ERROR: Directory "%TARGET_DIR%" does not exist.
    pause
    exit /b 1
)
for %%A in ("%TARGET_DIR%") do set "TARGET_DIR=%%~fA"
if "%TARGET_DIR:~-1%"=="\" (
    set "BASE=%TARGET_DIR:~0,-1%"
) else (
    set "BASE=%TARGET_DIR%"
)

set /a DIR_COUNT=0
set /a FILE_COUNT=0

title Generating Directory + File Tree...
echo.
echo ============================================
echo  DIRECTORY + FILE TREE GENERATOR - WORKING...
echo ============================================
echo.
echo  Root   : %TARGET_DIR%
echo  Output : %OUTPUT_FILE%
echo.
echo  Scanning directories and files...
echo.

> "%OUTPUT_FILE%" echo ============================================
>> "%OUTPUT_FILE%" echo  DIRECTORY + FILE TREE REPORT
>> "%OUTPUT_FILE%" echo  Root: %TARGET_DIR%
>> "%OUTPUT_FILE%" echo  Generated: %DATE% %TIME%
>> "%OUTPUT_FILE%" echo ============================================
>> "%OUTPUT_FILE%" echo.

for %%A in ("%BASE%") do set "ROOTNAME=%%~nxA"
if "%ROOTNAME%"=="" set "ROOTNAME=%BASE%"
>> "%OUTPUT_FILE%" echo %ROOTNAME%\

call :WalkDir "%BASE%" ""

echo.
echo ============================================
echo  COMPLETED
echo ============================================
echo.
echo  Directories : %DIR_COUNT%
echo  Files       : %FILE_COUNT%
echo  Output      : %OUTPUT_FILE%
echo.
pause
exit /b 0

:: ============================================
:WalkDir
setlocal enabledelayedexpansion
set "DIR=%~1"
set "PREFIX=%~2"

:: ---- Collect subdirectories ----
set /a SUBCOUNT=0
for /f "delims=" %%D in ('dir /b /ad /on "%DIR%\*" 2^>nul') do (
    set /a SUBCOUNT+=1
    set "SUB_!SUBCOUNT!=%%D"
)

:: ---- Collect files ----
set /a FILECOUNT=0
for /f "delims=" %%F in ('dir /b /a-d /on "%DIR%\*" 2^>nul') do (
    set /a FILECOUNT+=1
    set "FILE_!FILECOUNT!=%%F"
)

:: ---- Total children (for last-item detection) ----
set /a TOTAL=SUBCOUNT+FILECOUNT
set /a IDX=0

:: ---- Print subdirectories first ----
for /l %%I in (1,1,!SUBCOUNT!) do (
    set /a IDX+=1
    set "SUBNAME=!SUB_%%I!"

    if !IDX! EQU !TOTAL! (
        set "CONNECTOR=└── "
        set "NEXTPREFIX=!PREFIX!    "
    ) else (
        set "CONNECTOR=├── "
        set "NEXTPREFIX=!PREFIX!│   "
    )

    set /a DIR_COUNT+=1
    echo !PREFIX!!CONNECTOR!!SUBNAME!\
    >> "%OUTPUT_FILE%" echo !PREFIX!!CONNECTOR!!SUBNAME!\

    call :WalkDir "%DIR%\!SUBNAME!" "!NEXTPREFIX!"
)

:: ---- Print files ----
for /l %%I in (1,1,!FILECOUNT!) do (
    set /a IDX+=1
    set "FNAME=!FILE_%%I!"

    if !IDX! EQU !TOTAL! (
        set "CONNECTOR=└── "
    ) else (
        set "CONNECTOR=├── "
    )

    set /a FILE_COUNT+=1
    echo !PREFIX!!CONNECTOR!!FNAME!
    >> "%OUTPUT_FILE%" echo !PREFIX!!CONNECTOR!!FNAME!
)

endlocal
exit /b 0