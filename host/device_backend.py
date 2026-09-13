"""Own-device PiShock backend actor. No serial, RF, or operation publishing.

The caller supplies an already verified identity and synchronous callbacks. Raw
registration and operation data go only to those callbacks, never to logs.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from device_transport import RedisConnection, RespError, PubSubMessage, SubscriptionAck, parse_pubsub
from device_policy import PolicyError, strict_json


REGISTRATION_HOST = "ps.pishock.com"
REGISTRATION_PATH = "/pishock/register"
MAX_REGISTRATION_BYTES = 65536
HTTP_TIMEOUT = 5.0


class BackendError(Exception):
    def __init__(self, category="protocol"):
        self.category = category if category in {"authentication", "permission", "server", "protocol"} else "protocol"
        super().__init__("Device backend stopped: " + self.category + ".")


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, newurl):
        raise BackendError("permission")


def _identity_fields(identity):
    mac = getattr(identity, "mac", None)
    hub_id = getattr(identity, "hub_id", None)
    public_ip = getattr(identity, "public_ip", None)
    if not isinstance(mac, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac):
        raise BackendError()
    if type(hub_id) is not int or not 1 <= hub_id <= 0x7fffffff:
        raise BackendError()
    try:
        public_ip = str(ipaddress.IPv4Address(public_ip))
    except (ValueError, TypeError, ipaddress.AddressValueError):
        raise BackendError() from None
    return mac.lower().replace(":", "_"), hub_id, public_ip


def _fetch_registration(credential: str, public_ip: str):
    query = urllib.parse.urlencode({"mac": credential, "ip": public_ip})
    url = "https://" + REGISTRATION_HOST + REGISTRATION_PATH + "?" + query
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirects())
    request = urllib.request.Request(url, headers={"Accept": "application/json", "Accept-Encoding": "identity"})
    try:
        with opener.open(request, timeout=HTTP_TIMEOUT) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if (final.scheme, final.hostname, final.port, final.path) != (
                    "https", REGISTRATION_HOST, None, REGISTRATION_PATH):
                raise BackendError("permission")
            if response.status != 200:
                raise BackendError("server")
            declared = response.headers.get("Content-Length")
            if declared is not None and (not declared.isdecimal() or int(declared) > MAX_REGISTRATION_BYTES):
                raise BackendError()
            body = response.read(MAX_REGISTRATION_BYTES + 1)
            if len(body) > MAX_REGISTRATION_BYTES:
                raise BackendError()
            result = strict_json(body, limit=MAX_REGISTRATION_BYTES)
            if not isinstance(result, dict):
                raise BackendError()
            return result
    except BackendError:
        raise
    except urllib.error.HTTPError as error:
        raise BackendError("permission" if error.code in (401, 403) else "server") from None
    except (PolicyError, urllib.error.URLError, OSError, ValueError, UnicodeError, RecursionError):
        raise BackendError() from None


async def fetch_registration(credential: str, public_ip: str):
    """Fixed verified HTTPS origin; bounded body; never follow any redirect."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(_fetch_registration, credential, public_ip), HTTP_TIMEOUT + 1)
    except asyncio.TimeoutError:
        raise BackendError() from None


