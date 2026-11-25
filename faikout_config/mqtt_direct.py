"""Direct MQTT transport -- connects straight to a broker, no Home Assistant
required. This is the recommended path for a standalone installation; the
HA-based protocol (protocol.py's FaikoutHaWsProtocol + live_ha.py) remains
available for anyone who already has Home Assistant and would rather reuse
its existing broker connection.

Uses paho-mqtt (a small, widely-used, actively maintained MQTT client
library -- the standard choice for this; see requirements.txt). This is the
one new runtime dependency this project adds beyond the Python standard
library and Flask.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass

from .protocol import DeviceConfigProtocol, ProtocolError, RawSettings
from .discovery import find_candidates, DiscoveredDevice


@dataclass
class MqttConfig:
    host: str
    port: int = 1883
    username: str = ""
    password: str = ""
    client_id: str = "faikout-config-manager"
    keepalive: int = 60
    tls: bool = False


class DirectMqttProtocol(DeviceConfigProtocol):
    """One persistent connection, shared across all devices. A single
    background thread (paho-mqtt's own network loop) delivers messages into
    an internal queue; read_settings()/write_setting() are synchronous from
    the caller's point of view, matching the same interface
    FaikoutHaWsProtocol presents."""

    def __init__(self, config: MqttConfig, response_timeout_s: float = 8.0):
        import paho.mqtt.client as mqtt

        self._config = config
        self._timeout = response_timeout_s
        self._incoming: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._discovery_incoming: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._connected = threading.Event()
        self._connect_error: str | None = None

        self._client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                                    client_id=config.client_id, clean_session=True)
        if config.username:
            self._client.username_pw_set(config.username, config.password or None)
        if config.tls:
            self._client.tls_set()
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        try:
            self._client.connect(config.host, config.port, keepalive=config.keepalive)
        except Exception as e:
            raise ProtocolError(f"could not connect to MQTT broker {config.host}:{config.port}: {e}") from e
        self._client.loop_start()
        if not self._connected.wait(timeout=response_timeout_s):
            self._client.loop_stop()
            raise ProtocolError(f"MQTT broker {config.host}:{config.port} did not acknowledge "
                                 f"connection within {response_timeout_s}s")

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        # VERSION2 callback API: reason_code is 0 (or a ReasonCode that
        # compares equal to 0) on success.
        if reason_code == 0:
            client.subscribe("setting/#")
            self._connected.set()
        else:
            self._connect_error = f"MQTT connect refused: {reason_code}"

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._connected.clear()

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = ""
        if msg.topic.startswith("setting/"):
            self._incoming.put((msg.topic, payload))
        elif msg.topic.startswith("homeassistant/climate/"):
            self._discovery_incoming.put((msg.topic, payload))

    def read_settings(self, hostname: str) -> RawSettings:
        if not self._connected.is_set():
            raise ProtocolError("not connected to MQTT broker")
        # Drain any stale queued messages from a previous call before
        # publishing a new query, so a late reply for a DIFFERENT hostname
        # can't be mistaken for this one's answer.
        self._drain_queue()
        self._client.publish(f"setting/{hostname}", payload="", qos=0, retain=False)
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                topic, payload = self._incoming.get(timeout=max(0.01, remaining))
            except queue.Empty:
                break
            try:
                parsed = json.loads(payload)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, dict) and parsed.get("hostname") == hostname:
                return RawSettings(hostname=hostname, raw=parsed, captured_at=time.time())
        raise ProtocolError(f"no settings reply from {hostname} within {self._timeout}s")

    def write_setting(self, hostname: str, patch: dict) -> None:
        if not isinstance(patch, dict) or not patch:
            raise ProtocolError("write_setting requires a non-empty dict patch")
        if not self._connected.is_set():
            raise ProtocolError("not connected to MQTT broker")
        self._client.publish(f"setting/{hostname}", payload=json.dumps(patch), qos=0, retain=False)

    def discover(self, known_hostnames: set[str], timeout_s: float = 5.0) -> list[DiscoveredDevice]:
        """Subscribes to the MQTT discovery topic briefly, collects
        whatever's retained there (discovery configs are published
        retained, so this returns immediately for any device that's ever
        connected, without needing it to republish anything), then
        unsubscribes -- no standing subscription left behind."""
        if not self._connected.is_set():
            raise ProtocolError("not connected to MQTT broker")
        while not self._discovery_incoming.empty():
            self._discovery_incoming.get_nowait()
        self._client.subscribe("homeassistant/climate/+/config")
        messages = []
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                messages.append(self._discovery_incoming.get(timeout=max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                break
        self._client.unsubscribe("homeassistant/climate/+/config")
        return find_candidates(messages, known_hostnames)

    def _drain_queue(self) -> None:
        try:
            while True:
                self._incoming.get_nowait()
        except queue.Empty:
            pass
