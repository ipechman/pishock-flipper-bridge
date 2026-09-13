"""Windowed Windows setup and control application. Launching never operates a device."""
from __future__ import annotations

import argparse
import ctypes
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from types import SimpleNamespace
import webbrowser

import desktop_service as service
import flipper_install as installer
from desktop_paths import InstanceLock, preferences_path, profile_path, resource_root
from desktop_theme import Appearance, load_preference, save_preference


VERSION = '0.2.1'
INSPIRATION_URL = 'https://github.com/Droski1/PiShock-Unofficial-Documentation'


class ScrollPage(tk.Frame):
    """Keep every setup control reachable on small screens and at larger text sizes."""
    def __init__(self, parent, appearance):
        super().__init__(parent)
        appearance.paint(self, background='bg')
        self.canvas = appearance.paint(tk.Canvas(self, highlightthickness=0), background='bg')
        self.bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.content = appearance.paint(tk.Frame(self.canvas), background='bg')
        self.item = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.canvas.bind('<Configure>', self._resize)
        self.content.bind('<Configure>', lambda event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.bind_all('<MouseWheel>', self._wheel, add='+')

    def _resize(self, event):
        self.canvas.itemconfigure(self.item, width=event.width)
        def reflow(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.Label) and int(child.cget('wraplength')):
                    child.configure(wraplength=max(200, event.width - 44))
                reflow(child)
        reflow(self.content)

    def _wheel(self, event):
        widget = event.widget
        while widget is not None:
            if widget in (self.content, self.canvas):
                self.canvas.yview_scroll(-int(event.delta / 120) or (-1 if event.delta > 0 else 1), 'units')
                return
            widget = getattr(widget, 'master', None)


class DemoSession:
    """Explicit preview mode: no real ports, saved profiles, or network calls."""
    def __init__(self):
        self.running = False
        self.events = []

    def start(self, identity, port, *, beep_only=False):
        self.running = True
        self.events.append(SimpleNamespace(kind='ready', message='Demo connection ready.'))

    def request_stop(self):
        self.running = False
        self.events.append(SimpleNamespace(kind='stopped', message='Demo disconnected.'))

    def drain_events(self):
        events, self.events = self.events, []
        return tuple(events)


class BridgeApp(tk.Tk):
    def __init__(self, *, demo=False, first_run=False):
        super().__init__()
        self.demo = demo
        self.title('PiShock Bridge' + (' — Demo' if demo else ''))
        self.geometry('1080x790')
        self.minsize(960, 730)
        self.appearance_path = None if demo else preferences_path()
        self.appearance = Appearance(self, 'dark' if demo else load_preference(self.appearance_path))
        self.theme_choice = tk.StringVar(value=self.appearance.name.title())
        self.theme_note = tk.StringVar(value='')
        icon = resource_root() / 'assets/bridge.ico'
        if icon.is_file():
            self.iconbitmap(default=str(icon))
        self.option_add('*Font', ('Segoe UI', 10))
        self.profile = None
        self.destination = None if demo else profile_path()
        self.session = DemoSession() if demo else service.BridgeSession()
        self.jobs = queue.Queue()
        self.busy = False
        self.closing = False
        self.cancel_job = threading.Event()
        self.controls = []
        self.port_inventory = None
        self.radio_ports = []
        self.hub_ports = []
        self.last_problem = None
        self.console_ports = []
        self.discovery = None
        self.status_text = tk.StringVar(value='Not connected')
        self.detail_text = tk.StringVar(value='Set up your devices, then start a connection.')
        self.target_text = tk.StringVar(value='No device saved')
        self.beep_only = tk.BooleanVar(value=True)
        self.task_text = tk.StringVar(value='Ready when you are.')
        self.install_text = tk.StringVar(value='Bundled add-ons support API 87.1 (official 1.4.3) and API 88.9 on hardware f7.')
        self.hub_text = tk.StringVar(value='Connect your original hub, then find and read it.')
        self.current_page = 'connection'
        self._shell()
        self._connection_page()
        self._setup_page()
        self._help_page()
        self.protocol('WM_DELETE_WINDOW', self.close_app)
        if demo and not first_run:
            self.profile = SimpleNamespace(hub_id=4100, shocker_id=1234, channel=0)
        elif not demo and self.destination.exists():
            try:
                self.profile = service.load_profile(self.destination)
            except Exception:
                self.task_text.set('Your saved profile could not be opened. Use setup to import it again.')
        self._update_profile()
        self.show_page('connection' if self.profile else 'setup')
        self.after(100, self._poll)

    def _frame(self, parent, *, bg='card', border=False, **kw):
        frame = tk.Frame(parent, **kw)
        colors = dict(background=bg)
        if border:
            colors['highlightbackground'] = 'line'
        return self.appearance.paint(frame, **colors)

    def _label(self, parent, text='', *, size=10, color='ink', bold=False, bg='card', **kw):
        label = tk.Label(parent, text=text, anchor='w', justify='left',
                         font=('Segoe UI Semibold' if bold else 'Segoe UI', size), **kw)
        return self.appearance.paint(label, background=bg, foreground=color)

    def _appearance_changed(self, event=None):
        self.appearance.apply(self.theme_choice.get().lower())
        self.show_page(self.current_page)
        self.theme_note.set('')
        if not self.demo:
            try:
                save_preference(self.appearance_path, self.appearance.name)
            except OSError:
                self.theme_note.set('For this session only')

    def _shell(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        sidebar = self._frame(self, bg='navy', width=208)
        sidebar.grid(row=0, column=0, sticky='nsew')
        sidebar.grid_propagate(False)
        mark = self.appearance.paint(tk.Canvas(sidebar, width=46, height=46, highlightthickness=0),
                                     background='navy')
        mark.pack(anchor='w', padx=24, pady=(30, 14))
        mark.create_rectangle(2, 2, 44, 44, fill='#24bbb0', outline='')
        mark.create_line(12, 30, 12, 16, 31, 16, 31, 30, fill='#142b40', width=4)
        mark.create_oval(26, 25, 36, 35, fill='white', outline='')
        self._label(sidebar, 'PiShock\nBridge', size=22, bold=True, color='white', bg='navy').pack(anchor='w', padx=24)
        self._label(sidebar, 'FLIPPER + USB', size=9, color='nav_muted', bg='navy').pack(anchor='w', padx=24, pady=(10, 35))
        self.nav = {}
        for name, text in [('connection', 'Connection'), ('setup', 'Set up devices'), ('help', 'Help & about')]:
            button = tk.Button(sidebar, text=text, anchor='w', padx=18, pady=12,
                               font=('Segoe UI Semibold', 11), bd=0, cursor='hand2',
                               command=lambda value=name: self.show_page(value))
            self.appearance.paint(button, background='navy', foreground='nav_ink',
                                  activebackground='nav_hover', activeforeground='white')
            button.pack(fill='x', padx=12, pady=3)
            self.nav[name] = button
        self._label(sidebar, 'Desktop app  ' + VERSION + '\nWindows • private local setup',
                    size=9, color='nav_muted', bg='navy').pack(side='bottom', anchor='w', padx=24, pady=25)
        appearance = self._frame(sidebar, bg='navy')
        appearance.pack(side='bottom', fill='x', padx=24)
        self._label(appearance, 'Appearance', size=9, color='nav_muted', bg='navy').pack(anchor='w', pady=(0, 7))
        self.theme_combo = ttk.Combobox(appearance, state='readonly', values=('Dark', 'Light'),
                                        textvariable=self.theme_choice, width=13, style='Sidebar.TCombobox')
        self.theme_combo.pack(fill='x')
        self.theme_combo.bind('<<ComboboxSelected>>', self._appearance_changed)
        self._label(appearance, textvariable=self.theme_note, size=8, color='nav_muted',
                    bg='navy').pack(anchor='w', pady=(5, 0))
        workspace = self._frame(self, bg='bg')
        workspace.grid(row=0, column=1, sticky='nsew', padx=28, pady=23)
        workspace.grid_columnconfigure(0, weight=1)
        workspace.grid_rowconfigure(1, weight=1)
        header = self._frame(workspace, bg='bg')
        header.grid(row=0, column=0, sticky='ew', pady=(0, 18))
        self._label(header, 'YOUR DEVICES, CONNECTED', size=9, bold=True, color='muted', bg='bg').pack(side='left')
        self._label(header, 'DEMO · no device or network access' if self.demo else 'Original hub needed only for setup',
                    size=9, color='demo' if self.demo else 'muted', bg='bg').pack(side='right')
        self.container = self._frame(workspace, bg='bg')
        self.container.grid(row=1, column=0, sticky='nsew')
        self.container.grid_columnconfigure(0, weight=1)
        self.container.grid_rowconfigure(0, weight=1)
        self.pages = {}
        self.page_hosts = {}
        for name in ('connection', 'setup', 'help'):
            host = ScrollPage(self.container, self.appearance)
            host.grid(row=0, column=0, sticky='nsew')
            self.page_hosts[name] = host
            self.pages[name] = host.content
        self.progress = ttk.Progressbar(workspace, mode='determinate', maximum=100)
        self.progress.grid(row=2, column=0, sticky='ew', pady=(14, 7))
        self._label(workspace, textvariable=self.task_text, size=9, color='muted', bg='bg',
                    wraplength=770).grid(row=3, column=0, sticky='ew')

    def _heading(self, parent, title, subtitle):
        self._label(parent, title, size=25, bold=True, bg='bg').pack(anchor='w')
        self._label(parent, subtitle, size=10, color='muted', bg='bg',
                    wraplength=745).pack(anchor='w', pady=(8, 20))

    def _card(self, parent, *, pady=8):
        outer = self._frame(parent, bg='card', border=True, highlightthickness=1)
        outer.pack(fill='x', pady=(0, pady))
        inner = self._frame(outer, bg='card')
        inner.pack(fill='both', expand=True, padx=20, pady=16)
        return inner

    def _button(self, parent, text, command, *, primary=False, tracked=True):
        button = ttk.Button(parent, text=text, command=command,
                            style='Primary.TButton' if primary else 'TButton')
        if tracked:
            self.controls.append(button)
        return button

    def _connection_page(self):
        page = self.pages['connection']
        self._heading(page, 'Your Flipper. Your hub.', 'Use your existing PiShock website controls through a simple USB connection.')
        route = self._frame(page, bg='bg')
        route.pack(fill='x', pady=(0, 18))
        for index, (title, caption) in enumerate([('PiShock', 'Website controls'), ('This computer', 'Internet connection'),
                                                  ('Flipper', 'USB + radio'), ('Your shocker', 'Paired device')]):
            route.grid_columnconfigure(index, weight=1, uniform='route')
            tile = self._frame(route, bg='tile')
            tile.grid(row=0, column=index, sticky='nsew', padx=(0, 7 if index < 3 else 0))
            self._label(tile, title, bold=True, bg='tile').pack(anchor='w', padx=13, pady=(12, 3))
            self._label(tile, caption, size=9, color='muted', bg='tile').pack(anchor='w', padx=13, pady=(0, 12))
        card = self._card(page, pady=16)
        self.state_pill = self._label(card, textvariable=self.status_text, size=11, bold=True, color='accent')
        self.state_pill.pack(anchor='w')
        self._label(card, textvariable=self.target_text, size=20, bold=True).pack(anchor='w', pady=(15, 7))
        self._label(card, textvariable=self.detail_text, color='muted', wraplength=705).pack(anchor='w', pady=(0, 16))
        row = self._frame(card, bg='card')
        row.pack(fill='x')
        self.radio_combo = ttk.Combobox(row, state='readonly', width=29)
        self.radio_combo.pack(side='left', fill='x', expand=True, padx=(0, 10))
        self._button(row, 'Find Flipper', self.refresh_radio).pack(side='left')
        self.mode_toggle = ttk.Checkbutton(card, text='Beep-only test  ·  ignore shock and vibration', variable=self.beep_only)
        self.mode_toggle.pack(anchor='w', pady=(15, 12))
        actions = self._frame(card, bg='card')
        actions.pack(fill='x')
        self.connect_button = self._button(actions, 'Connect', self.connect, primary=True, tracked=False)
        self.connect_button.pack(side='left', padx=(0, 10))
        self.stop_button = ttk.Button(actions, text='Stop & disconnect', command=self.stop, style='Stop.TButton')
        self.stop_button.pack(side='left')
        self._button(actions, 'Open PiShock', self.open_website, tracked=False).pack(side='right')
        note = self._card(page)
        self._label(note, 'You stay in control', size=12, bold=True).pack(anchor='w')
        self._label(note, 'Press OK on the Flipper to arm. Back stops and disarms.\n'
                    'Set the local intensity limit on the Flipper while disarmed; it starts at 20%.',
                    color='muted', wraplength=710).pack(anchor='w', pady=(8, 0))
        self._label(page, 'Keep the original hub unplugged during use. Keep this app open and the computer awake.',
                    size=9, color='muted', bg='bg', wraplength=745).pack(anchor='w', pady=9)

    def _setup_page(self):
        page = self.pages['setup']
        self._heading(page, 'A few steps. Then you’re ready.', 'Install the Flipper add-on and bring over your existing hub. No commands to type.')
        card = self._card(page, pady=12)
        self._label(card, '1   Install the Flipper app', size=14, bold=True).pack(anchor='w')
        self._label(card, 'Connect the Flipper by USB and close any app running on it. Close qFlipper first.',
                    color='muted', wraplength=710).pack(anchor='w', pady=(6, 10))
        row = self._frame(card, bg='card')
        row.pack(fill='x')
        self.console_combo = ttk.Combobox(row, state='readonly', width=26)
        self.console_combo.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self._button(row, 'Find Flipper', self.refresh_consoles).pack(side='left', padx=(0, 8))
        self._button(row, 'Install app', self.install_flipper, primary=True).pack(side='left')
        self._label(card, textvariable=self.install_text, size=9, color='muted', wraplength=710).pack(anchor='w', pady=(10, 0))
        self._label(card, 'Already have PiShock USB Radio installed? Continue with step 2.',
                    size=9, color='muted').pack(anchor='w', pady=(4, 0))
        card = self._card(page, pady=12)
        self._label(card, '2   Import your hub', size=14, bold=True).pack(anchor='w')
        self._label(card, 'Connect your original PiShock hub by USB while it is online. We’ll find its paired devices.',
                    color='muted', wraplength=710).pack(anchor='w', pady=(6, 10))
        row = self._frame(card, bg='card')
        row.pack(fill='x')
        self.hub_combo = ttk.Combobox(row, state='readonly', width=26)
        self.hub_combo.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.hub_combo.bind('<<ComboboxSelected>>', self._hub_changed)
        self._button(row, 'Find hub', self.refresh_hubs).pack(side='left', padx=(0, 8))
        self._button(row, 'Read paired devices', self.read_hub).pack(side='left')
        self._label(card, textvariable=self.hub_text, size=9, color='muted', wraplength=710).pack(anchor='w', pady=(9, 9))
        row = self._frame(card, bg='card')
        row.pack(fill='x')
        self.shocker_combo = ttk.Combobox(row, state='readonly', width=33)
        self.shocker_combo.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self._button(row, 'Save this device', self.save_device, primary=True).pack(side='left')
        self._button(card, 'Use an existing CLI profile…', self.import_existing).pack(anchor='w', pady=(10, 0))
        bottom = self._card(page)
        self._label(bottom, '3   Unplug the original hub and connect', size=13, bold=True).pack(anchor='w')
        self._label(bottom, 'Open PiShock USB Radio on your Flipper, then go to Connection.\n'
                    'Start with a short website beep while the shocker is powered on and off-body.',
                    color='muted', wraplength=710).pack(anchor='w', pady=(6, 10))
        self._button(bottom, 'Go to connection', lambda: self.show_page('connection')).pack(anchor='w')

    def _help_page(self):
        page = self.pages['help']
        self._heading(page, 'Help, without the guesswork.', 'A quick reference for everyday use, with your full guide one click away.')
        card = self._card(page, pady=14)
        for title, text in [
            ('Connect each session', 'Open the Flipper app, choose Find Flipper, then Connect. Once connected, press OK on the Flipper to arm.'),
            ('If a beep is silent', 'Check shocker power, target, pairing and distance. The app can confirm the Flipper accepted a command; only hearing it confirms delivery.'),
            ('If the connection stops', 'Check USB, internet and the computer’s sleep state. Connect again, physically re-arm and send a fresh command. Commands are never replayed.'),
            ('Keep your profile private', 'Your identity is encrypted for your Windows account and stored in your local application data. Share source and installers, never device.dpapi or raw hub responses.'),
        ]:
            self._label(card, title, bold=True).pack(anchor='w', pady=(0, 4))
            self._label(card, text, color='muted', wraplength=710).pack(anchor='w', pady=(0, 12))
        self._button(card, 'Read the full user guide', self.open_guide).pack(anchor='w')
        about = self._card(page)
        self._label(about, 'Built on community knowledge', size=14, bold=True).pack(anchor='w')
        self._label(about, 'Acknowledgment to Droski1: PiShock-Unofficial-Documentation inspired this project.\n'
                    'Thanks also to OpenShock for the CaiXianlin encoder and protocol references.',
                    color='muted', wraplength=710).pack(anchor='w', pady=(8, 12))
        self._button(about, 'Droski1’s original documentation', self.open_inspiration, tracked=False).pack(anchor='w')
        self._label(about, 'Independent community software • GPL-3.0 • Version ' + VERSION + '\n'
                    'Not affiliated with PiShock or Flipper Devices.', size=9, color='muted').pack(anchor='w', pady=(12, 0))

    def show_page(self, name):
        self.current_page = name
        self.page_hosts[name].tkraise()
        for key, button in self.nav.items():
            self.appearance.paint(button, background='nav_selected' if key == name else 'navy',
                                  foreground='white' if key == name else 'nav_ink')

    def _update_profile(self):
        if self.profile:
            self.target_text.set(f'Hub {self.profile.hub_id}  /  Shocker {self.profile.shocker_id}')
            self.detail_text.set('Open PiShock USB Radio on your Flipper, find it below, then connect.')
        else:
            self.target_text.set('Let’s set up your devices')
            self.detail_text.set('Use Set up devices to install the add-on and save your hub.')
        self._update_controls()

    def _update_controls(self):
        locked = self.busy or self.session.running or self.closing
        for button in self.controls:
            button.configure(state='disabled' if locked else 'normal')
        for combo in (self.radio_combo, self.console_combo, self.hub_combo, self.shocker_combo):
            combo.configure(state='disabled' if locked else 'readonly')
        self.mode_toggle.configure(state='disabled' if locked else 'normal')
        self.connect_button.configure(state='normal' if self.profile and not locked else 'disabled')
        self.stop_button.configure(state='normal' if self.session.running and not self.closing else 'disabled')

    def _background(self, label, action, done):
        if self.busy or self.session.running or self.closing:
            return
        self.busy = True
        self.cancel_job.clear()
        self.task_text.set(label)
        self.progress.configure(value=0)
        self._update_controls()
        def worker():
            try:
                result = action()
                self.jobs.put(('done', done, result))
            except Exception as error:
                # Only this project's controlled exception messages are displayed.
                safe_types = tuple(t for t in (getattr(service, 'SetupError', None),
                                                getattr(service, 'DesktopError', None),
                                                getattr(installer, 'InstallError', None)) if isinstance(t, type))
                message = str(error) if safe_types and isinstance(error, safe_types) else (
                    'That step could not finish. Check the USB cable, close other device apps, and try again.')
                self.jobs.put(('error', None, message))
        threading.Thread(target=worker, name='device-setup', daemon=True).start()

    @staticmethod
    def _fill(combo, choices):
        combo.configure(values=[choice.label for choice in choices])
        if choices:
            combo.current(0)
        else:
            combo.set('No device found')

    def refresh_radio(self):
        def done(inventory):
            self.radio_ports = inventory.flippers
            self._fill(self.radio_combo, inventory.flippers)
            self.task_text.set('Flipper found. Connect when ready.' if inventory.flippers else
                               'No Flipper radio app found. Open PiShock USB Radio on the device, then try again.')
        self._background('Looking for the Flipper radio app…', self._inventory, done)

    def _inventory(self):
        if self.demo:
            return SimpleNamespace(hubs=[SimpleNamespace(device='COM6', label='PiShock hub · COM6 (demo)')],
                                   flippers=[SimpleNamespace(device='COM7', label='Flipper radio · COM7 (demo)')])
        return service.enumerate_ports()

    def refresh_consoles(self):
        def done(ports):
            self.console_ports = ports
            self._fill(self.console_combo, ports)
            self.install_text.set('Select your Flipper and choose Install app.' if ports else
                                  'Close the Flipper app and qFlipper, check the cable, then find it again.')
        self._background('Looking for a Flipper ready for app installation…',
                         lambda: [SimpleNamespace(device='COM5', label='Flipper USB · COM5 (demo)')]
                         if self.demo else installer.discover_console_ports(), done)

    def install_flipper(self):
        index = self.console_combo.current()
        if not 0 <= index < len(self.console_ports):
            self.task_text.set('Choose Find Flipper and select a device first.')
            return
        port = self.console_ports[index].device
        def action():
            if self.demo:
                return None
            return installer.install_app(port, resource_root(), cancel=self.cancel_job,
                                         progress=lambda percent, message: self.jobs.put(('progress', percent, message)))
        def done(result):
            self.install_text.set('App installed and verified. Open Apps → Sub-GHz → PiShock USB Radio on the Flipper.'
                                  if not self.demo else 'Demo installation complete. No files were sent to a device.')
            self.task_text.set('Flipper app step complete. Next, import your original hub.')
        self._background('Checking firmware compatibility and installing the Flipper add-on…', action, done)

    def refresh_hubs(self):
        def done(inventory):
            self.hub_ports = inventory.hubs
            self.discovery = None
            self.shocker_combo.set('')
            self.shocker_combo.configure(values=[])
            self._fill(self.hub_combo, inventory.hubs)
            self.hub_text.set('Choose Read paired devices to continue.' if inventory.hubs else
                              'No hub found. Connect the original PiShock hub by USB, then try again.')
        self._background('Looking for your original PiShock hub…', self._inventory, done)

    def read_hub(self):
        hubs = self.hub_ports
        index = self.hub_combo.current()
        if not 0 <= index < len(hubs):
            self.task_text.set('Choose Find hub and select your original hub first.')
            return
        port = hubs[index].device
        def action():
            if self.demo:
                return SimpleNamespace(port=port, hub_id=4100,
                                       shockers=[SimpleNamespace(shocker_id=1234, label='SmallOne · 1234 (demo)')])
            return service.discover_hub(port)
        def done(discovery):
            self.discovery = discovery
            self._fill(self.shocker_combo, discovery.shockers)
            self.hub_text.set(f'Hub {discovery.hub_id} found. Select the paired device to use with your Flipper.')
            self.task_text.set('Device information read. No operation was sent.')
        self._background('Reading paired devices from your original hub…', action, done)

    def _hub_changed(self, event=None):
        self.discovery = None
        self.shocker_combo.set('')
        self.shocker_combo.configure(values=[])
        self.hub_text.set('Read the selected hub before choosing a paired device.')

    def _has_saved_file(self):
        return bool(self.profile) if self.demo else self.destination.exists()

    def _replacement_allowed(self):
        return not self._has_saved_file() or messagebox.askyesno(
            'Replace saved device?', 'Replace the device saved in this desktop app? Your original hub and CLI profile will stay unchanged.', parent=self)

    def save_device(self):
        index = self.shocker_combo.current()
        if self.discovery is None or not 0 <= index < len(self.discovery.shockers):
            self.task_text.set('Read your hub’s paired devices and choose one first.')
            return
        if not self._replacement_allowed():
            return
        discovery, shocker = self.discovery, self.discovery.shockers[index]
        replace_existing = self._has_saved_file()
        def action():
            if self.demo:
                return SimpleNamespace(hub_id=discovery.hub_id, shocker_id=shocker.shocker_id, channel=0)
            return service.import_profile(discovery, shocker.shocker_id, self.destination, replace_existing=replace_existing)
        self._background('Saving your selected device securely on this computer…', action, self._saved)

    def import_existing(self):
        if self.demo:
            self.task_text.set('Demo mode does not read profiles. Use Import your hub to preview setup.')
            return
        if not self._replacement_allowed():
            return
        source = filedialog.askopenfilename(parent=self, title='Choose your existing CLI device.dpapi profile',
                                          filetypes=[('Encrypted device profile', '*.dpapi')])
        if not source:
            return
        replace_existing = self._has_saved_file()
        self._background('Importing your existing encrypted profile…',
                         lambda: service.import_saved_profile(Path(source), self.destination,
                                                              replace_existing=replace_existing), self._saved)

    def _saved(self, identity):
        self.profile = identity
        self._update_profile()
        self.hub_text.set('Saved. Unplug the original hub before connecting the desktop bridge.')
        self.task_text.set('Setup complete. Open the Flipper app, then go to Connection.')

    def connect(self):
        if self.busy or self.session.running or self.closing or not self.profile:
            return
        ports = self.radio_ports
        index = self.radio_combo.current()
        if not 0 <= index < len(ports):
            self.task_text.set('Open PiShock USB Radio on your Flipper, then choose Find Flipper.')
            return
        try:
            self.last_problem = None
            self.session.start(self.profile, ports[index].device, beep_only=self.beep_only.get())
        except Exception:
            self.task_text.set('The connection could not start. Check the device selection and try again.')
            return
        self.status_text.set('Connecting…')
        self.detail_text.set('Connecting to PiShock. Keep the original hub unplugged.')
        self._update_controls()

    def stop(self):
        self.session.request_stop()
        self.status_text.set('Stopping…')
        self.detail_text.set('Stopping output and disconnecting. You can always press Back on the Flipper.')
        self._update_controls()

    def _event(self, event):
        kind = event.kind
        if self.last_problem and self.last_problem[0] == 'warning' and kind in ('error', 'failure'):
            return  # Preserve the explicit physical-stop instruction through teardown.
        if kind == 'ready':
            self.status_text.set('Connected · arm on Flipper')
            self.detail_text.set('Press OK on the Flipper to arm, then use PiShock’s website. '
                                 + ('Beep-only test is on.' if self.beep_only.get() else 'Your Flipper’s local limit applies.'))
        elif kind in ('connecting', 'starting'):
            self.status_text.set('Connecting…')
        elif kind in ('revalidating', 'invalidated'):
            self.status_text.set('Refreshing permissions…')
            self.detail_text.set('Output is being stopped. Wait for the connection to be ready and re-arm on the Flipper.')
        elif kind in ('error', 'failure'):
            self.last_problem = ('error', event.message)
            self.status_text.set('Connection stopped')
            self.detail_text.set(event.message)
        elif kind in ('stop_unconfirmed', 'warning'):
            self.last_problem = ('warning', event.message)
            self.status_text.set('Check the Flipper')
            self.detail_text.set('Stop could not be confirmed over USB. Press Back on the Flipper.')
        elif kind == 'stopping':
            self.status_text.set('Stopping…')
        elif kind == 'stopped':
            if self.last_problem:
                self.status_text.set('Disconnected · check the Flipper' if self.last_problem[0] == 'warning' else 'Connection ended')
                self.detail_text.set(self.last_problem[1])
            else:
                self.status_text.set('Not connected')
                self.detail_text.set('Connect again when ready. Arming always happens on the Flipper.')
        if event.message and not (kind == 'stopped' and self.last_problem):
            self.task_text.set(event.message)

    def _poll(self):
        for _ in range(100):
            try:
                kind, action, value = self.jobs.get_nowait()
            except queue.Empty:
                break
            if kind == 'progress':
                self.progress.configure(value=action)
                self.task_text.set(value)
            else:
                self.busy = False
                self.progress.configure(value=100 if kind == 'done' else 0)
                if not self.closing:
                    if kind == 'done':
                        action(value)
                    else:
                        self.task_text.set(value)
        for event in self.session.drain_events():
            self._event(event)
        self._update_controls()
        if self.closing and not self.session.running and not self.busy:
            self.destroy()
            return
        self.after(100, self._poll)

    def close_app(self):
        if self.closing:
            return
        self.closing = True
        self.cancel_job.set()
        self.session.request_stop()
        self.task_text.set('Finishing the current step and stopping output before closing…')
        self._update_controls()

    def open_website(self):
        if self.demo:
            self.task_text.set('Demo: website links do not open.')
        else:
            webbrowser.open('https://pishock.com/')

    def open_inspiration(self):
        if self.demo:
            self.task_text.set('Droski1: ' + INSPIRATION_URL)
        else:
            webbrowser.open(INSPIRATION_URL)

    def open_guide(self):
        window = tk.Toplevel(self)
        window.title('PiShock Bridge — User guide')
        window.geometry('820x660')
        self.appearance.window(window)
        text = tk.Text(window, wrap='word', padx=24, pady=22,
                       font=('Segoe UI', 11), relief='flat', spacing3=6)
        self.appearance.paint(text, background='card', foreground='ink', insertbackground='ink',
                              selectbackground='select', selectforeground='ink')
        scroll = ttk.Scrollbar(window, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        try:
            content = (resource_root() / 'docs/USER_GUIDE.md').read_text(encoding='utf-8')
        except OSError:
            content = 'The user guide is unavailable. Reinstall the desktop application to restore its bundled guide.'
        text.tag_configure('heading', font=('Segoe UI Semibold', 15), spacing1=14, spacing3=9)
        for line in content.splitlines():
            text.insert('end', line.lstrip('#').strip() + '\n' if line.startswith('#') else line + '\n',
                        'heading' if line.startswith('#') else ())
        text.configure(state='disabled')

    def report_callback_exception(self, exc, value, traceback):
        # Do not include private paths, raw USB responses, or network URLs in UI errors.
        self.session.request_stop()
        self.task_text.set('The interface hit an unexpected error and requested a stop. Press Back on the Flipper, then restart the app.')


def self_test():
    import serial
    import device_backend
    import device_transport
    if not serial or not device_backend or not device_transport:
        return 1
    for name in ('docs/USER_GUIDE.md', 'LICENSE', 'NOTICE.md'):
        if not (resource_root() / name).is_file():
            return 2
    for api in installer.ASSETS:
        installer._asset_bytes(resource_root(), api)
    # Build every real page with a synthetic profile and a simulated controller.
    # This also checks packaged ttk themes/icons without opening a device.
    probe = BridgeApp(demo=True, first_run=True)
    probe.withdraw()
    probe.update_idletasks()
    for name in ('Light', 'Dark'):
        probe.theme_choice.set(name)
        probe._appearance_changed()
        probe.update_idletasks()
    probe.destroy()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='PiShock Bridge desktop application')
    parser.add_argument('--self-test', action='store_true', help='Check packaged imports/resources; no devices or profiles accessed')
    parser.add_argument('--demo', action='store_true', help='Preview with synthetic data and all device/network I/O disabled')
    parser.add_argument('--first-run', action='store_true', help='Show initial setup in demo mode')
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if sys.platform == 'win32':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    lock = InstanceLock()
    try:
        if not args.demo:
            lock.acquire()
        app = BridgeApp(demo=args.demo, first_run=args.first_run)
        app.mainloop()
    except RuntimeError as error:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('PiShock Bridge', str(error), parent=root)
        root.destroy()
        return 1
    finally:
        lock.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
