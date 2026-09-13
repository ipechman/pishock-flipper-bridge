"""Bounded RESP2 transport for the documented PiShock Redis endpoint.

This module contains no device discovery, stored credentials, operation policy,
automatic reconnect, retry, JSON deserialization, or logging. Its caller chooses
fixed Redis command arguments and owns interpretation of message payloads.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
from typing import Any


REDIS_HOST = "redis.pishock.com"
REDIS_PORT = 6379
DEFAULT_TIMEOUT = 5.0
MAX_MESSAGE_BYTES = 65536
MAX_STREAM_BUFFER = 65536
MAX_LINE_BYTES = 512
MAX_BULK_BYTES = 32768
MAX_ARRAY_ITEMS = 128
MAX_DEPTH = 8
MAX_NODES = 256
MAX_CHANNEL_BYTES = 512
MAX_CREDENTIAL_BYTES = 1024
INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1
ERROR_CATEGORIES = frozenset({"authentication", "permission", "server", "protocol"})
INTEGER_PATTERN = re.compile(rb"[+-]?(?:0|[1-9][0-9]*)")
LENGTH_PATTERN = re.compile(rb"(?:0|[1-9][0-9]*|-1)")


class RespError(Exception):
    """A safe fixed error category; no server text or credential is retained."""

    _MESSAGES = {
        "authentication": "Redis authentication failed.",
        "permission": "Redis denied permission for this request.",
        "server": "Redis rejected this request.",
        "protocol": "Redis connection or protocol validation failed.",
    }

    def __init__(self, category: str = "protocol"):
        self.category = category if category in ERROR_CATEGORIES else "protocol"
        super().__init__(self._MESSAGES[self.category])


@dataclass(frozen=True)
class SubscriptionAck:
    kind: str
    channel: bytes | None
    count: int


@dataclass(frozen=True)
class PubSubMessage:
    kind: str
    channel: bytes
    payload: bytes
    pattern: bytes | None = None


@dataclass
class _Budget:
    remaining: int = MAX_MESSAGE_BYTES
    nodes: int = 0

    def take(self, count: int) -> None:
        if count < 0 or count > self.remaining:
            raise RespError()
        self.remaining -= count


def _argument_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        result = value
    elif isinstance(value, (bytearray, memoryview)):
        if (value.nbytes if isinstance(value, memoryview) else len(value)) > MAX_BULK_BYTES:
            raise RespError()
        result = bytes(value)
    elif isinstance(value, str):
        if len(value) > MAX_BULK_BYTES:
            raise RespError()
        try:
            result = value.encode("utf-8")
        except UnicodeError:
            raise RespError() from None
    elif type(value) is int and INT64_MIN <= value <= INT64_MAX:
        result = str(value).encode("ascii")
    else:
        raise RespError()
    if len(result) > MAX_BULK_BYTES:
        raise RespError()
    return result


def encode_command(*arguments: bytes | str | int) -> bytes:
    """Encode a nonempty flat command with binary-safe bulk-string arguments."""
    if not 1 <= len(arguments) <= MAX_ARRAY_ITEMS:
        raise RespError()
    encoded = bytearray(f"*{len(arguments)}\r\n".encode("ascii"))
    for argument in arguments:
        data = _argument_bytes(argument)
        header = f"${len(data)}\r\n".encode("ascii")
        if len(encoded) + len(header) + len(data) + 2 > MAX_MESSAGE_BYTES:
            raise RespError()
        encoded.extend(header)
        encoded.extend(data)
        encoded.extend(b"\r\n")
    return bytes(encoded)


async def _take(reader: asyncio.StreamReader, count: int, budget: _Budget) -> bytes:
    budget.take(count)
    return await reader.readexactly(count)


async def _line(reader: asyncio.StreamReader, budget: _Budget) -> bytes:
    # A caller-supplied StreamReader may have a larger configured line limit.
    # Read small headers explicitly so even that reader cannot grow our buffer.
    line = bytearray()
    while True:
        byte = await _take(reader, 1, budget)
        if byte == b"\r":
            if await _take(reader, 1, budget) != b"\n":
                raise RespError()
            return bytes(line)
        if byte == b"\n" or len(line) >= MAX_LINE_BYTES:
            raise RespError()
        line.extend(byte)


def _number(raw: bytes, *, length: bool = False) -> int:
    if len(raw) > 20 or not (LENGTH_PATTERN if length else INTEGER_PATTERN).fullmatch(raw):
        raise RespError()
    value = int(raw)
    if not INT64_MIN <= value <= INT64_MAX:
        raise RespError()
    return value


def _server_error(line: bytes) -> RespError:
    first_word = line.partition(b" ")[0].upper()
    if first_word in (b"NOAUTH", b"WRONGPASS", b"AUTH"):
        return RespError("authentication")
    if first_word == b"NOPERM":
        return RespError("permission")
    return RespError("server")


async def _parse(reader: asyncio.StreamReader, budget: _Budget, depth: int = 0,
                 first_byte: bytes | None = None):
    if depth > MAX_DEPTH:
        raise RespError()
    budget.nodes += 1
    if budget.nodes > MAX_NODES:
        raise RespError()
    if first_byte is None:
        prefix = await _take(reader, 1, budget)
    else:
        budget.take(1)
        prefix = first_byte
    if prefix not in (b"+", b"-", b":", b"$", b"*"):
        raise RespError()
    line = await _line(reader, budget)
    if prefix == b"+":
        return line
    if prefix == b"-":
        raise _server_error(line)
    if prefix == b":":
        return _number(line)
    length = _number(line, length=True)
    if length == -1:
        return None
    if prefix == b"$":
        if length > MAX_BULK_BYTES or length + 2 > budget.remaining:
            raise RespError()
        data = await _take(reader, length, budget)
        if await _take(reader, 2, budget) != b"\r\n":
            raise RespError()
        return data
    if length > MAX_ARRAY_ITEMS or budget.nodes + length > MAX_NODES:
        raise RespError()
    return [await _parse(reader, budget, depth + 1) for _ in range(length)]


def _checked_timeout(timeout: float) -> float:
    if type(timeout) not in (int, float) or not 0 < timeout <= 60:
        raise RespError()
    return float(timeout)


async def read_response(reader: asyncio.StreamReader, timeout: float = DEFAULT_TIMEOUT):
    """Read one bounded response, returning bytes, int, list, or None.

    Server errors, malformed frames, truncation, and timeouts raise only a safe
    RespError. After any error the caller must discard this stream: some bytes
    may remain, and this function never attempts to resynchronize or retry.
    """
    timeout = _checked_timeout(timeout)
    try:
        return await asyncio.wait_for(_parse(reader, _Budget()), timeout)
    except RespError:
        raise
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError,
            OSError, ValueError, OverflowError):
        raise RespError() from None


def _channel(value: Any, *, allow_none: bool = False) -> bytes | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, bytes) or not 1 <= len(value) <= MAX_CHANNEL_BYTES:
        raise RespError()
    return value


def parse_pubsub(response: Any) -> SubscriptionAck | PubSubMessage:
    """Validate only known RESP2 pub/sub envelopes; leave payload bytes opaque."""
    if not isinstance(response, list) or not response or not isinstance(response[0], bytes):
        raise RespError()
    kind = response[0]
    if kind in (b"subscribe", b"psubscribe", b"unsubscribe", b"punsubscribe"):
        if len(response) != 3 or type(response[2]) is not int or not 0 <= response[2] <= MAX_ARRAY_ITEMS:
            raise RespError()
        channel = _channel(response[1], allow_none=kind in (b"unsubscribe", b"punsubscribe"))
        if kind in (b"subscribe", b"psubscribe") and response[2] == 0:
            raise RespError()
        return SubscriptionAck(kind.decode("ascii"), channel, response[2])
    if kind == b"message" and len(response) == 3:
        channel, payload = _channel(response[1]), response[2]
        pattern = None
    elif kind == b"pmessage" and len(response) == 4:
        pattern, channel, payload = _channel(response[1]), _channel(response[2]), response[3]
    else:
        raise RespError()
    if not isinstance(payload, bytes) or len(payload) > MAX_BULK_BYTES:
        raise RespError()
    return PubSubMessage(kind.decode("ascii"), channel, payload, pattern)


class RedisConnection:
    """One connection with serialized I/O; every error closes it, with no retry."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 timeout: float = DEFAULT_TIMEOUT):
        self.reader = reader
        self.writer = writer
        self.timeout = _checked_timeout(timeout)
        self.closed = False
        self.authenticated = False
        self._auth_attempted = False
        self._lock = asyncio.Lock()

    @classmethod
    async def connect(cls, timeout: float = DEFAULT_TIMEOUT) -> RedisConnection:
        timeout = _checked_timeout(timeout)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(REDIS_HOST, REDIS_PORT, limit=MAX_STREAM_BUFFER), timeout)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            raise RespError() from None
        return cls(reader, writer, timeout)

    async def __aenter__(self) -> RedisConnection:
        if self.closed:
            raise RespError()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.authenticated = False
        try:
            self.writer.close()
            await asyncio.wait_for(self.writer.wait_closed(), 1.0)
        except Exception:
            # Closing cannot replace an already sanitized protocol error with
            # an arbitrary transport exception. Cancellation still propagates.
            pass

    async def _exchange(self, data: bytes | None):
        if data is not None:
            self.writer.write(data)
            await self.writer.drain()
        return await _parse(self.reader, _Budget())

    async def _request(self, data: bytes | None, *, idle: bool = False):
        async with self._lock:
            if self.closed:
                raise RespError()
            try:
                if idle and data is None:
                    first_byte = await self.reader.readexactly(1)
                    return await asyncio.wait_for(
                        _parse(self.reader, _Budget(), first_byte=first_byte), self.timeout)
                return await asyncio.wait_for(self._exchange(data), self.timeout)
            except asyncio.CancelledError:
                await self.aclose()
                raise
            except RespError:
                await self.aclose()
                raise
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError,
                    OSError, ValueError, OverflowError):
                await self.aclose()
                raise RespError() from None

    async def execute(self, *arguments: bytes | str | int):
        """Send exactly once and read one response; caller supplies fixed commands."""
        data = encode_command(*arguments)
        return await self._request(data)

    async def authenticate(self, credential: bytes | str) -> None:
        """Attempt AUTH once, using the injected value as both username/password."""
        if self._auth_attempted:
            raise RespError("authentication")
        self._auth_attempted = True
        try:
            data = _argument_bytes(credential)
        except RespError:
            await self.aclose()
            raise RespError("authentication") from None
        if not data or len(data) > MAX_CREDENTIAL_BYTES:
            await self.aclose()
            raise RespError("authentication")
        try:
            response = await self.execute(b"AUTH", data, data)
        except RespError as error:
            if error.category == "server":
                raise RespError("authentication") from None
            raise
        if response != b"OK":
            await self.aclose()
            raise RespError("authentication")
        self.authenticated = True

    async def read_response(self, *, idle: bool = False):
        """With idle=True, start the frame deadline only after its first byte."""
        return await self._request(None, idle=idle)

    async def read_pubsub(self, *, idle: bool = False) -> SubscriptionAck | PubSubMessage:
        try:
            return parse_pubsub(await self.read_response(idle=idle))
        except RespError:
            await self.aclose()
            raise
