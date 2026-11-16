"""Unit tests for the MultiControl FastAPI Hub state, URL builder, and agent helpers."""

import asyncio
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "client"))

from multicontrol_agent import get_local_ipv4_addresses, re_sanitize

HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None


class TestAgentHelpers(unittest.TestCase):
    def test_re_sanitize(self) -> None:
        self.assertEqual(re_sanitize("My MacBook Pro!"), "my-macbook-pro")
        self.assertEqual(re_sanitize("..."), "node")

    def test_get_local_ipv4_addresses_returns_list(self) -> None:
        ips = get_local_ipv4_addresses()
        self.assertIsInstance(ips, list)
        for ip in ips:
            self.assertFalse(ip.startswith("127."))


@unittest.skipUnless(HAS_FASTAPI, "fastapi is not installed in this Python environment")
class TestServerHub(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp_dir = tempfile.mkdtemp(prefix="multicontrol_test_")
        cls._prev_data_dir = os.environ.get("MULTICONTROL_DATA_DIR")
        os.environ["MULTICONTROL_DATA_DIR"] = cls._tmp_dir
        import server.main as server_main  # noqa: E402

        cls.server_main = server_main

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._prev_data_dir is None:
            os.environ.pop("MULTICONTROL_DATA_DIR", None)
        else:
            os.environ["MULTICONTROL_DATA_DIR"] = cls._prev_data_dir
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_api_health_and_state_hides_token(self) -> None:
        health = asyncio.run(self.server_main.api_health())
        self.assertEqual(health, {"ok": True})

        payload = asyncio.run(self.server_main.api_get_state())
        self.assertEqual(payload["type"], "cluster_state")
        self.assertNotIn("token", payload)
        self.assertIn("server_info", payload)
        self.assertTrue(payload["server_info"]["require_token"])

    def test_build_server_urls_uses_request_host(self) -> None:
        fake_request = SimpleNamespace(headers={"host": "192.168.1.55:8765"})
        base_http, ws_urls = self.server_main._build_server_urls(fake_request)
        self.assertEqual(base_http, "http://192.168.1.55:8765")
        self.assertIn("ws://192.168.1.55:8765/ws/agent", ws_urls)


if __name__ == "__main__":
    unittest.main()
