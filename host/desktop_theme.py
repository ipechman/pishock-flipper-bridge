"""Desktop appearance only: no device identity, serial or network access."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
from weakref import WeakKeyDictionary


PALETTES = {
    'dark': {
        'bg': '#101820', 'card': '#17232e', 'ink': '#e7edf3', 'muted': '#a6b7c7',
        'navy': '#0c131b', 'accent': '#46c8b9', 'line': '#334554', 'tile': '#1e2d3a',
        'button': '#283a49', 'hover': '#354c5e', 'disabled': '#20303d',
        'disabled_ink': '#8c9eae', 'primary': '#137f76', 'primary_hover': '#0f6d65',
        'primary_disabled': '#254b4c', 'stop': '#b33c4b', 'stop_hover': '#982f3d',
        'stop_disabled': '#4d303a', 'field': '#111e29', 'select': '#24685f',
        'nav_selected': '#243e4e', 'nav_hover': '#1b3242', 'nav_ink': '#cedfe8',
        'nav_muted': '#9eb6c8', 'white': '#ffffff', 'demo': '#c7a5eb',
    },
    'light': {
        'bg': '#f3f6fa', 'card': '#ffffff', 'ink': '#152439', 'muted': '#52637a',
        'navy': '#142b40', 'accent': '#087d79', 'line': '#dce4ed', 'tile': '#e7edf4',
        'button': '#eaf0f6', 'hover': '#dde7f0', 'disabled': '#eef1f5',
        'disabled_ink': '#68788a', 'primary': '#087d79', 'primary_hover': '#056965',
        'primary_disabled': '#d6e4e4', 'stop': '#ac3443', 'stop_hover': '#8d2634',
        'stop_disabled': '#efdee2', 'field': '#ffffff', 'select': '#d5ece8',
        'nav_selected': '#23475d', 'nav_hover': '#21425a', 'nav_ink': '#cedfe8',
        'nav_muted': '#9eb6c8', 'white': '#ffffff', 'demo': '#7850a1',
    },
}


def load_preference(path: Path) -> str:
    """A missing, unreadable or invalid preference always starts in dark mode."""
    try:
        with path.open('r', encoding='utf-8') as stream:
            content = stream.read(4097)
        if len(content) > 4096:
            return 'dark'
        payload = json.loads(content)
        theme = payload.get('theme') if isinstance(payload, dict) else None
    except (OSError, ValueError, UnicodeError, RecursionError):
        return 'dark'
    return theme if isinstance(theme, str) and theme in PALETTES else 'dark'


def save_preference(path: Path, theme: str) -> None:
    """Persist only appearance, atomically and separately from the device profile."""
    if theme not in PALETTES:
        raise ValueError('Unknown appearance.')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.appearance-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({'theme': theme}, stream)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Appearance:
    """Keep widget colors semantic so a theme switch preserves UI/controller state."""
    def __init__(self, root: tk.Tk, name: str):
        self.root = root
        self.name = name if name in PALETTES else 'dark'
        self.widgets = WeakKeyDictionary()
        self.windows = WeakKeyDictionary()
        self.style = ttk.Style(root)
        self.style.theme_use('clam')
        self.apply(self.name)
        self.window(root)

    @property
    def colors(self):
        return PALETTES[self.name]

    def paint(self, widget, **roles):
        self.widgets.setdefault(widget, {}).update(roles)
        widget.configure(**{option: self.colors[role] for option, role in roles.items()})
        return widget

    def window(self, window):
        self.windows[window] = True
        self.paint(window, background='bg')
        window.bind('<Map>', lambda event: self._chrome(window) if event.widget is window else None, add='+')
        self._chrome(window)

    def _chrome(self, window):
        if sys.platform != 'win32':
            return
        try:
            parent = ctypes.windll.user32.GetParent
            parent.argtypes = [wintypes.HWND]
            parent.restype = wintypes.HWND
            handle = parent(window.winfo_id())
            set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
            set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            set_attribute.restype = ctypes.c_long
            dark = wintypes.BOOL(self.name == 'dark')
            # Windows 10/11 title bar; unsupported Windows builds retain native chrome.
            for attribute in (20, 19):
                if set_attribute(handle, attribute, ctypes.byref(dark), ctypes.sizeof(dark)) == 0:
                    break
        except (AttributeError, OSError, tk.TclError):
            pass

    def apply(self, name):
        if name not in PALETTES:
            raise ValueError('Unknown appearance.')
        self.name = name
        c, style = self.colors, self.style
        style.configure('.', background=c['card'], foreground=c['ink'],
                        bordercolor=c['line'], lightcolor=c['line'], darkcolor=c['line'],
                        focuscolor=c['accent'], font=('Segoe UI', 10))
        style.configure('TFrame', background=c['card'])
        style.configure('TLabel', background=c['card'], foreground=c['ink'])
        style.configure('TButton', padding=(15, 10), font=('Segoe UI Semibold', 10),
                        background=c['button'], foreground=c['ink'], borderwidth=0)
        style.map('TButton', background=[('disabled', c['disabled']), ('active', c['hover'])],
                  foreground=[('disabled', c['disabled_ink'])])
        for prefix, normal, hover, disabled in (
                ('Primary', 'primary', 'primary_hover', 'primary_disabled'),
                ('Stop', 'stop', 'stop_hover', 'stop_disabled')):
            style.configure(prefix + '.TButton', background=c[normal], foreground=c['white'])
            style.map(prefix + '.TButton', background=[('disabled', c[disabled]), ('active', c[hover])],
                      foreground=[('disabled', c['disabled_ink']), ('!disabled', c['white'])])
        style.configure('TCombobox', padding=7, font=('Segoe UI', 10), fieldbackground=c['field'],
                        background=c['button'], foreground=c['ink'], arrowcolor=c['ink'],
                        selectbackground=c['select'], selectforeground=c['ink'])
        style.map('TCombobox', fieldbackground=[('disabled', c['disabled']), ('readonly', c['field'])],
                  background=[('disabled', c['disabled']), ('active', c['hover'])],
                  foreground=[('disabled', c['disabled_ink']), ('readonly', c['ink'])],
                  arrowcolor=[('disabled', c['disabled_ink'])],
                  selectbackground=[('readonly', c['select'])], selectforeground=[('readonly', c['ink'])])
        style.configure('Sidebar.TCombobox', padding=6)
        style.map('Sidebar.TCombobox', fieldbackground=[('readonly', c['nav_selected'])],
                  foreground=[('readonly', c['white'])], arrowcolor=[('!disabled', c['white'])],
                  background=[('!disabled', c['nav_selected'])],
                  selectbackground=[('readonly', c['nav_selected'])],
                  selectforeground=[('readonly', c['white'])])
        style.configure('TCheckbutton', background=c['card'], foreground=c['ink'], padding=5,
                        indicatorbackground=c['field'], indicatorforeground=c['ink'],
                        upperbordercolor=c['line'], lowerbordercolor=c['line'])
        style.map('TCheckbutton', background=[('active', c['card'])],
                  foreground=[('disabled', c['disabled_ink'])],
                  indicatorbackground=[('disabled', c['disabled']), ('selected', c['select'])],
                  indicatorforeground=[('disabled', c['disabled_ink'])])
        style.configure('Horizontal.TProgressbar', background=c['accent'], troughcolor=c['line'],
                        borderwidth=0, thickness=5)
        style.configure('TScrollbar', background=c['button'], troughcolor=c['bg'],
                        arrowcolor=c['muted'], borderwidth=0, arrowsize=14)
        style.map('TScrollbar', background=[('active', c['hover'])], arrowcolor=[('active', c['ink'])])
        for option, role in (('background', 'field'), ('foreground', 'ink'),
                             ('selectBackground', 'select'), ('selectForeground', 'ink')):
            self.root.option_add('*TCombobox*Listbox.' + option, c[role])
        for widget, roles in list(self.widgets.items()):
            if widget.winfo_exists():
                widget.configure(**{option: c[role] for option, role in roles.items()})
        # Tk caches each combobox popup. Update already-created popups as well.
        def recolor_popups(widget):
            if isinstance(widget, ttk.Combobox):
                popup = str(widget) + '.popdown.f.l'
                if self.root.tk.call('winfo', 'exists', popup):
                    self.root.tk.call(popup, 'configure', '-background', c['field'], '-foreground', c['ink'],
                                      '-selectbackground', c['select'], '-selectforeground', c['ink'])
            for child in widget.winfo_children():
                recolor_popups(child)
        recolor_popups(self.root)
        for window in list(self.windows):
            if window.winfo_exists():
                self._chrome(window)
