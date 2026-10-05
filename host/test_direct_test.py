"""Offline direct-beep integration checks using the real USB protocol client."""
import asyncio
from contextlib import ExitStack
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import desktop_service as service
from identity import Identity
from radio import RadioError

try:
    import direct_test as direct
except ImportError:
    direct = None


IDENTITY = Identity("AA:BB:CC:DD:EE:FF", 4100, 1234, 2, 9000, "203.0.113.10")
SECRET = "PRIVATE_DEVICE_DATA"


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("Offline direct session did not reach the expected state.")


class SerialPort:
    """Byte-level USB fixture; no production radio methods are mocked."""

    def __init__(self):
        self.incoming = bytearray()
        self.writes = []
        self.thread_ids = set()
        self.closed = False
        self.on_write = lambda data: None
        self.on_close = lambda: None
        self.rejections = {}
        self.failures = set()
        self.suppressed = set()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.on_close()
        self.closed = True

    def write(self, data):
        self.thread_ids.add(threading.get_ident())
        self.writes.append(data)
        operation = data.split()[0]
        self.on_write(data)
        if operation in self.failures:
            raise OSError(SECRET)
        if operation in self.rejections:
            self.incoming.extend(b"ERR " + self.rejections[operation] + b"\n")
        elif operation not in self.suppressed:
            expected = b"RADIO1" if operation == b"HELLO" else operation
            self.incoming.extend(b"OK " + expected + b"\n")
        return len(data)

    def read(self, count):
        self.thread_ids.add(threading.get_ident())
        result = bytes(self.incoming[:count])
        del self.incoming[:count]
        if not result:
            time.sleep(0.001)
        return result

    @property
    def runs(self):
        return [data for data in self.writes if data.startswith(b"RUN ")]


class DirectSessionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(direct, "The direct beep session is not implemented.")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.session = direct.DirectBeepSession()
        self.port = SerialPort()
        usb = SimpleNamespace(device="COM7", vid=service.FLIPPER_USB[0],
                              pid=service.FLIPPER_USB[1], location="1-2:x.2",
                              interface=None)
        self.stack.enter_context(patch.object(service, "find_ports", return_value=[usb]))
        self.opened = self.stack.enter_context(patch.object(service, "open_serial", return_value=self.port))
        self.cloud = self.stack.enter_context(patch.object(service, "run_standalone",
                                                         side_effect=AssertionError("Cloud is forbidden")))
        self.events = []
        self.addCleanup(self.stop)

    def collect(self):
        self.events.extend(self.session.drain_events())
        return self.events

    def start(self):
        self.session.start(IDENTITY, "COM7")
        wait_for(lambda: self.session.can_beep)
        self.collect()

    def stop(self):
        self.session.request_stop()
        self.assertTrue(self.session.join(2))
        self.collect()

    def test_start_only_prepares_selected_usb_target_without_cloud_or_radio_operation(self):
        self.assertFalse(self.session.request_beep())
        self.start()
        self.assertEqual(self.port.writes[:5],
                         [b"HELLO\n", b"DISARM\n", b"SET 1234 2\n", b"PING\n", b"AWAKE 0\n"])
        self.assertEqual(self.port.runs, [])
        self.assertNotIn(b"AWAKE 1\n", self.port.writes)
        self.assertNotIn(b"ARM\n", self.port.writes)
        self.assertIn("direct_ready", [event.kind for event in self.events])
        self.cloud.assert_not_called()

    def test_click_sends_exactly_one_fixed_beep_and_rejects_double_click_and_cooldown(self):
        dispatch_times = []
        self.port.on_write = lambda data: dispatch_times.append(time.monotonic()) if data.startswith(b"RUN ") else None
        self.start()
        self.assertTrue(self.session.request_beep())
        self.assertFalse(self.session.can_beep)
        self.assertFalse(self.session.request_beep())
        wait_for(lambda: len(self.port.runs) == 1)
        time.sleep(0.1)
        self.assertFalse(self.session.request_beep())
        self.assertEqual(self.port.runs, [b"RUN 1 b 0 500\n"])
        wait_for(lambda: self.session.can_beep)
        self.assertGreaterEqual(time.monotonic() - dispatch_times[0], 1.0)
        self.assertEqual(self.port.runs, [b"RUN 1 b 0 500\n"])
        self.assertTrue(self.session.request_beep())
        wait_for(lambda: len(self.port.runs) == 2)
        self.assertEqual(self.port.runs[-1], b"RUN 2 b 0 500\n")
        self.assertIn("direct_busy", [event.kind for event in self.collect()])

    def test_acceptance_reserves_one_click_before_event_loop_scheduling(self):
        self.start()
        entered, release = threading.Event(), threading.Event()

        def hold_loop():
            entered.set()
            release.wait(1)

        self.session._loop.call_soon_threadsafe(hold_loop)
        self.assertTrue(entered.wait(1))
        try:
            self.assertTrue(self.session.request_beep())
            self.assertFalse(self.session.request_beep())
            self.assertFalse(self.session.request_beep())
            self.assertEqual(self.port.runs, [])
        finally:
            release.set()
        wait_for(lambda: len(self.port.runs) == 1)
        self.assertEqual(self.port.runs, [b"RUN 1 b 0 500\n"])

    def test_stop_drops_accepted_click_before_worker_dispatch(self):
        self.start()
        entered, release = threading.Event(), threading.Event()
        self.session._loop.call_soon_threadsafe(lambda: (entered.set(), release.wait(1)))
        self.assertTrue(entered.wait(1))
        try:
            self.assertTrue(self.session.request_beep())
            self.session.request_stop()
            self.assertFalse(self.session.request_beep())
        finally:
            release.set()
        self.stop()
        self.assertEqual(self.port.runs, [])
        self.assertEqual(self.port.writes[-2:], [b"STOP\n", b"DISARM\n"])

    def test_stop_during_pre_beep_ping_prevents_run(self):
        self.start()
        self.port.on_write = lambda data: self.session.request_stop() if data == b"PING\n" else None
        self.assertTrue(self.session.request_beep())
        self.assertTrue(self.session.join(2))
        self.assertEqual(self.port.runs, [])
        self.assertEqual(self.port.writes[-2:], [b"STOP\n", b"DISARM\n"])

    def test_delayed_request_is_discarded_after_half_second_without_replay(self):
        self.start()
        entered, release = threading.Event(), threading.Event()
        self.session._loop.call_soon_threadsafe(lambda: (entered.set(), release.wait(2)))
        self.assertTrue(entered.wait(1))
        try:
            self.assertTrue(self.session.request_beep())
            time.sleep(0.55)
        finally:
            release.set()
        self.assertTrue(self.session.join(2))
        self.assertEqual(self.port.runs, [])
        self.assertFalse(self.session.request_beep())
        self.assertEqual(self.port.writes[-2:], [b"STOP\n", b"DISARM\n"])

    def test_disarmed_rejection_needs_another_click_after_cooldown_and_never_arms(self):
        self.port.rejections[b"RUN"] = b"DISARMED"
        self.start()
        self.assertTrue(self.session.request_beep())
        wait_for(lambda: any("disarmed" in event.message.lower() for event in self.collect()))
        self.assertFalse(self.session.request_beep())
        wait_for(lambda: self.session.can_beep)
        self.assertEqual(self.port.runs, [b"RUN 1 b 0 500\n"])
        self.assertNotIn(b"ARM\n", self.port.writes)
        self.port.rejections.clear()
        self.assertTrue(self.session.request_beep())
        wait_for(lambda: len(self.port.runs) == 2)

    def test_busy_rejection_does_not_retry(self):
        self.port.rejections[b"RUN"] = b"BUSY"
        self.start()
        self.assertTrue(self.session.request_beep())
        wait_for(lambda: any("busy" in event.message.lower() for event in self.collect()))
        wait_for(lambda: self.session.can_beep)
        self.assertEqual(self.port.runs, [b"RUN 1 b 0 500\n"])
        self.assertTrue(self.session.running)

    def test_lost_beep_acknowledgment_stops_without_retry(self):
        self.port.suppressed.add(b"RUN")
        self.start()
        self.assertTrue(self.session.request_beep())
        self.assertTrue(self.session.join(2))
        self.assertEqual(self.port.runs, [b"RUN 1 b 0 500\n"])
        self.assertEqual(self.port.writes[-2:], [b"STOP\n", b"DISARM\n"])
        self.assertIn("error", [event.kind for event in self.collect()])
        self.assertFalse(self.session.request_beep())

    def test_setup_failure_attempts_stop_and_disarm_and_closes_before_stopped(self):
        for failed in (b"HELLO", b"DISARM", b"SET", b"PING", b"AWAKE"):
            with self.subTest(failed=failed):
                self.port.writes.clear()
                self.port.failures = {failed}
                self.port.on_close = lambda: self.assertNotIn(
                    "stopped", [event.kind for event in self.collect()])
                self.events.clear()
                self.session.start(IDENTITY, "COM7")
                self.assertTrue(self.session.join(2))
                self.assertEqual(self.port.writes[-2:], [b"STOP\n", b"DISARM\n"])
                self.assertTrue(self.port.closed)
                self.assertEqual(self.port.runs, [])
                self.assertFalse(self.session.can_beep)
                self.assertNotIn(SECRET, repr(self.collect()))
                self.assertEqual(self.events[-1].kind, "stopped")

    def test_old_addon_invalid_keepawake_command_can_prepare_but_other_rejections_stop(self):
        self.port.rejections[b"AWAKE"] = b"INVALID"
        self.start()
        self.stop()
        self.assertNotIn("error", [event.kind for event in self.events])
        self.port.rejections[b"AWAKE"] = b"BUSY"
        self.events.clear()
        self.session.start(IDENTITY, "COM7")
        self.assertTrue(self.session.join(2))
        self.assertIn("error", [event.kind for event in self.collect()])
        self.assertFalse(self.session.can_beep)

    def test_stop_during_setup_never_announces_ready_or_runs(self):
        self.port.on_write = lambda data: self.session.request_stop() if data == b"HELLO\n" else None
        self.session.start(IDENTITY, "COM7")
        self.assertTrue(self.session.join(2))
        self.assertEqual(self.port.writes, [b"HELLO\n", b"STOP\n", b"DISARM\n"])
        self.assertNotIn("direct_ready", [event.kind for event in self.collect()])
        self.assertFalse(self.session.request_beep())

    def test_worker_cancellation_attempts_both_stop_measures(self):
        async def exercise():
            client = service.MirrorRadioClient(self.port)
            task = asyncio.create_task(self.session._run(IDENTITY, client, False))
            await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(exercise())
        self.assertEqual(self.port.writes[-2:], [b"STOP\n", b"DISARM\n"])
        self.assertEqual(self.port.runs, [])

    def test_usb_heartbeats_continue_while_idle_and_all_serial_work_has_one_owner(self):
        ping_times = []
        self.port.on_write = lambda data: ping_times.append(time.monotonic()) if data == b"PING\n" else None
        self.start()
        wait_for(lambda: len(ping_times) >= 4)
        self.stop()
        self.assertTrue(all(0.20 <= right - left <= 0.50
                            for left, right in zip(ping_times, ping_times[1:])))
        self.assertEqual(len(self.port.thread_ids), 1)
        self.assertNotIn(threading.get_ident(), self.port.thread_ids)
        self.assertEqual(self.port.runs, [])

    def test_active_session_cannot_restart_or_publish_readiness_after_stop(self):
        self.start()
        with self.assertRaises(service.DesktopError):
            self.session.start(IDENTITY, "COM7")
        self.session.request_stop()
        self.session._post("direct_ready", "Late readiness")
        self.session._post("direct_busy", "Late busy")
        self.stop()
        self.assertFalse(self.session.can_beep)
        self.assertNotIn("Late readiness", repr(self.events))
        self.assertNotIn("Late busy", repr(self.events))
        self.assertEqual(self.opened.call_count, 1)

    def test_normal_bridge_has_no_local_beep_entry_point(self):
        normal = service.BridgeSession()
        self.assertFalse(hasattr(normal, "request_beep"))
        self.assertFalse(hasattr(normal, "can_beep"))

    def test_restricted_wire_client_rejects_arm_maintenance_and_other_operations_before_write(self):
        client = direct._BeepOnlyRadio(self.port)
        for command in ("ARM", "AWAKE 1", "RUN 1 s 1 500", "RUN 1 v 1 500",
                        "RUN 1 b 0 1000", "RUN 1 b 1 500", "REPLACE 1 b 0 500"):
            with self.subTest(command=command), self.assertRaises(RadioError):
                client.command(command, "RUN")
        self.assertEqual(self.port.writes, [])


if __name__ == "__main__":
    unittest.main()
