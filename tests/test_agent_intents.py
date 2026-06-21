import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import time
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

    def test_create_intent_rejects_caller_supplied_id_without_writing_file(self):
        with self.assertRaisesRegex(self.mod.AgentIntentError, "id is assigned by create_intent"):
            self.mod.create_intent(
                self.run_dir,
                {"id": "custom", "requested_by": "gm", "type": "project_message", "payload": {}},
            )

        self.assertFalse((self.run_dir / "intents" / "pending" / "custom.json").exists())
        self.assertEqual(self.mod.list_intents(self.run_dir), [])

    def test_missing_intent_transition_returns_intent_missing(self):
        result = self.mod.accept_intent(self.run_dir, "intent_999999", outputs={"message_id": "msg_000002"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["result"]["reason"], "intent_missing")

    def test_malformed_transition_id_returns_invalid_intent_id_without_writing_files(self):
        transition_calls = [
            ("accept", lambda intent_id: self.mod.accept_intent(self.run_dir, intent_id)),
            ("reject", lambda intent_id: self.mod.reject_intent(self.run_dir, intent_id, "bad")),
            ("complete", lambda intent_id: self.mod.complete_intent(self.run_dir, intent_id)),
            ("block", lambda intent_id: self.mod.block_intent(self.run_dir, intent_id, "bad")),
        ]
        for name, call in transition_calls:
            for intent_id in ["../intent_000001", "custom"]:
                with self.subTest(name=name, intent_id=intent_id):
                    result = call(intent_id)

                    self.assertFalse(result["ok"])
                    self.assertEqual(result["reason"], "invalid_intent_id")
                    self.assertEqual(result["result"]["reason"], "invalid_intent_id")

        self.assertFalse((self.run_dir / "intents").exists())

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

    def test_attach_source_message_updates_pending_intent(self):
        created = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "critic", "type": "repair_request", "payload": {"target": "story"}},
        )["intent"]

        attached = self.mod.attach_source_message(self.run_dir, created["id"], "msg_000001")

        self.assertTrue(attached["ok"])
        self.assertEqual(attached["intent"]["source_message_id"], "msg_000001")
        persisted = json.loads(
            (self.run_dir / "intents" / "pending" / f"{created['id']}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["source_message_id"], "msg_000001")

    def test_attach_source_message_rejects_empty_or_missing_intent(self):
        created = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "critic", "type": "repair_request", "payload": {"target": "story"}},
        )["intent"]

        empty_result = self.mod.attach_source_message(self.run_dir, created["id"], "")
        missing_result = self.mod.attach_source_message(self.run_dir, "intent_999999", "msg_000001")

        self.assertFalse(empty_result["ok"])
        self.assertEqual(empty_result["reason"], "invalid_source_message_id")
        self.assertFalse(missing_result["ok"])
        self.assertEqual(missing_result["reason"], "intent_missing")
        persisted = json.loads(
            (self.run_dir / "intents" / "pending" / f"{created['id']}.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("source_message_id", persisted)

    def test_attach_source_message_rejects_non_pending_intent(self):
        created = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "critic", "type": "repair_request", "payload": {"target": "story"}},
        )["intent"]
        self.mod.block_intent(self.run_dir, created["id"], "blocked_by_test")

        result = self.mod.attach_source_message(self.run_dir, created["id"], "msg_000001")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "intent_not_pending")
        blocked = self.mod.list_intents(self.run_dir, "blocked")[0]
        self.assertNotIn("source_message_id", blocked)

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

    def test_same_process_concurrent_create_intent_allocates_unique_monotonic_ids(self):
        original_write_json = self.mod._write_json

        def delayed_write_json(path, data):
            time.sleep(0.05)
            original_write_json(path, data)

        results = []
        failures = []
        results_lock = threading.Lock()
        self.mod._write_json = delayed_write_json
        try:
            def worker(index):
                try:
                    result = self.mod.create_intent(
                        self.run_dir,
                        {"requested_by": "gm", "type": "project_message", "payload": {"index": index}},
                    )
                    with results_lock:
                        results.append(result["intent"]["id"])
                except Exception as exc:
                    with results_lock:
                        failures.append(repr(exc))

            threads = [threading.Thread(target=worker, args=(index,)) for index in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
        finally:
            self.mod._write_json = original_write_json

        expected = [f"intent_{index:06d}" for index in range(1, 21)]
        self.assertEqual(failures, [])
        self.assertEqual(sorted(results), expected)
        self.assertEqual([item["id"] for item in self.mod.list_intents(self.run_dir)], expected)
        self.assertFalse((self.run_dir / "intents" / ".intents.lock").exists())

    def test_subprocess_concurrent_create_intent_allocates_unique_monotonic_ids(self):
        start_file = self.run_dir / "start.txt"
        code = r"""
import importlib.util
import sys
import time
from pathlib import Path

run_dir = Path(sys.argv[1])
skills_dir = Path(sys.argv[2])
index = int(sys.argv[3])
start_file = run_dir / "start.txt"
sys.path.insert(0, str(skills_dir))
spec = importlib.util.spec_from_file_location("agent_intents", skills_dir / "agent_intents.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original_write_json = module._write_json

def delayed_write_json(path, data):
    time.sleep(0.2)
    original_write_json(path, data)

module._write_json = delayed_write_json
while not start_file.exists():
    time.sleep(0.01)
result = module.create_intent(
    run_dir,
    {"requested_by": "gm", "type": "project_message", "payload": {"index": index}},
)
if not result["ok"]:
    raise RuntimeError(result)
"""
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", code, str(self.run_dir), str(ROOT / "skills"), str(index)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for index in range(20)
        ]
        start_file.write_text("go", encoding="utf-8")

        failures = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            if process.returncode != 0:
                failures.append((process.returncode, stdout, stderr))

        expected = [f"intent_{index:06d}" for index in range(1, 21)]
        listed = self.mod.list_intents(self.run_dir)
        self.assertEqual(failures, [])
        self.assertEqual([item["id"] for item in listed], expected)
        self.assertFalse((self.run_dir / "intents" / ".intents.lock").exists())

    def test_complete_intent_leaves_file_only_in_completed_state(self):
        created = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "project_message", "payload": {}},
        )["intent"]

        self.mod.accept_intent(self.run_dir, created["id"])
        result = self.mod.complete_intent(self.run_dir, created["id"])

        self.assertTrue(result["ok"])
        self.assertFalse((self.run_dir / "intents" / "pending" / f"{created['id']}.json").exists())
        self.assertFalse((self.run_dir / "intents" / "accepted" / f"{created['id']}.json").exists())
        self.assertTrue((self.run_dir / "intents" / "completed" / f"{created['id']}.json").exists())

    def test_file_lock_cleans_up_when_metadata_write_fails(self):
        original_write = self.mod.os.write

        def failing_write(fd, data):
            raise OSError("simulated lock metadata write failure")

        self.mod.os.write = failing_write
        try:
            with self.assertRaises(OSError):
                self.mod.create_intent(
                    self.run_dir,
                    {"requested_by": "gm", "type": "project_message", "payload": {}},
                )
        finally:
            self.mod.os.write = original_write

        self.assertFalse((self.run_dir / "intents" / ".intents.lock").exists())
        result = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "project_message", "payload": {}},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["intent"]["id"], "intent_000001")


if __name__ == "__main__":
    unittest.main()
