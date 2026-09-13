"""Offline desktop tests: synthetic USB, profiles and backend callbacks only."""
import asyncio
from contextlib import ExitStack
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import desktop_service as service
from identity import Identity
from radio import RadioError


IDENTITY = Identity("AA:BB:CC:DD:EE:FF", 4100, 1234, 0, 9000, "203.0.113.10")
SECRETS = "DO_NOT_DISPLAY_USB_SERIAL_OR_PASSWORD"


def port(name="COM7", *, hub=False, radio=True):
    vid, pid = service.HUB_USB if hub else service.FLIPPER_USB
    return SimpleNamespace(device=name, vid=vid, pid=pid,
                           location="4-2:x.2" if radio else "4-2:x.0",
                           hwid=SECRETS, interface=None, serial_number=SECRETS,
                           description=SECRETS, manufacturer=SECRETS)


def hub_info(**updates):
    info = {"clientId": 4100, "claimed": True, "type": 3,
            "macAddress": IDENTITY.mac, "ownerId": 9000, "publicIp": IDENTITY.public_ip,
            "shockers": [{"id": 1234, "type": 1, "name": SECRETS}],
            "networks": [{"password": SECRETS}], "otk": SECRETS}
    info.update(updates)
    return info


class SerialFixture:
    def __init__(self, info=None):
        self.input = io.BytesIO(b"TERMINALINFO:" + json.dumps(info or hub_info()).encode() + b"\n")
        self.writes = []
        self.closed = False
        self.reset_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def reset_input_buffer(self):
        self.reset_count += 1

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def read(self, length):
        return self.input.read(length)


class FakeRadio:
    def __init__(self):
        self.calls = []
        self.disconnected = False

    def _record(self, name, *args):
        self.calls.append((name, *args))
        if self.disconnected:
            raise RadioError(SECRETS)

    def hello(self):
        self._record("hello")

    def stop(self):
        self._record("stop")

    def disarm(self):
        self._record("disarm")

    def configure(self, target, channel):
        self._record("configure", target, channel)

    def ping(self):
        self._record("ping")


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Offline worker did not reach the expected state.")
        time.sleep(0.002)


