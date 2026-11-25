"""Named configuration profiles.

A profile is just a named, saved set of plane-D key/value pairs -- e.g.
"standard-baseline" or "office-quiet-hours". Applying a profile to a
selection of devices is implemented as building a SyncRequest and handing it
to sync.apply(), so profiles get every safety property sync already has
(pre-backup, dry-run, readback verify, rollback, audit) for free rather than
needing a second implementation of any of that.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .planes import classify_key, Plane
from .sync import SyncRequest, SyncItem
from .safety import assert_does_not_enable_ota_auto, FirmwareUpdateGuardError


@dataclass
class Profile:
    name: str
    settings: dict  # flat dotted-key -> value, plane-D keys only (enforced on save)
    description: str = ""

    @staticmethod
    def create(name: str, settings: dict, description: str = "") -> "Profile":
        rejected = [k for k in settings if classify_key(k) is not Plane.CONFIG]
        if rejected:
            raise ValueError(f"profile settings must be plane-D config keys, rejected: {rejected}")
        # Defense in depth: sync.apply() would catch an ota.auto=True value
        # anyway at profile-apply time (every write path shares that guard),
        # but failing here, at profile SAVE time, is strictly better --
        # the mistake surfaces immediately rather than only when someone
        # later tries to use the profile.
        nested: dict = {}
        for dotted, value in settings.items():
            parts = dotted.split(".")
            cursor = nested
            for p in parts[:-1]:
                cursor = cursor.setdefault(p, {})
            cursor[parts[-1]] = value
        try:
            assert_does_not_enable_ota_auto(nested)
        except FirmwareUpdateGuardError as e:
            raise ValueError(str(e)) from e
        return Profile(name=name, settings=dict(settings), description=description)

    def to_sync_request(self, target_hostnames: list[str], only_keys: list[str] | None = None) -> SyncRequest:
        """only_keys lets a caller apply a subset of the profile (the
        "applying selected compatible settings from a profile" requirement) --
        default is the whole profile."""
        keys = only_keys if only_keys is not None else list(self.settings.keys())
        items = [SyncItem(key=k, target_hostname=h, new_value=self.settings[k], source_hostname=f"profile:{self.name}")
                 for k in keys for h in target_hostnames if k in self.settings]
        return SyncRequest(items=items)


class ProfileStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, profile: Profile) -> Path:
        path = self.root / f"{profile.name}.json"
        path.write_text(json.dumps({
            "name": profile.name, "settings": profile.settings, "description": profile.description,
        }, sort_keys=True, indent=2))
        return path

    def load(self, name: str) -> Profile:
        path = self.root / f"{name}.json"
        d = json.loads(path.read_text())
        return Profile(**d)

    def list_names(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.json"))
