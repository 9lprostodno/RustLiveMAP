"""
Rust Live Map — десктопное приложение (PySide6).

Установка:
    pip install -r requirements.txt

Сборка в exe:
    pip install pyinstaller
    build_exe.bat   (на Windows; результат: dist/RustLiveMap.exe)
"""

from rustlive.app import main


if __name__ == "__main__":
    main()
