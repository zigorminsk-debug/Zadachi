"""System-tray icon on Windows via ctypes (no third-party libs).

IMPORTANT (bug history): the first version subclassed the Tk main window
and forwarded messages to Tk's previous WndProc. The ctypes calls did not
declare 64-bit signatures, so the previous-procedure pointer was truncated
to 32 bits and the process died silently on the very first window message
(the app "collapsed" right after opening).

This version never touches the Tk window: it creates its own message-only
Win32 window (HWND_MESSAGE) whose procedure receives the tray callback.
All WinAPI functions get explicit argtypes/restype, so pointers survive
64-bit Windows intact. The Tk main loop dispatches thread messages, which
is how this window receives its callbacks.

On non-Windows platforms (dev/CI) every call is a safe no-op. Any failure
here must never kill the application — the caller guards construction.
"""
from __future__ import annotations

import sys

WM_TRAY = 0x8000 + 101  # WM_APP + 101
NIM_ADD, NIM_DELETE = 0x0, 0x2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
WM_LBUTTONUP, WM_RBUTTONUP, WM_LBUTTONDBLCLK = 0x0202, 0x0205, 0x0203
IDI_APPLICATION = 32512
HWND_MESSAGE = -3


class TrayIcon:
    def __init__(self, root, on_restore, on_menu=None, icon_path=""):
        self.root = root
        self.on_restore = on_restore
        self.on_menu = on_menu
        self.icon_path = icon_path or ""
        self._added = False
        self._hwnd = None
        if sys.platform == "win32":
            self._setup()

    def _setup(self):
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32

        # --- explicit 64-bit-safe signatures ------------------------------
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        self._wndproc_type = WNDPROC

        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterClassW.restype = wintypes.WORD
        user32.RegisterClassW.argtypes = [ctypes.c_void_p]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.LoadIconW.restype = wintypes.HICON
        user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
        # LoadImageW also returns a handle: without an explicit restype,
        # ctypes would truncate it to 32 bits on 64-bit Windows.
        user32.LoadImageW.restype = wintypes.HICON
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
            ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]

        root = self.root

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAY:
                event = lparam & 0xFFFF
                try:
                    if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                        root.after(0, self.on_restore)
                    elif event == WM_RBUTTONUP and self.on_menu:
                        root.after(0, self.on_menu)
                except Exception:
                    pass
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc_ref = WNDPROC(wndproc)  # must stay alive

        class WNDCLASS(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HCURSOR), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
            ]

        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASS()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = "ZadachiTrayWindow"
        user32.RegisterClassW(ctypes.byref(wc))  # may already exist: fine

        self._hwnd = user32.CreateWindowExW(
            0, "ZadachiTrayWindow", "ZadachiTray", 0, 0, 0, 0, 0,
            HWND_MESSAGE, None, hinst, None)
        if not self._hwnd:
            raise OSError("CreateWindowExW for tray failed")

        class NOTIFYICONDATA(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", ctypes.c_wchar * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", ctypes.c_wchar * 256), ("uTimeoutOrVersion", wintypes.UINT),
                ("szInfoTitle", ctypes.c_wchar * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON),
            ]

        self._nid = NOTIFYICONDATA()
        self._nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        self._nid.hWnd = self._hwnd
        self._nid.uID = 1
        self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._nid.uCallbackMessage = WM_TRAY
        self._nid.hIcon = self._load_icon(user32)
        self._nid.szTip = "Zadachi — звонки и задачи"

        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid)):
            raise OSError("Shell_NotifyIconW(NIM_ADD) failed")
        self._added = True

    def _load_icon(self, user32):
        """App icon from the .ico file, stock application icon as fallback."""
        import ctypes
        import os
        if self.icon_path and os.path.isfile(self.icon_path):
            IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
            SM_CXSMICON, SM_CYSMICON = 49, 50
            try:
                cx = user32.GetSystemMetrics(SM_CXSMICON)
                cy = user32.GetSystemMetrics(SM_CYSMICON)
            except Exception:
                cx = cy = 0
            hicon = user32.LoadImageW(None, self.icon_path, IMAGE_ICON,
                                      cx, cy, LR_LOADFROMFILE)
            if hicon:
                return hicon
        return user32.LoadIconW(0, ctypes.cast(IDI_APPLICATION, ctypes.c_void_p))

    def remove(self):
        if not self._added:
            return
        try:
            ctypes = self._ctypes
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            if self._hwnd:
                ctypes.windll.user32.DestroyWindow(self._hwnd)
                self._hwnd = None
        except Exception:
            pass
        self._added = False
