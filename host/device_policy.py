"""Pure, bounded admission policy for one already owned PiShock hub.

No network or hardware access occurs here. The caller must invalidate a snapshot
on disconnect/configuration change and provide fresh registration state. Message
log metadata is never treated as authentication or proof of freshness.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from types import MappingProxyType
from typing import Mapping

from radio import RadioError
from serial_mirror import HubCommand, STOP

MAX_REGISTRATION_BYTES = 65536
MAX_COMMAND_BYTES = 4096
MAX_CHANNEL_BYTES = 256
MAX_SHOCKERS = 64
MAX_SHARES = 256
MAX_CODE_BYTES = 128
MAX_JSON_DEPTH = 8
MAX_JSON_NODES = 4096
MAX_JSON_STRING = 4096


class PolicyError(RadioError):
    """A fixed diagnostic which never retains rejected data."""

    def __init__(self):
        super().__init__("Hub configuration or command failed validation.")


def _integer(value, low=0, high=0x7FFFFFFF):
    return type(value) is int and low <= value <= high


def _identity_key(identity):
    try:
        result = (identity.hub_id, identity.owner_id, identity.shocker_id,
                  identity.channel)
    except AttributeError:
        raise PolicyError() from None
    if not (_integer(result[0], 1) and _integer(result[1], 1)
            and _integer(result[2], 1, 65535) and _integer(result[3], 0, 2)):
        raise PolicyError()
    return result


def _check_tree(value):
    remaining = MAX_JSON_NODES
    pending = [(value, 0)]
    while pending:
        node, depth = pending.pop()
        remaining -= 1
        if remaining < 0 or depth > MAX_JSON_DEPTH:
            raise PolicyError()
        if type(node) is dict:
            if len(node) > MAX_JSON_NODES:
                raise PolicyError()
            for key, item in node.items():
                if type(key) is not str or len(key) > MAX_JSON_STRING:
                    raise PolicyError()
                pending.append((item, depth + 1))
        elif type(node) is list:
            if len(node) > MAX_JSON_NODES:
                raise PolicyError()
            pending.extend((item, depth + 1) for item in node)
        elif type(node) is str:
            if len(node) > MAX_JSON_STRING:
                raise PolicyError()
        elif node is not None and type(node) not in (int, bool):
            # The supported protocol needs no floating point values.
            raise PolicyError()
        elif type(node) is int and not -(1 << 63) <= node < (1 << 63):
            raise PolicyError()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise PolicyError()
        result[key] = value
    return result


def _number(value):
    if len(value) > 20:
        raise PolicyError()
    return int(value)


def _unsupported_number(_value):
    raise PolicyError()


def strict_json(payload: bytes, limit: int = MAX_REGISTRATION_BYTES):
    """Decode bounded JSON without duplicate keys or numeric coercion."""
    if type(payload) is not bytes or len(payload) > limit:
        raise PolicyError()
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_int=_number, parse_float=_unsupported_number,
                           parse_constant=_unsupported_number)
        _check_tree(value)
        return value
    except (ValueError, UnicodeError, TypeError, RecursionError, OverflowError):
        raise PolicyError() from None


@dataclass(frozen=True)
class SharePermission:
    user_id: int = field(repr=False)
    paused: bool
    max_duration_ms: int
    max_intensity: int
    can_shock: bool
    can_vibrate: bool
    can_beep: bool


@dataclass(frozen=True)
class Snapshot:
    shocker_paused: bool
    shares: Mapping[bytes, SharePermission] = field(repr=False)
    identity_key: tuple[int, int, int, int] = field(repr=False)

    def __post_init__(self):
        # Copy so mutating the source mapping cannot mutate admitted policy.
        object.__setattr__(self, "shares", MappingProxyType(dict(self.shares)))


@dataclass(frozen=True)
class Admitted:
    command: HubCommand
    repeat: bool

    @property
    def replace(self):
        return self.repeat


def _code(value):
    if type(value) is not str or not 1 <= len(value) <= MAX_CODE_BYTES:
        raise PolicyError()
    if any(not 33 <= ord(character) <= 126 for character in value):
        raise PolicyError()
    return value.encode("ascii")


def parse_registration(payload: bytes, identity) -> Snapshot:
    return snapshot_from_registration(strict_json(payload), identity)


def snapshot_from_registration(value, identity) -> Snapshot:
    """Validate an already decoded response; use strict_json at its boundary."""
    key = _identity_key(identity)
    _check_tree(value)
    if type(value) is not dict or value.get("r") is not True:
        raise PolicyError()
    if value.get("t") not in ("R", "P") or value.get("cl") is not True:
        raise PolicyError()
    if not (_integer(value.get("c"), 1) and value["c"] == key[0]
            and _integer(value.get("oid"), 1) and value["oid"] == key[1]):
        raise PolicyError()
    shockers = value.get("s")
    permissions = value.get("p")
    if type(shockers) is not list or not 1 <= len(shockers) <= MAX_SHOCKERS:
        raise PolicyError()
    if type(permissions) is not dict or len(permissions) > MAX_SHARES:
        raise PolicyError()
    selected = None
    seen_ids = set()
    for shocker in shockers:
        if (type(shocker) is not dict or not _integer(shocker.get("id"), 1, 65535)
                or not _integer(shocker.get("t"), 0, 255)
                or type(shocker.get("p")) is not bool
                or shocker["id"] in seen_ids):
            raise PolicyError()
        seen_ids.add(shocker["id"])
        if shocker["id"] == key[2]:
            if shocker["t"] != 1:
                raise PolicyError()
            selected = shocker["p"]
    if selected is None:
        raise PolicyError()
    shares = {}
    seen_codes = set()
    for entry in permissions.values():
        if type(entry) is not dict:
            raise PolicyError()
        code = _code(entry.get("c"))
        if code in seen_codes:
            raise PolicyError()
        seen_codes.add(code)
        if (not _integer(entry.get("s"), 1, 65535) or entry["s"] not in seen_ids
                or not _integer(entry.get("u"), 0)
                or not _integer(entry.get("d"), 1, 255)
                or not _integer(entry.get("i"), 0, 100)
                or any(type(entry.get(flag)) is not bool for flag in ("p", "z", "v", "b"))):
            raise PolicyError()
        if entry["s"] == key[2]:
            shares[code] = SharePermission(entry["u"], entry["p"], entry["d"] * 1000,
                                           entry["i"], entry["z"], entry["v"], entry["b"])
    return Snapshot(selected, shares, key)


def admit(channel: bytes, payload: bytes, snapshot: Snapshot, identity) -> Admitted | None:
    """Return one validated decision; ignore unsupported channels or commands.

    `repeat=False` requires caller-side busy rejection. `repeat=True` permits
    replacement, never queued replay. Caller applies the independent local cap.
    """
    try:
        key = _identity_key(identity)
        if not isinstance(snapshot, Snapshot) or snapshot.identity_key != key:
            return None
        if type(channel) is not bytes or len(channel) > MAX_CHANNEL_BYTES:
            return None
        prefix = f"c{key[0]}".encode("ascii")
        permission = None
        if channel != prefix + b"-ops":
            shared_prefix = prefix + b"-sops-"
            if not channel.startswith(shared_prefix):
                return None
            permission = snapshot.shares.get(channel[len(shared_prefix):])
            if permission is None:
                return None
        command = strict_json(payload, MAX_COMMAND_BYTES)
        if type(command) is not dict or not set(command) <= {"id", "m", "i", "d", "r", "l"}:
            return None
        if (not _integer(command.get("id"), 1, 65535) or command["id"] != key[2]
                or type(command.get("m")) is not str or command["m"] not in ("s", "v", "b", "e")
                or not _integer(command.get("i"), 0, 100)
                or type(command.get("r")) is not bool
                or ("l" in command and type(command["l"]) is not dict)):
            return None
        mode, intensity, duration = command["m"], command["i"], command.get("d")
        if mode == "e":
            return Admitted(STOP, True) if _integer(duration, 0, 0) else None
        if not _integer(duration, 100, 10000) or snapshot.shocker_paused:
            return None
        if permission is not None:
            permitted = {"s": permission.can_shock, "v": permission.can_vibrate,
                         "b": permission.can_beep}[mode]
            if permission.paused or not permitted:
                return None
            duration = min(duration, permission.max_duration_ms)
            # The vendor validator caps shock intensity. Applying the same cap
            # to vibration is deliberately stricter and preserves local limits.
            intensity = min(intensity, permission.max_intensity)
        # Beep has no variable strength in this radio protocol. The USB client
        # requires zero in that field, so preserve the requested beep mode.
        if mode == 'b':
            intensity = 0
        return Admitted(HubCommand({"s": "shock", "v": "vibrate", "b": "beep"}[mode],
                                   intensity, duration), command["r"])
    except (PolicyError, ValueError, TypeError, KeyError, RecursionError):
        return None
