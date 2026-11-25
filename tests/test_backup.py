import tempfile
import time
import unittest
from pathlib import Path

from faikout_config.backup import SnapshotStore, Snapshot


class TestSnapshotStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SnapshotStore(Path(self._tmp.name) / "snapshots")

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_and_load_roundtrip(self):
        snap = Snapshot(hostname="ac-demo-a", captured_at=time.time(),
                         raw_settings={"ota": {"auto": False}}, label="manual-backup")
        path = self.store.save(snap)
        self.assertTrue(path.exists())
        loaded = self.store.load(path)
        self.assertEqual(loaded.hostname, "ac-demo-a")
        self.assertEqual(loaded.raw_settings, {"ota": {"auto": False}})

    def test_latest_for_returns_most_recent(self):
        self.store.save(Snapshot(hostname="ac-demo-d", captured_at=1.0, raw_settings={"a": 1}, label="old"))
        self.store.save(Snapshot(hostname="ac-demo-d", captured_at=2.0, raw_settings={"a": 2}, label="new"))
        latest = self.store.latest_for("ac-demo-d")
        self.assertEqual(latest.raw_settings["a"], 2)

    def test_latest_for_unknown_device_is_none(self):
        self.assertIsNone(self.store.latest_for("nonexistent"))

    def test_file_permissions_owner_only(self):
        import os
        import stat
        snap = Snapshot(hostname="ac-demo-b", captured_at=time.time(), raw_settings={}, label="x")
        path = self.store.save(snap)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        # owner read+write only -- no group/other bits. Skipped gracefully
        # on platforms where chmod semantics differ (best-effort by design).
        if os.name == "posix":
            self.assertEqual(mode & 0o077, 0)


if __name__ == "__main__":
    unittest.main()
