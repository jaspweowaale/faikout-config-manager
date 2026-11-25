"""Application configuration -- MQTT connection, optional Home Assistant
integration, and the web UI's own auth credentials. User-editable via the
web UI (MQTT Settings page) or by hand-editing the JSON file directly.
Nothing here is hardcoded to any specific deployment; a fresh install has
no MQTT connection configured until the user sets one.
"""
from __future__ import annotations

import json
import os
import secrets
import stat
from dataclasses import dataclass, asdict
from pathlib import Path


def default_data_root() -> Path:
    """Where this app keeps its devices/profiles/snapshots/config --
    overridable via FAIKOUT_CM_DATA_DIR (e.g. a Docker volume mount path).
    Defaults to a dotfile directory under the current user's home, never a
    path specific to any one deployment."""
    env = os.environ.get("FAIKOUT_CM_DATA_DIR")
    if env:
        return Path(env)
    return Path.home() / ".faikout_config_manager"


@dataclass
class MqttSettings:
    host: str = ""
    port: int = 1883
    username: str = ""
    password: str = ""
    tls: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.host)


class AppConfig:
    """Thread-naive (single-process web app), file-backed. MQTT credentials
    are stored like any other local secret: owner-only permissions, never
    logged, never returned by any API response that isn't the authenticated
    settings page itself requesting its own current value for editing."""

    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.data_root.mkdir(parents=True, exist_ok=True)
        self._mqtt_path = data_root / "mqtt_config.json"

    def get_mqtt_settings(self) -> MqttSettings:
        if not self._mqtt_path.exists():
            return MqttSettings()
        return MqttSettings(**json.loads(self._mqtt_path.read_text()))

    def set_mqtt_settings(self, settings: MqttSettings) -> None:
        self._mqtt_path.write_text(json.dumps(asdict(settings), indent=2))
        try:
            os.chmod(self._mqtt_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def load_or_create_web_credentials(data_root: Path) -> dict:
    """Generates a random admin password on first run; stable across
    restarts. See SECURITY.md for how to change it."""
    auth_file = data_root / "auth.json"
    if auth_file.exists():
        return json.loads(auth_file.read_text())
    creds = {"username": "admin", "password": secrets.token_urlsafe(18)}
    auth_file.write_text(json.dumps(creds))
    try:
        os.chmod(auth_file, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return creds
