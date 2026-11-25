"""Explicit safety scenarios: backup completeness, compatibility
validation, partial write failure across MULTIPLE targets in one batch,
readback failure, and rollback -- all against simulated controllers (no
real hardware touched)."""
import json
import tempfile
import unittest
from pathlib import Path

from faikout_config import planes, sync as sync_mod
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


class PartiallyFailingProtocol(FakeProtocol):
    """One specific (hostname, key) write silently fails to apply (readback
    will show the old value); every other write on every other
    hostname/key applies normally. Models one controller dropping a write
    (e.g. a momentary disconnect) while the rest of a multi-target batch
    succeeds -- the "safe handling of partial success" requirement."""

    def __init__(self, initial, fail_hostname, fail_key):
        super().__init__(initial)
        self.fail_hostname = fail_hostname
        self.fail_key = fail_key

    def write_setting(self, hostname, patch):
        if hostname == self.fail_hostname and self.fail_key in _flat_keys(patch):
            self.write_log.append((hostname, patch))
            return  # silently drop -- readback will show no change
        super().write_setting(hostname, patch)


def _flat_keys(d, prefix=""):
    out = []
    for k, v in d.items():
        path = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            out.extend(_flat_keys(v, path))
        else:
            out.append(path)
    return out


class TestBackupCompleteness(unittest.TestCase):
    def setUp(self):
        self.devices = _classified()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.snapshots = SnapshotStore(root / "snapshots")
        self.audit = AuditLog(root / "audit.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def test_pre_change_backup_contains_every_key_any_plane(self):
        """A backup must be COMPLETE -- every key from the original
        settings dump, including plane B/C keys that sync itself would
        never touch -- not just the plane-D subset being changed."""
        proto = FakeProtocol(INVENTORY)
        req = sync_mod.SyncRequest.select_cells([("tz", "ac-demo-a", "Europe/London")])
        sync_mod.apply(req, proto, self.devices, self.snapshots, self.audit)

        backup_path = self.snapshots.list_for("ac-demo-a")[0]
        backed_up = self.snapshots.load(backup_path)
        original = INVENTORY["ac-demo-a"]

        def flat(d, prefix=""):
            out = {}
            for k, v in d.items():
                path = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
                if isinstance(v, dict):
                    out.update(flat(v, path))
                else:
                    out[path] = v
            return out

        original_flat = flat(original)
        backup_flat = flat(backed_up.raw_settings)
        missing = set(original_flat) - set(backup_flat)
        self.assertEqual(missing, set(), f"backup is missing keys: {missing}")
        for key in ("no.comfort", "uart", "i2c.slsda", "hostname"):  # plane B/C spot-check
            self.assertIn(key, backup_flat, f"backup must include {key} even though sync never touches it")


class TestPartialBatchFailure(unittest.TestCase):
    def setUp(self):
        self.devices = _classified()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.snapshots = SnapshotStore(root / "snapshots")
        self.audit = AuditLog(root / "audit.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def test_one_target_fails_others_in_same_batch_still_succeed(self):
        proto = PartiallyFailingProtocol(INVENTORY, fail_hostname="ac-demo-b", fail_key="tz")
        req = sync_mod.SyncRequest.select_row_to_devices(
            "tz", "Europe/London", ["ac-demo-a", "ac-demo-b", "ac-demo-c"])
        result = sync_mod.apply(req, proto, self.devices, self.snapshots, self.audit)

        by_host = {e.hostname: e for e in result.audit_entries}
        self.assertTrue(by_host["ac-demo-a"].verified)
        self.assertFalse(by_host["ac-demo-a"].rolled_back)
        self.assertTrue(by_host["ac-demo-c"].verified)

        self.assertFalse(by_host["ac-demo-b"].verified)
        self.assertTrue(by_host["ac-demo-b"].rolled_back)

        # the two good targets actually have the new value; the failed one
        # is back to its original value (rollback attempted and verified)
        self.assertEqual(proto.read_settings("ac-demo-a").raw["tz"], "Europe/London")
        self.assertEqual(proto.read_settings("ac-demo-c").raw["tz"], "Europe/London")
        self.assertEqual(proto.read_settings("ac-demo-b").raw["tz"], "UTC")

        # every target in the batch got its own pre-change backup, success
        # or failure alike
        for h in ("ac-demo-a", "ac-demo-b", "ac-demo-c"):
            self.assertEqual(len(self.snapshots.list_for(h)), 1)

    def test_partial_failure_does_not_stop_processing_remaining_targets(self):
        """The failing target is NOT necessarily first or last in the
        selection order -- confirm a failure in the MIDDLE of a batch
        doesn't short-circuit the remaining targets."""
        proto = PartiallyFailingProtocol(INVENTORY, fail_hostname="ac-demo-c", fail_key="tz")
        req = sync_mod.SyncRequest.select_row_to_devices(
            "tz", "Europe/London", ["ac-demo-a", "ac-demo-c", "ac-demo-d"])
        result = sync_mod.apply(req, proto, self.devices, self.snapshots, self.audit)
        hosts_processed = {e.hostname for e in result.audit_entries}
        self.assertEqual(hosts_processed, {"ac-demo-a", "ac-demo-c", "ac-demo-d"})


if __name__ == "__main__":
    unittest.main()
