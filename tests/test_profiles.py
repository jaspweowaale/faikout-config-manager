import json
import tempfile
import unittest
from pathlib import Path

from faikout_config import planes, sync as sync_mod
from faikout_config.profiles import Profile, ProfileStore
from faikout_config.backup import SnapshotStore
from faikout_config.audit import AuditLog
from tests.fakes import FakeProtocol

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INVENTORY = json.loads((FIXTURES / "settings_inventory.json").read_text())
FIRMWARE = json.loads((FIXTURES / "firmware_versions.json").read_text())


def _classified():
    result = [planes.classify(h, FIRMWARE[h], raw) for h, raw in INVENTORY.items()]
    planes.classify_firmware_group_consistency(result)
    return {d.hostname: d for d in result}


class TestProfileCreation(unittest.TestCase):
    def test_create_rejects_non_plane_d_keys(self):
        with self.assertRaises(ValueError):
            Profile.create("bad", {"no.comfort": False})
        with self.assertRaises(ValueError):
            Profile.create("bad", {"uart": 2})

    def test_create_accepts_plane_d_keys(self):
        p = Profile.create("standard", {"tz": "UTC", "dark": True})
        self.assertEqual(p.settings["tz"], "UTC")

    def test_create_refuses_ota_auto_true_even_though_plane_d(self):
        """Explicit requirement: the OTA guard applies across every write
        path, including saving a profile that would enable it."""
        with self.assertRaises(ValueError) as ctx:
            Profile.create("bad", {"ota.auto": True})
        self.assertIn("ota.auto", str(ctx.exception))

    def test_create_allows_ota_auto_false(self):
        p = Profile.create("ok", {"ota.auto": False})
        self.assertEqual(p.settings["ota.auto"], False)


class TestProfileStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ProfileStore(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_and_load_roundtrip(self):
        p = Profile.create("standard", {"tz": "UTC"}, description="baseline tz")
        self.store.save(p)
        loaded = self.store.load("standard")
        self.assertEqual(loaded.settings, {"tz": "UTC"})
        self.assertEqual(loaded.description, "baseline tz")

    def test_list_names(self):
        self.store.save(Profile.create("a", {"tz": "UTC"}))
        self.store.save(Profile.create("b", {"dark": True}))
        self.assertEqual(self.store.list_names(), ["a", "b"])


class TestProfileApply(unittest.TestCase):
    def setUp(self):
        self.devices = _classified()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.snapshots = SnapshotStore(root / "snapshots")
        self.audit = AuditLog(root / "audit.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def test_apply_profile_to_selected_devices(self):
        proto = FakeProtocol(INVENTORY)
        profile = Profile.create("night-mode", {"tz": "Europe/London", "dark": False})
        req = profile.to_sync_request(target_hostnames=["ac-demo-a", "ac-demo-b"])
        result = sync_mod.apply(req, proto, self.devices, self.snapshots, self.audit)
        self.assertTrue(result.applied)
        self.assertEqual(proto.read_settings("ac-demo-a").raw["tz"], "Europe/London")
        self.assertEqual(proto.read_settings("ac-demo-a").raw["dark"], False)
        self.assertEqual(proto.read_settings("ac-demo-d").raw["tz"], "UTC")  # untouched

    def test_apply_selected_subset_of_profile_only(self):
        proto = FakeProtocol(INVENTORY)
        profile = Profile.create("night-mode", {"tz": "Europe/London", "dark": False})
        req = profile.to_sync_request(target_hostnames=["ac-demo-a"], only_keys=["tz"])
        sync_mod.apply(req, proto, self.devices, self.snapshots, self.audit)
        self.assertEqual(proto.read_settings("ac-demo-a").raw["tz"], "Europe/London")
        self.assertEqual(proto.read_settings("ac-demo-a").raw["dark"], True)  # unchanged, not in only_keys


if __name__ == "__main__":
    unittest.main()
