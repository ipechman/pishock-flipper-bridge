"""Offline checks only: fake serial ports, no radio or network activity."""

import contextlib
import io
import json
import unittest
from unittest.mock import patch

import radio


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 0.001
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Port:
    def __init__(self, incoming=b"", automatic=False, suppress_run=False):
        self.incoming = bytearray(incoming)
        self.writes = []
        self.automatic = automatic
        self.suppress_run = suppress_run

    def read(self, size):
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        return result

    def write(self, data):
        self.writes.append(data)
        if self.automatic:
            operation = data.split()[0].decode("ascii")
            if not (operation == "RUN" and self.suppress_run):
                response = "RADIO1" if operation == "HELLO" else operation
                self.incoming.extend(f"OK {response}\n".encode("ascii"))
        return len(data)


class IdleConnectionTests(unittest.TestCase):
    def test_idle_connection_configures_heartbeats_and_never_runs(self):
        port = Port(automatic=True)
        clock = Clock()
        client = radio.RadioClient(port, clock)
        with contextlib.redirect_stdout(io.StringIO()):
            radio.connect_idle(client, 1234, 0, lambda: clock.now > 0.8,
                               clock=clock, sleep=clock.sleep)
        self.assertIn(b"SET 1234 0\n", port.writes)
        self.assertGreaterEqual(port.writes.count(b"PING\n"), 3)
        self.assertEqual(port.writes[-1], b"DISARM\n")
        self.assertFalse(any(line.startswith(b"RUN") for line in port.writes))


class ResponseTests(unittest.TestCase):
    def test_exact_responses_and_crlf(self):
        radio.parse_response(b"OK RUN\n", "RUN")
        radio.parse_response(b"OK RADIO1\r\n", "RADIO1")

    def test_malformed_responses_are_rejected_without_echo(self):
        for response in (b"OK RUN", b"OK RUN extra\n", b"OK PING\n", b"OK RUN\nsecret\n",
                         b"OK RUN \n", b"\xffsecret\n", b"ERR secret password\n", b"A" * 129 + b"\n"):
            with self.subTest(response=response):
                with self.assertRaises(radio.RadioError) as error:
                    radio.parse_response(response, "RUN")
                self.assertNotIn("secret", str(error.exception))

    def test_bounded_device_reason(self):
        with self.assertRaisesRegex(radio.RejectedCommand, "NOT_ARMED"):
            radio.parse_response(b"ERR NOT_ARMED\n", "RUN")

    def test_command_collects_fragmented_response(self):
        port = Port(b"OK RADIO1\r\n")
        radio.RadioClient(port, Clock()).hello()
        self.assertEqual(port.writes, [b"HELLO\n"])

    def test_lost_run_ack_does_not_retry(self):
        port = Port()
        client = radio.RadioClient(port, Clock())
        with self.assertRaisesRegex(radio.RadioError, "timed out"):
            client.run("beep", 0, 100)
        self.assertEqual(port.writes, [b"RUN 1 b 0 100\n"])
        self.assertEqual(client.sequence, 1)

    def test_sequences_increase_and_never_wrap(self):
        port = Port(automatic=True)
        client = radio.RadioClient(port, Clock())
        client.run("beep", 0, 100)
        client.run("vibrate", 1, 200)
        self.assertEqual(port.writes, [b"RUN 1 b 0 100\n", b"RUN 2 v 1 200\n"])
        client.sequence = radio.MAX_SEQUENCE
        with self.assertRaisesRegex(radio.RadioError, "exhausted"):
            client.run("beep", 0, 100)
        self.assertEqual(len(port.writes), 2)


