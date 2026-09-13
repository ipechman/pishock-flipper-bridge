"""Offline own-device backend tests with injected fake network connections."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import device_backend as backend
from device_transport import RespError, PubSubMessage


IDENTITY = SimpleNamespace(mac="01:23:45:67:89:AB", hub_id=123, public_ip="203.0.113.1")
MAC = b"01_23_45_67_89_ab"


class Connection:
    def __init__(self, *, publisher=False):
        self.publisher = publisher
        self.commands = []
        self.authentications = []
        self.events = asyncio.Queue()
        self.closed = False
        self.count = 0
        self.failure = None

    async def authenticate(self, credential):
        self.authentications.append(credential)
        if self.failure:
            raise self.failure

    async def execute(self, *arguments):
        self.commands.append(arguments)
        if self.failure:
            raise self.failure
        if arguments[0] in ("PSUBSCRIBE", "SUBSCRIBE"):
            self.count += 1
            return [arguments[0].lower().encode(), arguments[1], self.count]
        if arguments[0] in ("HSET", "PUBLISH"):
            return 1
        raise AssertionError("Unexpected network command")

    async def read_pubsub(self, *, idle=False):
        assert idle is True
        event = await self.events.get()
        if isinstance(event, Exception):
            raise event
        return event

    async def aclose(self):
        self.closed = True

    def incoming(self, suffix=b"ops", payload=b"data", *, own_mac=False):
        prefix = MAC + b"-" if own_mac else b"c123-"
        self.events.put_nowait(PubSubMessage("pmessage", prefix + suffix, payload, prefix + b"*"))


async def eventually(predicate):
    async def waiting():
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(waiting(), 1)


class Harness:
    def __init__(self):
        self.sub = Connection()
        self.pub = Connection(publisher=True)
        self.connections = [self.sub, self.pub]
        self.created = 0
        self.snapshots = []
        self.messages = []
        self.invalidations = 0
        self.ready = 0
        self.failures = []
        self.fetch_calls = 0
        self.stop = asyncio.Event()
        self.fetcher = self.default_fetch

    async def factory(self):
        connection = self.connections[self.created]
        self.created += 1
        return connection

    async def default_fetch(self, credential, public_ip):
        self.fetch_calls += 1
        assert credential == MAC.decode()
        assert public_ip == IDENTITY.public_ip
        assert self.sub.commands == [("PSUBSCRIBE", MAC + b"-*"),
                                     ("PSUBSCRIBE", b"c123-*"), ("SUBSCRIBE", b"pingall")]
        return {"generation": self.fetch_calls}

    def invalidated(self):
        self.invalidations += 1

    def became_ready(self):
        self.ready += 1

    def start(self, **options):
        defaults = dict(heartbeat_seconds=3600, refresh_seconds=3600, lease_seconds=3600)
        defaults.update(options)
        return asyncio.create_task(backend.run_backend(
            IDENTITY, self.snapshots.append, lambda *event: self.messages.append(event),
            self.invalidated, self.became_ready, self.failures.append,
            self.stop, connection_factory=self.factory, fetch_snapshot=self.fetcher,
            wall_clock=lambda: 1700000000, **defaults))

    async def finish(self, task):
        self.stop.set()
        await asyncio.wait_for(task, 1)


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_owned_subscriptions_precede_registration_and_only_liveness_is_published(self):
        harness = Harness()
        task = harness.start()
        await eventually(lambda: harness.ready == 1 and len(harness.pub.commands) == 2)
        self.assertEqual(harness.created, 2)
        self.assertEqual(harness.sub.authentications, [MAC.decode()])
        self.assertEqual(harness.pub.authentications, [MAC.decode()])
        self.assertEqual(harness.pub.commands,
                         [("HSET", "lite:status", "123", "1700000000"),
                          ("PUBLISH", "123-ping", "1700000000")])
        harness.sub.incoming()
        await eventually(lambda: len(harness.messages) == 1)
        channel, payload, received = harness.messages[0]
        self.assertEqual((channel, payload), (b"c123-ops", b"data"))
        self.assertIsInstance(received, float)
        await harness.finish(task)
        self.assertTrue(harness.sub.closed and harness.pub.closed)
        self.assertEqual(harness.failures, [])

    async def test_control_immediately_invalidates_and_drops_commands_during_refresh(self):
        harness = Harness()
        refresh_started, release = asyncio.Event(), asyncio.Event()

        async def fetching(credential, public_ip):
            result = await harness.default_fetch(credential, public_ip)
            if harness.fetch_calls == 2:
                refresh_started.set()
                await release.wait()
            return result

        harness.fetcher = fetching
        task = harness.start()
        await eventually(lambda: harness.ready == 1)
        harness.sub.incoming(b"pause")
        harness.sub.incoming(b"ops", b"before-refresh-start")
        await asyncio.wait_for(refresh_started.wait(), 1)
        self.assertEqual(harness.invalidations, 2)  # startup + actual control
        harness.sub.incoming(b"sops-example", b"during-refresh")
        await asyncio.sleep(0.01)
        self.assertEqual(harness.messages, [])
        release.set()
        await eventually(lambda: harness.ready == 2)
        self.assertEqual(harness.messages, [])
        harness.sub.incoming(b"ops", b"fresh")
        await eventually(lambda: len(harness.messages) == 1)
        self.assertEqual(harness.messages[0][1], b"fresh")
        await harness.finish(task)

    async def test_new_control_during_fetch_discards_stale_snapshot(self):
        harness = Harness()
        second_started, release = asyncio.Event(), asyncio.Event()

        async def fetching(credential, public_ip):
            result = await harness.default_fetch(credential, public_ip)
            if harness.fetch_calls == 2:
                second_started.set()
                await release.wait()
            return result

        harness.fetcher = fetching
        task = harness.start()
        await eventually(lambda: harness.ready == 1)
        harness.sub.incoming(b"pause")
        await second_started.wait()
        harness.sub.incoming(b"rems")
        await eventually(lambda: harness.invalidations == 3)
        release.set()
        await eventually(lambda: harness.ready == 2)
        self.assertEqual(harness.snapshots, [{"generation": 1}, {"generation": 3}])
        await harness.finish(task)

    async def test_periodic_refresh_keeps_stop_delivery_without_physical_invalidation(self):
        harness = Harness()
        blocked = asyncio.Event()

        async def fetching(credential, public_ip):
            result = await harness.default_fetch(credential, public_ip)
            if harness.fetch_calls > 1:
                blocked.set()
                await asyncio.Event().wait()
            return result

        harness.fetcher = fetching
        task = harness.start(refresh_seconds=0.02)
        await blocked.wait()
        self.assertEqual(harness.invalidations, 1)
        stop_payload = b'{"id":234,"m":"e","i":0,"d":0}'
        harness.sub.incoming(payload=stop_payload)
        await eventually(lambda: len(harness.messages) == 1)
        self.assertEqual(harness.messages[0][:2], (b"c123-ops", stop_payload))
        self.assertEqual(harness.invalidations, 1)
        await harness.finish(task)

    async def test_alive_requests_coalesce_and_pingall_only_renews_lease(self):
        harness = Harness()
        task = harness.start()
        await eventually(lambda: len(harness.pub.commands) == 2 and harness.ready == 1)
        harness.sub.events.put_nowait(PubSubMessage("message", b"pingall", b"ignored"))
        await asyncio.sleep(0.01)
        self.assertEqual(len(harness.pub.commands), 2)
        harness.sub.incoming(b"alive")
        harness.sub.incoming(b"ping")
        await eventually(lambda: len(harness.pub.commands) == 4)
        self.assertEqual(harness.invalidations, 1)
        self.assertEqual(harness.fetch_calls, 1)
        await harness.finish(task)

    async def test_subscription_lease_fails_even_when_publisher_is_healthy(self):
        harness = Harness()
        task = harness.start(heartbeat_seconds=0.005, lease_seconds=0.035)
        await asyncio.wait_for(task, 1)
        self.assertGreater(len(harness.pub.commands), 2)
        self.assertEqual(harness.failures, ["protocol"])
        self.assertTrue(harness.sub.closed and harness.pub.closed)

    async def test_auth_denial_does_not_create_publisher_fetch_or_retry(self):
        harness = Harness()
        harness.sub.failure = RespError("authentication")
        task = harness.start()
        await task
        self.assertEqual(harness.created, 1)
        self.assertEqual(harness.fetch_calls, 0)
        self.assertEqual(harness.sub.authentications, [MAC.decode()])
        self.assertEqual(harness.failures, ["authentication"])

    async def test_disconnect_and_unknown_foreign_channels_stop_without_raw_errors(self):
        for event, expected in ((OSError("raw private information"), "protocol"),
                                (PubSubMessage("message", b"c999-ops", b"private"), "permission")):
            harness = Harness()
            task = harness.start()
            await eventually(lambda: harness.ready == 1)
            harness.sub.events.put_nowait(event)
            await asyncio.wait_for(task, 1)
            self.assertEqual(harness.failures, [expected])
            self.assertEqual(harness.messages, [])
            self.assertTrue(harness.sub.closed and harness.pub.closed)

    async def test_snapshot_validation_exception_never_opens_gate(self):
        harness = Harness()

        def rejecting(_):
            raise ValueError("private response content")

        task = asyncio.create_task(backend.run_backend(
            IDENTITY, rejecting, lambda *args: harness.messages.append(args),
            harness.invalidated, harness.became_ready, harness.failures.append,
            connection_factory=harness.factory, fetch_snapshot=harness.default_fetch))
        await asyncio.wait_for(task, 1)
        self.assertEqual(harness.ready, 0)
        self.assertEqual(harness.failures, ["protocol"])

    async def test_cancellation_closes_both_connections_and_invalidates(self):
        harness = Harness()
        task = harness.start()
        await eventually(lambda: harness.ready == 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(harness.sub.closed and harness.pub.closed)
        self.assertGreaterEqual(harness.invalidations, 2)


class HttpTests(unittest.TestCase):
    def test_registration_rejects_duplicate_keys_and_invalid_json_shapes(self):
        for body in (b'{"r":false,"r":true}', b'{"r":true,"p":[{"id":1,"id":2}]}',
                     b'{"r":1.5}', b'{"r":NaN}', b'{"p":' + b'[' * 10 + b'0' + b']' * 10 + b'}',
                     b'[]', b' ' * (backend.MAX_REGISTRATION_BYTES + 1)):
            with self.subTest(body_length=len(body)):
                response = unittest.mock.MagicMock()
                response.__enter__.return_value = response
                response.geturl.return_value = "https://ps.pishock.com/pishock/register"
                response.status = 200
                response.headers = {}
                response.read.return_value = body
                opener = unittest.mock.Mock()
                opener.open.return_value = response
                with patch.object(backend.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaises(backend.BackendError) as failure:
                        backend._fetch_registration(MAC.decode(), IDENTITY.public_ip)
                self.assertEqual(str(failure.exception), "Device backend stopped: protocol.")

    def test_redirect_is_rejected_without_echo(self):
        with self.assertRaises(backend.BackendError) as failure:
            backend._NoRedirects().redirect_request(None, None, 302, "private", {}, "https://foreign/private")
        self.assertNotIn("private", str(failure.exception))

    def test_fixed_https_request_and_bounded_read_without_real_network(self):
        class Response:
            status = 200
            headers = {"Content-Length": "10"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self):
                return "https://ps.pishock.com/pishock/register?private"

            def read(self, count):
                self.requested = count
                return b'{"r":true}'

        response = Response()
        opened = []

        class Opener:
            def open(self, request, timeout):
                opened.append((request, timeout))
                return response

        with patch.object(backend.urllib.request, "build_opener", return_value=Opener()):
            self.assertEqual(backend._fetch_registration(MAC.decode(), IDENTITY.public_ip), {"r": True})
        self.assertEqual(response.requested, backend.MAX_REGISTRATION_BYTES + 1)
        parsed = backend.urllib.parse.urlsplit(opened[0][0].full_url)
        self.assertEqual((parsed.scheme, parsed.hostname, parsed.path),
                         ("https", "ps.pishock.com", "/pishock/register"))
        self.assertEqual(backend.urllib.parse.parse_qs(parsed.query),
                         {"mac": [MAC.decode()], "ip": [IDENTITY.public_ip]})
        self.assertEqual(opened[0][1], 5.0)


if __name__ == "__main__":
    unittest.main()
