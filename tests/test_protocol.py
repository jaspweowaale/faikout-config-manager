"""Exercises FaikoutHaWsProtocol itself (the real production class) against
the FakeTransport simulating Home Assistant's WebSocket API -- as distinct
from the tests/fakes.FakeProtocol shortcut used everywhere else, this
proves the actual read/write protocol implementation is correct, not just
the code that calls it."""
import unittest

from faikout_config.protocol import FaikoutHaWsProtocol, ProtocolError
from tests.fakes import FakeTransport


class TestFaikoutHaWsProtocol(unittest.TestCase):
    def test_read_settings_via_empty_payload_query(self):
        transport = FakeTransport({"ac-demo-a": {"tz": "UTC", "ota": {"auto": False}}})
        proto = FaikoutHaWsProtocol(transport)
        result = proto.read_settings("ac-demo-a")
        self.assertEqual(result.hostname, "ac-demo-a")
        self.assertEqual(result.raw["tz"], "UTC")

    def test_read_unknown_device_times_out(self):
        transport = FakeTransport({"ac-demo-a": {"tz": "UTC"}})
        proto = FaikoutHaWsProtocol(transport, response_timeout_s=0.1)
        with self.assertRaises(ProtocolError):
            proto.read_settings("nonexistent")

    def test_write_setting_merges_patch(self):
        transport = FakeTransport({"ac-demo-a": {"tz": "UTC", "ota": {"auto": False, "host": "x"}}})
        proto = FaikoutHaWsProtocol(transport)
        proto.write_setting("ac-demo-a", {"ota": {"auto": False}})  # no-op value, still a real write
        after = proto.read_settings("ac-demo-a")
        self.assertEqual(after.raw["ota"]["host"], "x")  # untouched sibling key preserved

    def test_write_setting_rejects_empty_patch(self):
        transport = FakeTransport({"ac-demo-a": {}})
        proto = FaikoutHaWsProtocol(transport)
        with self.assertRaises(ProtocolError):
            proto.write_setting("ac-demo-a", {})

    def test_arbitrary_transport_failure_normalized_to_protocol_error(self):
        """reliability.py's retry loop only ever catches ProtocolError --
        any transport-level exception (a reconnect signal, or anything
        else) must be normalized here, not leak through as some other
        exception type that would crash the retry loop instead of being
        retried."""
        class ExplodingTransport(FakeTransport):
            def publish(self, topic, payload, retain=False):
                raise ConnectionResetError("simulated disconnect-and-reconnect signal")

        transport = ExplodingTransport({"ac-demo-a": {"tz": "UTC"}})
        proto = FaikoutHaWsProtocol(transport)
        with self.assertRaises(ProtocolError):
            proto.read_settings("ac-demo-a")
        with self.assertRaises(ProtocolError):
            proto.write_setting("ac-demo-a", {"tz": "Europe/London"})


if __name__ == "__main__":
    unittest.main()
