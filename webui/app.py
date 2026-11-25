"""Faikout Configuration Manager -- web interface (community edition).

Safety gates:
  - Live writes (sync/restore/profile-apply) default OFF. Set the
    environment variable ENABLE_LIVE_WRITES=true to enable them -- this is
    a deliberate, explicit, single switch (not a per-action toggle), so
    "writes are possible at all" is always a conscious decision, not an
    accident. See SECURITY.md before enabling this on hardware you care
    about.
  - Every route requires HTTP Basic Auth (one `before_request` hook, not
    per-route, so a new route can't ship unauthenticated by omission).
    Credentials are generated on first run; see README.md / SECURITY.md.
"""
from __future__ import annotations

import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, request, Response

from faikout_config import planes, diff as diff_mod, backup as backup_mod, audit as audit_mod
from faikout_config import profiles as profiles_mod, sync as sync_mod, restore as restore_mod
from faikout_config.matrix import build_matrix, FilterMode
from faikout_config.reliability import ReliableSettingsClient
from faikout_config.protocol import DeviceConfigProtocol, ProtocolError
from faikout_config.registry import DeviceRegistry, Device
from faikout_config.appconfig import AppConfig, MqttSettings, default_data_root, load_or_create_web_credentials
from webui.descriptions import describe

import os

LIVE_WRITES_ENABLED = os.environ.get("ENABLE_LIVE_WRITES", "false").strip().lower() in ("1", "true", "yes")

DATA_ROOT = default_data_root()
_registry = DeviceRegistry(DATA_ROOT / "devices.json")
_appconfig = AppConfig(DATA_ROOT)
_snapshots = backup_mod.SnapshotStore(DATA_ROOT / "snapshots")
_profiles = profiles_mod.ProfileStore(DATA_ROOT / "profiles")
_audit = audit_mod.AuditLog(DATA_ROOT / "audit.jsonl")
_CREDS = load_or_create_web_credentials(DATA_ROOT)


class _UnconfiguredProtocol(DeviceConfigProtocol):
    """Used until the user configures an MQTT connection -- every read/
    write fails with a clear, actionable message instead of a confusing
    connection-refused error from a half-initialized client."""
    def read_settings(self, hostname):
        raise ProtocolError("MQTT is not configured yet -- open MQTT Settings and connect a broker first")

    def write_setting(self, hostname, patch):
        raise ProtocolError("MQTT is not configured yet -- open MQTT Settings and connect a broker first")


_client = ReliableSettingsClient(_UnconfiguredProtocol(), min_interval_s=30.0, fresh_window_s=300.0)


def _build_live_protocol():
    mqtt_settings = _appconfig.get_mqtt_settings()
    if not mqtt_settings.configured:
        raise ProtocolError("MQTT is not configured yet -- open MQTT Settings and connect a broker first")
    from faikout_config.mqtt_direct import DirectMqttProtocol, MqttConfig
    return DirectMqttProtocol(MqttConfig(
        host=mqtt_settings.host, port=mqtt_settings.port,
        username=mqtt_settings.username, password=mqtt_settings.password, tls=mqtt_settings.tls,
    ))


app = Flask(__name__)


@app.before_request
def _enforce_auth():
    auth = request.authorization
    if not auth or not secrets.compare_digest(auth.username, _CREDS["username"]) \
            or not secrets.compare_digest(auth.password, _CREDS["password"]):
        return Response("Authentication required.", 401,
                         {"WWW-Authenticate": 'Basic realm="Faikout Config Manager"'})


def _classified_from_cache():
    hostnames = _registry.hostnames()
    result = []
    for h in hostnames:
        entry = _client.get_cached(h)
        result.append(planes.classify(h, "unknown", entry.raw or {}))
    planes.classify_firmware_group_consistency(result)
    return {c.hostname: c for c in result}


