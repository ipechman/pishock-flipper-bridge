"""Offline RESP2 stream and connection tests; no sockets or real credentials."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import device_transport as transport


def reader_with(data: bytes, *, eof: bool = True):
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    if eof:
        reader.feed_eof()
    return reader


class FakeWriter:
    def __init__(self, *, fail_drain=False, fail_close=False):
        self.writes = []
        self.closed = False
        self.fail_drain = fail_drain
        self.fail_close = fail_close

    def write(self, data):
        self.writes.append(data)

    async def drain(self):
        if self.fail_drain:
            raise OSError("raw private device credential")

    def close(self):
        self.closed = True
        if self.fail_close:
            raise OSError("raw private close information")

    async def wait_closed(self):
        await asyncio.sleep(0)


class CodecTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_supported_resp2_value_types(self):
        examples = {
            b"+OK\r\n": b"OK",
            b":-123\r\n": -123,
            b":+123\r\n": 123,
            b"$-1\r\n": None,
            b"*-1\r\n": None,
            b"$0\r\n\r\n": b"",
            b"*0\r\n": [],
            b"$5\r\na\x00\r\nb\r\n": b"a\x00\r\nb",
            b"*3\r\n+one\r\n:2\r\n*1\r\n$3\r\nend\r\n": [b"one", 2, [b"end"]],
        }
        for encoded, expected in examples.items():
            with self.subTest(encoded=encoded):
                self.assertEqual(await transport.read_response(reader_with(encoded)), expected)

    async def test_fragmented_response_and_next_message_are_preserved(self):
        reader = asyncio.StreamReader()

        async def feeding():
            for fragment in (b"*2\r", b"\n$3\r\nf", b"oo\r", b"\n:7\r", b"\n+NEXT\r\n"):
                await asyncio.sleep(0)
                reader.feed_data(fragment)
            reader.feed_eof()

        producer = asyncio.create_task(feeding())
        self.assertEqual(await transport.read_response(reader), [b"foo", 7])
        self.assertEqual(await transport.read_response(reader), b"NEXT")
        await producer

    async def test_error_categories_never_echo_raw_server_message(self):
        for tag, category in ((b"WRONGPASS", "authentication"), (b"NOAUTH", "authentication"),
                              (b"NOPERM", "permission"), (b"ERR", "server")):
            with self.subTest(tag=tag):
                with self.assertRaises(transport.RespError) as failure:
                    await transport.read_response(reader_with(b"-" + tag + b" private-secret\r\n"))
                self.assertEqual(failure.exception.category, category)
                self.assertNotIn("private", str(failure.exception))
                self.assertNotIn("private", repr(failure.exception))
                self.assertFalse(hasattr(failure.exception, "raw"))

    async def test_truncation_bad_frames_and_header_overflows_are_safe(self):
        invalid = (b"", b"+partial", b"$6\r\nshort", b"*2\r\n+one\r\n", b"?value\r\n",
                   b"+bad\n", b"+bad\rX", b":1.2\r\n", b":9223372036854775808\r\n",
                   b":-9223372036854775809\r\n", b"$-2\r\n", b"*+1\r\n", b"$01\r\nx\r\n",
                   b"$2\r\nabXY", b"+" + b"x" * (transport.MAX_LINE_BYTES + 1) + b"\r\n")
        for data in invalid:
            with self.subTest(data=data[:30]), self.assertRaises(transport.RespError) as failure:
                await transport.read_response(reader_with(data))
            self.assertEqual(failure.exception.category, "protocol")

    async def test_declared_bulk_array_depth_node_and_total_budgets(self):
        too_large_bulk = f"${transport.MAX_BULK_BYTES + 1}\r\n".encode()
        too_many_items = f"*{transport.MAX_ARRAY_ITEMS + 1}\r\n".encode()
        too_deep = b"*1\r\n" * (transport.MAX_DEPTH + 1) + b"+leaf\r\n"
        too_many_nodes = b"*2\r\n*128\r\n" + b":0\r\n" * 128 + b"*128\r\n" + b":0\r\n" * 128
        bulk = f"${transport.MAX_BULK_BYTES}\r\n".encode() + b"x" * transport.MAX_BULK_BYTES + b"\r\n"
        too_large_message = b"*2\r\n" + bulk + bulk
        for data in (too_large_bulk, too_many_items, too_deep, too_many_nodes, too_large_message):
            with self.subTest(size=len(data)), self.assertRaises(transport.RespError):
                await transport.read_response(reader_with(data))

    async def test_maximum_valid_bulk_and_signed_integer_boundaries(self):
        data = b"x" * transport.MAX_BULK_BYTES
        self.assertEqual(await transport.read_response(reader_with(
            f"${len(data)}\r\n".encode() + data + b"\r\n")), data)
        for number in (transport.INT64_MIN, transport.INT64_MAX):
            self.assertEqual(await transport.read_response(reader_with(f":{number}\r\n".encode())), number)

    async def test_read_timeout_is_bounded_and_redacted(self):
        with self.assertRaises(transport.RespError) as failure:
            await transport.read_response(reader_with(b"+private", eof=False), timeout=0.01)
        self.assertEqual(failure.exception.category, "protocol")
        self.assertNotIn("private", str(failure.exception))

    async def test_binary_safe_encoder_round_trip(self):
        wire = transport.encode_command("CMD", b"a\r\n\x00b", "café", -4)
        self.assertEqual(await transport.read_response(reader_with(wire)),
                         [b"CMD", b"a\r\n\x00b", "café".encode(), b"-4"])

    async def test_encoder_rejects_non_arguments_and_overflow_before_write(self):
        for arguments in ((), (True,), (None,), (3.14,), (object(),),
                          (b"x" * (transport.MAX_BULK_BYTES + 1),),
                          ("CMD",) * (transport.MAX_ARRAY_ITEMS + 1),
                          (b"x" * transport.MAX_BULK_BYTES,) * 2):
            with self.subTest(length=len(arguments)), self.assertRaises(transport.RespError):
                transport.encode_command(*arguments)


class EnvelopeTests(unittest.TestCase):
    def test_subscription_and_message_envelopes(self):
        self.assertEqual(transport.parse_pubsub([b"subscribe", b"c123-ops", 1]),
                         transport.SubscriptionAck("subscribe", b"c123-ops", 1))
        self.assertEqual(transport.parse_pubsub([b"psubscribe", b"c123-*", 1]),
                         transport.SubscriptionAck("psubscribe", b"c123-*", 1))
        self.assertEqual(transport.parse_pubsub([b"unsubscribe", None, 0]),
                         transport.SubscriptionAck("unsubscribe", None, 0))
        self.assertEqual(transport.parse_pubsub([b"message", b"c123-ops", b'{"unparsed":"raw"}']),
                         transport.PubSubMessage("message", b"c123-ops", b'{"unparsed":"raw"}'))
        self.assertEqual(transport.parse_pubsub([b"pmessage", b"c123-*", b"c123-ops", b"opaque"]),
                         transport.PubSubMessage("pmessage", b"c123-ops", b"opaque", b"c123-*"))

    def test_unknown_and_malformed_envelopes_are_not_interpreted(self):
        for envelope in (None, b"PONG", [], [b"unknown", b"private"],
                         [b"subscribe", b"channel", True], [b"subscribe", b"channel", 0],
                         [b"subscribe", None, 1], [b"subscribe", b"channel", 1, b"extra"],
                         [b"message", b"", b"data"], [b"message", b"channel", {}],
                         [b"message", b"c" * 513, b"data"],
                         [b"pmessage", b"*", b"channel", b"data", b"extra"]):
            with self.subTest(envelope=envelope), self.assertRaises(transport.RespError):
                transport.parse_pubsub(envelope)


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_uses_same_injected_value_once_and_requires_ok(self):
        writer = FakeWriter()
        connection = transport.RedisConnection(reader_with(b"+OK\r\n"), writer)
        await connection.authenticate(b"test-credential")
        self.assertTrue(connection.authenticated)
        self.assertEqual(writer.writes, [transport.encode_command("AUTH", b"test-credential", b"test-credential")])
        with self.assertRaises(transport.RespError):
            await connection.authenticate(b"different-test")
        self.assertEqual(len(writer.writes), 1)
        await connection.aclose()

    async def test_failed_auth_closes_without_retry_or_server_text(self):
        for response in (b"-WRONGPASS private-secret\r\n", b"-ERR private-secret\r\n", b"+NOTOK\r\n"):
            writer = FakeWriter()
            connection = transport.RedisConnection(reader_with(response), writer)
            with self.subTest(response=response), self.assertRaises(transport.RespError) as failure:
                await connection.authenticate(b"test-credential")
            self.assertEqual(failure.exception.category, "authentication")
            self.assertNotIn("private", repr(failure.exception))
            self.assertTrue(connection.closed)
            self.assertEqual(len(writer.writes), 1)
            with self.assertRaises(transport.RespError):
                await connection.authenticate(b"test-credential")
            self.assertEqual(len(writer.writes), 1)

    async def test_send_failure_never_retries_and_safe_close_cannot_leak(self):
        writer = FakeWriter(fail_drain=True, fail_close=True)
        connection = transport.RedisConnection(reader_with(b""), writer)
        with self.assertRaises(transport.RespError) as failure:
            await connection.execute("PING")
        self.assertEqual(writer.writes, [transport.encode_command("PING")])
        self.assertTrue(writer.closed)
        self.assertNotIn("private", str(failure.exception))

    async def test_timeout_closes_and_does_not_retransmit(self):
        writer = FakeWriter()
        connection = transport.RedisConnection(reader_with(b"", eof=False), writer, timeout=0.01)
        with self.assertRaises(transport.RespError):
            await connection.execute("PING")
        self.assertEqual(writer.writes, [transport.encode_command("PING")])
        self.assertTrue(connection.closed)

    async def test_reads_pubsub_ack_and_payload_without_extra_writes(self):
        wire = b"*3\r\n$9\r\nsubscribe\r\n$8\r\nc123-ops\r\n:1\r\n"
        wire += b"*3\r\n$7\r\nmessage\r\n$8\r\nc123-ops\r\n$4\r\ndata\r\n"
        writer = FakeWriter()
        connection = transport.RedisConnection(reader_with(wire), writer)
        ack = transport.parse_pubsub(await connection.execute("SUBSCRIBE", "c123-ops"))
        message = await connection.read_pubsub()
        self.assertEqual(ack, transport.SubscriptionAck("subscribe", b"c123-ops", 1))
        self.assertEqual(message.payload, b"data")
        self.assertEqual(writer.writes, [transport.encode_command("SUBSCRIBE", "c123-ops")])
        await connection.aclose()

    async def test_invalid_pubsub_frame_closes_instead_of_guessing(self):
        writer = FakeWriter()
        connection = transport.RedisConnection(reader_with(b"+unrecognized\r\n"), writer)
        with self.assertRaises(transport.RespError):
            await connection.read_pubsub()
        self.assertTrue(connection.closed)

    async def test_idle_read_waits_for_first_byte_then_bounds_incomplete_frame(self):
        writer = FakeWriter()
        reader = reader_with(b"", eof=False)
        connection = transport.RedisConnection(reader, writer, timeout=0.01)
        task = asyncio.create_task(connection.read_response(idle=True))
        await asyncio.sleep(0.03)
        self.assertFalse(task.done())
        reader.feed_data(b"+")
        with self.assertRaises(transport.RespError):
            await task
        self.assertTrue(connection.closed)

    async def test_context_manager_and_cancellation_close(self):
        writer = FakeWriter()
        async with transport.RedisConnection(reader_with(b"+PONG\r\n"), writer) as connection:
            self.assertEqual(await connection.execute("PING"), b"PONG")
        self.assertTrue(writer.closed)
        writer = FakeWriter()
        connection = transport.RedisConnection(reader_with(b"", eof=False), writer)
        task = asyncio.create_task(connection.read_response())
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(writer.closed)

    async def test_connect_uses_fixed_documented_endpoint_and_configured_stream_limit(self):
        writer = FakeWriter()
        with patch.object(asyncio, "open_connection", new_callable=AsyncMock,
                          return_value=(reader_with(b""), writer)) as opening:
            connection = await transport.RedisConnection.connect(timeout=1)
        opening.assert_awaited_once_with("redis.pishock.com", 6379, limit=transport.MAX_STREAM_BUFFER)
        await connection.aclose()

    async def test_connect_failure_is_safe_and_not_retried(self):
        with patch.object(asyncio, "open_connection", new_callable=AsyncMock,
                          side_effect=OSError("private network information")) as opening:
            with self.assertRaises(transport.RespError) as failure:
                await transport.RedisConnection.connect(timeout=1)
        self.assertEqual(opening.await_count, 1)
        self.assertNotIn("private", repr(failure.exception))


if __name__ == "__main__":
    unittest.main()
