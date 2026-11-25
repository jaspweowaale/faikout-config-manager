import json
import unittest
from pathlib import Path

from faikout_config import planes
from faikout_config.diff import Baseline, ExceptionSet, check_drift

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INVENTORY = json.loads((FIXTURES / "settings_inventory.json").read_text())
FIRMWARE = json.loads((FIXTURES / "firmware_versions.json").read_text())


def _classified_list(overrides=None):
    data = {h: dict(v) for h, v in INVENTORY.items()}
    if overrides:
        for h, patch in overrides.items():
            _merge(data[h], patch)
    result = [planes.classify(h, FIRMWARE[h], raw) for h, raw in data.items()]
    planes.classify_firmware_group_consistency(result)
    return result


def _merge(target, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _merge(target[k], v)
        else:
            target[k] = v


class TestDrift(unittest.TestCase):
    def test_no_drift_against_current_fleet(self):
        devices = _classified_list()
        baseline = Baseline(desired={"ota.auto": False, "ota.host": "ota.example.invalid", "tz": "UTC"})
        findings = check_drift(devices, baseline, ExceptionSet(per_device={}))
        self.assertEqual(findings, [])

    def test_ota_auto_drift_is_critical(self):
        devices = _classified_list(overrides={"ac-demo-d": {"ota": {"auto": True}}})
        baseline = Baseline(desired={"ota.auto": False})
        findings = check_drift(devices, baseline, ExceptionSet(per_device={}))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "critical")
        self.assertEqual(findings[0].hostname, "ac-demo-d")

    def test_approved_exception_suppresses_drift(self):
        devices = _classified_list(overrides={"ac-demo-d": {"tz": "Europe/London"}})
        baseline = Baseline(desired={"tz": "UTC"})
        exceptions = ExceptionSet(per_device={"ac-demo-d": {"tz": "Europe/London"}})
        findings = check_drift(devices, baseline, exceptions)
        self.assertEqual(findings, [])

    def test_wrong_value_still_flagged_even_with_unrelated_exception(self):
        devices = _classified_list(overrides={"ac-demo-d": {"tz": "Asia/Tokyo"}})
        baseline = Baseline(desired={"tz": "UTC"})
        exceptions = ExceptionSet(per_device={"ac-demo-d": {"region": "Zone9"}})  # different key
        findings = check_drift(devices, baseline, exceptions)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].key, "tz")

    def test_baseline_key_absent_on_device_is_not_drift(self):
        # ac-demo-e has no "region" key at all -- a baseline mentioning
        # region must not falsely flag it as drifted; absence is a
        # firmware-schema fact, not a config change.
        devices = _classified_list()
        baseline = Baseline(desired={"region": "Zone1"})
        findings = check_drift(devices, baseline, ExceptionSet(per_device={}))
        hosts_flagged = {f.hostname for f in findings}
        self.assertNotIn("ac-demo-e", hosts_flagged)


if __name__ == "__main__":
    unittest.main()
