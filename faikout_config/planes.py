"""Plane classification engine.

Four planes -- configuration must never be confused with live state or
detected hardware capability, or sync/backup/restore decisions end up
silently wrong:

  A - live operating state   -- out of scope entirely, not represented here.
  B - detected capability     -- the "no.*" map. Read-only reference, never a
                                  sync/backup/restore target.
  C - device-identity/wiring  -- hostname/uart/i2c/tx/rx/factorygpio/model.
                                  Same-device backup/restore only, NEVER a
                                  cross-device sync target.
  D - true configuration      -- the actual sync/backup/restore/baseline
                                  surface.

Within plane D there is a further, data-driven split: some D-shaped keys can
be present on one unit and absent on another unit running the BYTE-IDENTICAL
firmware build (observed directly on a real multi-unit fleet during this
project's development). That can't be a firmware-version difference, and
manual settings drift is possible but not provable either way from a
settings dump alone -- the safer default is to treat any such key as
"firmware-group-inconsistent" and exclude it from sync-eligibility and
baseline-drift-alerting until a human confirms which explanation applies.
This is implemented as a general detection rule (classify_firmware_group_
consistency), not a hardcoded list of today's specific keys, so it keeps
working if the fleet's firmware or hardware changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Plane(Enum):
    LIVE_STATE = "A_live_state"          # out of scope, never populated here
    CAPABILITY = "B_capability"          # read-only reference
    IDENTITY_WIRING = "C_identity_wiring"  # same-device only
    CONFIG = "D_config"                  # sync/backup/restore/baseline surface
    CONFIG_CONDITIONAL = "D_conditional"  # D-shaped but firmware-group-inconsistent -- excluded from sync/drift by default


# Top-level keys (or dotted paths) known, from direct investigation, to be
# capability (plane B) or identity/wiring (plane C). Anything not listed here
# defaults to plane D and is then re-checked by classify_firmware_group_
# consistency -- new/unknown keys are NOT silently trusted as safe to sync.
CAPABILITY_KEYS = {"no"}  # the whole "no" sub-object is plane B

IDENTITY_WIRING_KEYS = {
    "hostname", "uart", "i2c", "tx", "rx", "factorygpio", "model",
    "wifi.ip", "wifi.gw", "wifi.dns",  # per-device static network identity,
                                       # NOT the shared SSID -- see wifi.ssid
}

# Keys explicitly flagged as "do not standardize yet" per owner instruction --
# present in plane D for inventory/display purposes, but excluded from
# automatic baseline-compliance actions (sync suggestions, drift alerts)
# until a human clears the flag. Distinct from CONFIG_CONDITIONAL: this is a
# human policy hold, not an automated "we don't understand this yet" finding.
DO_NOT_STANDARDIZE_YET = {"region"}

# Settings this tool must never offer as sync/restore/profile targets under
# any circumstances, regardless of plane classification -- the firmware-
# update protection backstop. ota.auto may only ever be driven toward
# "disabled"; enabling it requires a separate, explicit, owner-approved path
# that this package does not implement (see sync.py SyncPlan.validate()).
FIRMWARE_UPDATE_KEYS = {"ota.auto"}


def _flatten(d: dict, prefix: str = "") -> dict:
    """Flatten a nested settings dict to dotted-path keys, e.g.
    {"ota": {"auto": False}} -> {"ota.auto": False}. Leaves lists/scalars
    as-is; a dict is only flattened one more level if it's a dict itself."""
    out = {}
    for k, v in d.items():
        path = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, path))
        else:
            out[path] = v
    return out


def classify_key(dotted_key: str) -> Plane:
    top = dotted_key.split(".", 1)[0]
    if top in CAPABILITY_KEYS or dotted_key in CAPABILITY_KEYS:
        return Plane.CAPABILITY
    if dotted_key in IDENTITY_WIRING_KEYS or top in IDENTITY_WIRING_KEYS:
        return Plane.IDENTITY_WIRING
    return Plane.CONFIG  # provisional; see classify_firmware_group_consistency


