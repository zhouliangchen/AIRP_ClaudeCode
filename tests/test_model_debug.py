import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("model_debug", ROOT / "skills" / "model_debug.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ModelDebugLoggerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.card.mkdir()
        self.module = _load_module()

    def tearDown(self):
        self.tmp.cleanup()

    def test_disabled_settings_return_no_logger(self):
        logger = self.module.logger_from_settings(
            self.card,
            "round-000001",
            {"modelDebugMode": False},
        )

        self.assertIsNone(logger)
        self.assertFalse((self.card / "debug").exists())

    def test_logger_writes_raw_model_input_output_and_index(self):
        logger = self.module.logger_from_settings(
            self.card,
            "round-000001",
            {"modelDebugMode": True},
        )

        record = logger.write_call(
            agent_key="story",
            cwd=str(self.card),
            prompt="RAW PROMPT\n隐藏设定必须完整保留",
            stdout="RAW STDOUT\n{\"content\":\"ok\"}",
            stderr="RAW STDERR",
            returncode=0,
            started_at="2026-06-21T00:00:00.000000+00:00",
            ended_at="2026-06-21T00:00:01.250000+00:00",
            duration_ms=1250,
        )

        log_path = self.card / record["relative_path"]
        payload = json.loads(log_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["agent_key"], "story")
        self.assertEqual(payload["raw_input"]["prompt"], "RAW PROMPT\n隐藏设定必须完整保留")
        self.assertEqual(payload["raw_output"]["stdout"], "RAW STDOUT\n{\"content\":\"ok\"}")
        self.assertEqual(payload["raw_output"]["stderr"], "RAW STDERR")
        self.assertEqual(payload["raw_output"]["returncode"], 0)
        self.assertEqual(payload["duration_ms"], 1250)

        index_lines = (self.card / "debug" / "model_calls" / "index.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(index_lines), 1)
        index_item = json.loads(index_lines[0])
        self.assertEqual(index_item["call_id"], payload["call_id"])
        self.assertEqual(index_item["relative_path"], record["relative_path"])


if __name__ == "__main__":
    unittest.main()
