"""Notification-icon tests use mocked Win32 APIs; no desktop icon is created."""
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import desktop_tray as tray


class TrayControllerTests(unittest.TestCase):
    def test_start_stop_are_idempotent_and_actions_cross_only_the_queue(self):
        entered = threading.Event()
        callbacks = {}
        stop_calls = []

        class Native:
            def __init__(self, path, post, available, stop):
                callbacks.update(post=post, available=available, stop=stop)

            def run(self):
                callbacks['available'](True)
                entered.set()
                callbacks['stop'].wait(2)

            def request_stop(self):
                stop_calls.append(True)

        icon = tray.TrayIcon(Path('assets/bridge.ico'), native_factory=Native)
        self.addCleanup(lambda: (icon.stop(), icon.join(2)))
        icon.start()
        icon.start()
        self.assertTrue(entered.wait(1))
        self.assertTrue(icon.available)
        callbacks['post']('open')
        callbacks['post']('stop')
        callbacks['post']('exit')
        callbacks['post']('DO_NOT_DISPLAY_PRIVATE_DATA')
        self.assertEqual(icon.drain_events(), ('open', 'stop', 'exit'))
        icon.stop()
        icon.stop()
        callbacks['post']('open')
        self.assertTrue(icon.join(2))
        self.assertFalse(icon.available)
        self.assertFalse(icon.running)
        self.assertEqual(stop_calls, [True])
        self.assertEqual(icon.drain_events(), ())

    def test_stop_during_native_initialization_never_creates_an_icon(self):
        entered, release = threading.Event(), threading.Event()
        run = Mock()

        def factory(*args):
            entered.set()
            release.wait(1)
            return SimpleNamespace(run=run, request_stop=Mock())

        icon = tray.TrayIcon(Path('assets/bridge.ico'), native_factory=factory)
        self.addCleanup(lambda: (release.set(), icon.stop(), icon.join(2)))
        icon.start()
        self.assertTrue(entered.wait(1))
        icon.stop()
        release.set()
        self.assertTrue(icon.join(2))
        run.assert_not_called()
        self.assertFalse(icon.available)

    def test_native_failure_is_sanitized_and_never_marks_icon_available(self):
        factory = Mock(side_effect=OSError('DO_NOT_DISPLAY_PRIVATE_PATH'))
        icon = tray.TrayIcon(Path('assets/bridge.ico'), native_factory=factory)
        icon.start()
        self.assertTrue(icon.join(2))
        self.assertEqual(icon.drain_events(), ('unavailable',))
        self.assertFalse(icon.available)

    def test_thread_start_failure_is_reported_without_stuck_running_state(self):
        icon = tray.TrayIcon(Path('assets/bridge.ico'))
        with patch.object(tray.threading, 'Thread', side_effect=OSError('PRIVATE')):
            icon.start()
        self.assertFalse(icon.running)
        self.assertFalse(icon.available)
        self.assertEqual(icon.drain_events(), ('unavailable',))

    def test_queue_is_bounded_and_keeps_latest_exit(self):
        icon = tray.TrayIcon(Path('assets/bridge.ico'))
        for _ in range(100):
            icon._post('open')
        icon._post('exit')
        actions = icon.drain_events()
        self.assertEqual(len(actions), 32)
        self.assertEqual(actions[-1], 'exit')


