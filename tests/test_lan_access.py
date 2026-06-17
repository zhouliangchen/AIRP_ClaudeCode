import re
import unittest
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_start_server():
    spec = importlib.util.spec_from_file_location("start_server", ROOT / "skills" / "start_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LanAccessConfigTest(unittest.TestCase):
    def test_server_default_bind_host_all_interfaces(self):
        source = (ROOT / "skills" / "server.py").read_text(encoding="utf-8")

        self.assertIn('"0.0.0.0"', source)
        self.assertNotIn('ThreadingHTTPServer(("127.0.0.1"', source)

    def test_frontend_bridge_uses_page_origin(self):
        html = (ROOT / "skills" / "styles" / "index.html").read_text(encoding="utf-8")
        match = re.search(r"const\s+BRIDGE\s*=\s*([^;]+);", html)

        self.assertIsNotNone(match)
        bridge_expr = match.group(1)
        self.assertIn("window.location.origin", bridge_expr)
        self.assertNotIn("localhost:8765", bridge_expr)

    def test_server_handles_browser_favicon_probe(self):
        source = (ROOT / "skills" / "server.py").read_text(encoding="utf-8")

        self.assertIn('parsed.path == "/favicon.ico"', source)
        self.assertIn("self.send_response(204)", source)

    def test_start_server_detects_loopback_only_listener(self):
        start_server = _load_start_server()

        self.assertTrue(start_server._listener_matches_host({"0.0.0.0"}, "0.0.0.0"))
        self.assertFalse(start_server._listener_matches_host({"127.0.0.1"}, "0.0.0.0"))
        self.assertTrue(start_server._listener_matches_host({"192.168.1.2"}, "192.168.1.2"))

    def test_server_probe_handles_empty_stdout_and_sets_utf8_decoding(self):
        start_server = _load_start_server()
        calls = []
        original_run = start_server.subprocess.run

        class Result:
            returncode = 0
            stdout = None

        def fake_run(*args, **kwargs):
            calls.append((args, kwargs))
            return Result()

        try:
            start_server.subprocess.run = fake_run

            self.assertFalse(start_server._server_responding())
        finally:
            start_server.subprocess.run = original_run

        self.assertEqual(calls[0][1]["encoding"], "utf-8")
        self.assertEqual(calls[0][1]["errors"], "replace")


if __name__ == "__main__":
    unittest.main()
