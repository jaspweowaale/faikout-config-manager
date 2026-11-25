import json
import tempfile
import unittest
from pathlib import Path

from faikout_config import planes, sync as sync_mod
from faikout_config.backup import SnapshotStore
from faikout_config.audit import AuditLog
from tests.fakes import FakeProtocol, FailingWriteProtocol

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INVENTORY = json.loads((FIXTURES / "settings_inventory.json").read_text())
FIRMWARE = json.loads((FIXTURES / "firmware_versions.json").read_text())


def _classified():
    result = [planes.classify(h, FIRMWARE[h], raw) for h, raw in INVENTORY.items()]
    planes.classify_firmware_group_consistency(result)
    return {d.hostname: d for d in result}


class _Harness(unittest.TestCase):
    def setUp(self):
        self.devices = _classified()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.snapshots = SnapshotStore(root / "snapshots")
        self.audit = AuditLog(root / "audit.jsonl")

    def tearDown(self):
        self._tmp.cleanup()


class TestValidation(_Harness):
    def test_capability_key_rejected(self):
        req = sync_mod.SyncRequest.select_cells([("no.comfort", "ac-demo-b", False)])
        problems = sync_mod.validate(req, self.devices)
        self.assertTrue(any("plane B" in p for p in problems))

    def test_identity_wiring_key_rejected(self):
        req = sync_mod.SyncRequest.select_cells([("uart", "ac-demo-b", 2)])
        problems = sync_mod.validate(req, self.devices)
        self.assertTrue(any("plane C" in p for p in problems))

    def test_conditional_key_rejected_without_override(self):
        req = sync_mod.SyncRequest.select_cells([("ha.enable", "ac-demo-e", True)])
        problems = sync_mod.validate(req, self.devices)
        self.assertTrue(any("firmware-group-inconsistent" in p for p in problems))

    def test_conditional_key_allowed_with_explicit_override(self):
        req = sync_mod.SyncRequest.select_cells([("ha.enable", "ac-demo-e", True)])
        problems = sync_mod.validate(req, self.devices, allow_conditional_override=True)
        self.assertEqual(problems, [])

    def test_ota_auto_enable_rejected_even_as_plain_config_key(self):
        req = sync_mod.SyncRequest.select_cells([("ota.auto", "ac-demo-b", True)])
        problems = sync_mod.validate(req, self.devices)
        self.assertTrue(any("refusing to write ota.auto" in p for p in problems))

    def test_safe_plane_d_key_accepted(self):
        req = sync_mod.SyncRequest.select_cells([("tz", "ac-demo-b", "Europe/London")])
        problems = sync_mod.validate(req, self.devices)
        self.assertEqual(problems, [])


class TestDryRun(_Harness):
    def test_preview_shows_current_and_new_value(self):
        proto = FakeProtocol(INVENTORY)
        req = sync_mod.SyncRequest.select_row_to_devices("tz", "Europe/London", ["ac-demo-a", "ac-demo-b"])
        preview = sync_mod.dry_run(req, proto, self.devices)
        self.assertTrue(preview.is_clean)
        by_host = {r.target_hostname: r for r in preview.rows}
        self.assertEqual(by_host["ac-demo-a"].current_value, "UTC")
        self.assertEqual(by_host["ac-demo-a"].new_value, "Europe/London")
        self.assertTrue(by_host["ac-demo-a"].would_change)

    def test_preview_no_change_when_value_already_matches(self):
        proto = FakeProtocol(INVENTORY)
        req = sync_mod.SyncRequest.select_cells([("tz", "ac-demo-a", "UTC")])  # already UTC
        preview = sync_mod.dry_run(req, proto, self.devices)
        self.assertFalse(preview.rows[0].would_change)

    def test_dry_run_never_writes(self):
        proto = FakeProtocol(INVENTORY)
        req = sync_mod.SyncRequest.select_cells([("tz", "ac-demo-a", "Europe/London")])
        sync_mod.dry_run(req, proto, self.devices)
        self.assertEqual(proto.write_log, [])


