"""End-to-end integration test: a real fake-but-wire-correct MQTT broker,
one or more simulated controllers (real paho-mqtt clients), and the real
DirectMqttProtocol -- no Home Assistant, no real hardware, entirely
synthetic data. This is the "independent installation using simulated
controllers" proof: everything here is generic, nothing is specific to any
particular deployment."""
import time
import unittest

from faikout_config.mqtt_direct import DirectMqttProtocol, MqttConfig
from faikout_config.registry import DeviceRegistry, Device
from faikout_config.protocol import ProtocolError
from tests.fake_mqtt_broker import FakeMqttBroker
from tests.simulated_controller import SimulatedController


class TestDirectMqttIntegration(unittest.TestCase):
    def setUp(self):
        self.broker = FakeMqttBroker()
        self.controllers = []

    def tearDown(self):
        for c in self.controllers:
            c.stop()
        self.broker.stop()

    def _spawn(self, hostname, mac, settings=None):
        c = SimulatedController("127.0.0.1", self.broker.port, hostname, mac, settings)
        self.controllers.append(c)
        return c

    def _protocol(self):
        return DirectMqttProtocol(MqttConfig(host="127.0.0.1", port=self.broker.port))

    def test_read_settings_from_simulated_controller(self):
        self._spawn("ac-demo-1", "AABBCCDDEE01")
        proto = self._protocol()
        try:
            result = proto.read_settings("ac-demo-1")
            self.assertEqual(result.hostname, "ac-demo-1")
            self.assertEqual(result.raw["tz"], "UTC")
        finally:
            proto.close()

    def test_write_setting_round_trips(self):
        self._spawn("ac-demo-1", "AABBCCDDEE01")
        proto = self._protocol()
        try:
            proto.write_setting("ac-demo-1", {"tz": "Europe/London"})
            time.sleep(0.2)
            result = proto.read_settings("ac-demo-1")
            self.assertEqual(result.raw["tz"], "Europe/London")
        finally:
            proto.close()

    def test_unknown_hostname_times_out_cleanly(self):
        self._spawn("ac-demo-1", "AABBCCDDEE01")
        proto = DirectMqttProtocol(MqttConfig(host="127.0.0.1", port=self.broker.port), response_timeout_s=1.0)
        try:
            with self.assertRaises(ProtocolError):
                proto.read_settings("nonexistent-device")
        finally:
            proto.close()

    def test_discovery_finds_simulated_controllers_not_yet_registered(self):
        self._spawn("ac-demo-1", "AABBCCDDEE01")
        self._spawn("ac-demo-2", "AABBCCDDEE02")
        proto = self._protocol()
        try:
            candidates = proto.discover(known_hostnames=set(), timeout_s=2.0)
            found = {c.hostname for c in candidates}
            self.assertEqual(found, {"ac-demo-1", "ac-demo-2"})
        finally:
            proto.close()

    def test_full_workflow_add_new_controller_without_touching_source_code(self):
        """The complete 'independent installation' scenario: start with an
        EMPTY registry (as a fresh install would), discover a brand-new
        simulated controller over the wire, and add it to the registry
        using only the registry's public API -- the same calls the web
        UI's device-management endpoints make. No .py file is read,
        edited, or reloaded anywhere in this test."""
        import tempfile
        from pathlib import Path

        self._spawn("ac-new-unit", "AABBCCDDEE99", settings={
            "hostname": "ac-new-unit", "ota": {"host": "x", "auto": False}, "tz": "UTC",
        })

        with tempfile.TemporaryDirectory() as tmp:
            registry = DeviceRegistry(Path(tmp) / "devices.json")
            self.assertEqual(registry.hostnames(), [])  # fresh install

            proto = self._protocol()
            try:
                candidates = proto.discover(known_hostnames=set(registry.hostnames()), timeout_s=2.0)
                self.assertEqual(len(candidates), 1)
                found = candidates[0]

                registry.add(Device(hostname=found.hostname, mac=found.mac, added_via="discovery"))
                self.assertEqual(registry.hostnames(), ["ac-new-unit"])

                # now the newly-added device is genuinely queryable
                result = proto.read_settings(found.hostname)
                self.assertEqual(result.hostname, "ac-new-unit")
            finally:
                proto.close()

    def test_partial_fleet_success_with_two_simulated_controllers_one_offline(self):
        self._spawn("ac-demo-1", "AABBCCDDEE01")
        # ac-demo-2 deliberately never spawned -- simulates an offline/
        # unreachable controller still present in the registry. Protocol's
        # own response timeout (not ReliableSettingsClient.timeout_s, which
        # the protocol layer doesn't currently consult per-call) governs
        # how long the unreachable query actually waits -- kept short here
        # so this test doesn't take 8+ seconds.
        proto = DirectMqttProtocol(MqttConfig(host="127.0.0.1", port=self.broker.port),
                                    response_timeout_s=1.0)
        try:
            from faikout_config.reliability import ReliableSettingsClient
            client = ReliableSettingsClient(proto, min_interval_s=0, max_retries=1,
                                             backoff_base_s=0.01, timeout_s=1.0)
            results = client.query_fleet(["ac-demo-1", "ac-demo-2"])
            self.assertTrue(results["ac-demo-1"].success)
            self.assertFalse(results["ac-demo-2"].success)
            self.assertIsNone(results["ac-demo-2"].raw)
        finally:
            proto.close()


if __name__ == "__main__":
    unittest.main()
