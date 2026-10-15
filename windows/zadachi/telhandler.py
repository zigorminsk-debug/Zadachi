"""Register Zadachi as the Windows handler for tel:/callto: links.

Without this, clicking a phone number on a web page goes nowhere (the
browser has no program to hand the `tel:` URI to). After registration a
click in any browser/app launches:

    Zadachi.exe --call "tel:+375291234567"

which forwards the number to the phone's default dialer.

Registration is per-user (HKCU, no admin rights): both the classic
Software\\Classes\\<scheme> keys (used directly by browsers) and the
modern Capabilities/RegisteredApplications entries (Настройки Windows ->
Приложения по умолчанию). Everything is a safe no-op off Windows.
"""
from __future__ import annotations

import sys

from .autostart import _base_launch_command

PROG_ID = "Zadachi.Call"
CAPABILITIES_KEY = r"Software\Zadachi\Capabilities"
SCHEMES = ("tel", "callto")


def _handler_command() -> str:
    return _base_launch_command() + ' --call "%1"'


def _is_ours(command: str) -> bool:
    c = (command or "").lower()
    return "--call" in c and ("zadachi" in c)


def _set_value(key, name, value):
    import winreg
    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _write_handler_keys(root_path: str, command: str):
    import os
    import winreg
    if getattr(sys, "frozen", False):
        exe = sys.executable
    else:  # dev mode: icon source is the exe that will be built; use pythonw
        exe = sys.executable.replace("python.exe", "pythonw.exe")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root_path) as key:
        _set_value(key, "", "Zadachi — позвонить через телефон")
        _set_value(key, "URL Protocol", "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root_path + r"\DefaultIcon") as key:
        _set_value(key, "", f'"{exe}",0')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                          root_path + r"\shell\open\command") as key:
        _set_value(key, "", command)


def is_tel_handler_registered() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Classes\tel\shell\open\command") as key:
            value, _ = winreg.QueryValueEx(key, "")
            return _is_ours(value)
    except (ImportError, OSError):
        return False


def set_tel_handler(enabled: bool) -> bool:
    """Register (True) or remove our registration (False). Win32 only."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        command = _handler_command()
        if enabled:
            # Classic per-scheme keys — browsers consult these directly.
            for scheme in SCHEMES:
                _write_handler_keys(rf"Software\Classes\{scheme}", command)
            # ProgID + Capabilities, so Zadachi also appears in
            # Windows Settings -> Default apps.
            _write_handler_keys(rf"Software\Classes\{PROG_ID}", command)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CAPABILITIES_KEY) as key:
                _set_value(key, "ApplicationName", "Zadachi")
                _set_value(key, "ApplicationDescription",
                           "Звонки через Android-телефон в локальной сети")
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                  CAPABILITIES_KEY + r"\URLAssociations") as key:
                for scheme in SCHEMES:
                    _set_value(key, scheme, PROG_ID)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                  r"Software\RegisteredApplications") as key:
                _set_value(key, "Zadachi", CAPABILITIES_KEY)
            return True
        _unregister(winreg)
        return True
    except (ImportError, OSError):
        return False


def _registered_command(winreg, root_path: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            root_path + r"\shell\open\command") as key:
            value, _ = winreg.QueryValueEx(key, "")
            return value or ""
    except OSError:
        return ""


def _delete_tree(winreg, root_path: str):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, root_path, 0,
                             winreg.KEY_ALL_ACCESS)
    except OSError:
        return
    with key:
        while True:
            try:
                sub = winreg.EnumKey(key, 0)
            except OSError:
                break
            _delete_tree(winreg, root_path + "\\" + sub)
        # values are removed with the key by DeleteKey on parent below
    parent, _, leaf = root_path.rpartition("\\")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, parent, 0,
                            winreg.KEY_SET_VALUE) as p:
            winreg.DeleteKey(p, leaf)
    except OSError:
        pass


def _unregister(winreg):
    # Only remove keys that still point at Zadachi — never stomp a handler
    # the user has since reassigned to another program.
    for scheme in SCHEMES:
        root = rf"Software\Classes\{scheme}"
        if _is_ours(_registered_command(winreg, root)):
            _delete_tree(winreg, root)
    prog = rf"Software\Classes\{PROG_ID}"
    if _is_ours(_registered_command(winreg, prog)):
        _delete_tree(winreg, prog)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\RegisteredApplications", 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, "Zadachi")
    except OSError:
        pass
    _delete_tree(winreg, CAPABILITIES_KEY)
    _delete_tree(winreg, r"Software\Zadachi")
