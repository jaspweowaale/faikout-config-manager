"""Command-line interface -- the settings matrix as a secondary, scriptable
view (the web UI, webui/app.py, is the primary interface).

SAFETY GATE: live writes default OFF, same switch as the web UI
(ENABLE_LIVE_WRITES=true environment variable). Read-only commands (matrix,
drift-check, backup) work regardless.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import planes, diff as diff_mod, backup, profiles as profiles_mod
from .matrix import build_matrix, FilterMode
from .registry import DeviceRegistry
from .appconfig import AppConfig, default_data_root
from .protocol import ProtocolError

LIVE_WRITES_ENABLED = os.environ.get("ENABLE_LIVE_WRITES", "false").strip().lower() in ("1", "true", "yes")


def _build_live_protocol(appconfig: AppConfig):
    settings = appconfig.get_mqtt_settings()
    if not settings.configured:
        raise ProtocolError("MQTT is not configured -- run 'faikout-config mqtt-set --host <broker>' first")
    from .mqtt_direct import DirectMqttProtocol, MqttConfig
    return DirectMqttProtocol(MqttConfig(host=settings.host, port=settings.port,
                                          username=settings.username, password=settings.password,
                                          tls=settings.tls))


def _fetch_all_classified(registry: DeviceRegistry, appconfig: AppConfig, live: bool):
    hostnames = registry.hostnames()
    if live:
        proto = _build_live_protocol(appconfig)
        try:
            raws = {h: proto.read_settings(h).raw for h in hostnames}
        finally:
            proto.close()
    else:
        raws = {h: {} for h in hostnames}  # offline mode has no cache of its own in the CLI
    classified = [planes.classify(h, "unknown", raws.get(h, {})) for h in hostnames]
    planes.classify_firmware_group_consistency(classified)
    return classified


def cmd_matrix(args):
    registry = DeviceRegistry(args.data_dir / "devices.json")
    appconfig = AppConfig(args.data_dir)
    classified = _fetch_all_classified(registry, appconfig, live=args.live)
    m = build_matrix(classified, include_capability=True)
    mode = FilterMode(args.filter)
    _render_matrix(m.hostnames, m.filtered(mode))


def _render_matrix(hostnames, rows):
    from .safety import SENSITIVE_KEYS, REDACTED
    if not hostnames:
        print("No controllers registered. Add one via the web UI's Devices tab, or edit devices.json.")
        return
    col_w = 22
    header = "SETTING".ljust(40) + "PLANE".ljust(14) + "".join(h.ljust(col_w) for h in hostnames)
    print(header)
    print("-" * len(header))
    for row in rows:
        tag = " [DO-NOT-STANDARDIZE]" if row.do_not_standardize else (" [PROTECTED]" if row.is_protected else "")
        line = (row.key + tag).ljust(40) + row.plane.value.ljust(14)
        for h in hostnames:
            v = row.values.get(h, "<absent>")
            if row.key in SENSITIVE_KEYS and v != "<absent>":
                v = REDACTED
            line += str(v).ljust(col_w)
        print(line)


def cmd_devices(args):
    registry = DeviceRegistry(args.data_dir / "devices.json")
    for d in registry.list():
        print(f"{d.hostname}\t{d.label}\t{d.physical_location}\t{d.real_model}\t{d.added_via}")


def cmd_device_add(args):
    from .registry import Device
    registry = DeviceRegistry(args.data_dir / "devices.json")
    registry.add(Device(hostname=args.hostname, label=args.label or "", physical_location=args.location or ""))
    print(f"added {args.hostname}")


def cmd_device_remove(args):
    registry = DeviceRegistry(args.data_dir / "devices.json")
    removed = registry.remove(args.hostname)
    print("removed" if removed else "not found")


def cmd_mqtt_set(args):
    from .appconfig import MqttSettings
    appconfig = AppConfig(args.data_dir)
    current = appconfig.get_mqtt_settings()
    appconfig.set_mqtt_settings(MqttSettings(
        host=args.host or current.host, port=args.port or current.port,
        username=args.username or current.username, password=args.password or current.password,
        tls=args.tls,
    ))
    print("saved")


def cmd_drift_check(args):
    registry = DeviceRegistry(args.data_dir / "devices.json")
    appconfig = AppConfig(args.data_dir)
    classified = _fetch_all_classified(registry, appconfig, live=args.live)
    baseline = diff_mod.Baseline.load(args.data_dir / "baseline.json")
    exceptions = diff_mod.ExceptionSet.load(args.data_dir / "exceptions.json")
    findings = diff_mod.check_drift(classified, baseline, exceptions)
    if not findings:
        print("No drift detected against baseline + approved exceptions.")
        return
    for f in findings:
        marker = "!! CRITICAL !!" if f.severity == "critical" else "warning"
        print(f"[{marker}] {f.hostname}: {f.key} = {f.live_value!r} (expected {f.expected_value!r})")


def cmd_backup(args):
    registry = DeviceRegistry(args.data_dir / "devices.json")
    appconfig = AppConfig(args.data_dir)
    store = backup.SnapshotStore(args.data_dir / "snapshots")
    proto = _build_live_protocol(appconfig)
    import time
    try:
        targets = registry.hostnames() if args.hostname == "all" else [args.hostname]
        for h in targets:
            raw = proto.read_settings(h)
            path = store.save(backup.Snapshot(hostname=h, captured_at=time.time(),
                                               raw_settings=raw.raw, label="manual-backup"))
            print(f"saved {path}")
    finally:
        proto.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="faikout-config", description=__doc__)
    p.add_argument("--data-dir", type=Path, default=default_data_root())
    sub = p.add_subparsers(dest="command", required=True)

    pm = sub.add_parser("matrix", help="Show the settings matrix")
    pm.add_argument("--filter", choices=[m.value for m in FilterMode], default="all")
    pm.add_argument("--live", action="store_true", help="Pull fresh data from the configured broker")
    pm.set_defaults(func=cmd_matrix)

    pd = sub.add_parser("devices", help="List registered controllers")
    pd.set_defaults(func=cmd_devices)

    pda = sub.add_parser("device-add", help="Register a controller")
    pda.add_argument("hostname")
    pda.add_argument("--label")
    pda.add_argument("--location")
    pda.set_defaults(func=cmd_device_add)

    pdr = sub.add_parser("device-remove", help="Remove a controller from the registry")
    pdr.add_argument("hostname")
    pdr.set_defaults(func=cmd_device_remove)

    pms = sub.add_parser("mqtt-set", help="Configure the MQTT broker connection")
    pms.add_argument("--host")
    pms.add_argument("--port", type=int)
    pms.add_argument("--username")
    pms.add_argument("--password")
    pms.add_argument("--tls", action="store_true")
    pms.set_defaults(func=cmd_mqtt_set)

    pdc = sub.add_parser("drift-check", help="Compare live settings against baseline + exceptions")
    pdc.add_argument("--live", action="store_true")
    pdc.set_defaults(func=cmd_drift_check)

    pb = sub.add_parser("backup", help="Read-only: snapshot current settings for one or all controllers")
    pb.add_argument("hostname")
    pb.set_defaults(func=cmd_backup)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
