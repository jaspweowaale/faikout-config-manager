"""Backup / restore snapshots.

A snapshot is the raw, unflattened settings dict for one device at one point
in time, plus enough metadata to know what it is later. Snapshots are the
foundation for: manual backups, the automatic pre-change backup every sync/
restore/profile-apply takes, and rollback (which is just "restore the pre-
change snapshot").

Security: snapshot files are written with owner-only permissions (0600) and
the directory with 0700 -- these files contain MQTT/WiFi identifiers that
count as sensitive per safety.py, and should be treated like any other
credential-adjacent file on disk.
"""
from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class Snapshot:
    hostname: str
    captured_at: float
    raw_settings: dict
    label: str = ""  # e.g. "pre-sync-2026-01-01T10:00:00", "manual-backup"

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, indent=2)

    @staticmethod
    def from_json(text: str) -> "Snapshot":
        d = json.loads(text)
        return Snapshot(**d)


class SnapshotStore:
    """Filesystem-backed snapshot store. One file per snapshot, named so
    history is browsable without an index: <hostname>__<ts>__<label>.json
    -- timestamp BEFORE label deliberately, so lexicographic filename sort
    (used by list_for/latest_for) is always chronological regardless of
    what two labels happen to compare as alphabetically."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, stat.S_IRWXU)  # 0700
        except OSError:
            pass  # best-effort on platforms where this doesn't apply

    def save(self, snapshot: Snapshot) -> Path:
        # microsecond precision -- two snapshots in the same multi-target
        # sync batch can otherwise land in the same wall-clock second.
        ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime(snapshot.captured_at)) + \
            f".{int((snapshot.captured_at % 1) * 1_000_000):06d}Z"
        safe_label = snapshot.label.replace("/", "_").replace(" ", "_") or "backup"
        path = self.root / f"{snapshot.hostname}__{ts}__{safe_label}.json"
        path.write_text(snapshot.to_json())
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass
        return path

    def list_for(self, hostname: str) -> list[Path]:
        return sorted(self.root.glob(f"{hostname}__*.json"))

    def latest_for(self, hostname: str) -> Snapshot | None:
        files = self.list_for(hostname)
        if not files:
            return None
        return Snapshot.from_json(files[-1].read_text())

    def load(self, path: Path) -> Snapshot:
        return Snapshot.from_json(path.read_text())
