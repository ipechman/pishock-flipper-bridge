"""Offline mirror checks with fake USB ports; no radio or network activity."""

from collections import deque
import contextlib
import io
import unittest
from unittest.mock import patch

import radio
import serial_mirror as mirror


def consumed(mode="beep", intensity=0, duration=1000, target=1234):
    return (f"Consuming shocker command: id {target}, mode {mode}, "
            f"intensity {intensity}%, duration {duration}ms").encode("ascii")


def admitted(mode="beep", intensity=0, duration=1000, target=1234):
    return (consumed(mode, intensity, duration, target) + b"\n" +
            f"Setting up timer to stop shocker in {duration}ms\n".encode("ascii"))


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 0.001
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class HubPort:
    def __init__(self, batches=(), initial=b""):
        self.batches = deque(batches)
        self.incoming = bytearray(initial)
        self.is_open = True
        self.timeout = 1
        self.resets = 0
        self.writes = []

    @property
    def in_waiting(self):
        if not self.incoming and self.batches:
            self.incoming.extend(self.batches.popleft())
        return len(self.incoming)

    def read(self, size):
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        return result

    def write(self, data):
        self.writes.append(data)
        raise AssertionError("The original hub must never receive writes.")

    def reset_input_buffer(self):
        self.resets += 1
        self.incoming.clear()


class RadioPort:
    def __init__(self, reasons=(), lose_replace_ack=False, on_replace=None):
        self.incoming = bytearray()
        self.writes = []
        self.reasons = deque(reasons)
        self.lose_replace_ack = lose_replace_ack
        self.on_replace = on_replace

    def read(self, size):
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        return result

    def write(self, data):
        self.writes.append(data)
        operation = data.split()[0].decode("ascii")
        if operation == "REPLACE":
            if self.on_replace is not None:
                self.on_replace()
            if self.lose_replace_ack:
                return len(data)
            if self.reasons:
                reason = self.reasons.popleft()
                if reason is not None:
                    self.incoming.extend(f"ERR {reason}\n".encode("ascii"))
                    return len(data)
        expected = "RADIO1" if operation == "HELLO" else operation
        self.incoming.extend(f"OK {expected}\n".encode("ascii"))
        return len(data)

    @property
    def replacements(self):
        return [item for item in self.writes if item.startswith(b"REPLACE ")]


def steps(count):
    counter = 0

    def finished():
        nonlocal counter
        counter += 1
        return counter > count

    return finished


class ParserTests(unittest.TestCase):
    def test_captured_vibrate_and_beep_and_stop(self):
        self.assertEqual(mirror.parse_consumed_line(consumed("vibrate", 0), 1234),
                         mirror.HubCommand("vibrate", 0, 1000))
        self.assertEqual(mirror.parse_consumed_line(consumed(), 1234), mirror.HubCommand("beep", 0, 1000))
        self.assertEqual(mirror.parse_consumed_line(consumed("stop", 0, 0), 1234), mirror.STOP)
        self.assertEqual(mirror.parse_consumed_line(consumed("shock", 100, 10000), 1234),
                         mirror.HubCommand("shock", 100, 10000))

    def test_only_selected_id_and_standalone_accepted_logs(self):
        for line in (consumed(target=22), b'TERMINALINFO: {"password":"secret"}',
                     b'Message on channel \'private\': "' + consumed() + b'"',
                     b"prefix " + consumed(), b"Consuming shocker command: 9",
                     b"Consuming shocker command: id secret", b"\xffsecret",
                     consumed(target=999999), consumed("unknown", target=22)):
            with self.subTest(line=line):
                self.assertIsNone(mirror.parse_consumed_line(line, 1234))

    def test_selected_malformed_and_unsupported_values_rejected_without_echo(self):
        for line in (consumed("vibrate", 101), consumed("beep", 1), consumed("stop", 1, 0),
                     consumed("stop", 0, 1), consumed("shock", 1, 0), consumed("shock", 1, 99),
                     consumed("shock", 1, 10001), consumed("shock", 1, 9999999999999),
                     consumed("secret"), consumed() + b" secret", consumed() + b"\xff",
                     consumed().replace(b"0%", b"-1%"), consumed().replace(b"0%", b"1.0%")):
            with self.subTest(line=line):
                with self.assertRaises(mirror.InvalidHubCommand) as error:
                    mirror.parse_consumed_line(line, 1234)
                self.assertNotIn("secret", str(error.exception))

    def test_partial_commands_never_parse(self):
        framing = mirror.LineBuffer()
        line = consumed()
        self.assertEqual(framing.feed(line[:30]), [])
        self.assertEqual(framing.feed(line[30:]), [])
        self.assertEqual(framing.feed(b"\r\n"), [line])
        self.assertEqual(framing.pending, bytearray())

    def test_framing_limit_clears_without_leaking(self):
        framing = mirror.LineBuffer()
        with self.assertRaises(radio.RadioError) as error:
            framing.feed(b"secret" + b"x" * 512)
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(framing.pending, bytearray())
        self.assertEqual(framing.feed(b"x" * 512 + b"\n"), [b"x" * 512])

    def test_old_partial_line_cannot_complete_with_a_fresh_suffix(self):
        framing = mirror.LineBuffer()
        line = consumed()
        self.assertEqual(framing.feed(line[:35], 0.0), [])
        with self.assertRaisesRegex(radio.RadioError, "Incomplete hub line expired"):
            framing.feed(line[35:] + b"\n", 0.501)
        self.assertEqual(framing.pending, bytearray())
        self.assertIsNone(framing.started)

    def test_timely_fragmented_line_completes_and_new_line_gets_its_own_deadline(self):
        framing = mirror.LineBuffer()
        line = consumed()
        self.assertEqual(framing.feed(line[:35], 0.0), [])
        self.assertEqual(framing.feed(line[35:] + b"\n" + line[:35], 0.49), [line])
        self.assertEqual(framing.feed(line[35:] + b"\n", 0.98), [line])
        self.assertIsNone(framing.started)


