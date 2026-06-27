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
        self.assertEqual(payload["api_metadata"], {})

        index_lines = (self.card / "debug" / "model_calls" / "index.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(index_lines), 1)
        index_item = json.loads(index_lines[0])
        self.assertEqual(index_item["call_id"], payload["call_id"])
        self.assertEqual(index_item["relative_path"], record["relative_path"])

    def test_logger_writes_api_metadata_to_call_record_without_breaking_index(self):
        logger = self.module.ModelDebugLogger(self.card, "round-000002")
        api_metadata = {
            "provider": "openai_compatible",
            "model": "test-model",
            "status": "success",
            "usage": {"input_tokens": 12, "output_tokens": 34},
            "raw_response": {
                "id": "resp_1",
                "choices": [{"message": {"content": "ok"}}],
                "headers": {
                    "authorization": "Bearer nested-token-123",
                    "x-api-key": "nested-api-key-123",
                },
                "header": "x-api-key: header-secret-123",
                "body": {
                    "api_key": "body-api-key-123",
                    "apiKey": "sk-camel-123",
                    "xApiKey": "x-camel-123",
                    "accessToken": "access-camel-123",
                    "refreshToken": "refresh-camel-123",
                    "error": "invalid sk-camel-123",
                    "nested": [
                        {"token": "nested-list-token-123"},
                        "Bearer inline-token-123",
                    ],
                },
            },
            "response_preview": "ok",
        }

        record = logger.write_call(
            agent_key="gm",
            cwd=str(self.card),
            prompt="# gm",
            stdout="ok",
            returncode=0,
            api_metadata=api_metadata,
        )

        payload = json.loads((self.card / record["relative_path"]).read_text(encoding="utf-8"))
        metadata = payload["api_metadata"]
        self.assertEqual(metadata["provider"], "openai_compatible")
        self.assertEqual(metadata["model"], "test-model")
        self.assertEqual(metadata["status"], "success")
        self.assertEqual(metadata["usage"], {"input_tokens": 12, "output_tokens": 34})
        self.assertEqual(metadata["raw_response"]["id"], "resp_1")
        self.assertEqual(metadata["raw_response"]["choices"][0]["message"]["content"], "ok")
        self.assertEqual(metadata["raw_response"]["headers"], "[redacted]")
        self.assertEqual(metadata["raw_response"]["header"], "[redacted]")
        self.assertEqual(metadata["raw_response"]["body"]["api_key"], "[redacted]")
        self.assertEqual(metadata["raw_response"]["body"]["apiKey"], "[redacted]")
        self.assertEqual(metadata["raw_response"]["body"]["xApiKey"], "[redacted]")
        self.assertEqual(metadata["raw_response"]["body"]["accessToken"], "[redacted]")
        self.assertEqual(metadata["raw_response"]["body"]["refreshToken"], "[redacted]")
        self.assertEqual(metadata["raw_response"]["body"]["error"], "invalid [redacted]")
        self.assertEqual(metadata["raw_response"]["body"]["nested"][0]["token"], "[redacted]")
        self.assertEqual(metadata["raw_response"]["body"]["nested"][1], "Bearer [redacted]")
        serialized_metadata = json.dumps(metadata, ensure_ascii=False)
        self.assertNotIn("nested-token-123", serialized_metadata)
        self.assertNotIn("nested-api-key-123", serialized_metadata)
        self.assertNotIn("header-secret-123", serialized_metadata)
        self.assertNotIn("body-api-key-123", serialized_metadata)
        self.assertNotIn("sk-camel-123", serialized_metadata)
        self.assertNotIn("x-camel-123", serialized_metadata)
        self.assertNotIn("access-camel-123", serialized_metadata)
        self.assertNotIn("refresh-camel-123", serialized_metadata)
        self.assertNotIn("nested-list-token-123", serialized_metadata)
        self.assertNotIn("inline-token-123", serialized_metadata)
        index_item = json.loads((self.card / "debug" / "model_calls" / "index.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(index_item["call_id"], payload["call_id"])
        self.assertNotIn("raw_response", index_item)

    def test_logger_converts_non_json_api_metadata_values(self):
        class SdkObject:
            def __str__(self):
                return "SdkObject(result=ok)"

        logger = self.module.ModelDebugLogger(self.card, "round-000003")

        record = logger.write_call(
            agent_key="gm",
            cwd=str(self.card),
            prompt="# gm",
            stdout="ok",
            returncode=0,
            api_metadata={
                "provider": "openai_compatible",
                "model": "test-model",
                "status": "success",
                "usage": {
                    "input_tokens": 12,
                    "raw_bytes": b"usage-bytes",
                    "token_set": {"a", "b"},
                },
                "raw_response": {
                    "sdk_object": SdkObject(),
                    "binary": b"abc",
                    "items": {1, 2},
                },
            },
        )

        payload = json.loads((self.card / record["relative_path"]).read_text(encoding="utf-8"))
        metadata = payload["api_metadata"]
        self.assertEqual(metadata["usage"]["input_tokens"], 12)
        self.assertIsInstance(metadata["usage"]["raw_bytes"], str)
        self.assertEqual(sorted(metadata["usage"]["token_set"]), ["a", "b"])
        self.assertEqual(metadata["raw_response"]["sdk_object"], "SdkObject(result=ok)")
        self.assertIsInstance(metadata["raw_response"]["binary"], str)
        self.assertEqual(sorted(metadata["raw_response"]["items"]), [1, 2])


if __name__ == "__main__":
    unittest.main()
