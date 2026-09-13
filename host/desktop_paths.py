"""Runtime paths and a Windows session lock; no device or network side effects."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import os
import sys


def resource_root():
    return Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]


def profile_path():
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        raise RuntimeError('Windows application storage is unavailable for this account.')
    return Path(base) / 'PiShockFlipperBridge' / 'device.dpapi'


def preferences_path():
    return profile_path().with_name('appearance.json')


class AlreadyRunningError(RuntimeError):
    """Another desktop instance owns this Windows session."""


class InstanceLock:
    """Prevent a second desktop controller in the current Windows session."""
    def __init__(self):
        self.handle = None

    def acquire(self):
        if sys.platform != 'win32':
            raise RuntimeError('PiShock Flipper Bridge currently requires Windows.')
        library = ctypes.WinDLL('kernel32', use_last_error=True)
        library.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        library.CreateMutexW.restype = wintypes.HANDLE
        library.CloseHandle.argtypes = [wintypes.HANDLE]
        library.CloseHandle.restype = wintypes.BOOL
        handle = library.CreateMutexW(None, False, 'Local\\PiShockFlipperBridge.Desktop')
        error = ctypes.get_last_error()
        if not handle:
            raise RuntimeError('Windows could not start the application session.')
        if error == 183:
            library.CloseHandle(handle)
            raise AlreadyRunningError('PiShock Flipper Bridge is already open. Use its window or icon near the clock.')
        self.handle, self.library = handle, library

    def close(self):
        if self.handle:
            self.library.CloseHandle(self.handle)
            self.handle = None
