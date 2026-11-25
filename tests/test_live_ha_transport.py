"""Tests HaLiveTransport's reconnect-on-disconnect logic without any real
network -- a fake websocket object (duck-typed to the subset of the
`websockets` client API this module uses: recv/send/close/closed) stands in
for the real connection, and HaLiveTransport._connect is monkeypatched to
hand out fake sockets instead of opening a real one."""
import json
import unittest

from faikout_config.live_ha import HaLiveTransport


class FakeWebSocket:
    """queued_recv: a list of either a JSON-serializable dict (delivered
    normally) or an Exception instance (raised instead, simulating a
    dropped connection)."""

    def __init__(self, queued_recv, closed=False):
        self._queue = list(queued_recv)
        self.closed = closed
        self.sent = []

    async def recv(self):
        if not self._queue:
            raise ConnectionResetError("fake socket exhausted")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return json.dumps(item)

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True


def _make_transport_with_sockets(socket_sequence):
    """socket_sequence: list of FakeWebSocket, handed out one per
    (re)connect, in order."""
    t = HaLiveTransport(url="http://example.invalid:8123", token="test-token",
                         reconnect_attempts=2, reconnect_backoff_s=0.01)
    remaining = list(socket_sequence)

    async def fake_connect():
        t._ws = remaining.pop(0)
        t._results.clear()
        t._events.clear()

    t._connect = fake_connect
    t._loop.run_until_complete(t._connect())
    return t


class TestReconnect(unittest.TestCase):
    def test_requires_a_token(self):
        import os
        saved = os.environ.pop("HA_TOKEN", None)
        try:
            with self.assertRaises(RuntimeError):
                HaLiveTransport(url="http://example.invalid:8123")
        finally:
            if saved is not None:
                os.environ["HA_TOKEN"] = saved

    def test_disconnect_then_reconnect_then_caller_retries(self):
        sock1 = FakeWebSocket([ConnectionResetError("simulated drop")])
        # mid=2 is consumed (and lost) by the failed first attempt; the
        # retry call that actually reaches sock2 sends mid=3.
        sock2 = FakeWebSocket([{"id": 3, "type": "result", "success": True, "result": None}])
        t = _make_transport_with_sockets([sock1, sock2])
        self.assertIs(t._ws, sock1)

        # First call hits the dropped connection -- our code must reconnect
        # and raise ConnectionResetError telling the CALLER to retry (the
        # transport heals itself; re-issuing the specific request is the
        # caller's job, same as reliability.py's retry loop already does
        # for settings queries).
        with self.assertRaises(ConnectionResetError):
            t._loop.run_until_complete(t._call({"type": "mqtt/subscribe", "topic": "setting/#"}))

        self.assertIs(t._ws, sock2)  # reconnected to the second fake socket
        self.assertEqual(t.reconnect_count, 1)

        # Retry now succeeds against the healed connection.
        resp = t._loop.run_until_complete(t._call({"type": "mqtt/subscribe", "topic": "setting/#"}))
        self.assertTrue(resp["success"])

    def test_reconnect_exhausts_attempts_raises_clear_error(self):
        sock1 = FakeWebSocket([ConnectionResetError("drop 1")])

        async def always_fail_connect():
            raise RuntimeError("network still down")

        t = HaLiveTransport(url="http://example.invalid:8123", token="test-token",
                             reconnect_attempts=2, reconnect_backoff_s=0.01)
        t._ws = sock1
        t._connect = always_fail_connect

        with self.assertRaises(RuntimeError) as ctx:
            t._loop.run_until_complete(t._call({"type": "mqtt/subscribe", "topic": "x"}))
        self.assertIn("network still down", str(ctx.exception))

    def test_is_connected_reflects_socket_state(self):
        sock1 = FakeWebSocket([], closed=False)
        t = _make_transport_with_sockets([sock1])
        self.assertTrue(t.is_connected())
        sock1.closed = True
        self.assertFalse(t.is_connected())

    def test_successful_drain_does_not_trigger_reconnect(self):
        sock1 = FakeWebSocket([{"id": 2, "type": "result", "success": True, "result": None}])
        t = _make_transport_with_sockets([sock1])
        t._loop.run_until_complete(t._call({"type": "mqtt/subscribe", "topic": "x"}))
        self.assertEqual(t.reconnect_count, 0)
        self.assertIs(t._ws, sock1)


if __name__ == "__main__":
    unittest.main()
