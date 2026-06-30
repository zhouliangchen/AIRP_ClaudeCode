import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))


class DebugLogTest(unittest.TestCase):
    def setUp(self):
        self.mod = importlib.import_module("runtime.debug_log")

    def test_append_event_writes_jsonl_when_enabled_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            card.mkdir()

            result = self.mod.append_event(
                card,
                "api_fallback",
                level="warning",
                details={
                    "provider": "image_generation",
                    "api_key": "secret-key",
                    "authorization": "Bearer secret-token",
                    "message": "failed with secret-key",
                },
                enabled=True,
                now=lambda: "2026-06-30T12:34:56+08:00",
            )

            log_path = Path(result["path"])
            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(log_path.name, "2026-06-30.jsonl")
        self.assertEqual(records[0]["event"], "api_fallback")
        self.assertEqual(records[0]["level"], "warning")
        self.assertNotIn("secret-key", json.dumps(records[0], ensure_ascii=False))
        self.assertNotIn("secret-token", json.dumps(records[0], ensure_ascii=False))
        self.assertIn("[redacted]", records[0]["details"]["message"])

    def test_append_event_noops_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            card.mkdir()

            result = self.mod.append_event(card, "api_fallback", enabled=False)

            self.assertEqual(result, {"ok": False, "reason": "debug_disabled"})
            self.assertFalse((card / "debug" / "log").exists())
