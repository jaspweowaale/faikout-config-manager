import json
import unittest
from pathlib import Path

from faikout_config import planes

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INVENTORY = json.loads((FIXTURES / "settings_inventory.json").read_text())
FIRMWARE = json.loads((FIXTURES / "firmware_versions.json").read_text())


def _classify_all():
    result = [planes.classify(h, FIRMWARE[h], raw) for h, raw in INVENTORY.items()]
    planes.classify_firmware_group_consistency(result)
    return {d.hostname: d for d in result}


class TestPlaneClassification(unittest.TestCase):
    def test_capability_keys_go_to_plane_b(self):
        d = _classify_all()["ac-demo-a"]
        self.assertIn("no.comfort", d.capability)
        self.assertNotIn("no.comfort", d.config)
        self.assertNotIn("no.comfort", d.config_conditional)

    def test_identity_wiring_keys_go_to_plane_c(self):
        d = _classify_all()["ac-demo-a"]
        self.assertIn("uart", d.identity_wiring)
        self.assertIn("i2c.slsda", d.identity_wiring)
        self.assertIn("tx", d.identity_wiring)
        self.assertIn("model", d.identity_wiring)

    def test_wifi_ssid_is_config_not_identity(self):
        # the shared network name is legitimately a plane-D setting;
        # only the per-device static ip/gw/dns are plane C.
        d = _classify_all()["ac-demo-a"]
        self.assertIn("wifi.ssid", d.config)
        self.assertNotIn("wifi.ssid", d.identity_wiring)
        self.assertIn("wifi.ip", d.identity_wiring)

    def test_uniform_keys_stay_in_config(self):
        d = _classify_all()
        # ota.host is identical across all 5 fixture units -- must NOT be
        # flagged as firmware-group-inconsistent.
        for host in d:
            self.assertIn("ota.host", d[host].config, f"{host} missing ota.host from plane D")
            self.assertNotIn("ota.host", d[host].config_conditional)

    def test_same_firmware_pair_inconsistency_detected(self):
        """ac-demo-d and ac-demo-e run byte-identical firmware ("build-new")
        but the fixture gives them different settings keys present -- the
        classifier must catch this automatically, not require it to be
        hardcoded per deployment."""
        d = _classify_all()
        for key in ("ha.enable", "ha.1c", "ha.uptime", "ble.enable", "temp.adjust", "udp.discovery"):
            self.assertIn(key, d["ac-demo-d"].config_conditional,
                          f"{key} present on ac-demo-d but same-firmware-inconsistent -- should be conditional")
            self.assertNotIn(key, d["ac-demo-d"].config, f"{key} should have been moved OUT of config")

    def test_single_member_firmware_group_not_flagged(self):
        """Confirms a plane-D key unique to one firmware-group member among
        a 3-member group (region on ac-demo-a, present only there among the
        three "build-stable" units) IS flagged, proving the >=2-member
        group logic actually ran."""
        d = _classify_all()
        self.assertIn("region", d["ac-demo-a"].config_conditional)

    def test_do_not_standardize_flag_present_on_region(self):
        self.assertIn("region", planes.DO_NOT_STANDARDIZE_YET)

    def test_firmware_update_key_identified(self):
        self.assertIn("ota.auto", planes.FIRMWARE_UPDATE_KEYS)


if __name__ == "__main__":
    unittest.main()
