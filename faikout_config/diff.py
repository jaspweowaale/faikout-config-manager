"""Baseline comparison and drift detection.

A Baseline states the desired value for plane-D keys. An ExceptionSet lists
per-device approved deviations from that baseline -- not drift, by
definition. Drift = a plane-D key whose live value differs from
(baseline, unless an approved exception says otherwise) OR differs from its
own last snapshot without a matching baseline/exception change. Plane B
(capability) and plane D_conditional (firmware-group-inconsistent) keys are
never drift-checked -- there's no "desired value" for something that's
either real hardware or not-yet-understood by design.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .planes import ClassifiedSettings, FIRMWARE_UPDATE_KEYS


@dataclass
class Baseline:
    desired: dict  # flat dotted-key -> desired value, plane-D keys only

    @staticmethod
    def load(path: Path) -> "Baseline":
        if not path.exists():
            return Baseline(desired={})
        return Baseline(desired=json.loads(path.read_text()))

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.desired, sort_keys=True, indent=2))


@dataclass
class ExceptionSet:
    # hostname -> {key: approved_value}
    per_device: dict = field(default_factory=dict)

    @staticmethod
    def load(path: Path) -> "ExceptionSet":
        if not path.exists():
            return ExceptionSet(per_device={})
        return ExceptionSet(per_device=json.loads(path.read_text()))

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.per_device, sort_keys=True, indent=2))

    def approved_value(self, hostname: str, key: str):
        return self.per_device.get(hostname, {}).get(key, _NO_EXCEPTION)


_NO_EXCEPTION = object()


@dataclass
class DriftFinding:
    hostname: str
    key: str
    live_value: object
    expected_value: object
    severity: str  # "critical" (firmware-update key) | "warning"


def check_drift(devices: list[ClassifiedSettings], baseline: Baseline,
                 exceptions: ExceptionSet) -> list[DriftFinding]:
    findings: list[DriftFinding] = []
    for d in devices:
        for key, desired in baseline.desired.items():
            # read from all_flat, not config: a key that's been excluded
            # into config_conditional for sync-safety reasons (see planes.py)
            # must still be checked against an explicit baseline -- that's a
            # different question, and must not go blind just because the
            # fleet-wide consistency heuristic flagged it.
            live = d.all_flat.get(key, _ABSENT)
            if live is _ABSENT:
                continue  # key not present on this device at all -- not drift,
                          # that's a firmware-schema fact, not a config change
            approved = exceptions.approved_value(d.hostname, key)
            expected = desired if approved is _NO_EXCEPTION else approved
            if _differs(live, expected):
                findings.append(DriftFinding(
                    hostname=d.hostname, key=key, live_value=live, expected_value=expected,
                    severity="critical" if key in FIRMWARE_UPDATE_KEYS else "warning",
                ))
    # sort critical first -- ota.auto drift is always the headline finding
    findings.sort(key=lambda f: (0 if f.severity == "critical" else 1, f.hostname, f.key))
    return findings


_ABSENT = object()


def _differs(a, b) -> bool:
    try:
        return json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True)
    except TypeError:
        return a != b
