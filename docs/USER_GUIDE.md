# User Guide

This walks through every tab in the web UI, then the CLI, then how the
safety features fit together. For installation, see the main
[README.md](../README.md).

## First run

1. Start the app (Docker or native -- see README).
2. Log in with the generated admin credentials (`auth.json` in your data
   directory).
3. Go to **MQTT Settings** and enter your broker's host/port, and a
   username/password if your broker requires auth. Click **Test Connection**
   before saving if you want to confirm it works first.
4. Go to **Devices** and either:
   - Click **Discover** to find controllers via their Home Assistant MQTT
     discovery messages (this subscribes to `homeassistant/climate/+/config`
     and reads retained messages -- it does not touch your controllers'
     settings), or
   - Add a controller manually with its MQTT hostname.
5. Go to **Settings Matrix** and click **Refresh selected from live**. The
   matrix populates with every controller's current settings, classified
   into planes.

Nothing above can write to a controller. Reading, discovering, comparing,
and backing up are always available regardless of the `ENABLE_LIVE_WRITES`
setting.

## Settings Matrix tab

One row per setting key, one column per registered device. Each row is
tagged with its plane and, where relevant, a badge:

- **PROTECTED** (red outline) -- `ota.auto`. This cell can be edited in the
  UI like any other, but any attempt to write a truthy value through
  sync/restore/profile-apply is refused by `faikout_config/safety.py`,
  regardless of how the value was typed in (string, int, or bool all go
  through type coercion and the same guard).
- **DO-NOT-STANDARDIZE** -- a key (e.g. `region`) that's been explicitly
  flagged as something you should decide per-device, not sync to match.
- Rows can also be auto-detected as **firmware-group-inconsistent**: if a
  plane-D key differs between two devices running byte-identical firmware,
  that's a signal the key isn't actually meant to be standardized across
  units (rather than a real drift), and it's excluded from sync/drift by
  default. You can override this per-call if you're sure.

Filter buttons: **All**, **Differences only** (rows where not every device
agrees), **Common** (uniform rows), **Protected**, **Hardware-dependent**
(plane B capability rows -- read-only, shown for reference).

To change a setting on one or more devices:

1. Tick the checkbox next to the cell(s) you want to change (or tick a
   column header to select every eligible cell in that column, or a row
   checkbox to select every eligible cell in that row).
2. Edit the value in the text field.
3. Click **Preview synchronization of selected cells**. This shows current
   vs. new value for every selected cell, plus any validation problems
   (e.g. "refusing to write ota.auto=True") -- nothing is written yet.
4. If the preview is clean and `ENABLE_LIVE_WRITES=true` is set, click
   **Apply**. Every write is preceded by an automatic backup of the full
   current settings for that device, followed by a readback to confirm the
   value actually took -- if the readback doesn't match, that device is
   rolled back automatically and the mismatch is recorded in History.

## Devices tab

Add, edit, or remove controllers. Removing a device also clears its cached
live-settings entry (so a later re-add starts fresh rather than showing
stale cached data). Fields beyond `hostname` (label, physical location,
model, capacity) are free-text and optional -- this tool has no way to
auto-detect a connected AC's real model or capacity, so it never invents
one.

## MQTT Settings tab

Configure the broker connection used for all live operations. The password
field is never pre-filled from the saved value (GET never echoes a stored
secret) -- leave it blank when saving other fields to keep the existing
password unchanged.

## Baseline & Drift tab

Declare a baseline (the settings you expect) and an exception set (approved
deviations), then see what's actually drifted on live hardware. Also shows
an OTA auto-update protection status table -- at a glance, which devices
currently have `ota.auto` disabled (protected) vs. enabled (not protected by
this tool -- you'd need to disable it directly on the device, since this
tool will never enable it, but also never force-disables it without your
say-so).

## Profiles tab

Save a named set of plane-D settings, then apply it to any subset of your
registered devices later. Profile creation itself enforces the plane
restriction -- you cannot save a capability or identity key into a profile.

## Backups tab

Lists every snapshot taken for each device (automatic pre-change backups,
plus any manual backup you trigger here). A backup captures every key in
every plane, not just the ones you were about to change, so a restore can
always put a device back exactly as it was.

## History tab

The full audit log: every write attempt, whether it was verified by
readback, and whether it was rolled back.

## CLI

```bash
faikout-config devices                          # list registered devices
faikout-config device-add ac-living-room --label "Living Room"
faikout-config device-remove ac-living-room
faikout-config mqtt-set --host 203.0.113.10 --port 1883 --username myuser --password mypass
faikout-config matrix --filter all               # offline matrix (no live query)
faikout-config matrix --filter all --live        # pull fresh data from the broker first
faikout-config drift-check --live
faikout-config backup ac-living-room             # or: backup all
```

The CLI respects the same `ENABLE_LIVE_WRITES` gate as the web UI, and reads
from the same data directory (`--data-dir`, defaulting to
`FAIKOUT_CM_DATA_DIR` or `~/.faikout_config_manager`) -- the web UI and CLI
are two views onto the same registry/config/backups, not separate state.

## How the safety features fit together

| Feature | What it guarantees |
|---|---|
| Plane classification | Capability/identity keys are structurally impossible to sync or restore cross-device |
| `ENABLE_LIVE_WRITES` | Writes are impossible at all unless deliberately enabled via environment variable |
| Dry-run preview | You see exactly what would change, and any validation failures, before anything is sent |
| Pre-change backup | Every write is preceded by a full snapshot of that device |
| Readback verification | A write that doesn't actually take is caught immediately |
| Automatic rollback | A failed readback restores the pre-change snapshot, not left half-applied |
| `assert_does_not_enable_ota_auto` | No write path, in any value representation, can turn on automatic firmware updates |
| Firmware-group-inconsistency detection | A plane-D key that varies even among identical-firmware devices is flagged, not silently treated as drift |