class ValidationTests(unittest.TestCase):
    def test_keepalive_session_gate_is_explicit_and_does_not_send_an_operation(self):
        port = Port(automatic=True)
        client = radio.RadioClient(port, Clock())
        client.set_keepalive(True)
        client.set_keepalive(False)
        self.assertEqual(port.writes, [b"AWAKE 1\n", b"AWAKE 0\n"])
        self.assertEqual(client.sequence, 0)
        for value in (1, 0, None, "1", [], 1.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.set_keepalive(value)
        self.assertEqual(len(port.writes), 2)

    def test_protocol_boundaries(self):
        radio.validate_target(1, 0)
        radio.validate_target(65535, 2)
        self.assertEqual(radio.validate_run("shock", 0, 100), "s")
        self.assertEqual(radio.validate_run("v", 100, 10000), "v")

    def test_invalid_bounds_or_types_never_write(self):
        port = Port()
        client = radio.RadioClient(port, Clock())
        for args in (("s", -1, 100), ("s", 101, 100), ("s", 20, 99),
                     ("s", 20, 10001), ("b", 1, 100), ("unknown", 0, 100),
                     ("s", True, 100), ("s", 1, 100.0)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                client.run(*args)
        for args in ((0, 0), (65536, 0), (1, -1), (1, 3), (True, 0)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                client.configure(*args)
        self.assertEqual(port.writes, [])

    def test_cli_rejects_invalid_run_before_opening_any_port(self):
        with patch("radio.open_serial") as opening, contextlib.redirect_stderr(io.StringIO()):
            result = radio.main(["run", "--port", "FAKE", "--id", "123", "shock",
                                 "--duration-ms", "10001"])
        self.assertEqual(result, 1)
        opening.assert_not_called()


class LifecycleTests(unittest.TestCase):
    def test_success_sends_once_and_disarms_after_heartbeat_period(self):
        clock = Clock()
        port = Port(automatic=True)
        client = radio.RadioClient(port, clock)

        def arming(current):
            finish = clock() + 1.0
            radio.keep_alive_until(current, lambda: clock() >= finish, clock, clock.sleep)

        with contextlib.redirect_stdout(io.StringIO()):
            radio.perform_run(client, 123, 0, "beep", 0, 500,
                              arm_wait=arming, clock=clock, sleep=clock.sleep)
        self.assertEqual(port.writes[:2], [b"DISARM\n", b"SET 123 0\n"])
        self.assertEqual(port.writes[-1], b"DISARM\n")
        self.assertEqual(sum(data.startswith(b"RUN ") for data in port.writes), 1)
        self.assertGreaterEqual(port.writes.count(b"PING\n"), 6)
        self.assertGreaterEqual(clock.now, 2.0)

    def test_cancel_before_run_disarms_and_never_runs(self):
        port = Port(automatic=True)
        client = radio.RadioClient(port, Clock())

        def cancel(_):
            raise KeyboardInterrupt

        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            radio.perform_run(client, 123, 0, "beep", 0, 100, arm_wait=cancel)
        self.assertEqual(port.writes[-1], b"DISARM\n")
        self.assertFalse(any(data.startswith(b"RUN ") for data in port.writes))

    def test_missing_run_ack_still_attempts_disarm_and_does_not_retry(self):
        port = Port(automatic=True, suppress_run=True)
        clock = Clock()
        client = radio.RadioClient(port, clock)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(radio.RadioError):
            radio.perform_run(client, 123, 0, "beep", 0, 100,
                              arm_wait=lambda _: None, clock=clock, sleep=clock.sleep)
        self.assertEqual(sum(data.startswith(b"RUN ") for data in port.writes), 1)
        self.assertEqual(port.writes[-1], b"DISARM\n")


class DiagnosticTests(unittest.TestCase):
    def test_info_filters_unknown_fields_at_every_level(self):
        info = {
            "version": "3.1.1.231119.1556", "type": 4, "clientId": 621,
            "networks": [{"ssid": "secret-network", "password": "secret-password"}],
            "otk": "secret-pairing-key", "token": "secret-account", "unknown": "secret-other",
            "shockers": [{"id": 420, "type": 1, "paused": False, "name": "secret-name",
                          "unknown": {"token": "secret-token"}}],
        }
        sanitized = radio.safe_hub_info(info)
        self.assertEqual(sanitized, {"version": "3.1.1.231119.1556", "type": 4, "clientId": 621,
                                     "shockers": [{"id": 420, "type": 1, "paused": False}]})
        self.assertNotIn("secret", json.dumps(sanitized))

    def test_info_rejects_secret_values_hidden_in_allowed_fields(self):
        value = {"version": "secret-token", "clientId": "secret-id", "type": {"secret": 1},
                 "shockers": [{"id": {"secret": 1}, "type": "secret", "paused": "secret"},
                              "secret"]}
        self.assertEqual(radio.safe_hub_info(value), {"shockers": []})

    def test_read_info_sends_only_documented_read_request(self):
        info = {"version": "3.1", "type": 4, "clientId": 621, "password": "secret"}
        port = Port(b"boot secret log\nTERMINALINFO: " + json.dumps(info).encode() + b"\r\n")
        result = radio.read_hub_info(port, clock=Clock())
        self.assertEqual(result, {"version": "3.1", "type": 4, "clientId": 621})
        self.assertEqual(port.writes, [b'{"cmd":"info"}\n'])

    def test_malformed_info_never_exposes_input(self):
        port = Port(b'TERMINALINFO: {"password":"secret" BROKEN}\n')
        with self.assertRaises(radio.RadioError) as error:
            radio.read_hub_info(port, clock=Clock())
        self.assertNotIn("secret", str(error.exception))

    def test_observation_is_passive_and_output_contains_shapes_only(self):
        port = Port(b'prefix-secret {"id":123,"m":"s","i":25,"d":1000,"secret-key":"secret"}\n'
                    b'TERMINALINFO: {"password":"secret"}\n'
                    b'secret plain text\n'
                    b'{"value":{"op":"beep","password":"secret"}}\n')
        result = radio.observe_hub(port, duration=1, clock=Clock())
        self.assertEqual(port.writes, [])
        encoded = json.dumps(result)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("123", encoded)
        self.assertNotIn("beep", encoded)
        self.assertEqual(result["line_categories"], {"prefixed_json": 1, "terminal_info_discarded": 1,
                                                     "text_discarded": 1, "json": 1})
        self.assertIn('"number"', encoded)
        self.assertIn('"unknown_field_count"', encoded)

    def test_oversized_lines_discarded_and_following_lines_recover(self):
        port = Port(b"secret" * 30 + b"\nnormal\n")
        with patch("radio.MAX_HUB_LINE", 100):
            result = radio.observe_hub(port, duration=1, clock=Clock())
        self.assertEqual(result["line_categories"], {"oversized": 1, "text_discarded": 1})
        self.assertEqual(result["json_shapes"], [])


if __name__ == "__main__":
    unittest.main()
