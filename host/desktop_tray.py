"""Native Windows notification icon, isolated from Tk and bridge operations.

Only fixed action names cross a bounded, thread-safe queue. The Win32 message
thread owns its window/icon/menu; the Tk thread decides how to handle actions.
No additional packages, device access, profiles, or networking are used.
"""
from __future__ import annotations

from collections import deque
import ctypes
from ctypes import wintypes as w
from pathlib import Path
import sys
import threading


WINDOW_CLASS = "PiShockFlipperBridge.NotificationWindow"
WINDOW_TITLE = "PiShock Flipper Bridge notification controller"
WM_CLOSE, WM_DESTROY, WM_NULL = 0x0010, 0x0002, 0x0000
WM_TRAY, WM_RESTORE = 0x8003, 0x8004
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP, WM_CONTEXTMENU = 0x0202, 0x0203, 0x0205, 0x007B
NIN_SELECT, NIN_KEYSELECT = 0x0400, 0x0401
MENU_ACTIONS = {1: "open", 2: "stop", 3: "exit"}
WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class _Guid(ctypes.Structure):
    _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD), ("Data4", w.BYTE * 8)]


class _NotifyIcon(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT),
                ("uFlags", w.UINT), ("uCallbackMessage", w.UINT), ("hIcon", w.HICON),
                ("szTip", w.WCHAR * 128), ("dwState", w.DWORD), ("dwStateMask", w.DWORD),
                ("szInfo", w.WCHAR * 256), ("uVersion", w.UINT),
                ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
                ("guidItem", _Guid), ("hBalloonIcon", w.HICON)]


class _WindowClass(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH),
                ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR)]


def _function(library, name, restype, *argtypes):
    function = getattr(library, name)
    function.restype, function.argtypes = restype, list(argtypes)
    return function


