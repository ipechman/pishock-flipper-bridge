"""Mirror fresh accepted PiShock hub commands to the Flipper over USB.

The original hub remains the PiShock backend client and still transmits RF.
Both the original hub and the Flipper can therefore transmit simultaneously.
This reads diagnostic output from stock firmware 3.1.4.251129.2525; it does
not authenticate as a replacement hub, change firmware, or write to the hub.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import re
import sys
import time
from typing import Any

from radio import (HEARTBEAT_SECONDS, MAX_SEQUENCE, RadioClient, RadioError,
                   RejectedCommand, open_serial, validate_run, validate_target)


MAX_LINE_BYTES = 512
MAX_BATCH_BYTES = 8192
MAX_LOOP_GAP_SECONDS = 0.5
MAX_ADMISSION_AGE_SECONDS = 0.5
MAX_PARTIAL_LINE_AGE_SECONDS = 0.5
CONSUMED_PREFIX = b"Consuming shocker command: "
TIMER_PREFIX = b"Setting up timer to stop shocker in "
TIMER_PATTERN = re.compile(rb"Setting up timer to stop shocker in ([0-9]{1,5})ms")
CAPACITY_REJECTION = b"Error: Too many shockers operated at once; dropping command!"
TARGET_PATTERN = re.compile(rb"Consuming shocker command: id ([0-9]{1,5}),")
COMMAND_PATTERN = re.compile(
    rb"Consuming shocker command: id ([0-9]{1,5}), mode (beep|vibrate|shock|stop), "
    rb"intensity ([0-9]{1,3})%, duration ([0-9]{1,5})ms")
CONTINUABLE_REASONS = frozenset({"DISARMED", "NOT_ARMED", "BUSY", "LIMIT"})


class InvalidHubCommand(Exception):
    """A selected consumed command is invalid; never include its raw bytes."""


@dataclass(frozen=True)
class HubCommand:
    mode: str
    intensity: int = 0
    duration_ms: int = 0


STOP = HubCommand("stop")


def parse_consumed_line(line: bytes, shocker_id: int) -> HubCommand | None:
    """Accept the exact observed ASCII format for one explicitly selected ID.

    Other logs, including TERMINALINFO and raw network messages, are ignored.
    Interleaved firmware output without a trustworthy selected ID is ignored.
    """
    if not isinstance(line, bytes):
        raise TypeError("Hub lines must be bytes.")
    if len(line) > MAX_LINE_BYTES:
        raise RadioError("Hub line exceeded the input limit; raw data discarded.")
    if not line.startswith(CONSUMED_PREFIX):
        return None
    target = TARGET_PATTERN.match(line)
    if target is None or int(target.group(1)) != shocker_id:
        return None
    parsed = COMMAND_PATTERN.fullmatch(line)
    if parsed is None:
        raise InvalidHubCommand("Malformed accepted command discarded.")
    target_id, mode_bytes, intensity_bytes, duration_bytes = parsed.groups()
    if int(target_id) != shocker_id:
        return None
    mode = mode_bytes.decode("ascii")
    intensity, duration_ms = int(intensity_bytes), int(duration_bytes)
    if mode == "stop":
        if intensity == 0 and duration_ms == 0:
            return STOP
        raise InvalidHubCommand("Invalid stop command discarded.")
    try:
        validate_run(mode, intensity, duration_ms)
    except ValueError:
        raise InvalidHubCommand("Accepted command outside supported limits discarded.") from None
    return HubCommand(mode, intensity, duration_ms)


class LineBuffer:
    """Bounded CR/LF framing; neither credentials nor arbitrary logs are emitted."""

    def __init__(self):
        self.pending = bytearray()
        self.started: float | None = None

    def clear(self) -> None:
        self.pending.clear()
        self.started = None

    def expire(self, now: float) -> None:
        if self.started is not None and now - self.started > MAX_PARTIAL_LINE_AGE_SECONDS:
            self.clear()
            raise RadioError("Incomplete hub line expired; pending commands discarded and session stopped.")

    def feed(self, chunk: bytes, now: float | None = None) -> list[bytes]:
        now = time.monotonic() if now is None else now
        # Check before accepting a late suffix: a healthy read loop does not
        # make bytes retained across earlier iterations fresh.
        self.expire(now)
        lines = []
        for byte in chunk:
            if byte in (10, 13):
                if self.pending:
                    lines.append(bytes(self.pending))
                    self.clear()
            else:
                if not self.pending:
                    self.started = now
                self.pending.append(byte)
                if len(self.pending) > MAX_LINE_BYTES:
                    self.clear()
                    raise RadioError("Hub line exceeded the input limit; raw data discarded.")
        return lines


class MirrorRadioClient(RadioClient):
    """One sequenced replacement per fresh command, including rejected attempts."""

    def replace(self, command: HubCommand) -> None:
        resolved = validate_run(command.mode, command.intensity, command.duration_ms)
        if self.sequence >= MAX_SEQUENCE:
            raise RadioError("Session sequence exhausted; reconnect while disarmed.")
        self.sequence += 1
        self.command(
            f"REPLACE {self.sequence} {resolved} {command.intensity} {command.duration_ms}",
            "REPLACE")


def safe_rejection_reason(error: RejectedCommand) -> str | None:
    matched = re.fullmatch(r"Flipper rejected the command: ([A-Z][A-Z0-9_]{0,39})", str(error))
    return matched.group(1) if matched is not None else None


def discard_hub_input(hub: Any, framing: LineBuffer) -> None:
    framing.clear()
    hub.reset_input_buffer()


class AdmissionTracker:
    """Correlate a consumed candidate with its later transmitter timer setup.

    The consumed line alone can precede a busy/capacity rejection. Any newer
    consumed line breaks correlation, even when it belongs to another target.
    """

    def __init__(self, shocker_id: int):
        self.shocker_id = shocker_id
        self.pending: HubCommand | None = None
        self.started = 0.0

    def clear(self) -> None:
        self.pending = None

    def expire(self, now: float) -> None:
        if self.pending is not None and now - self.started > MAX_ADMISSION_AGE_SECONDS:
            self.clear()

    def consume(self, line: bytes, now: float) -> HubCommand | None:
        self.expire(now)
        if line.startswith(CONSUMED_PREFIX):
            self.clear()
            parsed = parse_consumed_line(line, self.shocker_id)
            if parsed is None or parsed.mode == "stop":
                return parsed
            self.pending, self.started = parsed, now
        elif line.startswith(TIMER_PREFIX):
            timer = TIMER_PATTERN.fullmatch(line)
            pending = self.pending
            self.clear()
            if timer is not None and pending is not None and int(timer.group(1)) == pending.duration_ms:
                return pending
        elif line == CAPACITY_REJECTION or line.startswith(b"Ignoring new command for busy shocker "):
            rejected_selected = self.pending is not None
            self.clear()
            if rejected_selected:
                return STOP
        return None


def stop_and_disarm(client: MirrorRadioClient) -> bool:
    """Cleanup never resends an operation or prevents the second stop measure."""
    stopped = disarmed = False
    try:
        client.stop()
        stopped = True
    except (RadioError, OSError):
        pass
    try:
        client.disarm()
        disarmed = True
    except (RadioError, OSError):
        pass
    return stopped and disarmed


def run_mirror(hub: Any, client: MirrorRadioClient, shocker_id: int, channel: int = 0,
               *, beep_only: bool = False, finished=lambda: False,
               clock=time.monotonic, sleep=time.sleep, emit=print) -> None:
    """Single-threaded serial loop. The original hub is only read and flushed.

    Driver buffers cannot supply original event timestamps. Startup and rejection
    flushes, bounded batches, and the loop deadline reduce backlog; they cannot
    establish exact end-to-end freshness from this undocumented diagnostic feed.
    No automatic reconnect, arming, or operation retry is performed.
    """
    validate_target(shocker_id, channel)
    framing = LineBuffer()
    admission = AdmissionTracker(shocker_id)
    try:
        client.hello()
        client.disarm()
        client.configure(shocker_id, channel)
        client.ping()
        # Initialization may have taken long enough for old logs to accumulate.
        hub.timeout = 0
        discard_hub_input(hub, framing)
        emit(f"Connected for target {shocker_id}, channel {channel}; physically arm the Flipper to forward fresh commands.")
        emit("The original PiShock hub remains connected to its backend and also transmits RF.")
        if beep_only:
            emit("Beep-only commissioning: shock and vibration commands are ignored.")
        session_started = last_iteration = clock()
        next_ping = last_iteration + HEARTBEAT_SECONDS
        while not finished():
            now = clock()
            if now - last_iteration > MAX_LOOP_GAP_SECONDS:
                discard_hub_input(hub, framing)
                raise RadioError("Mirror loop was delayed; pending commands discarded and session stopped.")
            last_iteration = now
            framing.expire(now)
            admission.expire(now)
            if not hub.is_open:
                raise RadioError("Original hub USB connection closed.")
            if now >= next_ping:
                client.ping()
                next_ping = clock() + HEARTBEAT_SECONDS
            waiting = hub.in_waiting
            if waiting > MAX_BATCH_BYTES:
                discard_hub_input(hub, framing)
                raise RadioError("Hub input backlog exceeded the limit; session stopped.")
            command = None
            if waiting:
                chunk = hub.read(waiting)
                if not chunk:
                    raise RadioError("Original hub USB read failed.")
                # Parse the entire available batch before transmitting anything.
                # The final selected operation wins, including a final stop.
                for line in framing.feed(chunk, clock()):
                    try:
                        parsed = admission.consume(line, clock())
                    except InvalidHubCommand:
                        command = STOP
                        emit("Invalid accepted command discarded; stopping the selected target.")
                    else:
                        if parsed is not None:
                            command = parsed
            if command is not None:
                if command.mode == "stop":
                    client.stop()
                    emit(f"[{clock() - session_started:.3f}s] Forwarded stop.")
                elif beep_only and command.mode != "beep":
                    emit("Non-beep command ignored during commissioning.")
                else:
                    try:
                        client.replace(command)
                    except RejectedCommand as error:
                        reason = safe_rejection_reason(error)
                        if reason not in CONTINUABLE_REASONS:
                            raise RadioError("Flipper rejected the operation; session stopped.") from None
                        # Commands arriving during a rejected command's ACK wait
                        # must not become a queue to play when the user arms.
                        client.stop()
                        discard_hub_input(hub, framing)
                        admission.clear()
                        emit(f"Command discarded: {reason}. No operation was retried.")
                    else:
                        emit(f"[{clock() - session_started:.3f}s] Forwarded {command.mode}: {command.intensity}%, {command.duration_ms} ms.")
            sleep(0.01)
    finally:
        framing.clear()
        admission.clear()
        if not stop_and_disarm(client):
            emit("Stop/disarm acknowledgment unavailable. Use Back on the Flipper to stop.")


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Forward accepted stock PiShock hub USB logs to the Flipper. Both devices still transmit RF.")
    argument_parser.add_argument("--hub-port", required=True, help="Original PiShock hub USB port")
    argument_parser.add_argument("--port", required=True, help="Flipper USB Radio app port")
    argument_parser.add_argument("--id", required=True, type=int, dest="shocker_id")
    argument_parser.add_argument("--channel", type=int, default=0, help="Radio channel 0, 1 or 2; default 0")
    argument_parser.add_argument("--beep-only", action="store_true", help="Forward only beep and stop for commissioning")
    return argument_parser


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        validate_target(args.shocker_id, args.channel)
        if not args.hub_port.strip() or not args.port.strip() or args.hub_port.casefold() == args.port.casefold():
            raise ValueError("Select distinct original-hub and Flipper USB ports.")
        with open_serial(args.hub_port, original_hub=True) as hub:
            with open_serial(args.port) as flipper:
                run_mirror(hub, MirrorRadioClient(flipper), args.shocker_id,
                           args.channel, beep_only=args.beep_only)
        return 0
    except KeyboardInterrupt:
        print("Mirror stopped and disarmed.", file=sys.stderr)
        return 130
    except (RadioError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError:
        print("USB connection failed; mirror stopped. Use Back on the Flipper to stop.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
