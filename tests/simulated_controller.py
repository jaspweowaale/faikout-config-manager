"""A simulated Faikout controller -- a real paho-mqtt client (same library
the production code uses) that behaves like a real device closely enough
for integration testing: answers settings queries, accepts settings
writes, and publishes MQTT discovery so `discover()` can find it. Uses
entirely synthetic data -- no resemblance to any real deployment's
hostnames, MACs, or network addresses intended or should be assumed.
"""
from __future__ import annotations

import json
import threading


class SimulatedController:
    def __init__(self, broker_host: str, broker_port: int, hostname: str, mac: str,
                 initial_settings: dict | None = None):
        import paho.mqtt.client as mqtt

        self.hostname = hostname
        self.mac = mac
        self.settings = initial_settings or {
            "hostname": hostname,
            "ota": {"host": "ota.example.invalid", "auto": False},
            "tz": "UTC",
            "dark": True,
            "no": {"comfort": True, "streamer": True},
        }
        self._client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                                    client_id=f"sim-{hostname}")
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._connected = threading.Event()
        self._client.connect(broker_host, broker_port, keepalive=30)
        self._client.loop_start()
        if not self._connected.wait(timeout=5):
            raise RuntimeError("simulated controller could not connect to broker")
        self._publish_discovery()

    def stop(self):
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        client.subscribe(f"setting/{self.hostname}")
        self._connected.set()

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", errors="replace")
        if payload == "":
            # read request -- reply with current settings, non-retained,
            # matching the real documented protocol.
            reply = dict(self.settings)
            reply["hostname"] = self.hostname
            self._client.publish(f"setting/{self.hostname}", json.dumps(reply), qos=0, retain=False)
        else:
            try:
                patch = json.loads(payload)
            except (ValueError, TypeError):
                return
            self._deep_merge(self.settings, patch)

    def _publish_discovery(self):
        config = {
            "unique_id": self.mac,
            "dev": {"ids": [self.mac], "name": self.hostname},
            "avty_t": f"state/{self.hostname}",
            "action_topic": f"state/{self.hostname}",
            "name": self.hostname,
        }
        self._client.publish(f"homeassistant/climate/{self.mac}/config",
                              json.dumps(config), qos=0, retain=True)

    @staticmethod
    def _deep_merge(target: dict, patch: dict) -> None:
        for k, v in patch.items():
            if isinstance(v, dict) and isinstance(target.get(k), dict):
                SimulatedController._deep_merge(target[k], v)
            else:
                target[k] = v
