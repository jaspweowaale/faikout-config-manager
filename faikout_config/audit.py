"""Append-only audit log for every configuration change this tool makes
(or would make, for a dry run -- dry runs are logged too, tagged as such,
since "what did the preview say" is itself worth keeping).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

from .safety import redact


@dataclass
class AuditEntry:
    timestamp: float
    operation: str          # "sync" | "restore" | "profile_apply" | "dry_run"
    hostname: str
    key: str
    old_value: object
    new_value: object
    pre_change_backup: str   # path to the snapshot taken before this change
    verified: bool            # did a post-write readback confirm new_value?
    rolled_back: bool = False
    note: str = ""


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, entry: AuditEntry) -> None:
        d = asdict(entry)
        # redact() expects flat dotted-key dicts; old/new_value here are
        # single values, not a settings dict, so only the key name itself
        # (not its value) needs the sensitive check.
        from .safety import SENSITIVE_KEYS, REDACTED
        if d["key"] in SENSITIVE_KEYS:
            d["old_value"] = REDACTED
            d["new_value"] = REDACTED
        with self.path.open("a") as f:
            f.write(json.dumps(d, sort_keys=True) + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]
