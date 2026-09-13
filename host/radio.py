"""Local USB client for the PiShock USB Radio Flipper application.

This program has no network client. The original-hub commands only read
diagnostics; they never forward observations to the Flipper.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import sys
import threading
import time
from typing import Any


HEARTBEAT_SECONDS = 0.25
RESPONSE_SECONDS = 0.40
MAX_RADIO_LINE = 128
MAX_HUB_LINE = 65536
MAX_SEQUENCE = 0xFFFFFFFF
MODES = {"b": "b", "beep": "b", "v": "v", "vibrate": "v", "s": "s", "shock": "s"}


class RadioError(Exception):
    """A protocol failure. Error messages do not include arbitrary device data."""


class RejectedCommand(RadioError):
    pass


def bounded_int(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}.")
    return value


def validate_target(shocker_id: int, channel: int) -> None:
    bounded_int(shocker_id, "Shocker ID", 1, 65535)
    bounded_int(channel, "Channel", 0, 2)


def validate_run(mode: str, intensity: int, duration_ms: int) -> str:
    if mode not in MODES:
        raise ValueError("Mode must be beep, vibrate, or shock (b, v, or s).")
    bounded_int(intensity, "Intensity", 0, 100)
    bounded_int(duration_ms, "Duration in milliseconds", 100, 10000)
    resolved = MODES[mode]
    if resolved == "b" and intensity != 0:
        raise ValueError("Beep intensity must be 0.")
    return resolved


def parse_response(line: bytes, expected: str) -> None:
    """Reject partial, unrelated, non-ASCII and unsolicited responses."""
    if not isinstance(line, bytes) or len(line) > MAX_RADIO_LINE or not line.endswith(b"\n"):
        raise RadioError("Incomplete or oversized radio response.")
    try:
        response = line[:-1].removesuffix(b"\r").decode("ascii")
    except UnicodeDecodeError:
        raise RadioError("Radio response was not ASCII.") from None
    if response == f"OK {expected}":
        return
    if re.fullmatch(r"ERR [A-Z][A-Z0-9_]{0,39}", response):
        # Only the bounded protocol reason is exposed, never arbitrary logs.
        raise RejectedCommand("Flipper rejected the command: " + response[4:])
    raise RadioError("Unexpected radio response; no operation was retried.")


class RadioClient:
    """Synchronous protocol client; exactly one thread owns its serial port."""

    def __init__(self, port: Any, clock=time.monotonic):
        self.port = port
        self.clock = clock
        self.sequence = 0

    def command(self, command: str, expected: str) -> None:
        if not re.fullmatch(r"[A-Z0-9a-z ]{1,100}", command):
            raise ValueError("Invalid local protocol command.")
        data = (command + "\n").encode("ascii")
        if self.port.write(data) != len(data):
            raise RadioError("Incomplete USB write; no operation was retried.")
        deadline = self.clock() + RESPONSE_SECONDS
        response = bytearray()
        while self.clock() < deadline:
            fragment = self.port.read(1)
            if not fragment:
                continue
            response.extend(fragment)
            if len(response) > MAX_RADIO_LINE:
                raise RadioError("Oversized radio response.")
            if fragment == b"\n":
                parse_response(bytes(response), expected)
                return
        raise RadioError("Radio response timed out; no operation was retried.")

    def hello(self) -> None:
        self.command("HELLO", "RADIO1")

    def ping(self) -> None:
        self.command("PING", "PING")

    def configure(self, shocker_id: int, channel: int) -> None:
        validate_target(shocker_id, channel)
        self.command(f"SET {shocker_id} {channel}", "SET")

    def run(self, mode: str, intensity: int, duration_ms: int) -> None:
        resolved = validate_run(mode, intensity, duration_ms)
        if self.sequence >= MAX_SEQUENCE:
            raise RadioError("Session sequence exhausted; reconnect while disarmed.")
        self.sequence += 1
        # Consume the number before writing. A lost acknowledgment never
        # justifies a second RUN transmission.
        self.command(f"RUN {self.sequence} {resolved} {intensity} {duration_ms}", "RUN")

    def stop(self) -> None:
        self.command("STOP", "STOP")

    def disarm(self) -> None:
        self.command("DISARM", "DISARM")

    def best_effort_disarm(self) -> bool:
        try:
            self.disarm()
            return True
        except (RadioError, OSError):
            return False


def keep_alive_until(client: RadioClient, finished, clock=time.monotonic, sleep=time.sleep) -> None:
    """Maintain heartbeat while waiting for user input or a local deadline."""
    next_ping = clock()
    while not finished():
        now = clock()
        if now >= next_ping:
            client.ping()
            next_ping = clock() + HEARTBEAT_SECONDS
        sleep(0.02)


def wait_for_enter(client: RadioClient) -> None:
    if not sys.stdin.isatty():
        raise RadioError("RUN requires an interactive terminal for physical arming.")
    ready = threading.Event()
    canceled = threading.Event()

    def read_console() -> None:
        try:
            input("Arm on the Flipper, then press Enter here to send once. Ctrl+C cancels. ")
        except (EOFError, KeyboardInterrupt):
            canceled.set()
        finally:
            ready.set()

    # This thread reads the console only. All serial I/O remains on the caller.
    threading.Thread(target=read_console, daemon=True).start()
    keep_alive_until(client, ready.is_set)
    if canceled.is_set():
        raise RadioError("Operation canceled before transmission.")


def perform_run(client: RadioClient, shocker_id: int, channel: int, mode: str,
                intensity: int, duration_ms: int, arm_wait=wait_for_enter,
                clock=time.monotonic, sleep=time.sleep) -> None:
    validate_target(shocker_id, channel)
    resolved = validate_run(mode, intensity, duration_ms)
    try:
        client.disarm()
        client.configure(shocker_id, channel)
        client.ping()
        print(f"Target {shocker_id}, channel {channel}: {resolved}, {intensity}%, {duration_ms} ms.")
        arm_wait(client)
        client.ping()
        client.run(resolved, intensity, duration_ms)
        end = clock() + duration_ms / 1000 + 0.5
        keep_alive_until(client, lambda: clock() >= end, clock=clock, sleep=sleep)
        print("Command acknowledged; the USB connection remained active through its duration.")
    finally:
        if not client.best_effort_disarm():
            print("DISARM acknowledgment unavailable. Use Back on the Flipper to stop.", file=sys.stderr)


def connect_idle(client: RadioClient, shocker_id: int, channel: int, finished=lambda: False,
                 clock=time.monotonic, sleep=time.sleep) -> None:
    """Configure and maintain USB only. This mode never sends a RUN command."""
    validate_target(shocker_id, channel)
    try:
        client.disarm()
        client.configure(shocker_id, channel)
        client.ping()
        print(f"Connected to target {shocker_id}, channel {channel}; disarmed. "
              "USB only: PiShock website is not connected. Ctrl+C disconnects.", flush=True)
        keep_alive_until(client, finished, clock=clock, sleep=sleep)
    finally:
        client.best_effort_disarm()


def open_serial(port_name: str, *, original_hub: bool = False):
    try:
        import serial
    except ImportError:
        raise RadioError("Install the host requirements first: python -m pip install -r requirements.txt") from None
    connection = serial.Serial(port=None, baudrate=115200, bytesize=8,
                               parity="N", stopbits=1, timeout=0.02,
                               write_timeout=0.4, xonxoff=False,
                               rtscts=False, dsrdtr=False)
    # Suppress intentional reset/boot-line toggles for the ESP32 hub. A USB
    # driver may still briefly toggle these lines when the port is opened.
    connection.dtr = not original_hub
    connection.rts = False
    connection.port = port_name
    try:
        connection.open()
    except Exception:
        connection.close()
        raise RadioError("Could not open the selected USB port. Close other programs using it.") from None
    return connection


def list_ports() -> None:
    try:
        from serial.tools import list_ports as serial_ports
    except ImportError:
        raise RadioError("Install the host requirements before listing USB ports.") from None
    items = list(serial_ports.comports())
    if not items:
        print("No serial ports found.")
    for item in items:
        vid = f"{item.vid:04X}" if item.vid is not None else "----"
        pid = f"{item.pid:04X}" if item.pid is not None else "----"
        print(f"{item.device}\tVID:PID {vid}:{pid}\t{item.description}")
    if items:
        print("Select the extra Flipper COM port that appears when USB Radio opens.")


def hub_lines(port: Any, duration: float, clock=time.monotonic):
    """Read bounded lines without retaining oversized input or sending data."""
    deadline = clock() + duration
    buffered = bytearray()
    oversized = False
    while clock() < deadline:
        fragment = port.read(1)
        if not fragment:
            continue
        if fragment in (b"\n", b"\r"):
            if oversized:
                yield None
            elif buffered:
                yield bytes(buffered)
            buffered.clear()
            oversized = False
        elif not oversized:
            buffered.extend(fragment)
            if len(buffered) > MAX_HUB_LINE:
                buffered.clear()
                oversized = True
    # Incomplete lines are deliberately discarded.


def safe_hub_info(value: Any) -> dict[str, Any]:
    """Whitelist keys AND types; never return nested arbitrary firmware data."""
    if not isinstance(value, dict):
        raise RadioError("Hub info was not a JSON object.")
    result: dict[str, Any] = {}
    version = value.get("version")
    if isinstance(version, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,7}", version) and len(version) <= 64:
        result["version"] = version
    for key in ("type", "clientId"):
        item = value.get(key)
        if type(item) is int and 0 <= item <= 0xFFFFFFFF:
            result[key] = item
    shockers = value.get("shockers")
    if isinstance(shockers, list):
        entries = []
        for shocker in shockers[:256]:
            if not isinstance(shocker, dict):
                continue
            entry = {}
            for key in ("id", "type"):
                item = shocker.get(key)
                if type(item) is int and 0 <= item <= 0xFFFFFFFF:
                    entry[key] = item
            if type(shocker.get("paused")) is bool:
                entry["paused"] = shocker["paused"]
            if entry:
                entries.append(entry)
        result["shockers"] = entries
    return result


def read_hub_info(port: Any, timeout: float = 5.0, clock=time.monotonic) -> dict[str, Any]:
    request = b'{"cmd":"info"}\n'
    if port.write(request) != len(request):
        raise RadioError("Incomplete hub info request; it was not retried.")
    for line in hub_lines(port, timeout, clock=clock):
        if line is None or not line.startswith(b"TERMINALINFO:"):
            continue
        try:
            value = json.loads(line[len(b"TERMINALINFO:"):])
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise RadioError("Hub returned malformed info; raw content was discarded.") from None
        return safe_hub_info(value)
    raise RadioError("No hub info arrived. Verify the port and that the hub uses V3 firmware.")


SHAPE_KEYS = frozenset({
    "cmd", "value", "id", "m", "i", "d", "r", "l", "u", "ty", "w", "h", "o",
    "op", "duration", "intensity", "Commands", "Id", "Type", "Values", "Status",
    "ShockerId", "Duration", "Intensity", "Method", "Milliseconds", "Operation",
    "Body", "Payload", "Command", "Message", "ErrorCode", "IsError",
})


def json_shape(value: Any, depth: int = 0) -> Any:
    """Only known field names and primitive type names leave this function."""
    if depth >= 4:
        return "depth-limit"
    if isinstance(value, dict):
        shape = {key: json_shape(item, depth + 1)
                 for key, item in value.items() if key in SHAPE_KEYS}
        unknown = sum(key not in SHAPE_KEYS for key in value)
        if unknown:
            shape["unknown_field_count"] = unknown
        return shape
    if isinstance(value, list):
        return {"array_items": [json_shape(item, depth + 1) for item in value[:4]]}
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def observe_hub(port: Any, duration: float = 10, clock=time.monotonic) -> dict[str, Any]:
    counts: Counter = Counter()
    shapes = set()
    for line in hub_lines(port, duration, clock=clock):
        if line is None:
            counts["oversized"] += 1
            continue
        if line.startswith(b"TERMINALINFO:"):
            counts["terminal_info_discarded"] += 1
            continue
        # A firmware log may prefix its JSON with a timestamp or log level.
        # That prefix is not emitted or saved.
        starts = [index for index in (line.find(b"{"), line.find(b"[")) if index >= 0]
        if not starts:
            counts["text_discarded"] += 1
            continue
        found = False
        for index in sorted(starts):
            try:
                value = json.loads(line[index:])
            except (ValueError, UnicodeDecodeError, RecursionError):
                continue
            counts["json" if index == 0 else "prefixed_json"] += 1
            if len(shapes) < 32:
                shapes.add(json.dumps(json_shape(value), sort_keys=True))
            found = True
            break
        if not found:
            counts["unparsed_discarded"] += 1
    return {"line_categories": dict(counts), "json_shapes": [json.loads(shape) for shape in sorted(shapes)]}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="PiShock USB Radio local client; no backend connection.")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List local USB serial ports")
    for name in ("info", "stop", "disarm", "configure", "connect", "run", "hub-info", "hub-observe"):
        item = sub.add_parser(name)
        item.add_argument("--port", required=True, help="Explicit serial port, such as COM6")
        if name in ("configure", "connect", "run"):
            item.add_argument("--id", type=int, required=True, dest="shocker_id")
            item.add_argument("--channel", type=int, default=0, help="Radio channel 0, 1 or 2 (default 0)")
        if name == "run":
            item.add_argument("mode", choices=tuple(MODES))
            item.add_argument("--intensity", type=int, default=0, help="0–100; Flipper's local cap still applies")
            item.add_argument("--duration-ms", type=int, default=500, help="100–10000 (default 500)")
        if name in ("hub-info", "hub-observe"):
            item.add_argument("--seconds", type=int, default=5 if name == "hub-info" else 10,
                              help="Read duration in seconds, 1–60")
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "list":
            list_ports()
            return 0
        if args.command in ("configure", "connect", "run"):
            validate_target(args.shocker_id, args.channel)
        if args.command == "run":
            validate_run(args.mode, args.intensity, args.duration_ms)
        if args.command in ("hub-info", "hub-observe"):
            bounded_int(args.seconds, "Read duration", 1, 60)
            with open_serial(args.port, original_hub=True) as port:
                result = (read_hub_info(port, args.seconds) if args.command == "hub-info"
                          else observe_hub(port, args.seconds))
            print(json.dumps(result, indent=2))
            return 0
        with open_serial(args.port) as port:
            client = RadioClient(port)
            client.hello()
            if args.command == "info":
                client.ping()
                print("USB Radio protocol RADIO1 is responding. Check the Flipper screen for arming and limits.")
            elif args.command == "configure":
                client.disarm()
                client.configure(args.shocker_id, args.channel)
                print(f"Configured target {args.shocker_id}, channel {args.channel}; disarmed.")
            elif args.command == "run":
                perform_run(client, args.shocker_id, args.channel, args.mode,
                            args.intensity, args.duration_ms)
            elif args.command == "connect":
                connect_idle(client, args.shocker_id, args.channel)
            elif args.command == "stop":
                client.stop()
                print("STOP acknowledged.")
            elif args.command == "disarm":
                client.disarm()
                print("DISARM acknowledged.")
        return 0
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130
    except (RadioError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError:
        print("USB connection failed. Use Back on the Flipper to stop.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