@dataclass
class ClassifiedSettings:
    """One device's settings, flattened and classified into planes.

    all_flat retains every key's actual current value regardless of which
    plane bucket it landed in -- baseline/drift-checking (diff.py) reads
    from here, not from `config`, because "does reality match the owner's
    stated policy" is a different question from "is this safe to treat as
    uniform/sync-eligible." A key that gets moved to config_conditional for
    sync-safety reasons must not thereby become invisible to drift
    detection -- that would silently defeat the one thing baseline checking
    exists to catch.
    """
    hostname: str
    firmware_version: str
    capability: dict = field(default_factory=dict)   # plane B, read-only
    identity_wiring: dict = field(default_factory=dict)  # plane C
    config: dict = field(default_factory=dict)        # plane D (provisional)
    config_conditional: dict = field(default_factory=dict)  # plane D, firmware-group-inconsistent
    all_flat: dict = field(default_factory=dict)       # every key, any plane -- for diff.py only


def classify(hostname: str, firmware_version: str, raw_settings: dict) -> ClassifiedSettings:
    flat = _flatten(raw_settings)
    result = ClassifiedSettings(hostname=hostname, firmware_version=firmware_version)
    for key, value in flat.items():
        if key == "hostname":
            continue  # already carried as ClassifiedSettings.hostname
        result.all_flat[key] = value
        plane = classify_key(key)
        if plane is Plane.CAPABILITY:
            result.capability[key] = value
        elif plane is Plane.IDENTITY_WIRING:
            result.identity_wiring[key] = value
        else:
            result.config[key] = value  # provisional D; reclassified below
    return result


def classify_firmware_group_consistency(all_devices: list[ClassifiedSettings]) -> None:
    """Mutates each device's `config`/`config_conditional` dicts in place:
    any plane-D key whose presence or value is NOT uniform across all
    devices sharing the SAME firmware_version is moved into
    config_conditional on every device in that firmware group (even the ones
    where the value happens to match the majority) -- the inconsistency
    itself is the signal, not which side of it a given device is on.

    Also removed from scope: nothing is a true comparison if there's only one
    unit on that firmware version -- a single-member group can't be internally
    inconsistent by definition, so its keys stay in `config` (still subject to
    cross-firmware baseline/diff logic elsewhere, just not this specific
    same-firmware-group check).
    """
    by_fw: dict[str, list[ClassifiedSettings]] = {}
    for d in all_devices:
        by_fw.setdefault(d.firmware_version, []).append(d)

    for fw, members in by_fw.items():
        if len(members) < 2:
            continue
        all_keys = set()
        for m in members:
            all_keys |= set(m.config.keys())
        for key in all_keys:
            if key in FIRMWARE_UPDATE_KEYS:
                # Never move a firmware-update-protection key out of plane D,
                # even if it's inconsistent across the fleet -- that
                # inconsistency (e.g. one unit's ota.auto re-enabled while
                # its sibling's is off) IS the signal drift-checking exists
                # to catch, not noise to explain away as hardware-
                # conditional. Moving it to config_conditional would hide
                # exactly the finding this whole system was built to never
                # miss again.
                continue
            values = []
            present_flags = []
            for m in members:
                present = key in m.config
                present_flags.append(present)
                values.append(m.config.get(key, _ABSENT))
            uniform_presence = len(set(present_flags)) == 1
            uniform_value = len({_safe_repr(v) for v in values}) == 1
            if not (uniform_presence and uniform_value):
                for m in members:
                    if key in m.config:
                        m.config_conditional[key] = m.config.pop(key)


_ABSENT = object()


def _safe_repr(v: Any) -> str:
    # lists/dicts aren't hashable; use a stable string repr for the
    # uniformity check instead of putting raw values in a set.
    import json as _json
    try:
        return _json.dumps(v, sort_keys=True)
    except TypeError:
        return repr(v)
