"""Install only the bundled application through Flipper's official USB storage CLI.

This module never starts an app or changes firmware, radio settings, or identity.
Transfers use the documented length-framed storage commands, including binary
readback. Firmware's rename operation is copy-and-delete: verified staging and a
verified backup protect recoverable failures, but cannot guarantee atomicity if
the device loses power during the final copy.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import struct
import time
from typing import Callable
import uuid

from start_bridge import FLIPPER_USB, checked_port_name, is_radio_interface


APP_PATH = "/ext/apps/Sub-GHz/pishock_usb_radio.fap"
ASSET_NAME = "pishock_usb_radio-official-1.4.3.fap"
SUPPORTED_API = (87, 1)
ASSETS = {
    SUPPORTED_API: ASSET_NAME,
    (88, 9): "pishock_usb_radio-api-88.9.fap",
}
CHUNK_SIZE = 1024
MAX_FILE_SIZE = 2 * 1024 * 1024
IO_TIMEOUT = 8.0


class InstallError(Exception):
    """A user-facing failure without raw USB data or device identity."""


class InstallCancelled(InstallError):
    pass


@dataclass(frozen=True)
class PortChoice:
    device: str
    label: str


@dataclass(frozen=True)
class DeviceInfo:
    firmware_version: str
    api_major: int
    api_minor: int
    target: int

    @property
    def supported(self) -> bool:
        return self.target == 7 and (self.api_major, self.api_minor) in ASSETS


@dataclass(frozen=True)
class InstallResult:
    device: DeviceInfo
    path: str
    sha256: str


def _cancelled(cancel) -> bool:
    return bool(cancel and (cancel.is_set() if hasattr(cancel, "is_set") else cancel()))


def _check_cancel(cancel) -> None:
    if _cancelled(cancel):
        raise InstallCancelled("Installation cancelled. The application was not started.")


def discover_console_ports(ports=None) -> list[PortChoice]:
    """List Flipper console ports without opening them or exposing USB serials."""
    if ports is None:
        try:
            from serial.tools import list_ports
            ports = list_ports.comports()
        except Exception:
            raise InstallError("USB devices could not be listed. Reconnect the Flipper and try again.") from None
    choices = []
    for port in ports:
        if (getattr(port, "vid", None), getattr(port, "pid", None)) != FLIPPER_USB:
            continue
        if is_radio_interface(port):
            continue
        try:
            name = checked_port_name(port)
        except Exception:
            continue
        choices.append(PortChoice(name, f"Flipper USB — {name}"))
    return sorted(choices, key=lambda item: item.device)


def parse_device_info(response: bytes) -> DeviceInfo:
    # Whitelist only compatibility information; unique IDs never leave this call.
    wanted = {"hardware_model", "hardware_target", "firmware_target", "firmware_version",
              "firmware_api_major", "firmware_api_minor"}
    fields = {}
    for line in response.decode("utf-8", errors="replace").splitlines():
        key, separator, value = line.partition(":")
        key = key.strip().replace(".", "_")
        if separator and key in wanted:
            if key in fields:
                raise InstallError("Flipper returned ambiguous firmware information. Close its app and reconnect USB.")
            fields[key] = value.strip()
    try:
        if fields.get("hardware_model") not in ("Flipper Zero", "FlipperZero"):
            raise ValueError
        major, minor = int(fields["firmware_api_major"]), int(fields["firmware_api_minor"])
        targets = {int(fields[key]) for key in ("hardware_target", "firmware_target") if key in fields}
        if len(targets) != 1:
            raise ValueError
        target = targets.pop()
        if not (0 <= major <= 65535 and 0 <= minor <= 65535 and 0 <= target <= 65535):
            raise ValueError
    except (ValueError, KeyError):
        raise InstallError("Could not identify Flipper firmware. Close the Flipper app, close qFlipper, and try again.") from None
    version = fields.get("firmware_version", "Unknown")
    # Custom version strings may contain arbitrary user text; report only versions.
    if not re.fullmatch(r"(?:v)?[0-9]+(?:\.[0-9]+){1,3}", version):
        version = "Unknown"
    return DeviceInfo(version, major, minor, target)


def _asset_bytes(asset_dir: str | Path, api: tuple[int, int] = SUPPORTED_API) -> bytes:
    try:
        data = (Path(asset_dir) / ASSETS[api]).read_bytes()
        if not 52 <= len(data) <= MAX_FILE_SIZE or data[:7] != b"\x7fELF\x01\x01\x01":
            raise ValueError
        if struct.unpack_from("<H", data, 18)[0] != 40:  # ARM
            raise ValueError
        offset = struct.unpack_from("<I", data, 32)[0]
        entry_size, count, string_index = struct.unpack_from("<HHH", data, 46)
        if entry_size != 40 or not 0 < count <= 1024 or not string_index < count:
            raise ValueError
        if offset + entry_size * count > len(data):
            raise ValueError
        string_header = offset + entry_size * string_index
        string_offset, string_size = struct.unpack_from("<II", data, string_header + 16)
        if string_offset + string_size > len(data):
            raise ValueError
        names = data[string_offset:string_offset + string_size]
        manifests = []
        for index in range(count):
            header = offset + entry_size * index
            name_index = struct.unpack_from("<I", data, header)[0]
            if name_index >= len(names):
                raise ValueError
            name = names[name_index:].split(b"\0", 1)[0]
            if name == b".fapmeta":
                start, size = struct.unpack_from("<II", data, header + 16)
                if size < 14 or start + size > len(data):
                    raise ValueError
                manifests.append(struct.unpack_from("<IIIh", data, start))
        expected_api = (api[0] << 16) | api[1]
        if manifests != [(0x52474448, 1, expected_api, 7)]:
            raise ValueError
        return data
    except (KeyError, OSError, ValueError, struct.error):
        raise InstallError("The bundled Flipper application is missing or incompatible. Reinstall the desktop application.") from None


class _Console:
    PROMPT = b">: "

    def __init__(self, port: str, serial_factory=None):
        self.buffer = bytearray()
        self.aligned = False
        try:
            if serial_factory is None:
                import serial
                serial_factory = serial.Serial
            self.serial = serial_factory(port=port, baudrate=115200, timeout=0.1, write_timeout=IO_TIMEOUT)
        except Exception:
            raise InstallError("Could not open Flipper USB. Close qFlipper and any other bridge window, then try again.") from None

    def close(self) -> None:
        self.serial.close()

    def _write(self, data: bytes) -> None:
        deadline = time.monotonic() + IO_TIMEOUT
        position = 0
        while position < len(data):
            if time.monotonic() >= deadline:
                raise InstallError("Flipper USB stopped responding during installation. Reconnect USB and try again.")
            try:
                sent = self.serial.write(data[position:])
            except Exception:
                raise InstallError("Flipper USB disconnected during installation. Reconnect USB and try again.") from None
            if type(sent) is not int or not 0 <= sent <= len(data) - position:
                raise InstallError("Flipper USB returned an invalid transfer result.")
            position += sent

    def _receive(self, deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise InstallError("Flipper USB timed out. Close the Flipper app, reconnect USB, and try again.")
        try:
            data = self.serial.read(1024)
        except Exception:
            raise InstallError("Flipper USB disconnected during installation. Reconnect USB and try again.") from None
        self.buffer.extend(data)

    def until(self, marker: bytes, limit: int = 65536) -> bytes:
        deadline = time.monotonic() + IO_TIMEOUT
        while True:
            position = self.buffer.find(marker)
            if position >= 0:
                if position > limit:
                    raise InstallError("Flipper returned an unexpected USB response.")
                result = bytes(self.buffer[:position])
                del self.buffer[:position + len(marker)]
                return result
            if len(self.buffer) > limit:
                raise InstallError("Flipper returned an unexpected USB response.")
            self._receive(deadline)

    def exact(self, count: int) -> bytes:
        deadline = time.monotonic() + IO_TIMEOUT
        while len(self.buffer) < count:
            self._receive(deadline)
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def prompt(self) -> bytes:
        result = self.until(self.PROMPT)
        self.aligned = True
        return result

    @staticmethod
    def _error(response: bytes, *, missing_ok=False, exists_ok=False) -> None:
        if b"Storage error:" not in response:
            return
        if missing_ok and b"Storage error: file/dir not exist" in response:
            return
        if exists_ok and b"Storage error: file/dir already exist" in response:
            return
        raise InstallError("Flipper could not access its SD card. Check the card and close any app using it.")

    def command(self, text: str, **error_options) -> bytes:
        if not self.aligned:
            raise InstallError("Flipper USB is out of sync. Reconnect USB before trying again.")
        self.aligned = False
        self._write(text.encode("ascii") + b"\r")
        response = self.prompt()
        self._error(response, **error_options)
        return response

    def identify(self) -> DeviceInfo:
        # A newly opened port can already contain a banner/prompt. Synchronize
        # on a key in the requested response, not a possibly stale prompt.
        self._write(b"\rdevice_info\r")
        self.until(b"hardware_model")
        return parse_device_info(b"hardware_model" + self.prompt())

    def mkdir(self, path: str) -> None:
        self.command(f'storage mkdir "{path}"', exists_ok=True)

    def require_idle(self) -> None:
        response = self.command("loader info")
        lines = [line.strip() for line in response.splitlines() if line.strip()]
        if lines != [b"loader info", b"No application is running"]:
            raise InstallError("Close the app on your Flipper and return to its main screen, then try installation again.")

    def exists(self, path: str) -> bool:
        result = self.command(f'storage stat "{path}"', missing_ok=True)
        if b"Storage error: file/dir not exist" in result:
            return False
        if not re.search(rb"(?:^|\r\n)File, size: [0-9]+b(?:\r\n|$)", result):
            raise InstallError("The application destination is not a regular file. Installation stopped.")
        return True

    def remove(self, path: str) -> None:
        self.command(f'storage remove "{path}"', missing_ok=True)

    def _begin_stream(self, command: str) -> None:
        if not self.aligned:
            raise InstallError("Flipper USB is out of sync. Reconnect USB before trying again.")
        self.aligned = False
        self._write(command.encode("ascii") + b"\r")
        # Consume the CLI's echoed command before its response or binary payload.
        echo = self.until(b"\r\n")
        if echo.strip() != command.encode("ascii"):
            raise InstallError("Flipper returned an unexpected transfer response. Reconnect USB and try again.")

    def write_chunk(self, path: str, data: bytes) -> None:
        self._begin_stream(f'storage write_chunk "{path}" {len(data)}')
        answer = self.until(b"\r\n")
        if answer != b"Ready":
            self.prompt()
            self._error(answer)
            raise InstallError("Flipper did not accept the application transfer.")
        # Finish a started binary chunk even if cancellation is requested: a CLI
        # cleanup command must never be mistaken for bytes of the application.
        self._write(data)
        self._error(self.prompt())

    def read_file(self, path: str, expected_size: int | None = None) -> bytes:
        self._begin_stream(f'storage read_chunks "{path}" {CHUNK_SIZE}')
        answer = self.until(b"\r\n")
        if b"Storage error:" in answer:
            self.prompt()
            self._error(answer)
        match = re.fullmatch(rb"Size: ([0-9]+)", answer)
        if not match:
            raise InstallError("Flipper returned an unexpected application size.")
        size = int(match.group(1))
        if not 0 <= size <= MAX_FILE_SIZE:
            raise InstallError("The application on Flipper is too large to verify safely.")
        data = bytearray()
        while len(data) < size:
            ready = self.until(b"Ready?\r\n")
            if ready.strip():
                raise InstallError("Flipper returned an unexpected application readback.")
            self._write(b"y")
            data.extend(self.exact(min(CHUNK_SIZE, size - len(data))))
        self._error(self.prompt())
        if expected_size is not None and size != expected_size:
            raise InstallError("The installed application size did not match. Installation stopped.")
        return bytes(data)


def inspect_device(port: str, *, serial_factory=None, cancel=None) -> DeviceInfo:
    _check_cancel(cancel)
    console = _Console(port, serial_factory)
    try:
        return console.identify()
    finally:
        console.close()


def install_app(port: str, asset_dir: str | Path, *, progress: Callable[[int, str], None] | None = None,
                cancel=None, serial_factory=None) -> InstallResult:
    """Verify firmware, stage and read back the app, then install without launch.

    Cancellation is honored before changing the destination. Once final copying
    starts it is completed or restored before returning, so cancellation cannot
    leave a deliberately half-written application. A broken USB connection can
    leave this install's temporary file; no other files are removed as cleanup.
    """
    report = progress or (lambda percent, message: None)
    _check_cancel(cancel)
    console = _Console(port, serial_factory)
    token = uuid.uuid4().hex
    staging = f"{APP_PATH}.{token}.upload"
    backup = f"{APP_PATH}.{token}.backup"
    staged = backup_created = commit_started = success = False
    previous: bytes | None = None
    try:
        report(0, "Checking Flipper firmware…")
        device = console.identify()
        if not device.supported:
            supported = " and ".join(f"{major}.{minor}" for major, minor in ASSETS)
            raise InstallError(
                f"This Flipper uses API {device.api_major}.{device.api_minor} on hardware f{device.target}. "
                f"Bundled application builds support API {supported} on hardware f7. "
                "Keep your existing working app, or use a build matching your firmware. "
                "No firmware was changed.")
        data = _asset_bytes(asset_dir, (device.api_major, device.api_minor))
        digest = hashlib.sha256(data).hexdigest()
        _check_cancel(cancel)
        console.require_idle()
        for folder in ("/ext/apps", "/ext/apps/Sub-GHz"):
            console.mkdir(folder)
        # Refuse a (vanishingly unlikely) collision instead of appending to an
        # unrelated file. write_chunk always appends in official firmware.
        if console.exists(staging) or console.exists(backup):
            raise InstallError("A temporary installation name already exists. Please try again.")
        if console.exists(APP_PATH):
            previous = console.read_file(APP_PATH)
            if hashlib.sha256(previous).hexdigest() == digest:
                report(100, "Application already installed and verified. Open it on the Flipper.")
                return InstallResult(device, APP_PATH, digest)
        for position in range(0, len(data), CHUNK_SIZE):
            _check_cancel(cancel)
            staged = True
            console.write_chunk(staging, data[position:position + CHUNK_SIZE])
            report(5 + int(60 * min(position + CHUNK_SIZE, len(data)) / len(data)), "Copying application…")
        _check_cancel(cancel)
        report(70, "Verifying the copied application…")
        if hashlib.sha256(console.read_file(staging, len(data))).hexdigest() != digest:
            raise InstallError("The copied application did not pass verification. Your installed app was kept.")
        _check_cancel(cancel)
        if previous is not None:
            backup_created = True
            console.command(f'storage copy "{APP_PATH}" "{backup}"')
            if console.read_file(backup, len(previous)) != previous:
                raise InstallError("Could not verify a backup of the installed app. Your installed app was kept.")
        _check_cancel(cancel)
        console.require_idle()
        report(85, "Finishing installation. Keep Flipper connected…")
        commit_started = True
        console.command(f'storage rename "{staging}" "{APP_PATH}"')
        staged = False
        if hashlib.sha256(console.read_file(APP_PATH, len(data))).hexdigest() != digest:
            raise InstallError("The installed application did not pass verification.")
        success = True
        report(100, "Application installed and verified. Open PiShock USB Radio on the Flipper.")
        return InstallResult(device, APP_PATH, digest)
    except InstallError as error:
        if commit_started:
            restored = False
            if previous is not None and backup_created and console.aligned:
                try:
                    # Official storage copy uses CREATE_NEW, so remove only
                    # our fixed destination before restoring the saved bytes.
                    console.remove(APP_PATH)
                    console.command(f'storage copy "{backup}" "{APP_PATH}"')
                    restored = console.read_file(APP_PATH, len(previous)) == previous
                except InstallError:
                    pass
            if restored:
                raise InstallError("Installation failed. The previous application was restored and verified.") from None
            if previous is not None:
                raise InstallError("Installation was interrupted during the final copy. A backup was kept on the Flipper SD card; reconnect and reinstall the app before opening it.") from None
        raise error
    finally:
        # Never send cleanup text while firmware is waiting for binary bytes.
        if console.aligned:
            for path, remove in ((staging, staged), (backup, backup_created and (success or not commit_started))):
                if remove:
                    try:
                        console.remove(path)
                    except InstallError:
                        break
        console.close()
