"""Short, human-readable descriptions for known settings -- used by the
matrix UI's expandable row detail, kept out of the main table per the
"expandable descriptions rather than excessive information in the main
matrix" design goal. Unknown keys simply get no description (not an error).
"""

DESCRIPTIONS = {
    "ota.auto": "Days between automatic firmware update checks; 0/false disables auto-update entirely. "
                "PROTECTED -- this tool refuses to ever write this to an enabled value.",
    "ota.host": "Hostname the controller fetches OTA firmware updates from.",
    "ota.beta": "Whether to accept beta firmware builds during an OTA check.",
    "region": "Purpose not conclusively determined by this project -- checked the vendor's own docs, "
              "the underlying settings framework, and public discussions, found no authoritative "
              "answer. Not included in the default baseline until resolved -- if you find out what "
              "it does, please open an issue or PR.",
    "tz": "Timezone string used for the controller's internal clock/scheduling.",
    "dark": "Display/LED dimming preference.",
    "blink": "LED blink pattern, three values.",
    "dump": "Diagnostic: dump verbose internal state to logs.",
    "debug": "Diagnostic: enable verbose debug logging.",
    "livestatus": "Enables faster/more frequent live status reporting.",
    "lockmode": "Locks the controller's local physical controls.",
    "fixstatus": "Firmware-internal status-reporting fix flag, meaning not otherwise documented.",
    "wifi.ssid": "WiFi network name the controller associates to.",
    "wifi.ip": "Static IP override, if set -- per-device, never a sync target.",
    "wifi.gw": "Static gateway override, if set -- per-device, never a sync target.",
    "wifi.dns": "Static DNS override, if set -- per-device, never a sync target.",
    "mqtt.host": "MQTT broker address(es) the controller connects to.",
    "mqtt.user": "MQTT broker username.",
    "ha.switches": "Advertise Home Assistant switch entities via MQTT discovery.",
    "ha.fanrpm": "Advertise a fan-RPM diagnostic sensor.",
    "ha.comprpm": "Advertise a compressor-RPM diagnostic sensor.",
    "ha.ram": "Advertise a RAM diagnostic sensor -- seen only on some units even on identical "
             "firmware; plausibly hardware-conditional, see the matrix's 'hardware-dependent' filter.",
    "ha.domain": "HA discovery topic domain/prefix override.",
    "ha.enable": "Master enable for Home Assistant integration features -- can be firmware-group-"
                "inconsistent on otherwise-identical firmware; likely hardware-conditional "
                "registration, not simply a missing setting.",
    "ha.1c": "Meaning not documented upstream -- can be firmware-group-inconsistent, same caveat as "
            "ha.enable.",
    "ha.uptime": "Advertise an uptime diagnostic sensor.",
    "ble.enable": "Enable the Bluetooth LE radio.",
    "temp.adjust": "Temperature-reading calibration adjustment toggle.",
    "udp.discovery": "Enable UDP-based local network discovery.",
    "uart": "Which hardware UART bus talks to the AC's S21 connector -- wiring, never sync-eligible.",
    "i2c.slsda": "I2C data pin assignment -- wiring, never sync-eligible.",
    "i2c.slscl": "I2C clock pin assignment -- wiring, never sync-eligible.",
    "i2c.masda": "I2C data pin assignment (secondary bus) -- wiring, never sync-eligible.",
    "i2c.massl": "I2C clock pin assignment (secondary bus) -- wiring, never sync-eligible.",
    "tx": "S21 UART TX pin -- wiring, never sync-eligible.",
    "rx": "S21 UART RX pin -- wiring, never sync-eligible.",
    "factorygpio": "Factory-reset button GPIO pin -- wiring, never sync-eligible.",
    "model": "Device's own self-reported AC model string, if it publishes one -- cross-check against "
            "your own records of the physical unit's nameplate; this project does not verify it.",
}


def describe(key: str) -> str:
    return DESCRIPTIONS.get(key, "No description recorded for this setting yet.")
