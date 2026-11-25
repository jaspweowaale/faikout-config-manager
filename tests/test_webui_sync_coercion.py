"""Regression coverage for the webui layer's cell-value coercion.

HTML form inputs only ever send strings. Found while reviewing a demo
screenshot: a text input holding "true" for a boolean setting flowed
straight into sync validation as the string "true", which the ota.auto
safety guard (checking Python bool/int/float) did not recognize as an
enable attempt -- a real gap, since the guard's whole job is to make
enabling automatic firmware updates impossible via any write path.

safety.py now also recognizes string forms directly (defense in depth,
see tests/test_safety.py), but the coercion in webui/app.py is the fix
that keeps *every* other setting's "would_change" comparison correct too,
not just this one protected key.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="faikout_webui_test_")
os.environ["FAIKOUT_CM_DATA_DIR"] = _TMP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webui.app import app, _coerce_value_like, _registry, _CREDS  # noqa: E402
from faikout_config.registry import Device  # noqa: E402
from faikout_config import planes  # noqa: E402


class TestCoerceValueLike(unittest.TestCase):
    def test_bool_current_coerces_truthy_strings(self):
        for s in ("true", "True", "1", "yes", "on"):
            self.assertIs(_coerce_value_like(False, s), True)

    def test_bool_current_coerces_falsy_strings(self):
        for s in ("false", "False", "0", "no", "off", "anything-else"):
            self.assertIs(_coerce_value_like(True, s), False)

    def test_int_current_coerces_numeric_string(self):
        self.assertEqual(_coerce_value_like(5, "7"), 7)
        self.assertIsInstance(_coerce_value_like(5, "7"), int)

    def test_non_string_passthrough(self):
        self.assertEqual(_coerce_value_like(True, False), False)

    def test_absent_current_leaves_value_as_string(self):
        self.assertEqual(_coerce_value_like(None, "some text"), "some text")


class TestSyncPreviewCoercionIntegration(unittest.TestCase):
    """Proves the fix end-to-end through the real HTTP route: a string
    "true" submitted for a boolean ota.auto that is currently False must
    be caught by the safety guard, not silently accepted as clean."""

    def setUp(self):
        self.client = app.test_client()
        self.auth = (_CREDS["username"], _CREDS["password"])
        _registry.add(Device(hostname="ac-demo-coercion-test"))
        from webui import app as app_mod
        raw = {"hostname": "ac-demo-coercion-test", "ota": {"auto": False, "host": "ota.example.invalid"}}
        classified = planes.classify("ac-demo-coercion-test", "unknown", raw)
        self._orig = app_mod._classified_from_cache
        app_mod._classified_from_cache = lambda: {"ac-demo-coercion-test": classified}

    def tearDown(self):
        from webui import app as app_mod
        app_mod._classified_from_cache = self._orig
        _registry.remove("ac-demo-coercion-test")

    def test_string_true_for_ota_auto_is_flagged_not_silently_clean(self):
        resp = self.client.post("/api/sync/preview", json={
            "cells": [{"key": "ota.auto", "target": "ac-demo-coercion-test", "value": "true"}],
        }, headers=self._basic_auth_header())
        body = resp.get_json()
        self.assertFalse(body["clean"], "string 'true' must not sneak past the ota.auto guard: %r" % body)
        self.assertTrue(body["problems"])

    def test_string_false_for_ota_auto_stays_clean(self):
        resp = self.client.post("/api/sync/preview", json={
            "cells": [{"key": "ota.auto", "target": "ac-demo-coercion-test", "value": "false"}],
        }, headers=self._basic_auth_header())
        body = resp.get_json()
        self.assertTrue(body["clean"])
        self.assertFalse(body["rows"][0]["would_change"])  # False -> False, no real change

    def _basic_auth_header(self):
        import base64
        token = base64.b64encode(f"{self.auth[0]}:{self.auth[1]}".encode()).decode()
        return {"Authorization": f"Basic {token}"}


if __name__ == "__main__":
    unittest.main()
