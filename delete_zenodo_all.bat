@echo off
echo Opening 9 Zenodo edit pages...
echo.
start "" "https://zenodo.org/deposit/23375703"
timeout /t 2 /nobreak >nul
start "" "https://zenodo.org/deposit/23375207"
timeout /t 2 /nobreak >nul
start "" "https://zenodo.org/deposit/23375196"
timeout /t 2 /nobreak >nul
start "" "https://zenodo.org/deposit/23374932"
timeout /t 2 /nobreak >nul
start "" "https://zenodo.org/deposit/23374928"
timeout /t 2 /nobreak >nul
start "" "https://zenodo.org/deposit/23374922"
timeout /t 2 /nobreak >nul
start "" "https://zenodo.org/deposit/23374440"
timeout /t 2 /nobreak >nul
start "" "https://zenodo.org/deposit/23374438"
timeout /t 2 /nobreak >nul
start "" "https://zenodo.org/deposit/23374403"
echo.
echo All 9 pages should be opening in your browser now.
echo.
echo For each tab:
echo   1. Click Edit (top right)
echo   2. Scroll to the bottom
echo   3. Click Delete
echo   4. Confirm in the popup
echo   5. Close the tab
echo.
pause