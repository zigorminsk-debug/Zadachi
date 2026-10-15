"""Entry point: python Zadachi.py  /  PyInstaller one-file Zadachi.exe.

Self-contained: standard library only (tkinter, sqlite3, http.server...).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from zadachi.app import main

if __name__ == "__main__":
    main()
