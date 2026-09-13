"""Offline integration tests: fake backend callbacks and a stateful USB client."""
import asyncio
import json
import time
from types import SimpleNamespace
import unittest

from radio import RadioError, RejectedCommand, validate_run
from serial_mirror import HubCommand
from standalone import run_standalone


IDENTITY = SimpleNamespace(hub_id=4100, owner_id=9000, shocker_id=1234, channel=0)
CHANNEL = b"c4100-ops"


def registration(paused=False):
    return {"r": True, "t": "R", "cl": True, "c": 4100, "oid": 9000,
            "s": [{"id": 1234, "t": 1, "p": paused}], "p": {}}


def command(**changes):
    value = {"id": 1234, "m": "v", "i": 10, "d": 1000, "r": True}
    value.update(changes)
    return json.dumps(value).encode()


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 1.0)


class FakeRadio:
    """Model physical arming, busy refusal, and local parameter validation."""

    def __init__(self):
        self.calls = []
        self.armed = False
        self.active = None
        self.keepalive = False

    def count(self, name):
        return sum(call[0] == name for call in self.calls)

    @property
    def attempts(self):
        return [call for call in self.calls if call[0] in ("replace", "run")]

    def hello(self):
        self.calls.append(("hello",))
        self.armed = False

    def disarm(self):
        self.calls.append(("disarm",))
        self.armed = False
        self.active = None
        self.keepalive = False

    def stop(self):
        self.calls.append(("stop",))
        self.active = None

    def configure(self, shocker_id, channel):
        if self.armed:
            raise AssertionError("Configuration must occur while disarmed.")
        self.calls.append(("configure", shocker_id, channel))

    def ping(self):
        self.calls.append(("ping",))

    def set_keepalive(self, enabled):
        if enabled and (not self.count("configure") or not self.count("ping")):
            raise AssertionError("Keep-alive requires a configured target and USB lease.")
        self.calls.append(("keepalive", enabled))
        self.keepalive = enabled

    def press_ok(self):
        self.calls.append(("physical_arm",))
        self.armed = True

    def _operate(self, name, value):
        validate_run(value.mode, value.intensity, value.duration_ms)
        self.calls.append((name, value))
        if not self.armed:
            raise RejectedCommand("Flipper rejected the command: DISARMED")
        if name == "run" and self.active is not None:
            raise RejectedCommand("Flipper rejected the command: BUSY")
        self.active = value

    def replace(self, value):
        self._operate("replace", value)

    def run(self, mode, intensity, duration):
        self._operate("run", HubCommand(mode, intensity, duration))


class StandaloneTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, scenario, *, error=None, clock=time.monotonic, client=None, on_status=None):
        client = FakeRadio() if client is None else client
        logs = []
        finished = asyncio.Event()

        def emit(message, **_kwargs):
            logs.append(message)

        async def backend(identity, snapshot, message, invalidated, ready, failure, done):
            self.assertIs(identity, IDENTITY)
            callbacks = SimpleNamespace(snapshot=snapshot, message=message,
                                        invalidated=invalidated, ready=ready,
                                        failure=failure, finished=done)

            async def connect():
                invalidated()
                snapshot(registration())
                ready()
                await until(lambda: client.count("configure") == 1 and client.count("ping") >= 1)
                self.assertFalse(client.armed)

            callbacks.connect = connect
            await scenario(callbacks, client, logs)
            done.set()

        operation = run_standalone(IDENTITY, client, backend=backend, emit=emit,
                                   finished=finished, clock=clock, on_status=on_status)
        if error is None:
            await asyncio.wait_for(operation, 2.5)
        else:
            with self.assertRaisesRegex(RadioError, error):
                await asyncio.wait_for(operation, 2.5)
        self.assertFalse(client.armed)
        self.assertIsNone(client.active)
        self.assertFalse(client.keepalive)
        self.assertEqual(client.calls[-2:], [("stop",), ("disarm",)])
        return client, logs

    async def test_keepalive_requires_backend_ready_and_unchanged_refresh_keeps_timer(self):
        async def scenario(cb, client, _logs):
            await asyncio.sleep(0.025)
            self.assertEqual(client.count("keepalive"), 0)
            cb.snapshot(registration())
            await asyncio.sleep(0.025)
            self.assertEqual(client.count("keepalive"), 0)
            await cb.connect()
            self.assertTrue(client.keepalive)
            self.assertFalse(client.armed)
            names = [entry[0] for entry in client.calls]
            self.assertLess(names.index("configure"), names.index("keepalive"))
            self.assertLess(names.index("ping"), names.index("keepalive"))
            cb.snapshot(registration())
            cb.ready()
            await asyncio.sleep(0.025)
            self.assertEqual(client.count("keepalive"), 1)
            self.assertEqual(client.attempts, [])
        await self.exercise(scenario)

    async def test_keepalive_is_disabled_during_revalidation_despite_usb_pings(self):
        async def scenario(cb, client, _logs):
            await cb.connect()
            self.assertTrue(client.keepalive)
            before = client.count("disarm")
            cb.invalidated()
            await until(lambda: client.count("disarm") > before)
            self.assertFalse(client.keepalive)
            await until(lambda: client.count("ping") >= 2)
            self.assertFalse(client.keepalive)
            self.assertEqual(client.count("keepalive"), 1)
            cb.snapshot(registration())
            await asyncio.sleep(0.025)
            self.assertFalse(client.keepalive)
            cb.ready()
            await until(lambda: client.count("keepalive") == 2)
            self.assertTrue(client.keepalive)
            self.assertFalse(client.armed)
        await self.exercise(scenario)

    async def test_legacy_addon_gets_upgrade_status_and_preserves_command_support(self):
        class LegacyRadio(FakeRadio):
            def set_keepalive(self, enabled):
                self.calls.append(("keepalive", enabled))
                raise RejectedCommand("Flipper rejected the command: INVALID")

        statuses = []
        async def scenario(cb, client, _logs):
            await cb.connect()
            self.assertFalse(client.keepalive)
            self.assertIn("addon_update_required", statuses)
            client.press_ok()
            cb.message(CHANNEL, command(m="b", i=0), time.monotonic())
            await until(lambda: len(client.attempts) == 1)
        await self.exercise(scenario, client=LegacyRadio(), on_status=statuses.append)

    async def test_keepalive_ack_loss_or_nonlegacy_rejection_stops_without_retry(self):
        for error in (RadioError("Radio response timed out"),
                      RejectedCommand("Flipper rejected the command: DISARMED")):
            class BrokenRadio(FakeRadio):
                def set_keepalive(self, enabled):
                    self.calls.append(("keepalive", enabled))
                    raise error

            async def scenario(cb, client, _logs):
                cb.snapshot(registration())
                cb.ready()
                await cb.finished.wait()
            with self.subTest(error=type(error).__name__):
                client, _ = await self.exercise(scenario, client=BrokenRadio(), error="timed out|keep-alive setup failed")
                self.assertEqual(client.count("keepalive"), 1)
                self.assertEqual(client.attempts, [])

    async def test_control_invalidation_clears_pending_and_requires_fresh_ready_and_arm(self):
        async def scenario(cb, client, _logs):
            await cb.connect()
            client.press_ok()
            cb.message(CHANNEL, command(), time.monotonic())
            await until(lambda: len(client.attempts) == 1)
            before = client.count("disarm")
            # A control invalidation overtakes an admitted but unforwarded op.
            cb.message(CHANNEL, command(d=1500), time.monotonic())
            cb.invalidated()
            cb.message(CHANNEL, command(d=1600), time.monotonic())
            await until(lambda: client.count("disarm") > before)
            self.assertIsNone(client.active)
            self.assertFalse(client.armed)
            self.assertEqual(len(client.attempts), 1)
            # A snapshot alone must not reopen the command gate.
            cb.snapshot(registration())
            cb.message(CHANNEL, command(d=1700), time.monotonic())
            await asyncio.sleep(0.025)
            self.assertEqual(len(client.attempts), 1)
            before = client.count("disarm")
            cb.ready()
            await until(lambda: client.count("disarm") > before)
            cb.message(CHANNEL, command(d=1800), time.monotonic())
            await until(lambda: len(client.attempts) == 2)
            self.assertIsNone(client.active)  # Fresh ready never physically arms.
            client.press_ok()
            cb.message(CHANNEL, command(d=1900), time.monotonic())
            await until(lambda: len(client.attempts) == 3)
            self.assertEqual(client.active.duration_ms, 1900)
        await self.exercise(scenario)

    async def test_disarmed_rejection_is_discarded_without_retry_after_arming(self):
        async def scenario(cb, client, logs):
            await cb.connect()
            cb.message(CHANNEL, command(), time.monotonic())
            await until(lambda: any("Command discarded: DISARMED" in item for item in logs))
            self.assertEqual(len(client.attempts), 1)
            client.press_ok()
            await asyncio.sleep(0.035)
            self.assertEqual(len(client.attempts), 1)
            self.assertIsNone(client.active)
            cb.message(CHANNEL, command(d=500), time.monotonic())
            await until(lambda: len(client.attempts) == 2)
            self.assertEqual(client.active.duration_ms, 500)
        await self.exercise(scenario)

    async def test_nonrepeating_busy_refusal_preserves_running_operation(self):
        async def scenario(cb, client, logs):
            await cb.connect()
            client.press_ok()
            cb.message(CHANNEL, command(d=3000), time.monotonic())
            await until(lambda: client.active is not None)
            running = client.active
            stop_count, disarm_count = client.count("stop"), client.count("disarm")
            cb.message(CHANNEL, command(d=500, r=False), time.monotonic())
            await until(lambda: any("Command discarded: BUSY" in item for item in logs))
            self.assertEqual([attempt[0] for attempt in client.attempts], ["replace", "run"])
            self.assertIs(client.active, running)
            self.assertEqual(client.count("stop"), stop_count)
            self.assertEqual(client.count("disarm"), disarm_count)
            await asyncio.sleep(0.025)
            self.assertEqual(len(client.attempts), 2)
        await self.exercise(scenario)

    async def test_unchanged_refresh_preserves_arming_and_stop_delivery(self):
        async def scenario(cb, client, _logs):
            await cb.connect()
            client.press_ok()
            cb.message(CHANNEL, command(), time.monotonic())
            await until(lambda: client.active is not None)
            before = client.count("stop"), client.count("disarm")
            cb.snapshot(registration())
            cb.ready()
            await asyncio.sleep(0.025)
            self.assertEqual((client.count("stop"), client.count("disarm")), before)
            self.assertTrue(client.armed)
            cb.message(CHANNEL, command(m="e", d=0), time.monotonic())
            await until(lambda: client.count("stop") == before[0] + 1)
            self.assertIsNone(client.active)
            self.assertTrue(client.armed)
        await self.exercise(scenario)

    async def test_changed_snapshot_stops_disarms_and_discards_pending(self):
        async def scenario(cb, client, _logs):
            await cb.connect()
            client.press_ok()
            cb.message(CHANNEL, command(), time.monotonic())
            await until(lambda: client.active is not None)
            before = client.count("disarm")
            cb.message(CHANNEL, command(d=2000), time.monotonic())
            cb.snapshot(registration(paused=True))
            cb.ready()
            await until(lambda: client.count("disarm") > before)
            self.assertIsNone(client.active)
            self.assertFalse(client.armed)
            self.assertEqual(len(client.attempts), 1)
            cb.message(CHANNEL, command(d=3000), time.monotonic())
            await asyncio.sleep(0.025)
            self.assertEqual(len(client.attempts), 1)
        await self.exercise(scenario)

    async def test_stale_event_aborts_and_disarms_without_transmitting(self):
        async def scenario(cb, client, _logs):
            await cb.connect()
            client.press_ok()
            cb.message(CHANNEL, command(), time.monotonic() - 1)
            await cb.finished.wait()
        client, _ = await self.exercise(scenario, error="became stale")
        self.assertEqual(client.attempts, [])

    async def test_backend_failure_stops_active_output_and_redacts_reason(self):
        async def scenario(cb, client, _logs):
            await cb.connect()
            client.press_ok()
            cb.message(CHANNEL, command(), time.monotonic())
            await until(lambda: client.active is not None)
            cb.failure("PRIVATE_BACKEND_RESPONSE")
            await cb.finished.wait()
        _client, logs = await self.exercise(scenario, error="PiShock connection ended")
        self.assertNotIn("PRIVATE_BACKEND_RESPONSE", "\n".join(logs))

    async def test_beep_normalization_and_configured_heartbeat(self):
        offset = [0.0]

        def clock():
            return time.monotonic() + offset[0]

        async def scenario(cb, client, _logs):
            # No heartbeat before validated configuration.
            await asyncio.sleep(0.025)
            self.assertEqual(client.count("ping"), 0)
            await cb.connect()
            client.press_ok()
            cb.message(CHANNEL, command(m="b", i=99, d=750), clock())
            await until(lambda: len(client.attempts) == 1)
            self.assertEqual(client.active, HubCommand("beep", 0, 750))
            before = client.count("ping")
            offset[0] += 0.26
            await until(lambda: client.count("ping") > before)
            self.assertTrue(client.armed)
        await self.exercise(scenario, clock=clock)


if __name__ == "__main__":
    unittest.main()
