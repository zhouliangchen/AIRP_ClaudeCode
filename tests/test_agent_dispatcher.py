import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class AgentDispatcherFoundationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000001"
        self.run_dir.mkdir(parents=True)
        (self.card / ".agent_runs" / "current").write_text(str(self.run_dir.resolve()), encoding="utf-8")
        _write_json(self.run_dir / "manifest.json", {"round_id": "round-000001", "stage": "prepared"})
        _write_json(self.run_dir / "input.json", {"raw_text": "I listen.", "routed_input": {"role_channel": "I listen."}})
        self.dispatcher = _load("agent_dispatcher")
        self.intents = _load("agent_intents")

    def tearDown(self):
        self.tmp.cleanup()

    def test_dispatch_next_blocks_unsupported_intent(self):
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "assets_task", "payload": {"target": "scene"}},
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "unsupported_intent_type")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])

    def test_dispatch_next_uses_oldest_pending_intent(self):
        first = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "assets_task", "payload": {"target": "first"}},
        )["intent"]
        second = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "assets_task", "payload": {"target": "second"}},
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertEqual(result["intent_id"], first["id"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["id"] for item in pending], [second["id"]])

    def test_dispatch_next_blocks_stalled_runtime_when_no_pending_intents(self):
        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "stalled")
        self.assertEqual(result["reason"], "dispatcher_stalled")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "dispatcher_stalled")

    def test_dispatch_next_reports_delivered_without_blocking_when_manifest_delivered(self):
        _write_json(self.run_dir / "manifest.json", {"round_id": "round-000001", "stage": "delivered"})

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(result["reason"], "")
        self.assertEqual(self.intents.list_intents(self.run_dir, "blocked"), [])

    def test_artifact_path_rejects_absolute_relative_path(self):
        absolute = self.run_dir / "escape.json"

        with self.assertRaises(self.dispatcher.AgentDispatcherError):
            self.dispatcher.artifact_path(self.run_dir, str(absolute))

    def test_artifact_path_rejects_parent_escape(self):
        with self.assertRaises(self.dispatcher.AgentDispatcherError):
            self.dispatcher.artifact_path(self.run_dir, "../escape.json")

    def test_dispatch_next_preserves_existing_blocked_manifest_reason(self):
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "assets_task", "payload": {"target": "scene"}},
        )["intent"]

        first_result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)
        manifest_after_first = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        first_history = list(manifest_after_first.get("stage_history", []))
        second_result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)
        manifest_after_second = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertFalse(first_result["ok"])
        self.assertEqual(first_result["reason"], "unsupported_intent_type")
        self.assertFalse(second_result["ok"])
        self.assertEqual(second_result["status"], "blocked")
        self.assertEqual(second_result["intent_id"], "")
        self.assertEqual(second_result["reason"], "unsupported_intent_type")
        self.assertEqual(manifest_after_second["dispatcher"]["reason"], "unsupported_intent_type")
        self.assertEqual(manifest_after_second.get("stage_history", []), first_history)
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])

    def test_analyze_input_completes_and_creates_run_gm_turn(self):
        _write_json(self.run_dir / "input_analysis.output.json", {"analysis_mode": "fixture"})
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "main_agent",
                "type": "analyze_input",
                "payload": {"input_analysis_request_path": "input_analysis.request.md"},
            },
        )["intent"]
        apply_calls = []

        def fake_apply(card_folder, root_dir):
            apply_calls.append((Path(card_folder), Path(root_dir)))
            return {"ok": True, "analysis": {"analysis_mode": "fixture"}}

        self.dispatcher.input_analysis_apply.apply_current_run = fake_apply

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["artifacts"], ["artifacts/input_analysis.output.json"])
        self.assertTrue((self.run_dir / "artifacts" / "input_analysis.output.json").exists())
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_gm_turn"])
        self.assertEqual(result["created_intents"], [pending[0]["id"]])
        self.assertEqual(pending[0]["requested_by"], "input_analyst")
        self.assertEqual(pending[0]["payload"], {"reason": "input_analysis_applied"})
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(len(apply_calls), 1)
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        applied = [item for item in messages if item.get("type") == "analysis_applied"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(result["created_messages"], [applied[0]["id"]])
        self.assertEqual(applied[0]["to"], ["gm", "main_agent"])
        self.assertEqual(applied[0]["payload"]["applied"]["analysis"]["analysis_mode"], "fixture")

    def test_analyze_input_blocks_with_failure_when_apply_raises_after_accept(self):
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "main_agent",
                "type": "analyze_input",
                "payload": {"input_analysis_request_path": "input_analysis.request.md"},
            },
        )["intent"]

        def fail_apply(_card_folder, _root_dir):
            raise RuntimeError("fixture apply exploded")

        self.dispatcher.input_analysis_apply.apply_current_run = fail_apply

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "analyze_input_failed")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertIn("fixture apply exploded", blocked[0]["result"]["outputs"]["error"])
        self.assertEqual(self.intents.list_intents(self.run_dir, "completed"), [])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "analyze_input_failed")
        self.assertIn("fixture apply exploded", manifest["dispatcher"]["detail"]["error"])
        self.assertEqual(self.dispatcher.agent_messages.read_messages(self.run_dir), [])

    def test_analyze_input_reuses_apply_path_analysis_applied_message(self):
        _write_json(self.run_dir / "input_analysis.output.json", {"analysis_mode": "fixture"})
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "main_agent",
                "type": "analyze_input",
                "payload": {"input_analysis_request_path": "input_analysis.request.md"},
            },
        )["intent"]

        def fake_apply(card_folder, root_dir):
            message_result = self.dispatcher.agent_messages.append_message(
                self.run_dir,
                {
                    "from": "input_analyst",
                    "to": ["gm"],
                    "type": "analysis_applied",
                    "visibility": "gm_only",
                    "payload": {
                        "input_path": "input.json",
                        "analysis_path": "input_analysis.output.json",
                        "routed_characters": [],
                    },
                },
            )
            self.assertTrue(message_result["ok"])
            return {"ok": True, "analysis": {"analysis_mode": "fixture"}}

        self.dispatcher.input_analysis_apply.apply_current_run = fake_apply

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        applied = [item for item in messages if item.get("type") == "analysis_applied"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["to"], ["gm"])
        self.assertEqual(result["created_messages"], [applied[0]["id"]])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_gm_turn"])


if __name__ == "__main__":
    unittest.main()
