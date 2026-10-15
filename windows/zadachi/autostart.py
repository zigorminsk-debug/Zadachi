"""Windows autostart via HKCU\\...\\Run (no admin rights needed).

Falls back silently on non-Windows systems (development / CI).
"""
from __future__ import annotations

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Zadachi"


def _launch_command() -> str:
    if getattr(sys, "frozen", False):  # PyInstaller .exe
        return f'"{sys.executable}"'
    # dev mode: pythonw Zadachi.py
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Zadachi.py")
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pyw}" "{script}"'


def is_autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except (ImportError, OSError):
        return False


def set_autostart(enabled: bool) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _launch_command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except OSError:
                    pass
        return True
    except (ImportError, OSError):
        return False