class AdmissionTests(unittest.TestCase):
    def test_timer_confirmation_is_required_and_matches_duration(self):
        tracker = mirror.AdmissionTracker(1234)
        self.assertIsNone(tracker.consume(consumed(), 0))
        self.assertIsNone(tracker.consume(b"unrelated log", 0.01))
        self.assertEqual(tracker.consume(b"Setting up timer to stop shocker in 1000ms", 0.02),
                         mirror.HubCommand("beep", 0, 1000))
        self.assertIsNone(tracker.consume(b"Setting up timer to stop shocker in 1000ms", 0.03))

    def test_other_target_newer_candidate_or_expiry_prevent_false_admission(self):
        for interruption in (consumed(target=99), b"Consuming shocker command: 9",
                             b"Setting up timer to stop shocker in 999ms"):
            with self.subTest(interruption=interruption):
                tracker = mirror.AdmissionTracker(1234)
                tracker.consume(consumed(), 0)
                tracker.consume(interruption, 0.01)
                self.assertIsNone(tracker.consume(b"Setting up timer to stop shocker in 1000ms", 0.02))
        tracker = mirror.AdmissionTracker(1234)
        tracker.consume(consumed(), 0)
        self.assertIsNone(tracker.consume(b"Setting up timer to stop shocker in 1000ms", 0.51))

    def test_busy_and_capacity_rejection_prevent_forwarding(self):
        for rejection in (mirror.CAPACITY_REJECTION, b"Ignoring new command for busy shocker 1234"):
            with self.subTest(rejection=rejection):
                tracker = mirror.AdmissionTracker(1234)
                tracker.consume(consumed(), 0)
                self.assertEqual(tracker.consume(rejection, 0.01), mirror.STOP)
                self.assertIsNone(tracker.consume(b"Setting up timer to stop shocker in 1000ms", 0.02))

    def test_stop_needs_no_timer_and_clears_candidate(self):
        tracker = mirror.AdmissionTracker(1234)
        tracker.consume(consumed(), 0)
        self.assertEqual(tracker.consume(consumed("stop", 0, 0), 0.01), mirror.STOP)
        self.assertIsNone(tracker.consume(b"Setting up timer to stop shocker in 1000ms", 0.02))


class ClientTests(unittest.TestCase):
    def test_replacements_share_monotonic_sequence_and_never_wrap(self):
        port, clock = RadioPort(), Clock()
        client = mirror.MirrorRadioClient(port, clock)
        client.replace(mirror.HubCommand("beep", 0, 100))
        client.replace(mirror.HubCommand("vibrate", 1, 200))
        self.assertEqual(port.replacements, [b"REPLACE 1 b 0 100\n", b"REPLACE 2 v 1 200\n"])
        client.sequence = radio.MAX_SEQUENCE
        with self.assertRaisesRegex(radio.RadioError, "exhausted"):
            client.replace(mirror.HubCommand("beep", 0, 100))
        self.assertEqual(len(port.replacements), 2)

    def test_invalid_command_does_not_consume_sequence_or_write(self):
        port = RadioPort()
        client = mirror.MirrorRadioClient(port, Clock())
        with self.assertRaises(ValueError):
            client.replace(mirror.HubCommand("beep", 10, 100))
        self.assertEqual(port.writes, [])
        self.assertEqual(client.sequence, 0)


