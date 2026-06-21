import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_messages():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_messages", ROOT / "skills" / "agent_messages.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentMessagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "round-000001"
        self.run_dir.mkdir()
        self.mod = _load_agent_messages()

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_message_writes_log_and_inbox_indexes(self):
        result = self.mod.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["story", "critic"],
                "type": "message",
                "visibility": "story_facing",
                "payload": {"text": "Raw scene is ready."},
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["message"]["id"], "msg_000001")
        log_rows = self.mod.read_messages(self.run_dir)
        self.assertEqual(len(log_rows), 1)
        self.assertEqual(self.mod.read_inbox(self.run_dir, "story")[0]["id"], "msg_000001")
        self.assertEqual(self.mod.read_inbox(self.run_dir, "critic")[0]["id"], "msg_000001")

    def test_player_cannot_send_directly_to_character(self):
        result = self.mod.append_message(
            self.run_dir,
            {
                "from": "player",
                "to": ["character:Ada"],
                "type": "message",
                "visibility": "actor_facing",
                "payload": {"text": "Hi."},
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "acl_rejected")
        self.assertEqual(self.mod.read_inbox(self.run_dir, "character:Ada"), [])
        self.assertEqual(self.mod.read_messages(self.run_dir)[0]["status"], "rejected")

    def test_actor_facing_gm_message_requires_projection_marker(self):
        result = self.mod.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["character:Ada"],
                "type": "message",
                "visibility": "actor_facing",
                "payload": {"text": "You hear a bell."},
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "projection_required")

    def test_any_message_to_character_requires_projection_marker(self):
        result = self.mod.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["character:Ada"],
                "type": "message",
                "visibility": "gm_only",
                "payload": {"text": "Hidden direction."},
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "projection_required")
        self.assertEqual(self.mod.read_inbox(self.run_dir, "character:Ada"), [])
        self.assertEqual(self.mod.read_messages(self.run_dir)[0]["status"], "rejected")

    def test_projected_message_can_reach_actor_inbox(self):
        result = self.mod.append_message(
            self.run_dir,
            {
                "from": "projection",
                "to": ["character:Ada"],
                "type": "projected_message",
                "visibility": "actor_facing",
                "source_call_id": "call-character-Ada-1",
                "payload": {"gm_prompt": "You hear a bell."},
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(self.mod.read_inbox(self.run_dir, "character:Ada")[0]["type"], "projected_message")

    def test_safe_agent_filename_preserves_unicode_uniqueness(self):
        self.assertEqual(self.mod.safe_agent_filename("gm"), "gm.jsonl")
        self.assertEqual(self.mod.safe_agent_filename("character:Ada"), "character_Ada.jsonl")
        su_li = self.mod.safe_agent_filename("character:苏黎")
        lin_yu = self.mod.safe_agent_filename("character:林雨")
        self.assertNotEqual(su_li, lin_yu)
        self.assertTrue(su_li.endswith(".jsonl"))
        self.assertTrue(lin_yu.endswith(".jsonl"))

    def test_safe_agent_filename_avoids_escape_shaped_collision(self):
        accented = self.mod.safe_agent_filename("character:é")
        escaped_literal = self.mod.safe_agent_filename("character:_u0000e9")
        self.assertNotEqual(accented, escaped_literal)
        self.assertTrue(accented.endswith(".jsonl"))
        self.assertTrue(escaped_literal.endswith(".jsonl"))

    def test_repeated_messages_preserve_monotonic_ids(self):
        first = self.mod.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["story"],
                "type": "message",
                "visibility": "story_facing",
                "payload": {"text": "One."},
            },
        )
        second = self.mod.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["character:Ada"],
                "type": "message",
                "visibility": "gm_only",
                "payload": {"text": "Two."},
            },
        )
        third = self.mod.append_message(
            self.run_dir,
            {
                "from": "projection",
                "to": ["character:Ada"],
                "type": "projected_message",
                "visibility": "actor_facing",
                "payload": {"text": "Three."},
            },
        )

        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertTrue(third["ok"])
        self.assertEqual([row["id"] for row in self.mod.read_messages(self.run_dir)], ["msg_000001", "msg_000002", "msg_000003"])

    def test_concurrent_appends_preserve_unique_monotonic_ids(self):
        def append(index):
            self.mod.append_message(
                self.run_dir,
                {
                    "from": "gm",
                    "to": ["story"],
                    "type": "message",
                    "visibility": "story_facing",
                    "payload": {"text": f"Message {index}."},
                },
            )

        threads = [threading.Thread(target=append, args=(index,)) for index in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        ids = [row["id"] for row in self.mod.read_messages(self.run_dir)]
        expected = [f"msg_{index:06d}" for index in range(1, 21)]
        self.assertEqual(sorted(ids), expected)
        self.assertEqual(len(set(ids)), 20)


if __name__ == "__main__":
    unittest.main()
