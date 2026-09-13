"""Import only the connected owner's device identity; protect it with Windows DPAPI."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field, asdict
import ipaddress
import json
from pathlib import Path
import re
import sys

from radio import RadioError, hub_lines, open_serial, validate_target


@dataclass(frozen=True)
class Identity:
    mac: str = field(repr=False)
    hub_id: int
    shocker_id: int
    channel: int
    owner_id: int
    public_ip: str = field(repr=False)

    def validate(self):
        validate_target(self.shocker_id, self.channel)
        if type(self.hub_id) is not int or not 1 <= self.hub_id <= 0x7FFFFFFF:
            raise RadioError('Invalid saved hub identity.')
        if type(self.owner_id) is not int or not 1 <= self.owner_id <= 0x7FFFFFFF:
            raise RadioError('Original hub did not report a valid owner.')
        if not isinstance(self.mac, str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', self.mac):
            raise RadioError('Original hub did not report a valid device identity.')
        try:
            ipaddress.IPv4Address(self.public_ip)
        except (ValueError, TypeError):
            raise RadioError('Original hub did not report a valid public address.') from None
        return self


def identity_from_info(value, expected_hub: int, expected_shocker: int):
    if not isinstance(value, dict) or value.get('clientId') != expected_hub:
        raise RadioError('The connected hub does not match the selected hub.')
    if value.get('claimed') is not True or value.get('type') not in (3, 4):
        raise RadioError('Connect the already claimed PiShock Next or Lite hub.')
    shockers = value.get('shockers')
    if not isinstance(shockers, list) or not any(
            isinstance(item, dict) and item.get('id') == expected_shocker
            and type(item.get('id')) is int and item.get('type') == 1
            for item in shockers):
        raise RadioError('The selected SmallOne is not registered on this hub.')
    return Identity(value.get('macAddress'), expected_hub, expected_shocker, 0,
                    value.get('ownerId'), value.get('publicIp')).validate()


def import_connected_identity(port_name: str, expected_hub: int, expected_shocker: int):
    with open_serial(port_name, original_hub=True) as port:
        port.reset_input_buffer()
        request = b'{"cmd":"info"}\n'
        if port.write(request) != len(request):
            raise RadioError('Incomplete hub info request; no retry was made.')
        for line in hub_lines(port, 5):
            if line is None or not line.startswith(b'TERMINALINFO:'):
                continue
            try:
                info = json.loads(line[len(b'TERMINALINFO:'):])
            except (ValueError, UnicodeDecodeError, RecursionError):
                raise RadioError('Hub info was malformed; raw response discarded.') from None
            # Only identity fields survive this function. Wi-Fi passwords and
            # pairing keys from the same response are never stored or returned.
            identity = identity_from_info(info, expected_hub, expected_shocker)
            del info, line
            return identity
    raise RadioError('Original hub did not return its identity.')


class _Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _protect(data: bytes, decrypt: bool = False):
    if sys.platform != 'win32':
        raise RadioError('This saved-device profile requires Windows.')
    if len(data) > 65536:
        raise RadioError('Saved device profile exceeded its size limit.')
    library = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = _Blob(len(data), buffer)
    result = _Blob()
    name = 'CryptUnprotectData' if decrypt else 'CryptProtectData'
    function = getattr(library, name)
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN; per-user protection, not machine-wide.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise RadioError('Windows could not unlock the device profile.' if decrypt
                         else 'Windows could not protect the device profile.')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        if result.data:
            ctypes.memset(result.data, 0, result.size)
            kernel.LocalFree(result.data)
        ctypes.memset(buffer, 0, len(data))


def save_identity(path: Path, identity: Identity):
    identity.validate()
    protected = _protect(json.dumps(asdict(identity)).encode('utf-8'))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as output:
        output.write(protected)


def load_identity(path: Path):
    try:
        with path.open('rb') as source:
            protected = source.read(65537)
        data = json.loads(_protect(protected, decrypt=True))
        return Identity(**data).validate()
    except (ValueError, TypeError, OSError, RecursionError):
        raise RadioError('The saved device profile is unavailable or invalid.') from None
