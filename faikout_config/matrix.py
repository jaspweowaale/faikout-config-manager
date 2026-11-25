"""The settings matrix -- the primary management interface.

Rows = settings (dotted-path keys), columns = controllers, per explicit
owner instruction. This module builds the matrix as plain data
(MatrixRow objects); cli.py is responsible for rendering it as text, and a
future web UI could render the exact same data as an HTML table without any
change here -- that separation is the "reusable for future appliance
integrations" requirement in practice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .planes import ClassifiedSettings, Plane, DO_NOT_STANDARDIZE_YET, FIRMWARE_UPDATE_KEYS

_ABSENT = "<absent>"


class FilterMode(Enum):
    ALL = "all"
    DIFFERENCES_ONLY = "differences_only"
    COMMON = "common"
    PROTECTED = "protected"          # firmware-update-protection keys (ota.auto etc.)
    HARDWARE_DEPENDENT = "hardware_dependent"  # plane B + plane D_conditional


@dataclass
class MatrixRow:
    key: str
    plane: Plane
    values: dict[str, object] = field(default_factory=dict)  # hostname -> value or _ABSENT
    is_uniform: bool = True
    do_not_standardize: bool = False
    is_protected: bool = False


@dataclass
class SettingsMatrix:
    hostnames: list[str]
    rows: list[MatrixRow]

    def filtered(self, mode: FilterMode) -> list[MatrixRow]:
        if mode is FilterMode.ALL:
            return list(self.rows)
        if mode is FilterMode.DIFFERENCES_ONLY:
            return [r for r in self.rows if not r.is_uniform]
        if mode is FilterMode.COMMON:
            return [r for r in self.rows if r.is_uniform]
        if mode is FilterMode.PROTECTED:
            return [r for r in self.rows if r.is_protected]
        if mode is FilterMode.HARDWARE_DEPENDENT:
            return [r for r in self.rows
                    if r.plane in (Plane.CAPABILITY, Plane.CONFIG_CONDITIONAL)]
        raise ValueError(mode)


def build_matrix(devices: list[ClassifiedSettings], include_capability: bool = True,
                  include_identity_wiring: bool = False) -> SettingsMatrix:
    """include_identity_wiring defaults to False: plane C is same-device-only
    by design and cluttering the primary matrix with wiring pins invites
    someone to select a plane-C row for sync, which sync.py would then have
    to refuse anyway -- better to keep it out of the default view entirely
    and surface it via a separate `identity-wiring` matrix on request."""
    hostnames = [d.hostname for d in devices]
    rows: dict[str, MatrixRow] = {}

    def ensure_row(key: str, plane: Plane) -> MatrixRow:
        if key not in rows:
            rows[key] = MatrixRow(
                key=key, plane=plane,
                values={h: _ABSENT for h in hostnames},
                do_not_standardize=key in DO_NOT_STANDARDIZE_YET,
                is_protected=key in FIRMWARE_UPDATE_KEYS,
            )
        return rows[key]

    for d in devices:
        source_planes = [(d.config, Plane.CONFIG), (d.config_conditional, Plane.CONFIG_CONDITIONAL)]
        if include_capability:
            source_planes.append((d.capability, Plane.CAPABILITY))
        if include_identity_wiring:
            source_planes.append((d.identity_wiring, Plane.IDENTITY_WIRING))
        for source, plane in source_planes:
            for key, value in source.items():
                row = ensure_row(key, plane)
                row.values[d.hostname] = value

    for row in rows.values():
        present_values = [v for v in row.values.values() if v is not _ABSENT]
        import json as _json
        reprs = {_json.dumps(v, sort_keys=True) if not isinstance(v, str) else v for v in present_values}
        all_present = all(v is not _ABSENT for v in row.values.values())
        row.is_uniform = all_present and len(reprs) <= 1

    ordered = sorted(rows.values(), key=lambda r: (r.plane.value, r.key))
    return SettingsMatrix(hostnames=hostnames, rows=ordered)
