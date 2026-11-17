@echo off
chcp 65001 >nul
rem === Сборка портабл-EXE Rust Live Map (запускать на Windows, нужен Python 3.11 или 3.12) ===
cd /d "%~dp0"

if not exist .venv (
    python -m venv .venv || goto :err
)
call .venv\Scripts\activate.bat || goto :err
python -m pip install --upgrade pip
pip install PySide6 websocket-client rust-map-parser paramiko pyinstaller || goto :err

pyinstaller --noconfirm --clean --noconsole --onefile ^
    --name RustLiveMap ^
    --icon icon.ico ^
    --add-data "icon.png;." ^
    --collect-all rustmap_parser ^
    --hidden-import paramiko ^
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtCharts ^
    rust_live_map.py || goto :err

rem Сбросить кэш иконок Windows, чтобы Проводник сразу показал новую иконку
ie4uinit.exe -show >nul 2>&1
echo.
echo Готово: dist\RustLiveMap.exe
pause
exit /b 0

:err
echo.
echo ОШИБКА СБОРКИ
pause
exit /b 1
