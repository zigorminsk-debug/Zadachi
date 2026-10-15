@echo off
REM Build one self-contained Zadachi.exe (needs Python + pip on the build machine only)
cd /d %~dp0
python -m pip install --upgrade pyinstaller
python -m PyInstaller --noconfirm zadachi.spec
echo.
echo Done: dist\Zadachi.exe
pause
