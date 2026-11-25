import json
import tempfile
import unittest
from pathlib import Path

from faikout_config.registry import DeviceRegistry, Device


class TestDeviceRegistry(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.registry = DeviceRegistry(Path(self._tmp.name) / "devices.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_starts_empty(self):
        self.assertEqual(self.registry.list(), [])
        self.assertEqual(self.registry.hostnames(), [])

    def test_add_and_get(self):
        self.registry.add(Device(hostname="ac-demo-1", label="Demo unit"))
        d = self.registry.get("ac-demo-1")
        self.assertIsNotNone(d)
        self.assertEqual(d.label, "Demo unit")

    def test_add_duplicate_refused_by_default(self):
        self.registry.add(Device(hostname="ac-demo-1"))
        with self.assertRaises(ValueError):
            self.registry.add(Device(hostname="ac-demo-1"))

    def test_add_duplicate_allowed_with_overwrite(self):
        self.registry.add(Device(hostname="ac-demo-1", label="first"))
        self.registry.add(Device(hostname="ac-demo-1", label="second"), allow_overwrite=True)
        self.assertEqual(self.registry.get("ac-demo-1").label, "second")

    def test_remove(self):
        self.registry.add(Device(hostname="ac-demo-1"))
        self.assertTrue(self.registry.remove("ac-demo-1"))
        self.assertIsNone(self.registry.get("ac-demo-1"))

    def test_remove_unknown_returns_false(self):
        self.assertFalse(self.registry.remove("nonexistent"))

    def test_update(self):
        self.registry.add(Device(hostname="ac-demo-1", label="old"))
        updated = self.registry.update("ac-demo-1", label="new", physical_location="Workshop")
        self.assertEqual(updated.label, "new")
        self.assertEqual(updated.physical_location, "Workshop")

    def test_update_unknown_raises(self):
        with self.assertRaises(KeyError):
            self.registry.update("nonexistent", label="x")

    def test_matrix_grows_as_devices_are_added(self):
        """Direct proof of the 'matrix must expand dynamically' requirement
        at the registry level -- hostnames() is what the matrix/CLI/web UI
        iterate over, so adding a device here is sufficient for the whole
        rest of the system to pick it up."""
        self.assertEqual(len(self.registry.hostnames()), 0)
        self.registry.add(Device(hostname="ac-demo-1"))
        self.assertEqual(len(self.registry.hostnames()), 1)
        self.registry.add(Device(hostname="ac-demo-2"))
        self.assertEqual(len(self.registry.hostnames()), 2)
        self.registry.remove("ac-demo-1")
        self.assertEqual(self.registry.hostnames(), ["ac-demo-2"])

    def test_persists_across_instances(self):
        self.registry.add(Device(hostname="ac-demo-1", label="persisted"))
        reopened = DeviceRegistry(self.registry._path)
        self.assertEqual(reopened.get("ac-demo-1").label, "persisted")

    def test_file_is_plain_readable_json(self):
        self.registry.add(Device(hostname="ac-demo-1"))
        data = json.loads(self.registry._path.read_text())
        self.assertIn("ac-demo-1", data)


if __name__ == "__main__":
    unittest.main()
