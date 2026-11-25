"""Synchronization planning and execution.

Selection model matches the owner's requirement directly: a user can select
individual cells, whole settings rows (one key, many devices), or whole
controller columns (one device, many keys from a source) -- all three reduce
to the same underlying list of (key, target_hostname, new_value) tuples
before validation, so there's exactly one compatibility/safety path for all
three selection styles, not three.

Every apply() goes through, in this fixed order: validate -> dry_run preview
(always computed, even for a real apply, so the result can report "what we
previewed" alongside "what actually happened") -> pre-change backup -> write
-> readback verify -> rollback-on-mismatch -> audit log. No step is skippable
by a caller; there is no "fast path" that writes without a prior backup.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .planes import ClassifiedSettings, Plane, classify_key, FIRMWARE_UPDATE_KEYS
from .protocol import DeviceConfigProtocol
from .backup import SnapshotStore, Snapshot
from .audit import AuditLog, AuditEntry
from .safety import assert_does_not_enable_ota_auto, FirmwareUpdateGuardError


class SyncValidationError(Exception):
    pass


@dataclass(frozen=True)
class SyncItem:
    key: str           # dotted path, e.g. "ota.host"
    target_hostname: str
    new_value: object
    source_hostname: str = ""  # for audit/preview readability only


@dataclass
class SyncRequest:
    items: list[SyncItem] = field(default_factory=list)

    @staticmethod
    def select_cells(cells: list[tuple[str, str, object]], source_hostname: str = "") -> "SyncRequest":
        """cells: list of (key, target_hostname, new_value)."""
        return SyncRequest(items=[SyncItem(k, h, v, source_hostname) for k, h, v in cells])

    @staticmethod
    def select_row_to_devices(key: str, value: object, target_hostnames: list[str],
                               source_hostname: str = "") -> "SyncRequest":
        return SyncRequest(items=[SyncItem(key, h, value, source_hostname) for h in target_hostnames])

    @staticmethod
    def select_column_from_device(source: ClassifiedSettings, keys: list[str],
                                   target_hostnames: list[str]) -> "SyncRequest":
        items = []
        for key in keys:
            if key not in source.config:
                continue  # source doesn't have this key -- nothing to copy
            value = source.config[key]
            for h in target_hostnames:
                if h == source.hostname:
                    continue
                items.append(SyncItem(key, h, value, source.hostname))
        return SyncRequest(items=items)


def validate(request: SyncRequest, devices_by_hostname: dict[str, ClassifiedSettings],
             allow_conditional_override: bool = False) -> list[str]:
    """Returns a list of rejection reasons; empty list means the request is
    fully compatible. Never raises -- callers decide whether any rejection
    is fatal (apply() always treats a non-empty list as fatal)."""
    problems: list[str] = []
    # Global, not per-device: a key is treated as conditional for THIS
    # request if it's flagged conditional on ANY device in the fleet, not
    # just the specific target/source involved in one item. A per-device
    # check misses the common case of pushing a brand-new value for a key
    # the target never had at all (nothing to flag on the target side) for
    # a key that's known to be hardware-conditional elsewhere in the fleet
    # (e.g. only on the source, or on a sibling neither side is).
    conditional_keys_anywhere = {
        key for d in devices_by_hostname.values() for key in d.config_conditional
    }
    for item in request.items:
        plane = classify_key(item.key)
        if plane is Plane.CAPABILITY:
            problems.append(f"{item.key} -> {item.target_hostname}: refused, this is a detected "
                             f"hardware capability flag (plane B), never a sync target")
        if plane is Plane.IDENTITY_WIRING:
            problems.append(f"{item.key} -> {item.target_hostname}: refused, this is device-"
                             f"identity/wiring (plane C), cross-device sync is never permitted")
        if item.key in conditional_keys_anywhere and not allow_conditional_override:
            problems.append(f"{item.key} -> {item.target_hostname}: refused, this key is firmware-"
                             f"group-inconsistent (plane D-conditional) -- presence/value differs "
                             f"between units on identical firmware, meaning we don't yet know if "
                             f"it's hardware-conditional or genuine drift; pass "
                             f"allow_conditional_override=True after a human confirms it's safe")
        patch = _nest({item.key: item.new_value})
        try:
            assert_does_not_enable_ota_auto(patch)
        except FirmwareUpdateGuardError as e:
            problems.append(str(e))
    return problems


@dataclass
class DryRunRow:
    key: str
    target_hostname: str
    current_value: object
    new_value: object
    would_change: bool
    source_hostname: str = ""


@dataclass
class DryRunPreview:
    rows: list[DryRunRow]
    problems: list[str]

    @property
    def is_clean(self) -> bool:
        return not self.problems


def dry_run(request: SyncRequest, protocol: DeviceConfigProtocol,
            devices_by_hostname: dict[str, ClassifiedSettings],
            allow_conditional_override: bool = False) -> DryRunPreview:
    """Read-only. Reads current live values (fresh, not from the cached
    `devices_by_hostname` classification, so the preview reflects right now)
    to show exactly what would change on each controller."""
    problems = validate(request, devices_by_hostname, allow_conditional_override)
    rows = []
    for item in request.items:
        current = _get_dotted(protocol.read_settings(item.target_hostname).raw, item.key)
        rows.append(DryRunRow(
            key=item.key, target_hostname=item.target_hostname,
            current_value=current, new_value=item.new_value,
            would_change=_differs(current, item.new_value),
            source_hostname=item.source_hostname,
        ))
    return DryRunPreview(rows=rows, problems=problems)


@dataclass
class SyncResult:
    preview: DryRunPreview
    applied: bool
    audit_entries: list[AuditEntry] = field(default_factory=list)


def apply(request: SyncRequest, protocol: DeviceConfigProtocol,
          devices_by_hostname: dict[str, ClassifiedSettings],
          snapshots: SnapshotStore, audit: AuditLog,
          allow_conditional_override: bool = False) -> SyncResult:
    preview = dry_run(request, protocol, devices_by_hostname, allow_conditional_override)
    if not preview.is_clean:
        raise SyncValidationError("; ".join(preview.problems))

    # one pre-change backup per distinct target device, taken before ANY
    # write in this batch -- never partially backed up.
    backup_paths: dict[str, str] = {}
    for hostname in {row.target_hostname for row in preview.rows}:
        raw = protocol.read_settings(hostname)
        path = snapshots.save(Snapshot(hostname=hostname, captured_at=time.time(),
                                        raw_settings=raw.raw, label="pre-sync"))
        backup_paths[hostname] = str(path)

    entries: list[AuditEntry] = []
    for row in preview.rows:
        if not row.would_change:
            continue  # nothing to do, nothing to audit as a change
        patch = _nest({row.key: row.new_value})
        protocol.write_setting(row.target_hostname, patch)
        after = protocol.read_settings(row.target_hostname)
        readback_value = _get_dotted(after.raw, row.key)
        verified = not _differs(readback_value, row.new_value)
        rolled_back = False
        if not verified:
            rollback_patch = _nest({row.key: row.current_value})
            protocol.write_setting(row.target_hostname, rollback_patch)
            confirm = protocol.read_settings(row.target_hostname)
            rolled_back = not _differs(_get_dotted(confirm.raw, row.key), row.current_value)
        entry = AuditEntry(
            timestamp=time.time(), operation="sync", hostname=row.target_hostname,
            key=row.key, old_value=row.current_value, new_value=row.new_value,
            pre_change_backup=backup_paths[row.target_hostname],
            verified=verified, rolled_back=rolled_back,
            note="" if verified else "readback mismatch -- rollback attempted, see rolled_back",
        )
        audit.record(entry)
        entries.append(entry)

    return SyncResult(preview=preview, applied=True, audit_entries=entries)


def _nest(flat: dict) -> dict:
    """{"ota.auto": 0} -> {"ota": {"auto": 0}}"""
    out: dict = {}
    for dotted, value in flat.items():
        parts = dotted.split(".")
        cursor = out
        for p in parts[:-1]:
            cursor = cursor.setdefault(p, {})
        cursor[parts[-1]] = value
    return out


def _get_dotted(d: dict, dotted: str):
    cursor = d
    for p in dotted.split("."):
        if not isinstance(cursor, dict) or p not in cursor:
            return _ABSENT
        cursor = cursor[p]
    return cursor


_ABSENT = object()


def _differs(a, b) -> bool:
    import json
    try:
        return json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True)
    except TypeError:
        return a != b
