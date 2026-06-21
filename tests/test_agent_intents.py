import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_intents():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_intents", ROOT / "skills" / "agent_intents.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentIntentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "round-000001"
        self.run_dir.mkdir()
        self.mod = _load_agent_intents()

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_intent_writes_pending_file(self):
        result = self.mod.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "dispatch_actor",
                "source_message_id": "msg_000001",
                "payload": {"actor_id": "character:Ada"},
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent"]["id"], "intent_000001")
        self.assertTrue((self.run_dir / "intents" / "pending" / "intent_000001.json").exists())

    def test_accept_and_complete_intent_moves_files_and_records_result(self):
        created = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "project_message", "payload": {"target": "player"}},
        )["intent"]

        accepted = self.mod.accept_intent(self.run_dir, created["id"], outputs={"message_id": "msg_000002"})
        self.assertTrue(accepted["ok"])
        self.assertTrue((self.run_dir / "intents" / "accepted" / f"{created['id']}.json").exists())

        completed = self.mod.complete_intent(self.run_dir, created["id"], outputs={"artifact": "actor.outputs.json"})
        self.assertTrue(completed["ok"])
        self.assertTrue((self.run_dir / "intents" / "completed" / f"{created['id']}.json").exists())
        self.assertEqual(completed["result"]["status"], "completed")

    def test_reject_intent_records_reason(self):
        created = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "player", "type": "rollback", "payload": {}},
        )["intent"]
        rejected = self.mod.reject_intent(self.run_dir, created["id"], "acl_rejected")

        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["result"]["reason"], "acl_rejected")
        self.assertTrue((self.run_dir / "intents" / "rejected" / f"{created['id']}.json").exists())

    def test_create_intent_requires_structured_payload_fields(self):
        invalid_payloads = [
            {},
            {"requested_by": "", "type": "project_message", "payload": {}},
            {"requested_by": "gm", "type": "", "payload": {}},
            {"requested_by": "gm", "type": "project_message", "payload": []},
            {"requested_by": "gm", "type": "project_message", "payload": {}, "source_message_id": 1},
            {"requested_by": "gm", "type": "project_message", "payload": {}, "policy": []},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(self.mod.AgentIntentError):
                    self.mod.create_intent(self.run_dir, payload)

    def test_create_intent_allocates_next_id_after_existing_files(self):
        first = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "project_message", "payload": {}},
        )["intent"]
        self.mod.accept_intent(self.run_dir, first["id"])

        second = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "dispatch_actor", "payload": {"actor_id": "character:Ada"}},
        )["intent"]

        self.assertEqual(second["id"], "intent_000002")
        self.assertTrue((self.run_dir / "intents" / "pending" / "intent_000002.json").exists())

    def test_normalize_intent_rejects_duplicate_id(self):
        created = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "project_message", "payload": {}},
        )["intent"]

        with self.assertRaises(self.mod.AgentIntentError):
            self.mod.normalize_intent(
                self.run_dir,
                {"id": created["id"], "requested_by": "gm", "type": "project_message", "payload": {}},
            )

    def test_missing_intent_transition_returns_intent_missing(self):
        result = self.mod.accept_intent(self.run_dir, "intent_999999", outputs={"message_id": "msg_000002"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["result"]["reason"], "intent_missing")

    def test_block_intent_records_reason_and_returns_false(self):
        created = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "dispatch_actor", "payload": {"actor_id": "character:Ada"}},
        )["intent"]

        blocked = self.mod.block_intent(self.run_dir, created["id"], "waiting_for_player")

        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["result"]["status"], "blocked")
        self.assertEqual(blocked["result"]["reason"], "waiting_for_player")
        self.assertTrue((self.run_dir / "intents" / "blocked" / f"{created['id']}.json").exists())

    def test_list_intents_reads_state_in_id_order(self):
        first = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "project_message", "payload": {"target": "player"}},
        )["intent"]
        second = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "dispatch_actor", "payload": {"actor_id": "character:Ada"}},
        )["intent"]

        listed = self.mod.list_intents(self.run_dir)

        self.assertEqual([item["id"] for item in listed], [first["id"], second["id"]])

    def test_intent_json_is_utf8_without_ascii_escaping(self):
        text = "\u4f60\u597d"
        created = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "project_message", "payload": {"text": text}},
        )["intent"]

        raw = (self.run_dir / "intents" / "pending" / f"{created['id']}.json").read_bytes()
        self.assertIn(text.encode("utf-8"), raw)
        loaded = json.loads(raw.decode("utf-8"))
        self.assertEqual(loaded["payload"]["text"], text)


if __name__ == "__main__":
    unittest.main()
