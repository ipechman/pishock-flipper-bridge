"""Initialize the bundled Tcl runtime before Tk's first interpreter is created.

Some Python distributions do not call Tcl_FindExecutable in a frozen process.
Tcl needs this initialization for its filesystem and encoding support; otherwise
even a bundled, readable init.tcl can appear missing. This hook uses only the
Tcl DLL shipped with this application's tested Python 3.13 / Tk 8.6 build.
"""
import ctypes
import os
from pathlib import Path
import sys


if sys.platform == 'win32' and getattr(sys, 'frozen', False):
    _tcl_runtime = ctypes.CDLL(str(Path(sys._MEIPASS) / 'tcl86t.dll'))
    _tcl_runtime.Tcl_FindExecutable.argtypes = [ctypes.c_char_p]
    _tcl_runtime.Tcl_FindExecutable.restype = None
    # On Windows, NULL denotes a GUI process without a standard error console.
    _tcl_runtime.Tcl_FindExecutable(None if sys.stderr is None else os.fsencode(sys.executable))