class NativeTrayTests(unittest.TestCase):
    def setUp(self):
        self.post, self.available = Mock(), Mock()
        self.native = tray._NativeTray(Path('assets/bridge.ico'), self.post,
                                        self.available, threading.Event())
        self.native.user = Mock()
        self.native.kernel = Mock()
        self.native.shell = Mock()
        self.native.kernel.GetModuleHandleW.return_value = 1
        self.native.user.RegisterWindowMessageW.return_value = 900
        self.native.user.RegisterClassW.return_value = 1
        self.native.user.CreateWindowExW.return_value = 101
        self.native.user.LoadImageW.return_value = 201
        self.native.user.GetMessageW.return_value = 0
        self.native.shell.Shell_NotifyIconW.return_value = True
        self.native.taskbar_message = 900

    def test_native_lifecycle_adds_and_removes_icon_and_owned_resources(self):
        with patch.object(self.native, '_libraries'):
            self.native.run()
        self.assertEqual([call.args[0] for call in self.native.shell.Shell_NotifyIconW.call_args_list], [0, 4, 2])
        self.assertIn(((True,), {}), self.available.call_args_list)
        self.native.user.DestroyWindow.assert_called_once_with(101)
        self.native.user.DestroyIcon.assert_called_once_with(201)
        self.native.user.UnregisterClassW.assert_called_once_with(tray.WINDOW_CLASS, 1)
        self.assertIsNone(self.native.hwnd)

    def test_add_failure_still_releases_window_icon_and_class(self):
        self.native.shell.Shell_NotifyIconW.return_value = False
        with patch.object(self.native, '_libraries'), self.assertRaises(RuntimeError):
            self.native.run()
        self.native.user.DestroyWindow.assert_called_once_with(101)
        self.native.user.DestroyIcon.assert_called_once_with(201)
        self.assertNotIn(((True,), {}), self.available.call_args_list)

    def test_message_loop_failure_still_removes_icon(self):
        self.native.user.GetMessageW.return_value = -1
        with patch.object(self.native, '_libraries'), self.assertRaises(RuntimeError):
            self.native.run()
        self.assertEqual(self.native.shell.Shell_NotifyIconW.call_args_list[-1].args[0], 2)
        self.native.user.UnregisterClassW.assert_called_once()

    def test_stop_flag_is_checked_after_message_wait_before_dispatching(self):
        def stopped_message(*args):
            self.native.stop_event.set()
            return 1

        self.native.user.GetMessageW.side_effect = stopped_message
        with patch.object(self.native, '_libraries'):
            self.native.run()
        self.native.user.DispatchMessageW.assert_not_called()
        self.native.user.KillTimer.assert_called_once_with(101, 1)
        self.assertEqual(self.native.shell.Shell_NotifyIconW.call_args_list[-1].args[0], 2)

    def test_double_click_keyboard_and_second_launch_only_enqueue_open(self):
        for action in (tray.WM_LBUTTONDBLCLK, tray.NIN_KEYSELECT):
            self.native._message(101, tray.WM_TRAY, 0, (1 << 16) | action)
        self.native._message(101, tray.WM_RESTORE, 0, 0)
        self.assertEqual([call.args for call in self.post.call_args_list], [('open',)] * 3)

    def test_menu_selection_queues_stop_or_exit_and_always_destroys_menu(self):
        self.native.hwnd = 101
        self.native.user.CreatePopupMenu.return_value = 301
        self.native.user.GetCursorPos.return_value = True
        for selection, action in ((1, 'open'), (2, 'stop'), (3, 'exit')):
            self.native.user.TrackPopupMenu.return_value = selection
            self.native._menu()
            self.post.assert_called_with(action)
        self.assertEqual(self.native.user.DestroyMenu.call_count, 3)

    def test_explorer_restart_readds_icon_and_reports_failure(self):
        with patch.object(self.native, '_add_icon', return_value=True) as add:
            self.native._message(101, 900, 0, 0)
            add.assert_called_once()
        self.post.assert_not_called()
        with patch.object(self.native, '_add_icon', return_value=False):
            self.native._message(101, 900, 0, 0)
        self.post.assert_called_once_with('unavailable')

    def test_native_callback_errors_are_not_propagated_or_displayed(self):
        with patch.object(self.native, '_menu', side_effect=OSError('PRIVATE')):
            self.assertEqual(self.native._message(101, tray.WM_TRAY, 0, tray.WM_CONTEXTMENU), 0)
        self.post.assert_called_once_with('unavailable')
        self.native.user.PostMessageW.assert_called_once_with(101, tray.WM_CLOSE, 0, 0)

    def test_second_launch_requests_restore_with_fixed_window_identity(self):
        user = Mock()
        user.FindWindowW.return_value = 101
        user.PostMessageW.return_value = True
        with patch.object(tray.sys, 'platform', 'win32'), patch.object(tray.ctypes, 'WinDLL', return_value=user, create=True):
            self.assertTrue(tray.restore_existing())
        user.FindWindowW.assert_called_once_with(tray.WINDOW_CLASS, tray.WINDOW_TITLE)
        user.PostMessageW.assert_called_once_with(101, tray.WM_RESTORE, 0, 0)


if __name__ == '__main__':
    unittest.main()
