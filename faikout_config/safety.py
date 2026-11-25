"""Cross-cutting safety rules used by every other module.

Two concerns live here because both are "whole system must respect this,
not just one call site": sensitive-field redaction (for logs/backups/any
unrestricted display) and the firmware-update-protection backstop.
"""
from __future__ import annotations

# Keys whose VALUE must never appear in a log line, an unrestricted dashboard
# view, or any rendered matrix cell without explicit unmasking -- credentials
# and anything that identifies the private network layout beyond what's
# already visible in HA itself.
SENSITIVE_KEYS = {
    "mqtt.user",       # broker username
    "mqtt.host",       # broker address (internal LAN topology)
    "wifi.ssid",       # network name
    "wifi.ip", "wifi.gw", "wifi.dns",  # static network identity (also plane C)
}

REDACTED = "***redacted***"


def redact(flat_settings: dict) -> dict:
    """Returns a copy safe to log or display in an unrestricted view."""
    return {k: (REDACTED if k in SENSITIVE_KEYS else v) for k, v in flat_settings.items()}


class FirmwareUpdateGuardError(Exception):
    pass


def assert_does_not_enable_ota_auto(patch: dict) -> None:
    """Raises if a patch would ever set ota.auto to a truthy value. This is
    the one rule every write path (sync, profile-apply, restore) must run
    the final patch through before it reaches the protocol layer -- no
    operation in this package may re-enable automatic firmware updates,
    full stop. Enabling it on purpose is not a feature this package offers
    at all; it would need a separate, explicitly-named owner-approved tool,
    never a side effect of a sync/restore/profile action.
    """
    ota = patch.get("ota")
    if isinstance(ota, dict) and "auto" in ota:
        value = ota["auto"]
        truthy_strings = {"true", "1", "yes", "on"}
        is_enable_attempt = (
            value is True
            or (isinstance(value, (int, float)) and not isinstance(value, bool) and value != 0)
            or (isinstance(value, str) and value.strip().lower() in truthy_strings)
        )
        if is_enable_attempt:
            raise FirmwareUpdateGuardError(
                "refusing to write ota.auto=%r via sync/restore/profile-apply -- "
                "enabling automatic firmware updates is never an allowed side "
                "effect of this tool; it requires a separate, explicit, "
                "owner-approved action outside this package" % (value,)
            )