def _connection_status():
    now = time.monotonic()
    out = {}
    for h in _registry.hostnames():
        entry = _client.get_cached(h)
        status = entry.status(now, _client.fresh_window_s)
        out[h] = {
            "status": status.value,
            "last_success_age_s": None if entry.last_success_at is None else round(now - entry.last_success_at, 1),
            "last_error": entry.last_error,
            "has_data": entry.raw is not None,
        }
    return out


# ---------- pages ----------

@app.route("/")
def index():
    return Response((Path(__file__).parent / "index.html").read_text(), mimetype="text/html")


@app.route("/api/status")
def api_status():
    return jsonify({"live_writes_enabled": LIVE_WRITES_ENABLED, "version": "0.1.0"})


@app.route("/app.js")
def app_js():
    return Response((Path(__file__).parent / "app.js").read_text(), mimetype="application/javascript")


@app.route("/style.css")
def style_css():
    return Response((Path(__file__).parent / "style.css").read_text(), mimetype="text/css")


# ---------- device management ----------

@app.route("/api/devices", methods=["GET", "POST"])
def api_devices():
    if request.method == "GET":
        return jsonify([d.to_dict() for d in _registry.list()])
    body = request.get_json(force=True)
    hostname = (body.get("hostname") or "").strip()
    if not hostname:
        return jsonify({"error": "hostname is required"}), 400
    device = Device(
        hostname=hostname, mac=body.get("mac", ""), label=body.get("label", ""),
        physical_location=body.get("physical_location", ""), real_model=body.get("real_model", ""),
        real_capacity_hp=body.get("real_capacity_hp"), real_capacity_kw=body.get("real_capacity_kw"),
        notes=body.get("notes", ""), added_via=body.get("added_via", "manual"),
    )
    try:
        _registry.add(device)
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify(device.to_dict())


@app.route("/api/devices/<hostname>", methods=["PATCH", "DELETE"])
def api_device_detail(hostname):
    if request.method == "DELETE":
        removed = _registry.remove(hostname)
        _client.forget(hostname)
        if not removed:
            return jsonify({"error": "no such device"}), 404
        return jsonify({"removed": hostname})
    body = request.get_json(force=True)
    try:
        updated = _registry.update(hostname, **{k: v for k, v in body.items() if k != "hostname"})
    except KeyError:
        return jsonify({"error": "no such device"}), 404
    return jsonify(updated.to_dict())


@app.route("/api/discover", methods=["POST"])
def api_discover():
    try:
        proto = _build_live_protocol()
    except ProtocolError as e:
        return jsonify({"error": str(e)}), 409
    try:
        candidates = proto.discover(known_hostnames=set(_registry.hostnames()), timeout_s=5.0)
        return jsonify([{"hostname": c.hostname, "mac": c.mac} for c in candidates])
    finally:
        proto.close()


# ---------- MQTT connection settings ----------

@app.route("/api/mqtt-settings", methods=["GET", "POST"])
def api_mqtt_settings():
    if request.method == "GET":
        s = _appconfig.get_mqtt_settings()
        # password is intentionally omitted from GET -- the settings page
        # shows "(unchanged)" rather than ever echoing a stored secret back.
        return jsonify({"host": s.host, "port": s.port, "username": s.username,
                         "tls": s.tls, "configured": s.configured})
    body = request.get_json(force=True)
    current = _appconfig.get_mqtt_settings()
    new_settings = MqttSettings(
        host=body.get("host", current.host), port=int(body.get("port", current.port)),
        username=body.get("username", current.username),
        password=body.get("password") or current.password,
        tls=bool(body.get("tls", current.tls)),
    )
    _appconfig.set_mqtt_settings(new_settings)
    return jsonify({"saved": True})


@app.route("/api/mqtt-settings/test", methods=["POST"])
def api_mqtt_test():
    try:
        proto = _build_live_protocol()
    except ProtocolError as e:
        return jsonify({"ok": False, "error": str(e)}), 200
    proto.close()
    return jsonify({"ok": True})