class DiscoveryTests(unittest.TestCase):
    def test_port_inventory_drops_metadata_and_unrelated_interfaces(self):
        values = [port("COM11"), port("COM7"), port("com6", hub=True),
                  port("COM5", radio=False), port("COM7"),
                  SimpleNamespace(device=SECRETS, vid=1, pid=2)]
        with patch.object(service, "find_ports", return_value=values):
            inventory = service.enumerate_ports()
        self.assertEqual([p.device for p in inventory.flippers], ["COM7", "COM11"])
        self.assertEqual([p.device for p in inventory.hubs], ["COM6"])
        self.assertNotIn(SECRETS, repr(inventory))

    def test_enumeration_failure_never_displays_raw_exception(self):
        with patch.object(service, "find_ports", side_effect=OSError(SECRETS)):
            with self.assertRaises(service.DesktopError) as captured:
                service.enumerate_ports()
        self.assertNotIn(SECRETS, str(captured.exception))

    def test_discovery_uses_only_info_and_returns_supported_numeric_selections(self):
        serial = SerialFixture(hub_info(shockers=[{"id": 5678, "type": 1, "name": SECRETS},
                                                 {"id": 1234, "type": 1}, {"id": 6, "type": 2}]))
        with patch.object(service, "find_ports", return_value=[port("COM6", hub=True)]), \
                patch.object(service, "open_serial", return_value=serial) as opened:
            result = service.discover_hub("com6")
        opened.assert_called_once_with("COM6", original_hub=True)
        self.assertEqual(serial.writes, [b'{"cmd":"info"}\n'])
        self.assertEqual(serial.reset_count, 1)
        self.assertTrue(serial.closed)
        self.assertEqual(result.hub_id, 4100)
        self.assertEqual([choice.shocker_id for choice in result.shockers], [1234, 5678])
        for secret in (SECRETS, IDENTITY.mac, IDENTITY.public_ip):
            self.assertNotIn(secret, json.dumps(asdict(result)))

    def test_discovery_rejects_ambiguous_invalid_and_unsupported_targets(self):
        cases = [{"shockers": [{"id": 1234, "type": 1}, {"id": 1234, "type": 1}]},
                 {"shockers": [{"id": True, "type": 1}]},
                 {"shockers": [{"id": 65536, "type": 1}]},
                 {"shockers": [{"id": 1234, "type": 2}]},
                 {"type": 1}, {"clientId": 0}, {"clientId": True}]
        for change in cases:
            with self.subTest(change=change), \
                    patch.object(service, "find_ports", return_value=[port("COM6", hub=True)]), \
                    patch.object(service, "open_serial", return_value=SerialFixture(hub_info(**change))):
                with self.assertRaises(service.DesktopError):
                    service.discover_hub("COM6")

    def test_removed_hub_is_not_opened(self):
        with patch.object(service, "find_ports", return_value=[]), patch.object(service, "open_serial") as opened:
            with self.assertRaises(service.DesktopError):
                service.discover_hub("COM6")
        opened.assert_not_called()


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.destination = Path(directory) / "app" / "device.dpapi"
        # Stand in for DPAPI with opaque on-disk tokens and in-memory payloads.
        # Real Windows DPAPI is exercised separately by test_identity.py.
        self.sealed = {}

        def protect(data, decrypt=False):
            if decrypt:
                return self.sealed[data]
            token = f"opaque-test-token-{len(self.sealed)}".encode()
            self.sealed[token] = data
            return token

        self.stack.enter_context(patch("identity._protect", side_effect=protect))
        self.stack.enter_context(patch.object(service, "find_ports", return_value=[port("COM6", hub=True)]))
        self.importer = self.stack.enter_context(patch.object(service, "import_connected_identity", return_value=IDENTITY))
        self.discovery = service.HubDiscovery("COM6", 4100, (service.ShockerChoice(1234, "SmallOne shocker 1234"),))

    def save(self, **kwargs):
        return service.import_profile(self.discovery, 1234, self.destination, **kwargs)

    def test_save_revalidates_selected_hub_and_writes_only_protected_data(self):
        self.assertEqual(self.save(), IDENTITY)
        self.importer.assert_called_once_with("COM6", 4100, 1234)
        self.assertEqual(service.load_profile(self.destination), IDENTITY)
        self.assertNotIn(IDENTITY.mac.encode(), self.destination.read_bytes())
        self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])

    def test_existing_profile_requires_explicit_replacement(self):
        self.save()
        previous = self.destination.read_bytes()
        self.importer.return_value = replace(IDENTITY, public_ip="203.0.113.20")
        with self.assertRaisesRegex(service.DesktopError, "already saved"):
            self.save()
        self.assertEqual(self.destination.read_bytes(), previous)
        self.save(replace_existing=True)
        self.assertEqual(service.load_profile(self.destination), self.importer.return_value)

    def test_failed_verification_preserves_previous_profile_and_cleans_temp(self):
        self.save()
        previous = self.destination.read_bytes()
        with patch.object(service, "load_identity", return_value=replace(IDENTITY, owner_id=9001)):
            with self.assertRaisesRegex(service.DesktopError, "verified"):
                self.save(replace_existing=True)
        self.assertEqual(self.destination.read_bytes(), previous)
        self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])

    def test_failed_publication_preserves_previous_profile(self):
        self.save()
        previous = self.destination.read_bytes()
        with patch.object(service.os, "replace", side_effect=OSError(SECRETS)):
            with self.assertRaises(service.DesktopError) as captured:
                self.save(replace_existing=True)
        self.assertNotIn(SECRETS, str(captured.exception))
        self.assertEqual(self.destination.read_bytes(), previous)

    def test_racing_initial_save_does_not_clobber_another_profile(self):
        original_link = service.os.link

        def racing_link(source, destination):
            Path(destination).write_bytes(b"other-import")
            return original_link(source, destination)

        with patch.object(service.os, "link", side_effect=racing_link):
            with self.assertRaisesRegex(service.DesktopError, "already saved"):
                self.save()
        self.assertEqual(self.destination.read_bytes(), b"other-import")

    def test_selection_not_discovered_and_boolean_selection_are_rejected_before_io(self):
        for selection in (5678, True, "1234"):
            with self.subTest(selection=selection), self.assertRaises(service.DesktopError):
                service.import_profile(self.discovery, selection, self.destination)
        self.importer.assert_not_called()

    def test_identity_changed_after_discovery_cannot_be_imported(self):
        self.importer.side_effect = RadioError(SECRETS)
        with self.assertRaises(service.DesktopError) as captured:
            self.save()
        self.assertNotIn(SECRETS, str(captured.exception))
        self.assertFalse(self.destination.exists())

    def test_explicit_saved_profile_migration_is_reprotected_and_verified(self):
        self.save()
        migrated = self.destination.parent / "chosen-destination.dpapi"
        self.importer.reset_mock()
        result = service.import_saved_profile(self.destination, migrated)
        self.assertEqual(result, IDENTITY)
        self.assertEqual(service.load_profile(migrated), IDENTITY)
        self.assertNotEqual(migrated.read_bytes(), self.destination.read_bytes())
        self.importer.assert_not_called()

    def test_profile_load_errors_do_not_expose_paths_or_raw_data(self):
        with patch.object(service, "load_identity", side_effect=OSError(SECRETS)):
            with self.assertRaises(service.DesktopError) as captured:
                service.load_profile(self.destination)
        self.assertNotIn(SECRETS, str(captured.exception))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.session = service.BridgeSession()
        self.addCleanup(self.stop_session)
        self.serial = SerialFixture()
        self.radio = FakeRadio()
        self.stack.enter_context(patch.object(service, "find_ports", return_value=[port()]))
        self.opened = self.stack.enter_context(patch.object(service, "open_serial", return_value=self.serial))
        self.stack.enter_context(patch.object(service, "MirrorRadioClient", return_value=self.radio))

    def stop_session(self):
        self.session.request_stop()
        self.assertTrue(self.session.join(2), "Offline session failed to shut down.")

    def events(self):
        return self.session.drain_events()

    def test_stop_before_loop_starts_never_opens_usb_or_backend(self):
        entered, release = threading.Event(), threading.Event()

        def inventory():
            entered.set()
            release.wait(1)
            return service.PortInventory((), (service.PortChoice("COM7", "Flipper"),))

        with patch.object(service, "enumerate_ports", side_effect=inventory), \
                patch.object(service, "run_standalone") as runner:
            self.session.start(IDENTITY, "COM7")
            self.assertTrue(entered.wait(1))
            self.session.request_stop()
            release.set()
            self.assertTrue(self.session.join(2))
        self.opened.assert_not_called()
        runner.assert_not_called()
        self.assertEqual(self.events()[-1].kind, "stopped")

    def test_stop_during_usb_open_stops_and_disarms_without_starting_backend(self):
        entered, release = threading.Event(), threading.Event()

        def opening(_port):
            entered.set()
            release.wait(1)
            return self.serial

        self.opened.side_effect = opening
        with patch.object(service, "run_standalone") as runner:
            self.session.start(IDENTITY, "COM7")
            self.assertTrue(entered.wait(1))
            self.session.request_stop()
            release.set()
            self.assertTrue(self.session.join(2))
        runner.assert_not_called()
        self.assertEqual(self.radio.calls, [("stop",), ("disarm",)])
        self.assertTrue(self.serial.closed)

    def test_duplicate_start_is_blocked_until_cleanup_and_close_complete(self):
        entered, cleanup, release = threading.Event(), threading.Event(), threading.Event()

        async def runner(_identity, _client, *, finished, **kwargs):
            entered.set()
            await finished.wait()
            cleanup.set()
            while not release.is_set():
                await asyncio.sleep(0.001)

        with patch.object(service, "run_standalone", side_effect=runner):
            self.session.start(IDENTITY, "COM7")
            self.assertTrue(entered.wait(1))
            with self.assertRaises(service.DesktopError):
                self.session.start(IDENTITY, "COM7")
            self.session.request_stop()
            self.assertTrue(cleanup.wait(1))
            self.assertTrue(self.session.running)
            with self.assertRaises(service.DesktopError):
                self.session.start(IDENTITY, "COM7")
            release.set()
            self.assertTrue(self.session.join(2))
        self.assertTrue(self.serial.closed)
        self.opened.assert_called_once()

    def test_stop_is_idempotent_and_late_ready_events_are_ignored(self):
        entered = threading.Event()

        async def runner(_identity, _client, *, finished, on_status, **kwargs):
            entered.set()
            await finished.wait()
            on_status("ready")
            on_status("revalidating")
            on_status("stop_unconfirmed")

        with patch.object(service, "run_standalone", side_effect=runner):
            self.session.request_stop()
            self.session.start(IDENTITY, "COM7")
            self.assertTrue(entered.wait(1))
            self.session.request_stop()
            self.session.request_stop()
            self.assertTrue(self.session.join(2))
        kinds = [event.kind for event in self.events()]
        self.assertEqual(kinds.count("stopping"), 1)
        self.assertNotIn("ready", kinds)
        self.assertNotIn("revalidating", kinds)
        self.assertIn("warning", kinds)

    def test_unknown_emit_and_exception_data_are_never_displayed(self):
        async def runner(_identity, _client, *, emit, on_status, **kwargs):
            emit(SECRETS)
            emit("Direct connection ready: hub 4100, shocker 1234.")
            on_status("ready")
            raise OSError(SECRETS)

        with patch.object(service, "run_standalone", side_effect=runner):
            self.session.start(IDENTITY, "COM7")
            self.assertTrue(self.session.join(2))
        events = self.events()
        self.assertNotIn(SECRETS, repr(events))
        self.assertNotIn("4100", repr(events))
        self.assertEqual([e.kind for e in events][-2:], ["error", "stopped"])
        self.assertTrue(self.serial.closed)

    def test_real_runner_stop_uses_finally_and_never_arms_or_operates(self):
        entered = threading.Event()
        real_runner = service.run_standalone

        async def backend(_identity, snapshot, _message, _invalidated, ready, _failure, finished):
            snapshot({"r": True, "t": "R", "cl": True, "c": 4100, "oid": 9000,
                      "s": [{"id": 1234, "t": 1, "p": False}], "p": {}})
            ready()
            entered.set()
            await finished.wait()

        async def runner(identity, client, **kwargs):
            self.assertTrue(kwargs["beep_only"])
            return await real_runner(identity, client, backend=backend, **kwargs)

        with patch.object(service, "run_standalone", side_effect=runner):
            self.session.start(IDENTITY, "COM7", beep_only=True)
            self.assertTrue(entered.wait(1))
            wait_for(lambda: any(call[0] == "ping" for call in self.radio.calls))
            self.session.request_stop()
            self.assertTrue(self.session.join(2))
        self.assertEqual(self.radio.calls[-2:], [("stop",), ("disarm",)])
        self.assertTrue(self.serial.closed)
        self.assertTrue(all(call[0] in {"hello", "stop", "disarm", "configure", "ping"} for call in self.radio.calls))

    def test_real_runner_disconnect_attempts_both_stop_measures_and_warns(self):
        real_runner = service.run_standalone
        self.radio.disconnected = True

        async def runner(identity, client, **kwargs):
            return await real_runner(identity, client, **kwargs)

        with patch.object(service, "run_standalone", side_effect=runner):
            self.session.start(IDENTITY, "COM7")
            self.assertTrue(self.session.join(2))
        self.assertEqual(self.radio.calls[-2:], [("stop",), ("disarm",)])
        events = self.events()
        self.assertIn("warning", [event.kind for event in events])
        self.assertIn("error", [event.kind for event in events])
        self.assertNotIn(SECRETS, repr(events))

    def test_status_callback_failure_cannot_interrupt_actual_radio_cleanup(self):
        real_runner = service.run_standalone
        categories = []

        def broken_status(category):
            categories.append(category)
            raise RuntimeError(SECRETS)

        async def backend(_identity, snapshot, _message, _invalidated, ready, _failure, finished):
            snapshot({"r": True, "t": "R", "cl": True, "c": 4100, "oid": 9000,
                      "s": [{"id": 1234, "t": 1, "p": False}], "p": {}})
            ready()
            await finished.wait()

        async def runner(identity, client, **kwargs):
            kwargs["on_status"] = broken_status
            return await real_runner(identity, client, backend=backend, **kwargs)

        with patch.object(service, "run_standalone", side_effect=runner):
            self.session.start(IDENTITY, "COM7")
            wait_for(lambda: "ready" in categories)
            self.session.request_stop()
            self.assertTrue(self.session.join(2))
        self.assertEqual(self.radio.calls[-2:], [("stop",), ("disarm",)])
        self.assertEqual(categories[-2:], ["stopping", "stopped"])
        self.assertTrue(self.serial.closed)
        self.assertNotIn("error", [event.kind for event in self.events()])

    def test_removed_radio_interface_is_not_opened_or_retried(self):
        with patch.object(service, "find_ports", return_value=[]):
            self.session.start(IDENTITY, "COM7")
            self.assertTrue(self.session.join(2))
        self.opened.assert_not_called()
        self.assertEqual([event.kind for event in self.events()][-2:], ["error", "stopped"])

    def test_manual_restart_resets_state_and_stop_after_completion_is_ignored(self):
        async def runner(_identity, _client, **kwargs):
            return

        with patch.object(service, "run_standalone", side_effect=runner):
            self.session.start(IDENTITY, "COM7")
            self.assertTrue(self.session.join(2))
            self.events()
            self.session.request_stop()
            self.assertEqual(self.events(), ())
            self.session.start(IDENTITY, "COM7")
            self.assertTrue(self.session.join(2))
        self.assertEqual(self.opened.call_count, 2)
        self.assertEqual(self.events()[-1].kind, "stopped")

    def test_thread_start_failure_does_not_leave_session_running(self):
        with patch.object(service.threading, "Thread", side_effect=OSError(SECRETS)):
            with self.assertRaises(service.DesktopError) as captured:
                self.session.start(IDENTITY, "COM7")
        self.assertFalse(self.session.running)
        self.assertNotIn(SECRETS, str(captured.exception))
        self.opened.assert_not_called()

    def test_telemetry_is_bounded_and_stopped_is_retained(self):
        async def runner(_identity, _client, *, emit, **kwargs):
            for _ in range(500):
                emit("[0.000s] Forwarded beep: 0%, 500 ms.")

        with patch.object(service, "run_standalone", side_effect=runner):
            self.session.start(IDENTITY, "COM7")
            self.assertTrue(self.session.join(2))
        events = self.events()
        self.assertLessEqual(len(events), 128)
        self.assertEqual(events[-1].kind, "stopped")


if __name__ == "__main__":
    unittest.main()
