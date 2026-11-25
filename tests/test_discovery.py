import json
import unittest

from faikout_config.discovery import find_candidates


def _discovery_payload(unique_id, mac, hostname):
    return json.dumps({
        "unique_id": unique_id,
        "dev": {"ids": [mac], "name": hostname},
        "avty_t": f"state/{hostname}",
        "action_topic": f"state/{hostname}",
    })


class TestFindCandidates(unittest.TestCase):
    def test_finds_new_device(self):
        messages = [("homeassistant/climate/AABBCC/config", _discovery_payload("AABBCC", "AABBCC112233", "ac-demo-1"))]
        candidates = find_candidates(messages, known_hostnames=set())
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].hostname, "ac-demo-1")
        self.assertEqual(candidates[0].mac, "AABBCC112233")

    def test_excludes_already_known(self):
        messages = [("homeassistant/climate/AABBCC/config", _discovery_payload("AABBCC", "AABBCC112233", "ac-demo-1"))]
        candidates = find_candidates(messages, known_hostnames={"ac-demo-1"})
        self.assertEqual(candidates, [])

    def test_deduplicates_multiple_messages_same_device(self):
        payload = _discovery_payload("AABBCC", "AABBCC112233", "ac-demo-1")
        messages = [("t1", payload), ("t2", payload)]
        candidates = find_candidates(messages, known_hostnames=set())
        self.assertEqual(len(candidates), 1)

    def test_finds_multiple_distinct_devices(self):
        messages = [
            ("t1", _discovery_payload("A", "AAAAAAAAAAAA", "ac-demo-1")),
            ("t2", _discovery_payload("B", "BBBBBBBBBBBB", "ac-demo-2")),
        ]
        candidates = find_candidates(messages, known_hostnames=set())
        hostnames = {c.hostname for c in candidates}
        self.assertEqual(hostnames, {"ac-demo-1", "ac-demo-2"})

    def test_malformed_payload_skipped_not_raised(self):
        messages = [("t1", "not json"), ("t2", "42"), ("t3", json.dumps(["a", "list"]))]
        candidates = find_candidates(messages, known_hostnames=set())
        self.assertEqual(candidates, [])

    def test_missing_state_topic_skipped(self):
        messages = [("t1", json.dumps({"unique_id": "x", "dev": {"ids": ["m"]}}))]  # no avty_t/action_topic
        candidates = find_candidates(messages, known_hostnames=set())
        self.assertEqual(candidates, [])

    def test_no_mac_still_returns_hostname_with_empty_mac(self):
        payload = json.dumps({"avty_t": "state/ac-demo-1"})
        candidates = find_candidates([("t1", payload)], known_hostnames=set())
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].mac, "")


if __name__ == "__main__":
    unittest.main()
