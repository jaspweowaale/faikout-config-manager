"""Faikout Configuration Manager.

An open-source settings matrix, backup/restore, synchronization, and
drift-detection tool for fleets of RevK Faikout AC controllers, talking
MQTT directly or via an existing Home Assistant installation.

Design note: this package deliberately keeps three concerns apart --
conflating any two of them is what makes a "configuration manager" give
confidently wrong answers:

  - Live operating state (state/<id> MQTT topics) is entirely out of scope --
    this package never reads or writes it.
  - Detected hardware capability (the "no.*" map) is read-only reference data,
    surfaced for context, never a sync/restore/backup target.
  - True configuration settings are the only thing this package manages.

See planes.py for the classification engine and README.md for the full design.
"""

__version__ = "0.1.0"
