# Example configuration files

All data here is synthetic (RFC 5737 documentation IP addresses, fake MQTT
credentials, fake MAC addresses) -- nothing in this directory is a real
device or network.

- `devices.example.json` -- shape of the device registry file. Copy to
  `$FAIKOUT_CM_DATA_DIR/devices.json` (default `~/.faikout_config_manager/devices.json`)
  and edit the hostnames to match your real controllers, or just use the
  web UI's Devices tab instead -- you don't need to hand-edit this file.
- `mqtt_config.example.json` -- shape of the saved MQTT broker connection.
  Same deal: use the web UI's MQTT Settings tab instead of hand-editing this
  in normal use; it's here so you can see the format and so Docker-volume
  users can pre-seed a config before first boot.
- `profile.example.json` -- shape of a saved settings profile (a named
  set of plane-D config keys you can apply to one or more devices later).
  Copy to `$FAIKOUT_CM_DATA_DIR/profiles/<name>.json`.
