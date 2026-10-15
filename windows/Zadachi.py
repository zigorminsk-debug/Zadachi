"""Entry point: python Zadachi.py  /  PyInstaller one-file Zadachi.exe.

Self-contained: standard library only (tkinter, sqlite3, http.server...).
No silent deaths: any fatal startup error is written to
%APPDATA%/Zadachi/error.log and shown in a message box.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from zadachi.app import log_error, main

if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_error("fatal startup error")
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "Zadachi не удалось запустить.\n"
                    "Подробности: %APPDATA%\\Zadachi\\error.log",
                    "Zadachi — ошибка запуска", 0x10)
            except Exception:
                pass
        raise SystemExit(1)
