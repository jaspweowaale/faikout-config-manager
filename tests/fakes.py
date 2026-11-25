"""In-memory fakes for fully offline testing -- no network, no real
hardware. FakeProtocol implements the same DeviceConfigProtocol interface a
live implementation would, so sync.py/restore.py/profiles.py are tested
against the real interface contract, not a shortcut."""
from __future__ import annotations

import copy
import json

from faikout_config.protocol import DeviceConfigProtocol, RawSettings, ProtocolError
from faikout_config.protocol import HaWsTransport, Subscription, Message


class FakeProtocol(DeviceConfigProtocol):
    def __init__(self, initial: dict[str, dict]):
        self._store = copy.deepcopy(initial)
        self.write_log: list[tuple[str, dict]] = []  # for assertions in tests

    def read_settings(self, hostname: str) -> RawSettings:
        if hostname not in self._store:
            raise ProtocolError(f"unknown device {hostname}")
        return RawSettings(hostname=hostname, raw=copy.deepcopy(self._store[hostname]), captured_at=0)

    def write_setting(self, hostname: str, patch: dict) -> None:
        if hostname not in self._store:
            raise ProtocolError(f"unknown device {hostname}")
        self.write_log.append((hostname, copy.deepcopy(patch)))
        _deep_merge(self._store[hostname], patch)


class FlakyReadProtocol(FakeProtocol):
    """Simulates intermittent transport failures NOT correlated with a
    cooldown timer (see reliability.py's module docstring for why that
    matters). fail_pattern is a dict hostname -> list of bools, consumed in
    order per read_settings() call (True = fail this attempt); once
    exhausted, all further reads for that hostname succeed."""

    def __init__(self, initial: dict[str, dict], fail_pattern: dict[str, list]):
        super().__init__(initial)
        self._fail_pattern = {h: list(v) for h, v in fail_pattern.items()}
        self.read_attempts: dict[str, int] = {}

    def read_settings(self, hostname: str) -> RawSettings:
        self.read_attempts[hostname] = self.read_attempts.get(hostname, 0) + 1
        pattern = self._fail_pattern.get(hostname, [])
        if pattern:
            should_fail = pattern.pop(0)
            if should_fail:
                raise ProtocolError(f"simulated transient failure for {hostname}")
        return super().read_settings(hostname)


class FailingWriteProtocol(FakeProtocol):
    """Writes succeed on the broker side (MQTT publish never fails) but the
    device never actually applies them -- simulates the readback-mismatch /
    rollback path."""
    def write_setting(self, hostname: str, patch: dict) -> None:
        self.write_log.append((hostname, copy.deepcopy(patch)))
        # deliberately do NOT apply the patch -- readback will show no change


def _deep_merge(target: dict, patch: dict) -> None:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _deep_merge(target[k], v)
        else:
            target[k] = v


class FakeTransport(HaWsTransport):
    """Exercises FaikoutHaWsProtocol itself (not just the FakeProtocol
    shortcut) against an in-memory pub/sub simulating the real
    setting/<hostname> empty-payload-query convention."""

    def __init__(self, devices: dict[str, dict]):
        self._devices = copy.deepcopy(devices)
        self._published: list[tuple[str, str, bool]] = []

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self._published.append((topic, payload, retain))
        if topic.startswith("setting/") and payload:
            hostname = topic.split("/", 1)[1]
            if hostname in self._devices:
                _deep_merge(self._devices[hostname], json.loads(payload))

    def subscribe(self, topic: str) -> "Subscription":
        return _FakeSubscription(self)


class _FakeSubscription(Subscription):
    def __init__(self, transport: FakeTransport):
        self._t = transport
        self._served = set()

    def next(self, timeout: float):
        # simulates the device replying to the most recent empty-payload
        # query with its full current settings dump, once.
        for topic, payload, _retain in reversed(self._t._published):
            if topic.startswith("setting/") and payload == "" and topic not in self._served:
                hostname = topic.split("/", 1)[1]
                if hostname in self._t._devices:
                    self._served.add(topic)
                    reply = dict(self._t._devices[hostname])
                    reply["hostname"] = hostname
                    return Message(topic=f"setting/{hostname}", payload=json.dumps(reply))
        return None
