"""Offline checks for network and diagnostic exposure defaults."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class SecurityContracts(unittest.TestCase):
    def test_remote_engine_requires_opt_in_lan_binding(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("${HASDR_BIND:-127.0.0.1}:8099:8099", compose)
        self.assertNotIn("${HASDR_BIND:-0.0.0.0}", compose)

    def test_websocket_errors_do_not_log_remote_payload(self):
        api = (ROOT / "custom_components/rtl_sdr/api.py").read_text()
        self.assertNotIn('message.data)', api.split("except json.JSONDecodeError:")[1].split("continue")[0])

if __name__ == "__main__":
    unittest.main()
