"""One explicit, fixed USB beep per fresh click; no cloud or remote arming."""
from __future__ import annotations

import asyncio
import re
from time import monotonic

from desktop_service import BridgeSession
from identity import Identity
from radio import HEARTBEAT_SECONDS, RadioClient, RadioError, RejectedCommand
from serial_mirror import safe_rejection_reason, stop_and_disarm


MAX_GAP_SECONDS = 0.5
BEEP_COOLDOWN_SECONDS = 1.0


class _BeepOnlyRadio(RadioClient):
    """Keep the direct-test wire protocol narrower than the normal bridge."""

    def command(self, command: str, expected: str) -> None:
        allowed = command in {"HELLO", "DISARM", "STOP", "PING", "AWAKE 0"}
        allowed |= re.fullmatch(r"SET [0-9]{1,5} [012]", command) is not None
        allowed |= re.fullmatch(r"RUN [0-9]{1,10} b 0 500", command) is not None
        if not allowed:
            raise RadioError("The direct test accepts only its fixed beep operation.")
        super().command(command, expected)


class DirectBeepSession(BridgeSession):
    """Reuse the desktop worker, serial ownership and STOP/DISARM lifecycle.

    Ready means USB setup is complete, not that the Flipper is armed. Only
    physical OK arms it; Back stops it. The GUI reserves at most one request
    under the inherited lock before waking the worker. That worker alone does
    serial I/O, discards stale requests and never replays an attempted beep.
    """

    starting_message = "Preparing the direct Flipper beep test over USB…"
    failure_message = "The direct beep test stopped. No beep was retried. Check USB and press Back on the Flipper."
    stopped_message = "Direct beep test stopped and USB released. Choose Prepare to start again."

    def __init__(self):
        super().__init__()
        self._direct_ready = False
        self._pending_beep: float | None = None
        self._send_after = float("inf")
        self._wake: asyncio.Event | None = None

    def start(self, identity: Identity, port: str) -> None:
        with self._lock:
            if not self.running:
                self._direct_ready = False
                self._pending_beep = None
                self._send_after = float("inf")
            super().start(identity, port)

    @property
    def can_beep(self) -> bool:
        with self._lock:
            return (self._active and self._direct_ready and self._pending_beep is None
                    and not self._stop_requested.is_set() and monotonic() >= self._send_after)

    def request_beep(self) -> bool:
        """Reserve one current click without doing serial I/O on the GUI thread."""
        with self._lock:
            if not self.can_beep or self._loop is None or self._wake is None:
                return False
            self._pending_beep = monotonic()
            self._direct_ready = False
            try:
                self._loop.call_soon_threadsafe(self._wake.set)
            except RuntimeError:
                self._pending_beep = None
                return False
            self._post("direct_busy", "Sending one 0.5-second beep. Wait before clicking again.")
            return True

    async def _run(self, identity: Identity, client, beep_only: bool) -> None:
        # The inherited worker owns this same handle until its context closes.
        # This restricted client exposes no cloud callbacks or remote ARM.
        client = _BeepOnlyRadio(client.port)
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._finished = asyncio.Event()
            self._wake = asyncio.Event()
            finished, wake = self._finished, self._wake
            if self._stop_requested.is_set():
                finished.set()
        try:
            for prepare in (client.hello, client.disarm,
                            lambda: client.configure(identity.shocker_id, identity.channel), client.ping):
                if finished.is_set() or self._stop_requested.is_set():
                    return
                prepare()
            last_ping = monotonic()
            if finished.is_set() or self._stop_requested.is_set():
                return
            try:
                client.set_keepalive(False)
            except RejectedCommand as error:
                # Legacy add-ons have no maintenance gate to enable.
                if safe_rejection_reason(error) != "INVALID":
                    raise
            next_ping = last_ping + HEARTBEAT_SECONDS
            self._send_after = monotonic()
            while not finished.is_set() and not self._stop_requested.is_set():
                now = monotonic()
                if now - last_ping > MAX_GAP_SECONDS:
                    raise RadioError("USB heartbeat was delayed; no request was replayed.")
                with self._lock:
                    pending = self._pending_beep
                if pending is not None and now - pending > MAX_GAP_SECONDS:
                    raise RadioError("The beep request expired; no request was replayed.")
                if pending is not None or now >= next_ping:
                    client.ping()
                    now = monotonic()
                    if now - last_ping > MAX_GAP_SECONDS:
                        raise RadioError("USB heartbeat was delayed; no request was replayed.")
                    last_ping = now
                    next_ping = now + HEARTBEAT_SECONDS
                if pending is not None:
                    with self._lock:
                        if finished.is_set() or self._stop_requested.is_set():
                            break
                        now = monotonic()
                        if now - pending > MAX_GAP_SECONDS:
                            raise RadioError("The beep request expired; no request was replayed.")
                        self._pending_beep = None
                        self._send_after = now + BEEP_COOLDOWN_SECONDS
                    try:
                        client.run("beep", 0, 500)
                    except RejectedCommand as error:
                        reason = safe_rejection_reason(error)
                        if reason in {"DISARMED", "NOT_ARMED"}:
                            self._post("status", "The Flipper is disarmed. Press OK on it, then click once to try again.")
                        elif reason == "BUSY":
                            self._post("status", "The Flipper was busy. Nothing was retried; wait, then click once.")
                        else:
                            raise
                    else:
                        self._post("status", "The Flipper accepted one 0.5-second beep over USB. Only hearing the receiver confirms delivery.")
                with self._lock:
                    if (not self._direct_ready and self._pending_beep is None
                            and monotonic() >= self._send_after and not self._stop_requested.is_set()):
                        self._direct_ready = True
                        self._post("direct_ready", "USB ready. Press OK on the Flipper to arm; each click sends one beep. Back stops it.")
                wake.clear()
                try:
                    await asyncio.wait_for(wake.wait(), timeout=max(0, min(0.05, next_ping - monotonic())))
                except asyncio.TimeoutError:
                    pass
        finally:
            with self._lock:
                self._direct_ready = False
                self._pending_beep = None
                self._wake = None
            if not stop_and_disarm(client):
                self._on_status("stop_unconfirmed")
