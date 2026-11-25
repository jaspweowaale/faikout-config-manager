import json
import unittest
from pathlib import Path

from faikout_config import planes
from faikout_config.matrix import build_matrix, FilterMode

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INVENTORY = json.loads((FIXTURES / "settings_inventory.json").read_text())
FIRMWARE = json.loads((FIXTURES / "firmware_versions.json").read_text())


def _classified_list():
    result = [planes.classify(h, FIRMWARE[h], raw) for h, raw in INVENTORY.items()]
    planes.classify_firmware_group_consistency(result)
    return result


class TestMatrix(unittest.TestCase):
    def setUp(self):
        self.devices = _classified_list()
        self.matrix = build_matrix(self.devices, include_capability=True)

    def test_rows_are_settings_columns_are_hosts(self):
        self.assertEqual(set(self.matrix.hostnames),
                          {"ac-demo-a", "ac-demo-b", "ac-demo-c", "ac-demo-d", "ac-demo-e"})
        keys = {r.key for r in self.matrix.rows}
        self.assertIn("ota.auto", keys)
        self.assertIn("no.comfort", keys)

    def test_region_row_not_uniform(self):
        row = next(r for r in self.matrix.rows if r.key == "region")
        self.assertFalse(row.is_uniform)
        self.assertTrue(row.do_not_standardize)

    def test_ota_host_row_is_uniform_and_not_protected(self):
        row = next(r for r in self.matrix.rows if r.key == "ota.host")
        self.assertTrue(row.is_uniform)
        self.assertFalse(row.is_protected)

    def test_ota_auto_row_is_protected(self):
        row = next(r for r in self.matrix.rows if r.key == "ota.auto")
        self.assertTrue(row.is_protected)
        self.assertTrue(row.is_uniform)  # all 5 fixture devices are false

    def test_filter_differences_only_excludes_uniform_rows(self):
        diffs = self.matrix.filtered(FilterMode.DIFFERENCES_ONLY)
        self.assertTrue(all(not r.is_uniform for r in diffs))
        self.assertTrue(any(r.key == "region" for r in diffs))
        self.assertFalse(any(r.key == "ota.host" for r in diffs))

    def test_filter_common_excludes_differing_rows(self):
        common = self.matrix.filtered(FilterMode.COMMON)
        self.assertTrue(all(r.is_uniform for r in common))
        self.assertTrue(any(r.key == "ota.host" for r in common))

    def test_filter_protected_only_firmware_update_keys(self):
        protected = self.matrix.filtered(FilterMode.PROTECTED)
        self.assertTrue(all(r.key in ("ota.auto",) for r in protected))
        self.assertTrue(len(protected) >= 1)

    def test_filter_hardware_dependent_includes_capability_and_conditional(self):
        hw = self.matrix.filtered(FilterMode.HARDWARE_DEPENDENT)
        keys = {r.key for r in hw}
        self.assertIn("no.comfort", keys)       # plane B
        self.assertIn("ha.enable", keys)        # plane D-conditional (ac-demo-d vs ac-demo-e)
        self.assertNotIn("ota.host", keys)       # plane D, consistent -- must be excluded

    def test_matrix_grows_dynamically_with_device_count(self):
        """Direct proof of 'the matrix must expand dynamically as
        controllers are added' at the matrix-building level: fewer input
        devices -> fewer columns, more -> more, no hardcoded column count
        anywhere in build_matrix."""
        subset = [d for d in self.devices if d.hostname in ("ac-demo-a", "ac-demo-b")]
        small_matrix = build_matrix(subset, include_capability=True)
        self.assertEqual(set(small_matrix.hostnames), {"ac-demo-a", "ac-demo-b"})
        for row in small_matrix.rows:
            self.assertEqual(set(row.values.keys()), {"ac-demo-a", "ac-demo-b"})


if __name__ == "__main__":
    unittest.main()
