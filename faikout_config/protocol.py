"""Device configuration protocol abstraction.

The interface (DeviceConfigProtocol) is intentionally generic so a new
transport -- direct MQTT, a Home Assistant integration, or a future
appliance family entirely -- can implement it without touching anything
else in this package. Two concrete implementations ship today:
FaikoutHaWsProtocol (below, talks to a Faikout fleet via an existing Home
Assistant MQTT installation) and DirectMqttProtocol (mqtt_direct.py, talks
to any MQTT broker directly -- the recommended path if you don't already
have Home Assistant).

Safety note: write_setting() and HaWsTransport.publish() are fully
implemented and unit-testable (see tests/), but nothing in this package's
CLI or web UI wires a real write into a live run by default. Enabling live
writes is a deliberate, explicit, separate configuration step -- see
cli.py's and webui/app.py's module docstrings.
"""
from __future__ import annotations

import abc
import json
import time
from dataclasses import dataclass
from typing import Optional


class ProtocolError(Exception):
    pass


@dataclass
class RawSettings:
    """One device's settings dump, exactly as returned by the device --
    unparsed into planes. Plane classification happens one layer up
    (planes.py), deliberately kept separate from the transport layer."""
    hostname: str
    raw: dict
    captured_at: float


class DeviceConfigProtocol(abc.ABC):
    """Generic interface any appliance-config integration should implement."""

    @abc.abstractmethod
    def read_settings(self, hostname: str) -> RawSettings:
        """Read-only. Must never have a side effect beyond the query itself."""

    @abc.abstractmethod
    def write_setting(self, hostname: str, patch: dict) -> None:
        """Merge-write a partial settings patch (e.g. {"ota": {"auto": 0}}).
        Must never replace the whole config -- callers are responsible for
        patches being minimal; implementations must not widen that scope."""


class FaikoutHaWsProtocol(DeviceConfigProtocol):
    """Faikout over Home Assistant's MQTT WebSocket API.

    Read: publish an empty payload to setting/<hostname> (documented RevK
    protocol -- the device replies with a one-shot JSON settings dump).
    Write: publish a JSON patch to setting/<hostname> (deep-merge -- sends
    only the changed key(s), never a full-object replace).
    """

    def __init__(self, transport: "HaWsTransport", response_timeout_s: float = 8.0):
        self._transport = transport
        self._timeout = response_timeout_s

    def read_settings(self, hostname: str) -> RawSettings:
        # Every transport-level failure (timeout, disconnect-triggered
        # reconnect signal, anything else) is normalized to ProtocolError
        # here, at the protocol boundary -- callers (reliability.py's retry
        # loop) only ever need to handle one exception type regardless of
        # what went wrong underneath. A reconnect-triggered error means the
        # TRANSPORT already healed itself; re-raising as ProtocolError is
        # exactly what makes the retry loop correctly re-issue this read
        # against the now-healthy connection instead of crashing out.
        try:
            topic = f"setting/{hostname}"
            with self._transport.subscribe("setting/#") as sub:
                self._transport.publish(topic, payload="", retain=False)
                deadline = time.time() + self._timeout
                while time.time() < deadline:
                    msg = sub.next(timeout=deadline - time.time())
                    if msg is None:
                        break
                    try:
                        parsed = json.loads(msg.payload)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(parsed, dict) and parsed.get("hostname") == hostname:
                        return RawSettings(hostname=hostname, raw=parsed, captured_at=time.time())
        except ProtocolError:
            raise
        except Exception as e:
            raise ProtocolError(f"transport failure reading {hostname}: {e}") from e
        raise ProtocolError(f"no settings reply from {hostname} within {self._timeout}s")

    def write_setting(self, hostname: str, patch: dict) -> None:
        if not isinstance(patch, dict) or not patch:
            raise ProtocolError("write_setting requires a non-empty dict patch")
        topic = f"setting/{hostname}"
        try:
            self._transport.publish(topic, payload=json.dumps(patch), retain=False)
        except ProtocolError:
            raise
        except Exception as e:
            raise ProtocolError(f"transport failure writing {hostname}: {e}") from e


class HaWsTransport(abc.ABC):
    """Thin transport boundary so protocol.py never talks to a live
    WebSocket directly -- makes FaikoutHaWsProtocol fully unit-testable."""

    @abc.abstractmethod
    def publish(self, topic: str, payload: str, retain: bool = False) -> None: ...

    @abc.abstractmethod
    def subscribe(self, topic: str) -> "Subscription": ...


class Subscription(abc.ABC):
    @abc.abstractmethod
    def next(self, timeout: float) -> Optional["Message"]: ...

    def close(self) -> None:
        """Default no-op; a real transport overrides this to actually
        unsubscribe server-side. Explicit cleanup, not left to GC/connection
        teardown -- repeatedly subscribing without ever unsubscribing (the
        original FaikoutHaWsProtocol.read_settings did exactly this, once
        per call) was a real candidate contributing factor considered
        during the transport-reliability investigation, even though the
        controlled experiment pointed more toward QoS-0 loss as the likely
        cause -- cleaning this up costs nothing and removes one more
        plausible variable."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@dataclass
class Message:
    topic: str
    payload: str
