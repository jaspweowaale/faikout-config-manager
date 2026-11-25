"""Restore from a snapshot -- complete (same device) or partial (cross-device).

Two genuinely different operations, kept explicit rather than inferred, per
the design doc's own warning about conflating them:

  - restore_same_device: replays a past snapshot of a device back onto
    ITSELF. Safe to include every key, any plane, because source and target
    are the same physical unit -- this is just "undo my changes."

  - restore_cross_device: applies ONLY the plane-D (non-conditional) subset
    of one device's snapshot onto a DIFFERENT device. Plane B/C keys are
    dropped unconditionally, never offered, regardless of what's in the
    source snapshot file -- restoring one unit's wiring pins onto another
    unit is exactly the mistake this whole package exists to prevent.
"""
from __future__ import annotations

from .backup import Snapshot
from .planes import classify, classify_firmware_group_consistency, Plane, ClassifiedSettings
from .sync import SyncRequest, SyncItem


def restore_same_device(snapshot: Snapshot, only_keys: list[str] | None = None) -> SyncRequest:
    flat = _flatten(snapshot.raw_settings)
    keys = only_keys if only_keys is not None else list(flat.keys())
    items = [SyncItem(key=k, target_hostname=snapshot.hostname, new_value=flat[k],
                      source_hostname=snapshot.hostname)
             for k in keys if k in flat and k != "hostname"]
    return SyncRequest(items=items)


def restore_cross_device(snapshot: Snapshot, target_hostnames: list[str],
                          firmware_version: str = "unknown",
                          other_devices_same_firmware: list[ClassifiedSettings] | None = None,
                          only_keys: list[str] | None = None) -> SyncRequest:
    """firmware_version is required for a meaningful firmware-group-
    inconsistency check -- snapshots don't carry it themselves (it's plane-A
    live-state data, not part of the settings dump), so the caller must
    supply the value that was true when the snapshot was taken (typically
    looked up from the dataset-comparison firmware_versions.json or a fresh
    plane-A read at snapshot time). Left as "unknown" by default so this
    function still works standalone; with the default, the snapshot forms
    its own single-member firmware group and the inconsistency check is
    effectively skipped for it -- NOT the same as confirming it's safe,
    just a degraded-but-functional fallback. other_devices_same_firmware, if
    given, lets the check run against real sibling data; omitting it means
    eligibility is computed locally only, with validate() as the remaining
    safety net (see tests for both paths)."""
    classified = classify(snapshot.hostname, firmware_version, snapshot.raw_settings)
    if other_devices_same_firmware:
        classify_firmware_group_consistency([classified, *other_devices_same_firmware])
    eligible = classified.config  # plane D, non-conditional only -- B/C/conditional excluded by construction
    keys = only_keys if only_keys is not None else list(eligible.keys())
    items = [SyncItem(key=k, target_hostname=h, new_value=eligible[k],
                      source_hostname=snapshot.hostname)
             for k in keys if k in eligible for h in target_hostnames if h != snapshot.hostname]
    return SyncRequest(items=items)


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        path = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, path))
        else:
            out[path] = v
    return out