# ---------- matrix / drift / connection ----------

@app.route("/api/matrix")
def api_matrix():
    filter_name = request.args.get("filter", "all")
    classified = _classified_from_cache()
    m = build_matrix(list(classified.values()), include_capability=True)
    rows = m.filtered(FilterMode(filter_name))
    from faikout_config.safety import SENSITIVE_KEYS, REDACTED
    out_rows = []
    for row in rows:
        values = {}
        for h in m.hostnames:
            v = row.values.get(h, "<absent>")
            if row.key in SENSITIVE_KEYS and v != "<absent>":
                v = REDACTED
            values[h] = v
        out_rows.append({
            "key": row.key, "plane": row.plane.value, "values": values,
            "is_uniform": row.is_uniform, "do_not_standardize": row.do_not_standardize,
            "is_protected": row.is_protected, "description": describe(row.key),
            "editable": row.plane.value == "D_config",
        })
    return jsonify({"hostnames": m.hostnames, "rows": out_rows})


@app.route("/api/connection")
def api_connection():
    return jsonify(_connection_status())


@app.route("/api/drift")
def api_drift():
    classified = list(_classified_from_cache().values())
    baseline = diff_mod.Baseline.load(DATA_ROOT / "baseline.json")
    exceptions = diff_mod.ExceptionSet.load(DATA_ROOT / "exceptions.json")
    findings = diff_mod.check_drift(classified, baseline, exceptions)
    return jsonify({
        "baseline": baseline.desired,
        "findings": [{"hostname": f.hostname, "key": f.key, "live_value": f.live_value,
                       "expected_value": f.expected_value, "severity": f.severity} for f in findings],
    })


@app.route("/api/ota-status")
def api_ota_status():
    classified = _classified_from_cache()
    out = {}
    for h, c in classified.items():
        ota_auto = c.all_flat.get("ota.auto", "<unknown>")
        out[h] = {"ota_auto": ota_auto, "protected": ota_auto in (False, 0, "false")}
    return jsonify(out)


# ---------- profiles ----------

