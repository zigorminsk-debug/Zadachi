"""Minimal system-tray icon on Windows via ctypes (no third-party libs).

Uses the stock application icon. Left click restores the window, right
click shows a small Tk popup menu (handled by the caller through the
Tk event pump: we post virtual events to the root window).
On non-Windows platforms every call is a safe no-op.
"""
from __future__ import annotations

import sys

WM_TRAY = 0x8000 + 101  # WM_APP + 101
NIM_ADD, NIM_DELETE = 0x0, 0x2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
IDI_APPLICATION = 32512


class TrayIcon:
    def __init__(self, root, on_restore, on_menu=None):
        self.root = root
        self.on_restore = on_restore
        self.on_menu = on_menu
        self._added = False
        self._old_wndproc = None
        if sys.platform == "win32":
            self._setup()

    def _setup(self):
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
        root = self.root
        root.update_idletasks()
        self._hwnd = root.winfo_id()

        class NOTIFYICONDATA(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", ctypes.c_wchar * 128),
            ]

        self._nid = NOTIFYICONDATA()
        self._nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        self._nid.hWnd = self._hwnd
        self._nid.uID = 1
        self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._nid.uCallbackMessage = WM_TRAY
        self._nid.hIcon = user32.LoadIconW(0, IDI_APPLICATION)
        self._nid.szTip = "Zadachi — звонки и задачи"

        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM)
        old = user32.GetWindowLongPtrW(self._hwnd, -4)  # GWLP_WNDPROC

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAY:
                event = lparam & 0xFFFF
                if event == WM_LBUTTONUP:
                    root.after(0, self.on_restore)
                elif event == WM_RBUTTONUP and self.on_menu:
                    root.after(0, self.on_menu)
            return user32.CallWindowProcW(old, hwnd, msg, wparam, lparam)

        self._wndproc_ref = WNDPROC(wndproc)  # keep alive
        self._old_wndproc = old
        user32.SetWindowLongPtrW(self._hwnd, -4, self._wndproc_ref)
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid))
        self._added = True

    def remove(self):
        if self._added and sys.platform == "win32":
            ctypes = self._ctypes
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            self._added = False
