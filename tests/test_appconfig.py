import tempfile
import unittest
from pathlib import Path

from faikout_config.appconfig import AppConfig, MqttSettings, load_or_create_web_credentials


class TestAppConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config = AppConfig(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_defaults_to_unconfigured(self):
        settings = self.config.get_mqtt_settings()
        self.assertFalse(settings.configured)
        self.assertEqual(settings.host, "")

    def test_save_and_reload(self):
        self.config.set_mqtt_settings(MqttSettings(host="203.0.113.5", port=1884, username="u", password="p"))
        reloaded = self.config.get_mqtt_settings()
        self.assertTrue(reloaded.configured)
        self.assertEqual(reloaded.host, "203.0.113.5")
        self.assertEqual(reloaded.port, 1884)

    def test_file_permissions_owner_only(self):
        import os, stat
        self.config.set_mqtt_settings(MqttSettings(host="203.0.113.5"))
        mode = stat.S_IMODE(os.stat(self.config._mqtt_path).st_mode)
        if os.name == "posix":
            self.assertEqual(mode & 0o077, 0)


class TestWebCredentials(unittest.TestCase):
    def test_generates_once_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = load_or_create_web_credentials(root)
            second = load_or_create_web_credentials(root)
            self.assertEqual(first, second)
            self.assertEqual(first["username"], "admin")
            self.assertTrue(len(first["password"]) > 10)


if __name__ == "__main__":
    unittest.main()