class LifecycleTests(unittest.TestCase):
    def run_loop(self, hub, port=None, *, count=10, beep_only=False, sleep=None):
        port = port or RadioPort()
        clock = Clock()
        logs = []
        mirror.run_mirror(hub, mirror.MirrorRadioClient(port, clock), 1234,
                          beep_only=beep_only, finished=steps(count), clock=clock,
                          sleep=sleep or clock.sleep, emit=logs.append)
        return port, logs

    def test_initialization_flushes_and_finally_disarms_without_hub_writes(self):
        hub = HubPort([admitted()], initial=admitted("shock", 99))
        port, _ = self.run_loop(hub)
        self.assertEqual(port.writes[:4], [b"HELLO\n", b"DISARM\n", b"SET 1234 0\n", b"PING\n"])
        self.assertEqual(port.replacements, [b"REPLACE 1 b 0 1000\n"])
        self.assertEqual(port.writes[-2:], [b"STOP\n", b"DISARM\n"])
        self.assertEqual(hub.writes, [])
        self.assertEqual(hub.resets, 1)
        self.assertFalse(any(b"ARM" == item.strip() for item in port.writes))

    def test_latest_batch_selected_command_wins_and_final_stop_cancels(self):
        hub = HubPort([admitted() + admitted("vibrate", 2),
                       admitted("shock", 3) + consumed("stop", 0, 0) + b"\n"])
        port, _ = self.run_loop(hub)
        self.assertEqual(port.replacements, [b"REPLACE 1 v 2 1000\n"])
        self.assertEqual(port.writes.count(b"STOP\n"), 2)

    def test_other_targets_do_not_replace_latest_selected_command(self):
        hub = HubPort([admitted() + admitted("shock", 90, target=2)])
        port, _ = self.run_loop(hub)
        self.assertEqual(port.replacements, [b"REPLACE 1 b 0 1000\n"])

    def test_fragmented_and_interleaved_logs_forward_only_complete_command(self):
        command = admitted()
        hub = HubPort([b"Consuming shocker command: 9\n" + command[:35], command[35:]])
        port, _ = self.run_loop(hub)
        self.assertEqual(port.replacements, [b"REPLACE 1 b 0 1000\n"])

    def test_beep_only_drops_other_modes_and_does_not_fall_back_to_earlier_beep(self):
        hub = HubPort([admitted() + admitted("vibrate"),
                       admitted(), admitted("shock", 3),
                       consumed("stop", 0, 0) + b"\n"])
        port, logs = self.run_loop(hub, beep_only=True)
        self.assertEqual(port.replacements, [b"REPLACE 1 b 0 1000\n"])
        self.assertEqual(logs.count("Non-beep command ignored during commissioning."), 2)

    def test_invalid_selected_command_stops_and_never_clamps(self):
        hub = HubPort([consumed("shock", 1, 10001) + b"\n"])
        port, _ = self.run_loop(hub)
        self.assertEqual(port.replacements, [])
        self.assertEqual(port.writes.count(b"STOP\n"), 2)

    def test_disarmed_rejection_discards_partial_and_queued_commands_before_arm(self):
        first = admitted() + consumed("shock", 50)[:35]
        second = consumed("shock", 50)[35:] + b"\n" + admitted("vibrate", 1)
        hub = HubPort([first, second])
        port = RadioPort(reasons=["DISARMED", None])
        calls = 0

        def during_ack():
            nonlocal calls
            calls += 1
            if calls == 1:
                hub.incoming.extend(admitted("shock", 99))

        port.on_replace = during_ack
        _, logs = self.run_loop(hub, port)
        self.assertEqual(port.replacements, [b"REPLACE 1 b 0 1000\n", b"REPLACE 2 v 1 1000\n"])
        self.assertEqual(hub.resets, 2)
        self.assertTrue(any("DISARMED" in line for line in logs))

    def test_limit_busy_rejections_discard_without_retry_and_keep_connection(self):
        for reason in ("LIMIT", "BUSY", "NOT_ARMED"):
            with self.subTest(reason=reason):
                hub = HubPort([admitted(), admitted("vibrate", 1)])
                port = RadioPort(reasons=[reason, None])
                self.run_loop(hub, port)
                self.assertEqual(port.replacements, [b"REPLACE 1 b 0 1000\n", b"REPLACE 2 v 1 1000\n"])

    def test_lost_ack_does_not_retry_and_disarms(self):
        hub = HubPort([admitted()])
        port = RadioPort(lose_replace_ack=True)
        with self.assertRaisesRegex(radio.RadioError, "timed out"):
            self.run_loop(hub, port)
        self.assertEqual(port.replacements, [b"REPLACE 1 b 0 1000\n"])
        self.assertEqual(port.writes[-2:], [b"STOP\n", b"DISARM\n"])

    def test_oversized_batch_or_line_disarms_without_forwarding(self):
        for invalid in (b"secret" * 2000, admitted() + b"secret" * 100):
            with self.subTest(size=len(invalid)):
                port = RadioPort()
                with self.assertRaises(radio.RadioError) as error:
                    self.run_loop(HubPort([invalid]), port)
                self.assertNotIn("secret", str(error.exception))
                self.assertEqual(port.replacements, [])
                self.assertEqual(port.writes[-2:], [b"STOP\n", b"DISARM\n"])

    def test_unexpected_device_error_stops_session_without_exposing_response(self):
        port = RadioPort(reasons=["UNKNOWN"])
        with self.assertRaisesRegex(radio.RadioError, "session stopped"):
            self.run_loop(HubPort([admitted()]), port)
        self.assertEqual(len(port.replacements), 1)
        self.assertEqual(port.writes[-1], b"DISARM\n")

    def test_ignored_credentials_never_leave_log_output(self):
        port, logs = self.run_loop(HubPort([b'TERMINALINFO: {"password":"secret"}\n'
                                           b'Message on channel \'secret\': "secret"\n']))
        self.assertNotIn("secret", " ".join(logs))
        self.assertEqual(port.replacements, [])

    def test_hub_disconnect_and_keyboard_interrupt_disarm(self):
        for interrupted in (False, True):
            hub, port = HubPort(), RadioPort()

            def sleeping(_):
                if interrupted:
                    raise KeyboardInterrupt
                hub.is_open = False

            with self.subTest(interrupted=interrupted):
                with self.assertRaises(KeyboardInterrupt if interrupted else radio.RadioError):
                    self.run_loop(hub, port, sleep=sleeping)
                self.assertEqual(port.writes[-2:], [b"STOP\n", b"DISARM\n"])

    def test_stalled_loop_discards_backlog_and_disarms(self):
        hub, port, clock = HubPort(), RadioPort(), Clock()

        def delayed(_):
            hub.incoming.extend(admitted("shock", 99))
            clock.now += 1

        with self.assertRaisesRegex(radio.RadioError, "delayed"):
            mirror.run_mirror(hub, mirror.MirrorRadioClient(port, clock), 1234,
                              finished=steps(3), clock=clock, sleep=delayed, emit=lambda _: None)
        self.assertEqual(port.replacements, [])
        self.assertEqual(port.writes[-1], b"DISARM\n")

    def test_partial_line_expires_during_healthy_loop_and_disarms_before_late_suffix(self):
        command = admitted("beep", 0, 100)
        hub = HubPort([command[:35]] + [b""] * 100 + [command[35:]])
        port = RadioPort()
        with self.assertRaisesRegex(radio.RadioError, "Incomplete hub line expired"):
            self.run_loop(hub, port, count=110)
        self.assertEqual(port.replacements, [])
        self.assertEqual(port.writes[-2:], [b"STOP\n", b"DISARM\n"])

    def test_heartbeat_keeps_idle_connection_alive(self):
        port, _ = self.run_loop(HubPort(), count=100)
        self.assertGreaterEqual(port.writes.count(b"PING\n"), 4)


class CliTests(unittest.TestCase):
    def test_invalid_targets_and_duplicate_ports_fail_before_open(self):
        for options in (["--id", "0"], ["--id", "1234", "--channel", "3"]):
            with patch.object(mirror, "open_serial") as opening, contextlib.redirect_stderr(io.StringIO()):
                result = mirror.main(["--hub-port", "COM6", "--port", "COM7", *options])
            self.assertEqual(result, 1)
            opening.assert_not_called()
        with patch.object(mirror, "open_serial") as opening, contextlib.redirect_stderr(io.StringIO()):
            result = mirror.main(["--hub-port", "COM6", "--port", "com6", "--id", "1234"])
        self.assertEqual(result, 1)
        opening.assert_not_called()


if __name__ == "__main__":
    unittest.main()
