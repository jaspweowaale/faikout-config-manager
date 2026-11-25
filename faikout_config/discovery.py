"""MQTT-based device discovery.

Faikout controllers publish standard MQTT discovery (`homeassistant/<domain
>/<object>/config`) for their climate entity on every connect, including a
`dev` block with the device's MAC-derived unique id and an availability/
action topic such as `state/<hostname>`. Listening for these and extracting
the hostname from the topic is how "Discover controllers" finds devices
without needing anything else -- no vendor-specific UDP protocol, no manual
IP scanning, just the same discovery convention the controller already uses
to announce itself to Home Assistant.

The parsing logic here is pure (no network) and unit-tested directly;
DirectMqttProtocol.discover() in mqtt_direct.py does the actual subscribing
and hands its raw (topic, payload) pairs to find_candidates().
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class DiscoveredDevice:
    hostname: str
    mac: str
    source_topic: str


def find_candidates(messages: list[tuple[str, str]], known_hostnames: set[str]) -> list[DiscoveredDevice]:
    """messages: list of (topic, payload) pairs collected from
    `homeassistant/climate/+/config`. Returns one candidate per distinct
    hostname seen, excluding any already in known_hostnames. Malformed
    payloads are skipped, not raised -- discovery must not crash on an
    unrelated device's climate discovery message that happens to share the
    topic pattern but isn't a Faikout unit."""
    seen: dict[str, DiscoveredDevice] = {}
    for topic, payload in messages:
        try:
            parsed = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if not isinstance(parsed, dict):
            continue
        hostname = _extract_hostname(parsed)
        if not hostname or hostname in known_hostnames or hostname in seen:
            continue
        mac = _extract_mac(parsed)
        seen[hostname] = DiscoveredDevice(hostname=hostname, mac=mac, source_topic=topic)
    return list(seen.values())


def _extract_hostname(discovery_payload: dict) -> str:
    for key in ("avty_t", "action_topic", "stat_t", "temp_stat_t"):
        topic = discovery_payload.get(key)
        if isinstance(topic, str) and topic.startswith("state/"):
            return topic[len("state/"):]
    return ""


def _extract_mac(discovery_payload: dict) -> str:
    dev = discovery_payload.get("dev", {})
    ids = dev.get("ids") if isinstance(dev, dict) else None
    if isinstance(ids, list) and ids:
        return str(ids[0])
    return ""