@app.route("/api/profiles", methods=["GET", "POST"])
def api_profiles():
    if request.method == "GET":
        return jsonify(_profiles.list_names())
    body = request.get_json(force=True)
    try:
        p = profiles_mod.Profile.create(body["name"], body["settings"], body.get("description", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _profiles.save(p)
    return jsonify({"saved": p.name})


# ---------- backups ----------

@app.route("/api/backups")
def api_backups():
    out = {}
    for h in _registry.hostnames():
        out[h] = [p.name for p in _snapshots.list_for(h)]
    return jsonify(out)


@app.route("/api/backups/<hostname>", methods=["POST"])
def api_trigger_backup(hostname):
    entry = _client.get_cached(hostname)
    if entry.raw is None:
        return jsonify({"error": "no cached settings available to back up -- refresh first"}), 409
    snap = backup_mod.Snapshot(hostname=hostname, captured_at=time.time(), raw_settings=entry.raw,
                                label="manual-backup")
    path = _snapshots.save(snap)
    return jsonify({"saved": path.name})


# ---------- history ----------

@app.route("/api/history")
def api_history():
    return jsonify(_audit.read_all())


# ---------- live refresh (read-only) ----------

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    body = request.get_json(silent=True) or {}
    hostnames = body.get("hostnames") or _registry.hostnames()
    force = bool(body.get("force", False))
    try:
        proto = _build_live_protocol()
    except ProtocolError as e:
        return jsonify({"error": str(e)}), 409
    try:
        _client._protocol = proto
        results = _client.query_fleet(hostnames, force=force)
    finally:
        proto.close()
        _client._protocol = _UnconfiguredProtocol()
    return jsonify({h: {"success": r.success, "error": r.error, "attempts": r.attempts, "from_cache": r.from_cache}
                     for h, r in results.items()})


# ---------- sync / restore / profile-apply ----------

def _refuse_write():
    return jsonify({"error": "Refusing: live writes are not enabled in this installation. Set the "
                              "ENABLE_LIVE_WRITES environment variable to 'true' to allow them -- read "
                              "SECURITY.md first. This is a single, explicit, all-or-nothing switch, not "
                              "reachable from any UI action."}), 403


def _coerce_value_like(current, raw):
    """HTML form inputs only ever send strings. Coerce a cell's edited
    value back to the type of the existing live value before it reaches
    sync validation -- otherwise a checkbox's "true" stays a string
    forever, "would_change" compares bool to str (always unequal, even
    with no real change), and the ota.auto safety guard -- which checks
    Python types -- never sees the type it's looking for.
    """
    if not isinstance(raw, str):
        return raw
    if isinstance(current, bool):
        return raw.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(current, int):
        try:
            return int(raw)
        except ValueError:
            return raw
    if isinstance(current, float):
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw


def _coerce_cells(raw_cells, classified):
    cells = []
    for c in raw_cells:
        target = classified.get(c["target"])
        current = target.all_flat.get(c["key"]) if target else None
        cells.append((c["key"], c["target"], _coerce_value_like(current, c["value"])))
    return cells


@app.route("/api/sync/preview", methods=["POST"])
def api_sync_preview():
    body = request.get_json(force=True)
    classified = _classified_from_cache()
    cells = _coerce_cells(body["cells"], classified)
    req = sync_mod.SyncRequest.select_cells(cells, source_hostname=body.get("source", ""))
    problems = sync_mod.validate(req, classified, allow_conditional_override=body.get("allow_conditional", False))
    preview_rows = []
    for item in req.items:
        target = classified.get(item.target_hostname)
        current = target.all_flat.get(item.key, "<absent>") if target else "<absent>"
        preview_rows.append({"key": item.key, "target": item.target_hostname,
                              "current_value": current, "new_value": item.new_value,
                              "would_change": current != item.new_value})
    return jsonify({"problems": problems, "rows": preview_rows, "clean": not problems})


def _live_write_route(apply_fn):
    if not LIVE_WRITES_ENABLED:
        return _refuse_write()
    try:
        proto = _build_live_protocol()
    except ProtocolError as e:
        return jsonify({"error": str(e)}), 409
    try:
        classified = _classified_from_cache()
        result = apply_fn(proto, classified)
        return jsonify({"applied": result.applied, "entries": [
            {"hostname": e.hostname, "key": e.key, "verified": e.verified, "rolled_back": e.rolled_back}
            for e in result.audit_entries]})
    except sync_mod.SyncValidationError as e:
        return jsonify({"error": str(e)}), 400
    finally:
        proto.close()


@app.route("/api/sync/apply", methods=["POST"])
def api_sync_apply():
    if not LIVE_WRITES_ENABLED:
        return _refuse_write()
    body = request.get_json(force=True)

    def do_apply(proto, classified):
        cells = _coerce_cells(body["cells"], classified)
        req = sync_mod.SyncRequest.select_cells(cells, source_hostname=body.get("source", ""))
        return sync_mod.apply(req, proto, classified, _snapshots, _audit,
                               allow_conditional_override=body.get("allow_conditional", False))
    return _live_write_route(do_apply)


@app.route("/api/profiles/apply", methods=["POST"])
def api_profile_apply():
    if not LIVE_WRITES_ENABLED:
        return _refuse_write()
    body = request.get_json(force=True)
    profile = _profiles.load(body["name"])

    def do_apply(proto, classified):
        req = profile.to_sync_request(target_hostnames=body["targets"], only_keys=body.get("only_keys"))
        return sync_mod.apply(req, proto, classified, _snapshots, _audit)
    return _live_write_route(do_apply)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8765"))
    app.run(host="0.0.0.0", port=port, debug=False)
