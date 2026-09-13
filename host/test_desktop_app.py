"""Windowed UI state tests with demo data and all real I/O blocked.

Windows CI runs these normally. Hosts without Tk or a display skip this class;
the controller, installer and protocol tests remain independent of the GUI.
"""
from contextlib import ExitStack
import gc
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

try:
    import tkinter as tk
except ImportError:
    tk = None

if tk is not None:
    # Missing application dependencies must fail CI instead of silently
    # masquerading as an unavailable display.
    import desktop_app as desktop
    import desktop_theme as appearance
else:
    desktop = None


def event(kind, message="Synthetic status"):
    return SimpleNamespace(kind=kind, message=message)


@unittest.skipIf(desktop is None, "Tk is unavailable on this host")
class DesktopAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            probe = tk.Tk()
            probe.withdraw()
            probe.update_idletasks()
            probe.destroy()
        except tk.TclError:
            raise unittest.SkipTest("Tk has no available display") from None

    def setUp(self):
        # This runs last, after window destruction and all patch cleanup. Tk
        # variables must be collected on their owner thread, never by a later
        # synthetic setup worker that happens to trigger cyclic collection.
        self.addCleanup(self.release_fixture)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.worker_threads = []
        thread_type = threading.Thread

        def setup_thread(*args, **kwargs):
            thread = thread_type(*args, **kwargs)
            self.worker_threads.append(thread)
            return thread

        self.stack.enter_context(patch.object(desktop.threading, "Thread", side_effect=setup_thread))
        self.blocked = []
        for owner, names in (
                (desktop.service, ("enumerate_ports", "load_profile", "import_profile",
                                   "import_saved_profile", "BridgeSession")),
                (desktop.installer, ("discover_console_ports", "install_app")),
                (desktop.webbrowser, ("open",)),
                (desktop, ("profile_path", "preferences_path", "load_preference", "save_preference"))):
            for name in names:
                mocked = self.stack.enter_context(patch.object(
                    owner, name, side_effect=AssertionError("Real I/O is forbidden in desktop tests.")))
                self.blocked.append(mocked)
        self.confirm = self.stack.enter_context(patch.object(desktop.messagebox, "askyesno", return_value=False))
        self.picker = self.stack.enter_context(patch.object(desktop.filedialog, "askopenfilename", return_value=""))
        self.app = desktop.BridgeApp(demo=True, first_run=True)
        self.app_reference = weakref.ref(self.app)
        self.app.withdraw()
        self.app.update_idletasks()
        self.addCleanup(self.destroy_app)
        self.addCleanup(self.assert_no_real_io)
        self.addCleanup(self.join_workers)

    def join_workers(self):
        # A queued result can be polled just before its worker actually exits.
        # Per-test release events run before this cleanup if a test fails early.
        for thread in self.worker_threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive(), "A synthetic setup worker outlived its test.")

    def release_fixture(self):
        reference = getattr(self, "app_reference", None)
        # Dialog mocks retain parent=self.app in call history. Clearing only
        # self.app is therefore insufficient, even after Tk.destroy().
        for name in ("app", "confirm", "picker", "blocked", "stack", "worker_threads"):
            setattr(self, name, None)
        gc.collect()
        if reference is not None:
            self.assertIsNone(reference(), "A completed test retained its Tk window.")

    def assert_no_real_io(self):
        for mocked in self.blocked:
            mocked.assert_not_called()

    def destroy_app(self):
        try:
            for identifier in self.app.tk.call("after", "info"):
                self.app.after_cancel(identifier)
            self.app.destroy()
        except tk.TclError:
            pass

    def until(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while not predicate():
            self.app.update()
            if time.monotonic() > deadline:
                self.fail("The synthetic UI did not reach the expected state.")
            time.sleep(0.002)
        self.app.update_idletasks()

    def run_step(self, action):
        action()
        self.until(lambda: not self.app.busy)

    def fill_profile(self):
        self.app.profile = SimpleNamespace(hub_id=4100, shocker_id=1234, channel=0)
        self.app._update_profile()

    def fill_discovery(self):
        self.app.discovery = SimpleNamespace(
            port="COM6", hub_id=4100,
            shockers=[SimpleNamespace(shocker_id=1234, label="SmallOne 1234 (demo)")])
        self.app._fill(self.app.shocker_combo, self.app.discovery.shockers)

    def test_first_run_requires_setup_and_does_not_scan_or_load_automatically(self):
        self.assertIsNone(self.app.profile)
        self.assertEqual(self.app.current_page, "setup")
        self.assertTrue(self.app.connect_button.instate(["disabled"]))
        self.assertTrue(self.app.stop_button.instate(["disabled"]))
        self.assertTrue(self.app.beep_only.get())
        self.assertEqual(self.app.radio_ports, [])
        self.assertEqual(self.app.hub_ports, [])

    def test_busy_job_locks_mode_and_setup_until_completion_is_polled(self):
        self.fill_profile()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        completed = []

        def action():
            entered.set()
            release.wait(1)
            return "synthetic result"

        self.app._background("Synthetic setup", action, completed.append)
        self.assertTrue(entered.wait(1))
        self.assertTrue(self.app.busy)
        self.assertTrue(self.app.mode_toggle.instate(["disabled"]))
        self.assertTrue(self.app.connect_button.instate(["disabled"]))
        self.assertTrue(all(button.instate(["disabled"]) for button in self.app.controls))
        self.assertEqual(str(self.app.hub_combo.cget("state")), "disabled")
        rejected = []
        self.app._background("Must not start", lambda: rejected.append(True), completed.append)
        release.set()
        self.until(lambda: not self.app.busy)
        self.assertEqual(completed, ["synthetic result"])
        self.assertEqual(rejected, [])
        self.assertTrue(self.app.mode_toggle.instate(["!disabled"]))
        self.assertTrue(self.app.connect_button.instate(["!disabled"]))
        self.assertEqual(str(self.app.hub_combo.cget("state")), "readonly")

    def test_connected_session_locks_mode_setup_and_device_choices(self):
        self.fill_profile()
        self.run_step(self.app.refresh_radio)
        self.app.connect()
        self.until(lambda: self.app.status_text.get().startswith("Connected"))
        self.assertTrue(self.app.session.running)
        self.assertTrue(self.app.mode_toggle.instate(["disabled"]))
        self.assertTrue(self.app.connect_button.instate(["disabled"]))
        self.assertTrue(self.app.stop_button.instate(["!disabled"]))
        self.assertTrue(all(button.instate(["disabled"]) for button in self.app.controls))
        for combo in (self.app.radio_combo, self.app.console_combo, self.app.hub_combo, self.app.shocker_combo):
            self.assertEqual(str(combo.cget("state")), "disabled")
        self.app.stop()
        self.until(lambda: self.app.status_text.get() == "Not connected")
        self.assertTrue(self.app.mode_toggle.instate(["!disabled"]))
        self.assertTrue(self.app.stop_button.instate(["disabled"]))

    def test_missing_radio_selection_does_not_start_session(self):
        self.fill_profile()
        self.app.connect()
        self.assertFalse(self.app.session.running)
        self.assertIn("Find Flipper", self.app.task_text.get())

    def test_error_survives_stopped_and_new_connect_clears_it(self):
        message = "Synthetic connection failure. Check the USB cable."
        self.app._event(event("error", message))
        self.app._event(event("stopped", "Disconnected"))
        self.assertEqual(self.app.detail_text.get(), message)
        self.assertEqual(self.app.task_text.get(), message)
        self.assertEqual(self.app.status_text.get(), "Connection ended")
        self.fill_profile()
        self.run_step(self.app.refresh_radio)
        self.app.connect()
        self.until(lambda: self.app.status_text.get().startswith("Connected"))
        self.assertIsNone(self.app.last_problem)

    def test_unconfirmed_stop_warning_survives_stopped(self):
        message = "Stop acknowledgment unavailable. Press Back on the Flipper."
        self.app._event(event("warning", message))
        self.app._event(event("stopped", "Disconnected"))
        self.assertEqual(self.app.detail_text.get(), message)
        self.assertEqual(self.app.task_text.get(), message)
        self.assertEqual(self.app.status_text.get(), "Disconnected · check the Flipper")

    def test_unconfirmed_stop_survives_later_generic_error_and_stopped(self):
        message = "Stop acknowledgment unavailable. Press Back on the Flipper."
        self.app._event(event("warning", message))
        self.app._event(event("error", "The connection ended. Check USB and internet."))
        self.app._event(event("stopped", "Disconnected"))
        self.assertEqual(self.app.last_problem, ("warning", message))
        self.assertEqual(self.app.detail_text.get(), message)
        self.assertEqual(self.app.status_text.get(), "Disconnected · check the Flipper")

    def test_connect_cannot_start_a_session_after_close_has_begun(self):
        self.fill_profile()
        self.run_step(self.app.refresh_radio)
        self.app.close_app()
        with patch.object(self.app.session, "start") as started:
            self.app.connect()
        started.assert_not_called()
        self.assertTrue(self.app.closing)
        self.assertFalse(self.app.session.running)

    def test_revalidation_never_claims_confirmed_disarm_or_arming(self):
        self.app._event(event("revalidating", "Checking updated settings"))
        self.assertEqual(self.app.status_text.get(), "Refreshing permissions…")
        self.assertIn("being stopped", self.app.detail_text.get())
        self.assertNotIn("is disarmed", self.app.detail_text.get())
        self.assertNotIn("is armed", self.app.detail_text.get())

    def test_hub_selection_change_discards_stale_discovery_and_target(self):
        self.fill_discovery()
        self.assertEqual(self.app.shocker_combo.current(), 0)
        self.app._hub_changed()
        self.assertIsNone(self.app.discovery)
        self.assertEqual(self.app.shocker_combo.get(), "")
        self.assertEqual(tuple(self.app.shocker_combo.cget("values")), ())
        self.app.save_device()
        self.assertIsNone(self.app.profile)
        self.assertIn("Read your hub", self.app.task_text.get())

    def test_radio_and_hub_inventories_remain_independent(self):
        self.run_step(self.app.refresh_radio)
        selected_radio = self.app.radio_combo.get()
        self.run_step(self.app.refresh_hubs)
        self.run_step(self.app.read_hub)
        discovery = self.app.discovery
        self.assertEqual(self.app.hub_ports[0].device, "COM6")
        self.assertEqual(self.app.radio_ports[0].device, "COM7")
        self.assertEqual(self.app.radio_combo.get(), selected_radio)
        self.run_step(self.app.refresh_radio)
        self.assertIs(self.app.discovery, discovery)
        self.assertEqual(self.app.hub_ports[0].device, "COM6")
        self.run_step(self.app.refresh_hubs)
        self.assertIsNone(self.app.discovery)
        self.assertEqual(self.app.radio_ports[0].device, "COM7")

    def test_corrupt_existing_profile_still_requires_replacement_approval(self):
        # Simulate a file that exists but was not successfully loaded. Only the
        # existence check is used; no real profile path or filesystem is read.
        self.app.demo = False
        self.app.destination = SimpleNamespace(exists=lambda: True)
        self.app.profile = None
        self.fill_discovery()
        self.confirm.return_value = False
        self.app.save_device()
        self.confirm.assert_called_once()
        self.assertFalse(self.app.busy)
        self.assertIsNone(self.app.profile)
        self.picker.assert_not_called()

    def test_existing_profile_decline_prevents_file_picker_and_import(self):
        self.app.demo = False
        self.app.destination = SimpleNamespace(exists=lambda: True)
        self.confirm.return_value = False
        self.app.import_existing()
        self.confirm.assert_called_once()
        self.picker.assert_not_called()
        self.assertFalse(self.app.busy)

    def test_close_waits_for_session_without_blocking_other_ui_callbacks(self):
        class WaitingSession:
            running = True

            def __init__(self):
                self.stops = 0

            def request_stop(self):
                self.stops += 1

            def drain_events(self):
                return ()

        controller = WaitingSession()
        self.app.session = controller
        responsive = []
        with patch.object(self.app, "destroy") as destroyed:
            self.app.close_app()
            self.app.close_app()
            self.assertEqual(controller.stops, 1)
            self.assertTrue(self.app.cancel_job.is_set())
            self.assertTrue(self.app.closing)
            self.assertTrue(self.app.mode_toggle.instate(["disabled"]))
            self.app.after(0, lambda: responsive.append(True))
            self.until(lambda: bool(responsive))
            destroyed.assert_not_called()
            controller.running = False
            self.until(lambda: destroyed.called)
            destroyed.assert_called_once()

    def test_close_waits_for_setup_completion_without_applying_late_result(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        results = []

        def action():
            entered.set()
            release.wait(1)
            return "late result"

        self.app._background("Synthetic setup", action, results.append)
        self.assertTrue(entered.wait(1))
        with patch.object(self.app, "destroy") as destroyed:
            self.app.close_app()
            self.app.update()
            destroyed.assert_not_called()
            release.set()
            self.until(lambda: destroyed.called)
        self.assertEqual(results, [])
        self.assertFalse(self.app.busy)

    def test_first_run_demo_setup_connect_and_stop_through_async_poll(self):
        self.assertEqual(self.app.current_page, "setup")
        self.run_step(self.app.refresh_consoles)
        self.assertEqual(self.app.console_ports[0].device, "COM5")
        self.run_step(self.app.install_flipper)
        self.assertIn("Demo installation complete", self.app.install_text.get())
        self.run_step(self.app.refresh_hubs)
        self.run_step(self.app.read_hub)
        self.assertEqual(self.app.discovery.hub_id, 4100)
        self.assertEqual(self.app.discovery.shockers[0].shocker_id, 1234)
        self.run_step(self.app.save_device)
        self.assertEqual(self.app.profile.shocker_id, 1234)
        self.confirm.assert_not_called()
        self.assertIn("Unplug the original hub", self.app.hub_text.get())
        self.app.show_page("connection")
        self.run_step(self.app.refresh_radio)
        self.app.connect()
        self.until(lambda: self.app.status_text.get().startswith("Connected"))
        self.assertIn("arm on Flipper", self.app.status_text.get())
        self.assertIn("Beep-only test is on", self.app.detail_text.get())
        self.app.stop()
        self.until(lambda: self.app.status_text.get() == "Not connected")
        self.assertFalse(self.app.session.running)
        self.assertTrue(self.app.connect_button.instate(["!disabled"]))

    def test_unexpected_worker_errors_are_sanitized(self):
        def action():
            raise OSError("DO_NOT_DISPLAY_PRIVATE_PATH_OR_CREDENTIAL")

        self.run_step(lambda: self.app._background("Synthetic failure", action, lambda result: None))
        self.assertNotIn("DO_NOT_DISPLAY", self.app.task_text.get())
        self.assertIn("could not finish", self.app.task_text.get())
        self.assertFalse(self.app.busy)

    def test_dark_is_default_and_theme_switch_preserves_connected_state(self):
        self.assertEqual(self.app.appearance.name, 'dark')
        self.assertEqual(self.app.cget('background'), appearance.PALETTES['dark']['bg'])
        self.fill_profile()
        self.run_step(self.app.refresh_radio)
        self.app.connect()
        self.until(lambda: self.app.status_text.get().startswith('Connected'))
        controller = self.app.session
        snapshot = (self.app.profile, self.app.radio_combo.get(), self.app.current_page,
                    self.app.beep_only.get(), self.app.status_text.get(), self.app.detail_text.get())
        for name in ('Light', 'Dark'):
            self.app.theme_choice.set(name)
            self.app._appearance_changed()
            self.app.update_idletasks()
            self.assertEqual(self.app.appearance.name, name.lower())
            self.assertEqual(self.app.cget('background'), appearance.PALETTES[name.lower()]['bg'])
            self.assertIs(self.app.session, controller)
            self.assertTrue(controller.running)
            self.assertTrue(self.app.mode_toggle.instate(['disabled']))
            self.assertTrue(self.app.stop_button.instate(['!disabled']))
            self.assertEqual(snapshot, (self.app.profile, self.app.radio_combo.get(), self.app.current_page,
                                       self.app.beep_only.get(), self.app.status_text.get(), self.app.detail_text.get()))

    def test_open_dropdown_and_guide_follow_theme_switch(self):
        popup = self.app.tk.call('ttk::combobox::PopdownWindow', str(self.app.radio_combo))
        listbox = str(popup) + '.f.l'
        self.assertEqual(self.app.tk.call(listbox, 'cget', '-background'),
                         appearance.PALETTES['dark']['field'])
        self.app.open_guide()
        guide = next(widget for widget in self.app.winfo_children() if isinstance(widget, tk.Toplevel))
        guide.withdraw()
        text = next(widget for widget in guide.winfo_children() if isinstance(widget, tk.Text))
        for name in ('Light', 'Dark'):
            self.app.theme_choice.set(name)
            self.app._appearance_changed()
            self.assertEqual(self.app.tk.call(listbox, 'cget', '-background'),
                             appearance.PALETTES[name.lower()]['field'])
            self.assertEqual(text.cget('background'), appearance.PALETTES[name.lower()]['card'])
            self.assertEqual(str(text.cget('state')), 'disabled')

    def test_preference_save_failure_keeps_theme_and_connection_warning(self):
        self.app.demo = False
        self.app.appearance_path = Path('synthetic-appearance.json')
        self.app._event(event('warning', 'Press Back on the Flipper.'))
        detail, task = self.app.detail_text.get(), self.app.task_text.get()
        with patch.object(desktop, 'save_preference', side_effect=OSError('private path')) as save:
            self.app.theme_choice.set('Light')
            self.app._appearance_changed()
        save.assert_called_once_with(self.app.appearance_path, 'light')
        self.assertEqual(self.app.appearance.name, 'light')
        self.assertEqual(self.app.theme_note.get(), 'For this session only')
        self.assertEqual((self.app.detail_text.get(), self.app.task_text.get()), (detail, task))


@unittest.skipIf(desktop is None, 'Tk is unavailable on this host')
class AppearancePreferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'settings' / 'appearance.json'

    def test_missing_invalid_and_unreadable_preferences_default_to_dark(self):
        self.assertEqual(appearance.load_preference(self.path), 'dark')
        self.path.parent.mkdir()
        for content in ('broken', '[]', '[' * 4000, '{"theme":false}', '{"theme":[]}',
                        '{"theme":"unknown"}', '{"theme":"light"}' + ' ' * 4096):
            self.path.write_text(content, encoding='utf-8')
            self.assertEqual(appearance.load_preference(self.path), 'dark')
        with patch.object(Path, 'open', side_effect=PermissionError('unreadable')):
            self.assertEqual(appearance.load_preference(self.path), 'dark')

    def test_save_and_reload_is_separate_from_encrypted_profile(self):
        self.path.parent.mkdir()
        profile = self.path.with_name('device.dpapi')
        profile.write_bytes(b'synthetic encrypted profile sentinel')
        for theme in ('light', 'dark'):
            appearance.save_preference(self.path, theme)
            self.assertEqual(appearance.load_preference(self.path), theme)
            self.assertEqual(profile.read_bytes(), b'synthetic encrypted profile sentinel')
            self.assertEqual(set(appearance.json.loads(self.path.read_text())), {'theme'})
        self.assertEqual(sorted(path.name for path in self.path.parent.iterdir()),
                         ['appearance.json', 'device.dpapi'])

    def test_interrupted_save_preserves_previous_preference_and_cleans_temporary_file(self):
        appearance.save_preference(self.path, 'light')
        with patch.object(appearance.os, 'replace', side_effect=OSError('simulated failure')):
            with self.assertRaises(OSError):
                appearance.save_preference(self.path, 'dark')
        self.assertEqual(appearance.load_preference(self.path), 'light')
        self.assertEqual([path.name for path in self.path.parent.iterdir()], ['appearance.json'])


if __name__ == "__main__":
    unittest.main()
