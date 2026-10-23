@echo off
setlocal
cd /d "%~dp0"

set PYTHON_EXE=D:\Python38\python.exe

rem ---- Add TeX Live to the system PATH for this session ----
set "TEXLIVE_PATH=E:\archives\Processings\Doctorate\texlive\2024\bin\windows"
set "PATH=%TEXLIVE_PATH%;%PATH%"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python not found at %PYTHON_EXE%
    pause
    exit /b 1
)

if not exist "compile_tex.py" (
    echo [ERROR] compile_tex.py not found next to this .bat file.
    pause
    exit /b 1
)

if not exist "%TEXLIVE_PATH%\pdflatex.exe" (
    echo [ERROR] pdflatex.exe not found at %TEXLIVE_PATH%
    pause
    exit /b 1
)

echo ============================================================
echo  LaTeX Local Compiler
echo ============================================================
echo.

rem ---- Pass any arguments (like --force) to the Python script ----
"%PYTHON_EXE%" compile_tex.py %*

set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE%==0 (
    echo Finished successfully.
) else (
    echo Compilation finished with some errors ^(exit code %EXITCODE%^).
)
echo.
pause
endlocal