class TestApply(_Harness):
    def test_successful_apply_writes_backs_up_and_verifies(self):
        proto = FakeProtocol(INVENTORY)
        req = sync_mod.SyncRequest.select_row_to_devices("tz", "Europe/London", ["ac-demo-a"])
        result = sync_mod.apply(req, proto, self.devices, self.snapshots, self.audit)
        self.assertTrue(result.applied)
        self.assertEqual(proto.read_settings("ac-demo-a").raw["tz"], "Europe/London")
        self.assertEqual(len(result.audit_entries), 1)
        self.assertTrue(result.audit_entries[0].verified)
        self.assertFalse(result.audit_entries[0].rolled_back)
        # pre-change backup must exist and contain the ORIGINAL value
        backups = self.snapshots.list_for("ac-demo-a")
        self.assertEqual(len(backups), 1)
        backup_snap = self.snapshots.load(backups[0])
        self.assertEqual(backup_snap.raw_settings["tz"], "UTC")

    def test_unsafe_request_raises_before_any_write(self):
        proto = FakeProtocol(INVENTORY)
        req = sync_mod.SyncRequest.select_cells([("no.comfort", "ac-demo-a", False)])
        with self.assertRaises(sync_mod.SyncValidationError):
            sync_mod.apply(req, proto, self.devices, self.snapshots, self.audit)
        self.assertEqual(proto.write_log, [])
        self.assertEqual(self.snapshots.list_for("ac-demo-a"), [])

    def test_ota_auto_enable_raises_before_any_write_even_if_requested_directly(self):
        proto = FakeProtocol(INVENTORY)
        req = sync_mod.SyncRequest.select_cells([("ota.auto", "ac-demo-b", True)])
        with self.assertRaises(sync_mod.SyncValidationError):
            sync_mod.apply(req, proto, self.devices, self.snapshots, self.audit)
        self.assertEqual(proto.write_log, [])

    def test_readback_mismatch_triggers_rollback(self):
        proto = FailingWriteProtocol(INVENTORY)
        req = sync_mod.SyncRequest.select_cells([("tz", "ac-demo-a", "Europe/London")])
        result = sync_mod.apply(req, proto, self.devices, self.snapshots, self.audit)
        entry = result.audit_entries[0]
        self.assertFalse(entry.verified)
        self.assertTrue(entry.rolled_back)
        # FailingWriteProtocol never actually changes anything, so the
        # "rollback" readback trivially matches the original -- this proves
        # the rollback PATH ran and was itself verified, not that a real
        # device's rollback would always succeed.

    def test_no_unnecessary_writes_for_unchanged_values(self):
        proto = FakeProtocol(INVENTORY)
        req = sync_mod.SyncRequest.select_cells([("tz", "ac-demo-a", "UTC")])  # already this value
        result = sync_mod.apply(req, proto, self.devices, self.snapshots, self.audit)
        self.assertEqual(proto.write_log, [])
        self.assertEqual(result.audit_entries, [])

    def test_multi_target_sync_backs_up_each_target_once(self):
        proto = FakeProtocol(INVENTORY)
        req = sync_mod.SyncRequest.select_row_to_devices(
            "tz", "Europe/London", ["ac-demo-a", "ac-demo-b", "ac-demo-c"])
        sync_mod.apply(req, proto, self.devices, self.snapshots, self.audit)
        for h in ("ac-demo-a", "ac-demo-b", "ac-demo-c"):
            self.assertEqual(len(self.snapshots.list_for(h)), 1)


class TestColumnSelection(_Harness):
    def test_select_column_copies_eligible_keys_only(self):
        source = self.devices["ac-demo-d"]
        req = sync_mod.SyncRequest.select_column_from_device(
            source, keys=["tz", "dark", "no.comfort", "ha.enable"], target_hostnames=["ac-demo-e"])
        # select_column_from_device reads only from source.config (plane D,
        # non-conditional) -- "no.comfort" (plane B) and "ha.enable" (plane
        # D-conditional for this exact pair) live in source.capability /
        # source.config_conditional respectively, so they're silently
        # excluded at selection time, never even becoming SyncItems.
        selected_keys = {item.key for item in req.items}
        self.assertEqual(selected_keys, {"tz", "dark"})
        problems = sync_mod.validate(req, self.devices)
        self.assertEqual(problems, [])

    def test_select_column_defense_in_depth_if_conditional_key_forced_in(self):
        # even if a caller bypasses the helper and hand-builds a SyncItem for
        # a conditional key, validate() must still catch it -- the selection
        # helper excluding it is a convenience, not the only safeguard.
        req = sync_mod.SyncRequest.select_cells([("ha.enable", "ac-demo-e", True)], source_hostname="ac-demo-d")
        problems = sync_mod.validate(req, self.devices)
        self.assertTrue(any("firmware-group-inconsistent" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
