import json
import time
import unittest
from pathlib import Path

from faikout_config import planes, restore as restore_mod, sync as sync_mod
from faikout_config.backup import Snapshot

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INVENTORY = json.loads((FIXTURES / "settings_inventory.json").read_text())
FIRMWARE = json.loads((FIXTURES / "firmware_versions.json").read_text())


def _classified():
    result = [planes.classify(h, FIRMWARE[h], raw) for h, raw in INVENTORY.items()]
    planes.classify_firmware_group_consistency(result)
    return {d.hostname: d for d in result}


class TestRestoreSameDevice(unittest.TestCase):
    def test_full_restore_includes_every_plane(self):
        snap = Snapshot(hostname="ac-demo-a", captured_at=time.time(),
                         raw_settings=INVENTORY["ac-demo-a"], label="pre-change")
        req = restore_mod.restore_same_device(snap)
        keys = {item.key for item in req.items}
        self.assertIn("ota.auto", keys)      # plane D
        self.assertIn("uart", keys)          # plane C -- fine, same device
        self.assertIn("no.comfort", keys)    # plane B -- fine, same device
        self.assertTrue(all(item.target_hostname == "ac-demo-a" for item in req.items))

    def test_partial_restore_respects_only_keys(self):
        snap = Snapshot(hostname="ac-demo-a", captured_at=time.time(),
                         raw_settings=INVENTORY["ac-demo-a"], label="pre-change")
        req = restore_mod.restore_same_device(snap, only_keys=["tz", "dark"])
        self.assertEqual({item.key for item in req.items}, {"tz", "dark"})


class TestRestoreCrossDevice(unittest.TestCase):
    def test_cross_device_excludes_capability_and_wiring_always(self):
        # even WITHOUT fleet context, plane B/C are structurally excluded
        # (restore_cross_device only ever reads from classified.config).
        snap = Snapshot(hostname="ac-demo-d", captured_at=time.time(),
                         raw_settings=INVENTORY["ac-demo-d"], label="pre-change")
        req = restore_mod.restore_cross_device(snap, target_hostnames=["ac-demo-e"])
        keys = {item.key for item in req.items}
        self.assertNotIn("uart", keys)
        self.assertNotIn("no.comfort", keys)
        self.assertNotIn("hostname", keys)
        self.assertIn("tz", keys)
        self.assertTrue(all(item.target_hostname == "ac-demo-e" for item in req.items))
        self.assertTrue(all(item.target_hostname != "ac-demo-d" for item in req.items))

    def test_cross_device_without_fleet_context_is_caught_by_validate_anyway(self):
        # defense in depth: called without other_devices_same_firmware, so
        # ha.enable/dump/debug/region look locally-safe to restore_cross_
        # device -- but validate(), given the REAL fleet classification,
        # must still refuse them because they're conditional on the source.
        devices = _classified()
        snap = Snapshot(hostname="ac-demo-d", captured_at=time.time(),
                         raw_settings=INVENTORY["ac-demo-d"], label="pre-change")
        req = restore_mod.restore_cross_device(snap, target_hostnames=["ac-demo-e"])
        problems = sync_mod.validate(req, devices)
        self.assertTrue(any("ha.enable" in p for p in problems))
        self.assertTrue(any("firmware-group-inconsistent" in p for p in problems))

    def test_cross_device_with_fleet_context_excludes_conditional_keys_upfront(self):
        # the well-formed usage: pass the real fleet so eligibility is
        # computed correctly at selection time, and validate() then passes
        # clean -- defense layer 1 doing its job, layer 2 not even needed.
        devices = _classified()
        other = [d for h, d in devices.items() if h != "ac-demo-d"]
        snap = Snapshot(hostname="ac-demo-d", captured_at=time.time(),
                         raw_settings=INVENTORY["ac-demo-d"], label="pre-change")
        req = restore_mod.restore_cross_device(snap, target_hostnames=["ac-demo-e"],
                                                 firmware_version=FIRMWARE["ac-demo-d"],
                                                 other_devices_same_firmware=other)
        keys = {item.key for item in req.items}
        self.assertNotIn("ha.enable", keys)
        self.assertNotIn("region", keys)
        self.assertIn("tz", keys)
        problems = sync_mod.validate(req, devices)
        self.assertEqual(problems, [])


class TestOtaGuardAcrossEveryWritePath(unittest.TestCase):
    """Explicit requirement: the protected OTA policy must be enforced on
    EVERY write path, not just sync.py directly. restore_same_device
    includes every key verbatim (by design, for same-device full restore)
    -- if a historical snapshot ever had ota.auto=True recorded in it,
    restoring that snapshot must still be refused for that one key."""

    def test_restoring_a_snapshot_with_ota_auto_true_is_refused(self):
        devices = _classified()
        tainted_settings = dict(INVENTORY["ac-demo-d"])
        tainted_settings["ota"] = {"host": "ota.example.invalid", "auto": True}
        snap = Snapshot(hostname="ac-demo-d", captured_at=time.time(), raw_settings=tainted_settings, label="old")
        req = restore_mod.restore_same_device(snap)
        problems = sync_mod.validate(req, devices)
        self.assertTrue(any("ota.auto" in p for p in problems))

    def test_restoring_the_rest_of_that_same_snapshot_is_unaffected(self):
        """The guard rejects the whole batch at validate() today (apply()
        raises SyncValidationError on ANY problem) -- confirming that
        behavior explicitly, so it's a documented choice, not a surprise:
        a tainted snapshot blocks its own restore entirely rather than
        silently applying everything except the dangerous key."""
        devices = _classified()
        tainted_settings = dict(INVENTORY["ac-demo-d"])
        tainted_settings["ota"] = {"host": "ota.example.invalid", "auto": True}
        snap = Snapshot(hostname="ac-demo-d", captured_at=time.time(), raw_settings=tainted_settings, label="old")
        req = restore_mod.restore_same_device(snap)
        problems = sync_mod.validate(req, devices)
        self.assertTrue(len(problems) >= 1)


if __name__ == "__main__":
    unittest.main()