def restore_existing() -> bool:
    """Ask the current instance to restore its window; no input simulation."""
    if sys.platform != "win32":
        return False
    try:
        user = ctypes.WinDLL("user32", use_last_error=True)
        find = _function(user, "FindWindowW", w.HWND, w.LPCWSTR, w.LPCWSTR)
        post = _function(user, "PostMessageW", w.BOOL, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        hwnd = find(WINDOW_CLASS, WINDOW_TITLE)
        return bool(hwnd and post(hwnd, WM_RESTORE, 0, 0))
    except Exception:
        return False


class TrayIcon:
    """Nonblocking lifecycle; drain_events belongs on Tk's owner thread."""
    def __init__(self, icon_path: Path, *, native_factory=None):
        self.icon_path = Path(icon_path)
        self._factory = native_factory or _NativeTray
        self._lock = threading.RLock()
        self._events = deque(maxlen=32)
        self._stop = threading.Event()
        self._thread = None
        self._native = None
        self._available = False
        self._running = False

    @property
    def available(self):
        with self._lock:
            return self._available and self._running and not self._stop.is_set()

    @property
    def running(self):
        with self._lock:
            return self._running

    def _post(self, action):
        if action not in {"open", "stop", "exit", "unavailable"}:
            return
        with self._lock:
            if not self._stop.is_set():
                self._events.append(action)

    def _availability(self, value):
        with self._lock:
            self._available = bool(value)

    def drain_events(self):
        with self._lock:
            values = tuple(self._events)
            self._events.clear()
            return values

    def start(self):
        with self._lock:
            if self._running:
                return
            self._stop.clear()
            self._running = True
            self._available = False
            self._events.clear()
            try:
                self._thread = threading.Thread(target=self._run, name="Windows notification icon", daemon=False)
                self._thread.start()
            except Exception:
                self._running = False
                self._events.append("unavailable")

    def stop(self):
        with self._lock:
            if self._stop.is_set():
                return
            self._stop.set()
            self._available = False
            native = self._native
        if native is not None:
            try:
                native.request_stop()
            except Exception:
                # The stop flag also gates native startup and its message loop.
                # Never let a presentation teardown exception reach Tk cleanup.
                pass

    def join(self, timeout=None):
        """For final cleanup/tests, never a blocking wait in Tk callbacks."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return not self.running

    def _run(self):
        try:
            native = self._factory(self.icon_path, self._post, self._availability, self._stop)
            with self._lock:
                self._native = native
            if not self._stop.is_set():
                native.run()
        except Exception:
            self._post("unavailable")
        finally:
            with self._lock:
                unexpected = not self._stop.is_set()
                self._native = None
                self._available = False
                self._running = False
                if unexpected and (not self._events or self._events[-1] != "unavailable"):
                    self._events.append("unavailable")


class _NativeTray:
    def __init__(self, icon_path, post, availability, stop):
        self.path, self.post, self.availability, self.stop_event = icon_path, post, availability, stop
        self.hwnd = self.icon = None
        self.icon_owned = False
        self.added = False
        self._wndproc = None

    def _libraries(self):
        if sys.platform != "win32":
            raise RuntimeError("Windows notification icons are unavailable.")
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, result, args in [
            ("RegisterClassW", w.ATOM, [ctypes.POINTER(_WindowClass)]),
            ("UnregisterClassW", w.BOOL, [w.LPCWSTR, w.HINSTANCE]),
            ("CreateWindowExW", w.HWND, [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p]),
            ("DefWindowProcW", ctypes.c_ssize_t, [w.HWND, w.UINT, w.WPARAM, w.LPARAM]),
            ("DestroyWindow", w.BOOL, [w.HWND]),
            ("PostMessageW", w.BOOL, [w.HWND, w.UINT, w.WPARAM, w.LPARAM]),
            ("PostQuitMessage", None, [ctypes.c_int]),
            ("GetMessageW", ctypes.c_int, [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]),
            ("TranslateMessage", w.BOOL, [ctypes.POINTER(w.MSG)]),
            ("DispatchMessageW", ctypes.c_ssize_t, [ctypes.POINTER(w.MSG)]),
            ("RegisterWindowMessageW", w.UINT, [w.LPCWSTR]),
            ("SetTimer", ctypes.c_size_t, [w.HWND, ctypes.c_size_t, w.UINT, ctypes.c_void_p]),
            ("KillTimer", w.BOOL, [w.HWND, ctypes.c_size_t]),
            ("LoadImageW", w.HANDLE, [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]),
            ("LoadIconW", w.HICON, [w.HINSTANCE, ctypes.c_void_p]),
            ("DestroyIcon", w.BOOL, [w.HICON]),
            ("CreatePopupMenu", w.HMENU, []),
            ("AppendMenuW", w.BOOL, [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]),
            ("DestroyMenu", w.BOOL, [w.HMENU]),
            ("GetCursorPos", w.BOOL, [ctypes.POINTER(w.POINT)]),
            ("SetForegroundWindow", w.BOOL, [w.HWND]),
            ("TrackPopupMenu", w.UINT, [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, w.HWND, ctypes.c_void_p]),
        ]:
            _function(self.user, name, result, *args)
        _function(self.shell, "Shell_NotifyIconW", w.BOOL, w.DWORD, ctypes.POINTER(_NotifyIcon))
        _function(self.kernel, "GetModuleHandleW", w.HMODULE, w.LPCWSTR)

    def _add_icon(self):
        self.availability(False)
        self.data = _NotifyIcon()
        self.data.cbSize = ctypes.sizeof(_NotifyIcon)
        self.data.hWnd, self.data.uID, self.data.hIcon = self.hwnd, 1, self.icon
        self.data.uFlags, self.data.uCallbackMessage = 0x1 | 0x2 | 0x4 | 0x80, WM_TRAY
        self.data.szTip = "PiShock Bridge — Open, stop or exit"
        self.added = bool(self.shell.Shell_NotifyIconW(0, ctypes.byref(self.data)))
        if self.added:
            self.data.uVersion = 4
            self.shell.Shell_NotifyIconW(4, ctypes.byref(self.data))
        self.availability(self.added)
        return self.added

    def _remove_icon(self):
        if self.added:
            self.shell.Shell_NotifyIconW(2, ctypes.byref(self.data))
            self.added = False
        self.availability(False)

    def _menu(self):
        menu = self.user.CreatePopupMenu()
        if not menu:
            self.post("open")
            return
        try:
            for identifier, label in ((1, "Open PiShock Bridge"), (2, "Stop and disconnect"), (3, "Exit")):
                if not self.user.AppendMenuW(menu, 0, identifier, label):
                    self.post("open")
                    return
            point = w.POINT()
            if not self.user.GetCursorPos(ctypes.byref(point)):
                self.post("open")
                return
            self.user.SetForegroundWindow(self.hwnd)
            chosen = self.user.TrackPopupMenu(menu, 0x0100 | 0x0002, point.x, point.y, 0, self.hwnd, None)
            self.user.PostMessageW(self.hwnd, WM_NULL, 0, 0)
            if chosen in MENU_ACTIONS:
                self.post(MENU_ACTIONS[chosen])
        finally:
            self.user.DestroyMenu(menu)

    def _message(self, hwnd, message, wparam, lparam):
        try:
            if message == WM_TRAY:
                action = lparam & 0xffff
                if action in (WM_LBUTTONUP, WM_LBUTTONDBLCLK, NIN_SELECT, NIN_KEYSELECT):
                    self.post("open")
                elif action in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._menu()
                return 0
            if message == WM_RESTORE:
                self.post("open")
                return 0
            if message == self.taskbar_message and not self.stop_event.is_set():
                # Explorer recreation removes every icon; re-establish ours.
                self.added = False
                if not self._add_icon():
                    self.post("unavailable")
                return 0
            if message == WM_CLOSE:
                self._remove_icon()
                self.user.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                self.hwnd = None
                self.user.PostQuitMessage(0)
                return 0
            return self.user.DefWindowProcW(hwnd, message, wparam, lparam)
        except Exception:
            self.post("unavailable")
            self.user.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            return 0

    def request_stop(self):
        hwnd = self.hwnd
        if hwnd:
            self.user.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    def run(self):
        self._libraries()
        instance = self.kernel.GetModuleHandleW(None)
        self.taskbar_message = self.user.RegisterWindowMessageW("TaskbarCreated")
        if not self.taskbar_message:
            raise RuntimeError("Notification messages are unavailable.")
        self._wndproc = WNDPROC(self._message)
        window_class = _WindowClass()
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance, window_class.lpszClassName = instance, WINDOW_CLASS
        if not self.user.RegisterClassW(ctypes.byref(window_class)):
            raise RuntimeError("Notification window could not be created.")
        try:
            self.hwnd = self.user.CreateWindowExW(0, WINDOW_CLASS, WINDOW_TITLE, 0,
                                                   0, 0, 0, 0, None, None, instance, None)
            if not self.hwnd:
                raise RuntimeError("Notification window could not be created.")
            # Bound stop latency even if a cross-thread PostMessage fails.
            if not self.user.SetTimer(self.hwnd, 1, 250, None):
                raise RuntimeError("Notification shutdown timer could not be created.")
            self.icon = self.user.LoadImageW(None, str(self.path), 1, 0, 0, 0x0010 | 0x0040)
            self.icon_owned = bool(self.icon)
            if not self.icon:
                self.icon = self.user.LoadIconW(None, ctypes.c_void_p(32512))
            if not self.icon or not self._add_icon():
                raise RuntimeError("Notification icon could not be created.")
            if self.stop_event.is_set():
                return
            message = w.MSG()
            while not self.stop_event.is_set():
                result = self.user.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise RuntimeError("Notification messages stopped.")
                if self.stop_event.is_set():
                    break
                self.user.TranslateMessage(ctypes.byref(message))
                self.user.DispatchMessageW(ctypes.byref(message))
        finally:
            self._remove_icon()
            if self.hwnd:
                self.user.KillTimer(self.hwnd, 1)
                self.user.DestroyWindow(self.hwnd)
                self.hwnd = None
            if self.icon and self.icon_owned:
                self.user.DestroyIcon(self.icon)
            self.user.UnregisterClassW(WINDOW_CLASS, instance)
            self._wndproc = None
