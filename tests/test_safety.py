import unittest

from faikout_config.safety import redact, assert_does_not_enable_ota_auto, FirmwareUpdateGuardError, REDACTED


class TestRedaction(unittest.TestCase):
    def test_sensitive_keys_redacted(self):
        flat = {"mqtt.host": "203.0.113.10", "mqtt.user": "mqttuser", "tz": "UTC"}
        out = redact(flat)
        self.assertEqual(out["mqtt.host"], REDACTED)
        self.assertEqual(out["mqtt.user"], REDACTED)
        self.assertEqual(out["tz"], "UTC")  # non-sensitive passes through


class TestOtaAutoGuard(unittest.TestCase):
    def test_disabling_is_allowed(self):
        assert_does_not_enable_ota_auto({"ota": {"auto": 0}})
        assert_does_not_enable_ota_auto({"ota": {"auto": False}})

    def test_enabling_true_is_refused(self):
        with self.assertRaises(FirmwareUpdateGuardError):
            assert_does_not_enable_ota_auto({"ota": {"auto": True}})

    def test_enabling_truthy_int_is_refused(self):
        with self.assertRaises(FirmwareUpdateGuardError):
            assert_does_not_enable_ota_auto({"ota": {"auto": 7}})

    def test_unrelated_patch_is_fine(self):
        assert_does_not_enable_ota_auto({"tz": "Europe/London"})
        assert_does_not_enable_ota_auto({"ota": {"host": "ota.example.invalid"}})

    def test_enabling_via_string_true_is_refused(self):
        # HTML form inputs only ever send strings. A naive type check here
        # would let "true" (string) through where Python True is caught --
        # exactly the gap this backstop exists to close.
        for s in ("true", "True", "TRUE", "1", "yes", "on"):
            with self.assertRaises(FirmwareUpdateGuardError):
                assert_does_not_enable_ota_auto({"ota": {"auto": s}})

    def test_disabling_via_string_false_is_allowed(self):
        assert_does_not_enable_ota_auto({"ota": {"auto": "false"}})
        assert_does_not_enable_ota_auto({"ota": {"auto": "0"}})


if __name__ == "__main__":
    unittest.main()
