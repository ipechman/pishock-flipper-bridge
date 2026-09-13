"""PiShock backend to the existing Flipper add-on, without the original hub."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
import time

from device_backend import run_backend
from device_policy import snapshot_from_registration, admit, PolicyError
from identity import load_identity
from radio import RadioError, RejectedCommand, HEARTBEAT_SECONDS, open_serial
from serial_mirror import MirrorRadioClient, safe_rejection_reason, stop_and_disarm
from start_bridge import FLIPPER_USB, is_radio_interface, checked_port_name


PROFILE = Path(__file__).resolve().parents[1] / '.local/device.dpapi'
MAX_EVENT_AGE = 0.5
MAX_LOOP_GAP = 0.5
DEPENDENCY_HINT = ('USB support (pyserial) is missing. Use the same Python interpreter '
                   'to run -m pip install -r host/requirements.txt from the project folder. '
                   'See README.md for first-time setup.')


def find_flipper_port():
    try:
        from serial.tools import list_ports
    except ImportError:
        raise RadioError(DEPENDENCY_HINT) from None
    candidates = [port for port in list_ports.comports()
                  if (port.vid, port.pid) == FLIPPER_USB and is_radio_interface(port)]
    if len(candidates) != 1:
        raise RadioError('Connect one Flipper and open PiShock USB Radio before starting.')
    return checked_port_name(candidates[0])


@dataclass
class Mailbox:
    snapshot: object = None
    ready: bool = False
    pending: object = None
    pending_at: float = 0.0
    disarm: bool = False
    ready_notice: bool = False
    rearm_on_ready: bool = True
    failure: str | None = None


async def run_standalone(identity, client, *, beep_only=False, backend=run_backend,
                         emit=print, finished=None, clock=time.monotonic):
    """The event loop alone owns the Flipper serial client and latest command."""
    finished = finished if finished is not None else asyncio.Event()
    state = Mailbox()

    def on_snapshot(data):
        fresh = snapshot_from_registration(data, identity)
        if state.snapshot is not None and state.snapshot != fresh:
            state.disarm = True
            state.pending = None
            state.rearm_on_ready = True
        state.snapshot = fresh

    def on_message(channel, payload, received):
        if not state.ready or state.snapshot is None:
            return
        decision = admit(channel, payload, state.snapshot, identity)
        if decision is not None:
            state.pending, state.pending_at = decision, received

    def on_invalidated():
        state.ready = False
        state.snapshot = None
        state.pending = None
        state.disarm = True
        state.rearm_on_ready = True

    def on_ready():
        state.ready = True
        if state.rearm_on_ready:
            state.disarm = True
            state.ready_notice = True
            state.rearm_on_ready = False

    def on_failure(reason):
        on_invalidated()
        # Backend supplies a fixed category, not raw response/URL text.
        state.failure = 'PiShock connection ended; the Flipper was disarmed.'

    task = None
    configured = False
    try:
        client.hello()
        client.disarm()
        emit('Connecting directly to PiShock for your saved hub...', flush=True)
        task = asyncio.create_task(backend(identity, on_snapshot, on_message,
                                         on_invalidated, on_ready, on_failure, finished))
        last_loop = clock()
        next_ping = last_loop
        session_start = last_loop
        while not finished.is_set():
            now = clock()
            if now - last_loop > MAX_LOOP_GAP:
                raise RadioError('Computer processing was delayed. Restart and re-arm; no commands were replayed.')
            last_loop = now
            if state.disarm:
                state.disarm = False
                state.pending = None
                client.stop()
                client.disarm()
            if state.failure:
                raise RadioError(state.failure)
            if task.done():
                await task
                if not finished.is_set():
                    raise RadioError('PiShock connection ended. Restart the bridge and re-arm.')
                break
            if state.ready and not configured:
                client.configure(identity.shocker_id, identity.channel)
                configured = True
            if configured and now >= next_ping:
                client.ping()
                next_ping = clock() + HEARTBEAT_SECONDS
            if state.ready_notice and configured:
                state.ready_notice = False
                emit(f'Direct connection ready: hub {identity.hub_id}, shocker {identity.shocker_id}.', flush=True)
                emit('Original hub is not needed. Press OK on the Flipper to arm; Back stops the Flipper.', flush=True)
                if beep_only:
                    emit('Beep-only test: shock and vibration are ignored.', flush=True)
            pending, state.pending = state.pending, None
            if pending is not None and configured and state.ready:
                if clock() - state.pending_at > MAX_EVENT_AGE:
                    raise RadioError('An incoming command became stale. Restart and re-arm.')
                command = pending.command
                if command.mode == 'stop':
                    client.stop()
                    emit(f'[{clock() - session_start:.3f}s] Stop forwarded.', flush=True)
                elif beep_only and command.mode != 'beep':
                    emit('Non-beep command ignored during this test.', flush=True)
                else:
                    try:
                        if pending.repeat:
                            client.replace(command)
                        else:
                            client.run(command.mode, command.intensity, command.duration_ms)
                    except RejectedCommand as error:
                        reason = safe_rejection_reason(error)
                        if reason not in ('DISARMED', 'LIMIT', 'BUSY'):
                            raise RadioError('Flipper rejected the operation; the connection was stopped.') from None
                        # BUSY with r=false preserves the previous operation,
                        # matching the stock transmitter's nonreplacement path.
                        if reason != 'BUSY':
                            client.stop()
                        state.pending = None
                        emit(f'Command discarded: {reason}. It was not retried.', flush=True)
                    else:
                        emit(f'[{clock() - session_start:.3f}s] Forwarded {command.mode}: '
                             f'{command.intensity}%, {command.duration_ms} ms.', flush=True)
            await asyncio.sleep(0.01)
    finally:
        # Stop the physical output before waiting for network teardown.
        if not stop_and_disarm(client):
            emit('Stop acknowledgment unavailable. Press Back on the Flipper.', flush=True)
        finished.set()
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description='Use PiShock website controls through USB Flipper without the original hub.')
    parser.add_argument('--port', help='Flipper radio COM port; automatically selected if omitted')
    parser.add_argument('--beep-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        if not PROFILE.is_file():
            raise RadioError('No device identity is saved yet. Complete the one-time setup in '
                             'README.md with your original hub connected. '
                             'For setup options, run python host/setup_identity.py --help.')
        try:
            import serial  # Check USB support before opening either connection.
        except ImportError:
            raise RadioError(DEPENDENCY_HINT) from None
        identity = load_identity(PROFILE)
        port = args.port or find_flipper_port()
        with open_serial(port) as connection:
            asyncio.run(run_standalone(identity, MirrorRadioClient(connection), beep_only=args.beep_only))
        return 0
    except KeyboardInterrupt:
        print('Standalone bridge stopped and disarmed.')
        return 130
    except (RadioError, PolicyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print('Standalone connection failed and was stopped. No raw credentials or response were logged.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
