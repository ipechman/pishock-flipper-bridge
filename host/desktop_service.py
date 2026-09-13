"""UI-independent desktop setup and bridge lifecycle.

Discovery, profile operations and ``join`` belong on a worker thread. ``start``,
``request_stop``, ``running`` and ``drain_events`` are safe on the GUI thread.
Only fixed messages and selected numeric device IDs cross the GUI boundary;
USB serial numbers, raw hub responses and backend exceptions never do.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path
import re
import tempfile
import threading

from identity import Identity, import_connected_identity, load_identity, save_identity
from radio import open_serial, read_hub_info
from serial_mirror import MirrorRadioClient, stop_and_disarm
from standalone import run_standalone
from start_bridge import FLIPPER_USB, HUB_USB, checked_port_name, find_ports, is_radio_interface


class DesktopError(Exception):
    """A fixed, displayable desktop error without raw system or device data."""


@dataclass(frozen=True)
class PortChoice:
    device: str
    label: str


@dataclass(frozen=True)
class PortInventory:
    hubs: tuple[PortChoice, ...]
    flippers: tuple[PortChoice, ...]


@dataclass(frozen=True)
class ShockerChoice:
    shocker_id: int
    label: str


@dataclass(frozen=True)
class HubDiscovery:
    port: str
    hub_id: int
    shockers: tuple[ShockerChoice, ...]


@dataclass(frozen=True)
class BridgeEvent:
    # starting, ready, revalidating, status, warning, error, stopping, stopped
    kind: str
    message: str


def _port_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"COM[1-9][0-9]{0,5}", value, re.IGNORECASE):
        raise DesktopError("Choose a detected USB port, then try again.")
    return value.upper()


def enumerate_ports() -> PortInventory:
    """Return only recognized COM names and generic labels, never USB metadata."""
    try:
        hubs, flippers = {}, {}
        for item in find_ports():
            usb = (getattr(item, "vid", None), getattr(item, "pid", None))
            if usb != HUB_USB and not (usb == FLIPPER_USB and is_radio_interface(item)):
                continue
            device = checked_port_name(item)
            if usb == HUB_USB:
                hubs[device] = PortChoice(device, f"Possible PiShock hub ({device})")
            else:
                flippers[device] = PortChoice(device, f"Flipper radio app ({device})")
        order = lambda entry: int(entry.device[3:])
        return PortInventory(tuple(sorted(hubs.values(), key=order)),
                             tuple(sorted(flippers.values(), key=order)))
    except Exception:
        raise DesktopError("USB devices could not be listed. Reconnect the USB cable and try Refresh.") from None


def _require_port(name: str, *, hub: bool) -> str:
    name = _port_name(name)
    inventory = enumerate_ports()
    choices = inventory.hubs if hub else inventory.flippers
    if name not in {choice.device for choice in choices}:
        raise DesktopError("The selected hub is no longer connected. Reconnect it and refresh."
                           if hub else "The Flipper radio app is not connected. Open it on the Flipper and refresh.")
    return name


def discover_hub(port: str) -> HubDiscovery:
    """Send only the read-only hub info request and return supported selections.

    Claim status and the complete selected identity are verified on import;
    discovery deliberately receives only read_hub_info's whitelisted fields.
    """
    port = _require_port(port, hub=True)
    try:
        with open_serial(port, original_hub=True) as connection:
            connection.reset_input_buffer()
            info = read_hub_info(connection)
        hub_id = info.get("clientId")
        if type(hub_id) is not int or not 1 <= hub_id <= 0x7fffffff or info.get("type") not in (3, 4):
            raise DesktopError("Connect a PiShock Next or Lite hub that is already set up in your PiShock account.")
        raw_shockers = info.get("shockers")
        if not isinstance(raw_shockers, list):
            raise DesktopError("The hub did not report registered shockers. Check pairing on the PiShock website.")
        selections = {}
        for entry in raw_shockers:
            if not isinstance(entry, dict) or type(entry.get("type")) is not int or entry["type"] != 1:
                continue
            target = entry.get("id")
            if type(target) is not int or not 1 <= target <= 65535 or target in selections:
                raise DesktopError("The hub reported an ambiguous or unsupported shocker list. Check pairing and try again.")
            selections[target] = ShockerChoice(target, f"SmallOne shocker {target}")
        if not selections:
            raise DesktopError("No registered SmallOne shocker was found. Check pairing on the PiShock website.")
        return HubDiscovery(port, hub_id, tuple(selections[key] for key in sorted(selections)))
    except DesktopError:
        raise
    except Exception:
        raise DesktopError("The hub could not be read. Close other programs using its USB connection, then try again.") from None


def load_profile(path: Path) -> Identity:
    """Load a user-selected encrypted profile without returning raw errors."""
    try:
        return load_identity(Path(path))
    except Exception:
        raise DesktopError("This device profile could not be opened. Use setup, or select a profile saved by this Windows account.") from None


def _save_verified_profile(path: Path, value: Identity, *, replace_existing: bool) -> Identity:
    """Verify DPAPI round-trip before atomic publication in the same directory.

    A new import uses an atomic no-clobber hard link; an explicit replacement
    uses os.replace. A failed write or verification preserves the old profile.
    Only encrypted bytes are ever written to disk.
    """
    path = Path(path)
    try:
        if type(replace_existing) is not bool:
            raise DesktopError("Choose whether to replace the saved device before importing.")
        value.validate()
        if path.exists() and not replace_existing:
            raise DesktopError("A device is already saved. Choose Replace saved device to change it.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="profile-import-", dir=path.parent) as directory:
            temporary = Path(directory) / "device.dpapi"
            save_identity(temporary, value)
            if load_identity(temporary) != value:
                raise DesktopError("The encrypted profile could not be verified. The existing profile was kept.")
            with temporary.open("r+b") as source:
                os.fsync(source.fileno())
            if replace_existing:
                os.replace(temporary, path)
            else:
                os.link(temporary, path)
        return value
    except DesktopError:
        raise
    except FileExistsError:
        raise DesktopError("A device is already saved. Choose Replace saved device to change it.") from None
    except Exception:
        raise DesktopError("The device profile could not be saved and verified. The existing profile was kept.") from None


def import_profile(discovery: HubDiscovery, shocker_id: int, path: Path,
                   *, replace_existing: bool = False) -> Identity:
    """Re-read the selected physical hub and save only its verified identity."""
    if (not isinstance(discovery, HubDiscovery) or type(discovery.hub_id) is not int
            or not 1 <= discovery.hub_id <= 0x7fffffff or type(shocker_id) is not int
            or not isinstance(discovery.shockers, tuple)
            or not all(isinstance(item, ShockerChoice) and type(item.shocker_id) is int
                       and 1 <= item.shocker_id <= 65535 for item in discovery.shockers)
            or shocker_id not in {item.shocker_id for item in discovery.shockers}):
        raise DesktopError("Choose one of the shockers found on the connected hub.")
    port = _require_port(discovery.port, hub=True)
    try:
        value = import_connected_identity(port, discovery.hub_id, shocker_id)
    except Exception:
        raise DesktopError("The selected hub and shocker could not be verified. Read the hub again and check its PiShock account setup.") from None
    return _save_verified_profile(path, value, replace_existing=replace_existing)


def import_saved_profile(source: Path, destination: Path, *, replace_existing: bool = False) -> Identity:
    """Explicitly migrate a chosen CLI profile; never search for or auto-load one."""
    value = load_profile(Path(source))
    return _save_verified_profile(Path(destination), value, replace_existing=replace_existing)


class BridgeSession:
    """Own one background event loop and one serial connection at a time.

    Stopping sets run_standalone's finish event on its own loop. The controller
    remains running until STOP/DISARM, network teardown and serial close finish.
    It never arms, retries an operation, or reconnects by itself.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._active = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._finished: asyncio.Event | None = None
        self._stop_requested = threading.Event()
        self._events: deque[BridgeEvent] = deque(maxlen=128)

    @property
    def running(self) -> bool:
        with self._lock:
            return self._active

    def drain_events(self) -> tuple[BridgeEvent, ...]:
        with self._lock:
            result = tuple(self._events)
            self._events.clear()
            return result

    def _post(self, kind: str, message: str) -> None:
        with self._lock:
            if self._stop_requested.is_set() and kind in {"starting", "ready", "revalidating", "status"}:
                return
            self._events.append(BridgeEvent(kind, message))

    def start(self, identity: Identity, port: str, *, beep_only: bool = False) -> None:
        """Schedule a connection; returns before USB enumeration or access."""
        try:
            if not isinstance(identity, Identity) or type(beep_only) is not bool:
                raise ValueError()
            identity.validate()
            port = _port_name(port)
        except Exception:
            raise DesktopError("Choose a valid saved device and detected Flipper port before connecting.") from None
        with self._lock:
            if self.running:
                raise DesktopError("The bridge is still running or stopping. Wait for it to stop before reconnecting.")
            self._stop_requested.clear()
            self._events.clear()
            self._active = True
            self._events.append(BridgeEvent("starting", "Connecting to the Flipper and PiShock…"))
            try:
                self._thread = threading.Thread(target=self._worker, args=(identity, port, beep_only),
                                                name="PiShock bridge", daemon=False)
                self._thread.start()
            except Exception:
                self._thread = None
                self._active = False
                self._events.clear()
                raise DesktopError("The connection could not be started. Close and reopen the application.") from None

    def request_stop(self) -> None:
        """Idempotent, nonblocking stop; safe even before the event loop exists."""
        with self._lock:
            if not self.running or self._stop_requested.is_set():
                return
            self._stop_requested.set()
            self._events.append(BridgeEvent("stopping", "Stopping and disarming the Flipper…"))
            if self._loop is not None and self._finished is not None:
                try:
                    self._loop.call_soon_threadsafe(self._finished.set)
                except RuntimeError:
                    # A loop can finish between the request and scheduling.
                    # Its run_standalone finally already performed cleanup.
                    pass

    def join(self, timeout: float | None = None) -> bool:
        """Wait for complete teardown on a test/worker thread, never Tk's thread."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return not self.running

    def _emit(self, message, **_kwargs) -> None:
        # Translate only known internal messages. Never display unknown strings,
        # exception reprs, device response fields, or operation parameters.
        if not isinstance(message, str):
            return
        if message == "Non-beep command ignored during this test.":
            self._post("status", "Beep-only mode ignored a non-beep command.")
        elif re.fullmatch(r"\[[0-9]+\.[0-9]{3}s\] Stop forwarded\.", message):
            self._post("status", "Stop sent to the Flipper.")
        elif re.fullmatch(r"\[[0-9]+\.[0-9]{3}s\] Forwarded (?:beep|vibrate|shock): [0-9]+%, [0-9]+ ms\.", message):
            self._post("status", "A fresh website command was sent to the Flipper.")
        else:
            rejected = re.fullmatch(r"Command discarded: (DISARMED|LIMIT|BUSY)\. It was not retried\.", message)
            if rejected:
                explanations = {
                    "DISARMED": "Command ignored: the Flipper is disarmed. Press OK on the Flipper to arm it.",
                    "LIMIT": "Command ignored: it exceeds the Flipper's local limit.",
                    "BUSY": "Command ignored: the Flipper is already running an operation.",
                }
                self._post("status", explanations[rejected.group(1)])

    def _on_status(self, category: str) -> None:
        mapping = {
            "connecting": ("starting", "Connecting to PiShock…"),
            "revalidating": ("revalidating", "Checking updated PiShock settings. Output is being stopped."),
            "ready": ("ready", "Connected. Press OK on the Flipper when you are ready to arm it."),
            "stopping": ("stopping", "Stopping and disarming the Flipper…"),
            "stop_unconfirmed": ("warning", "Stop acknowledgment unavailable. Press Back on the Flipper."),
        }
        if category in mapping:
            self._post(*mapping[category])
        # Only _worker emits 'stopped', after the serial handle is closed.

    async def _run(self, identity: Identity, client, beep_only: bool) -> None:
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._finished = asyncio.Event()
            if self._stop_requested.is_set():
                self._finished.set()
            finished = self._finished
        if finished.is_set():
            if not stop_and_disarm(client):
                self._on_status("stop_unconfirmed")
            return
        await run_standalone(identity, client, beep_only=beep_only,
                             finished=finished, emit=self._emit, on_status=self._on_status)

    def _worker(self, identity: Identity, port: str, beep_only: bool) -> None:
        try:
            if self._stop_requested.is_set():
                return
            port = _require_port(port, hub=False)
            if self._stop_requested.is_set():
                return
            with open_serial(port) as connection:
                asyncio.run(self._run(identity, MirrorRadioClient(connection), beep_only))
        except DesktopError as error:
            self._post("error", str(error))
        except Exception:
            self._post("error", "The connection ended. Check the USB cable and internet, then reconnect. Press Back on the Flipper.")
        finally:
            with self._lock:
                self._loop = None
                self._finished = None
                self._active = False
                self._events.append(BridgeEvent("stopped", "Disconnected. The bridge will stay stopped until you choose Connect."))