class _Backend:
    def __init__(self, identity, on_snapshot, on_message, on_invalidated, on_ready, on_failure,
                 finished, connection_factory, fetch_snapshot, heartbeat_seconds, refresh_seconds,
                 lease_seconds, monotonic, wall_clock):
        self.credential, self.hub_id, self.public_ip = _identity_fields(identity)
        self.hub_prefix = f"c{self.hub_id}-".encode("ascii")
        self.mac_prefix = (self.credential + "-").encode("ascii")
        self.patterns = (self.mac_prefix + b"*", self.hub_prefix + b"*")
        self.on_snapshot = on_snapshot
        self.on_message = on_message
        self.on_invalidated = on_invalidated
        self.on_ready = on_ready
        self.on_failure = on_failure
        self.finished = finished if finished is not None else asyncio.Event()
        self.connection_factory = connection_factory or RedisConnection.connect
        self.fetch_snapshot = fetch_snapshot or fetch_registration
        for interval in (heartbeat_seconds, refresh_seconds, lease_seconds):
            if type(interval) not in (int, float) or not 0 < interval <= 3600:
                raise BackendError()
        self.heartbeat_seconds = heartbeat_seconds
        self.refresh_seconds = refresh_seconds
        self.lease_seconds = lease_seconds
        self.monotonic = monotonic
        self.wall_clock = wall_clock
        self.subscriber = self.publisher = None
        self.ready = False
        self.generation = 0
        self.last_pingall = monotonic()
        self.refresh_requested = asyncio.Event()
        self.heartbeat_requested = asyncio.Event()

    def invalidate(self, *, control: bool) -> None:
        self.ready = False
        self.generation += 1
        if control:
            self.on_invalidated()
        self.refresh_requested.set()

    def process_message(self, event: PubSubMessage) -> None:
        channel = event.channel
        if event.kind == "pmessage" and event.pattern not in self.patterns:
            raise BackendError()
        if channel == b"pingall":
            self.last_pingall = self.monotonic()
            return
        if not (channel.startswith(self.hub_prefix) or channel.startswith(self.mac_prefix)):
            raise BackendError("permission")
        if channel in (self.hub_prefix + b"ping", self.hub_prefix + b"alive"):
            self.heartbeat_requested.set()
            return
        suffix = channel[len(self.hub_prefix):] if channel.startswith(self.hub_prefix) else None
        is_operation = suffix == b"ops" or (suffix is not None and suffix.startswith(b"sops-") and len(suffix) > 5)
        if is_operation:
            if self.ready:
                self.on_message(channel, event.payload, self.monotonic())
            return
        # Revocations, pause, pairing/ownership changes and every unknown own
        # control topic close the gate before any HTTP request is awaited.
        self.invalidate(control=True)

    async def subscribe(self, kind: str, target: bytes) -> None:
        reply = await self.subscriber.execute(kind, target)
        deadline = self.monotonic() + 5.0
        while True:
            event = parse_pubsub(reply)
            if isinstance(event, SubscriptionAck):
                if event.kind != kind.lower() or event.channel != target:
                    raise BackendError()
                return
            self.process_message(event)  # ready=False: startup operations drop.
            if self.monotonic() >= deadline:
                raise BackendError()
            reply = await self.subscriber.read_response()

    async def read_loop(self) -> None:
        while True:
            event = await self.subscriber.read_pubsub(idle=True)
            if not isinstance(event, PubSubMessage):
                raise BackendError()
            self.process_message(event)

    async def refresh_loop(self) -> None:
        while True:
            await self.refresh_requested.wait()
            self.refresh_requested.clear()
            generation = self.generation
            snapshot = await self.fetch_snapshot(self.credential, self.public_ip)
            if generation != self.generation:
                continue
            self.on_snapshot(snapshot)
            # Callbacks are synchronous on this loop; validation must succeed
            # before any operation can pass the gate.
            self.ready = True
            self.on_ready()

    async def periodic_refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self.refresh_seconds)
            # The last validated policy remains active during routine refresh,
            # including STOP delivery. Actual control events invalidate it.
            self.refresh_requested.set()

    async def heartbeat_loop(self) -> None:
        while True:
            self.heartbeat_requested.clear()
            timestamp = int(self.wall_clock())
            if not 0 <= timestamp <= 0x7fffffffffffffff:
                raise BackendError()
            result = await self.publisher.execute("HSET", "lite:status", str(self.hub_id), str(timestamp))
            if type(result) is not int or result not in (0, 1):
                raise BackendError()
            result = await self.publisher.execute("PUBLISH", f"{self.hub_id}-ping", str(timestamp))
            if type(result) is not int or result < 0:
                raise BackendError()
            try:
                await asyncio.wait_for(self.heartbeat_requested.wait(), self.heartbeat_seconds)
            except asyncio.TimeoutError:
                pass

    async def lease_loop(self) -> None:
        while True:
            if self.monotonic() - self.last_pingall >= self.lease_seconds:
                raise BackendError()
            await asyncio.sleep(min(1.0, self.lease_seconds / 4))

    async def run(self) -> None:
        tasks = []
        failure = None
        try:
            if self.finished.is_set():
                return
            self.on_invalidated()
            self.subscriber = await self.connection_factory()
            await self.subscriber.authenticate(self.credential)
            for pattern in self.patterns:
                await self.subscribe("PSUBSCRIBE", pattern)
            await self.subscribe("SUBSCRIBE", b"pingall")
            self.last_pingall = self.monotonic()
            self.publisher = await self.connection_factory()
            await self.publisher.authenticate(self.credential)
            # All own channels are subscribed before the first registration GET.
            self.invalidate(control=False)
            tasks = [asyncio.create_task(coroutine) for coroutine in (
                self.read_loop(), self.refresh_loop(), self.periodic_refresh_loop(),
                self.heartbeat_loop(), self.lease_loop(), self.finished.wait())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
            if not self.finished.is_set():
                raise BackendError()
        except asyncio.CancelledError:
            raise
        except (BackendError, RespError) as error:
            failure = error.category
        except Exception:
            failure = "protocol"
        finally:
            self.ready = False
            try:
                self.on_invalidated()
            except Exception:
                failure = "protocol"
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for connection in (self.subscriber, self.publisher):
                if connection is not None:
                    try:
                        await connection.aclose()
                    except Exception:
                        failure = "protocol"
            if failure is not None:
                self.on_failure(failure)


async def run_backend(identity, on_snapshot, on_message, on_invalidated, on_ready, on_failure,
                      finished=None, *, connection_factory=None, fetch_snapshot=None,
                      heartbeat_seconds=15.0, refresh_seconds=30.0, lease_seconds=59.0,
                      monotonic=time.monotonic, wall_clock=time.time) -> None:
    """Run until finished is set, cancellation, or the first fatal error.

    Callbacks are synchronous and run on this event loop. on_snapshot receives a
    raw mapping and must validate policy or raise. on_message receives opaque
    channel/payload bytes plus local monotonic receive time only while ready.
    on_invalidated() immediately closes the caller's command gate for actual
    control events and shutdown. Routine refresh keeps the last validated policy
    and command flow active; on_snapshot must stop/disarm if policy changes.
    on_failure gets only one
    safe authentication/permission/server/protocol category; failures never retry.

    Injected connection_factory is async with no arguments. fetch_snapshot is
    async and receives the normalized own MAC and profile public IPv4 address.
    """
    backend = _Backend(identity, on_snapshot, on_message, on_invalidated, on_ready, on_failure,
                       finished, connection_factory, fetch_snapshot, heartbeat_seconds,
                       refresh_seconds, lease_seconds, monotonic, wall_clock)
    await backend.run()
