"""Device registry -- user-managed, not hardcoded.

Replaces a fixed list of controllers with a small JSON-backed store the user
builds up via the web UI's Device Management page (add / remove / rename),
or via MQTT discovery, or by hand-editing the JSON file directly. Starts
empty on a fresh install.

Fields are intentionally optional beyond `hostname`: unlike the vendor's own
nameplate data (which a previous, closed deployment of this tool had
available from physically reading each unit's label), this project has no
way to auto-detect a connected AC's real model/capacity -- those fields are
free-text labels the user can fill in if they want the identity header to
be meaningful, not something this project claims to verify.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict, field
from pathlib import Path


@dataclass
class Device:
    hostname: str                       # required, must match the Faikout's own MQTT hostname
    mac: str = ""                       # optional, usually learned via discovery
    label: str = ""                     # optional user-facing name, e.g. "Living room AC"
    physical_location: str = ""         # optional, user-entered
    real_model: str = ""                # optional, user-entered -- not auto-verified
    real_capacity_hp: float | None = None
    real_capacity_kw: float | None = None
    notes: str = ""
    added_via: str = "manual"           # "manual" | "discovery"

    def to_dict(self) -> dict:
        return asdict(self)


class DeviceRegistry:
    """Thread-safe, file-backed. One JSON file, one dict entry per
    hostname. No device identity data is ever invented -- an empty/missing
    optional field just stays empty; nothing here guesses a model or
    capacity from the hostname (the opposite of what this project's own
    history found to be a bad idea)."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write({})

    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text() or "{}")

    def _write(self, data: dict) -> None:
        self._path.write_text(json.dumps(data, indent=2, sort_keys=True))

    def list(self) -> list[Device]:
        with self._lock:
            data = self._read()
        return [Device(**v) for v in data.values()]

    def hostnames(self) -> list[str]:
        return sorted(d.hostname for d in self.list())

    def get(self, hostname: str) -> Device | None:
        with self._lock:
            data = self._read()
        v = data.get(hostname)
        return Device(**v) if v else None

    def add(self, device: Device, allow_overwrite: bool = False) -> None:
        with self._lock:
            data = self._read()
            if device.hostname in data and not allow_overwrite:
                raise ValueError(f"device '{device.hostname}' is already registered")
            data[device.hostname] = device.to_dict()
            self._write(data)

    def update(self, hostname: str, **fields) -> Device:
        with self._lock:
            data = self._read()
            if hostname not in data:
                raise KeyError(f"no such device: {hostname}")
            data[hostname].update(fields)
            self._write(data)
            return Device(**data[hostname])

    def remove(self, hostname: str) -> bool:
        with self._lock:
            data = self._read()
            if hostname not in data:
                return False
            del data[hostname]
            self._write(data)
            return True
