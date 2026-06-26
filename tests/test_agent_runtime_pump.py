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


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class AgentRuntimePumpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000001"
        self.run_dir.mkdir(parents=True)
        self.intents = _load("agent_intents")
        self.pump = _load("agent_runtime_pump")

    def tearDown(self):
        self.tmp.cleanup()

    def test_after_input_analysis_leaves_assets_task_pending_for_after_critic(self):
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "input_analyst",
                "type": "assets_task",
                "payload": {
                    "kind": "scene",
                    "target": "rain",
                    "prompt": "rainy street",
                },
            },
        )["intent"]

        result = self.pump.run_pending_intents(
            self.card,
            self.run_dir,
            phase="after_input_analysis",
        )

        self.assertEqual(result["phase"], "after_input_analysis")
        self.assertEqual(result["processed"], [])
        self.assertEqual(result["skipped"][0]["intent_id"], created["id"])
        self.assertEqual(result["skipped"][0]["reason"], "phase_deferred")
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual(pending[0]["id"], created["id"])

    def test_run_pending_intents_completes_assets_task_as_deferred_after_critic(self):
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "input_analyst",
                "type": "assets_task",
                "payload": {
                    "kind": "scene",
                    "target": "rain",
                    "prompt": "rainy street",
                },
            },
        )["intent"]

        result = self.pump.run_pending_intents(
            self.card,
            self.run_dir,
            phase="after_critic",
        )

        self.assertEqual(result["phase"], "after_critic")
        self.assertEqual(result["processed"][0]["intent_id"], created["id"])
        self.assertEqual(result["processed"][0]["status"], "completed")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        completed = self.intents.list_intents(self.run_dir, "completed")[0]
        self.assertEqual(completed["result"]["outputs"]["status"], "deferred")
        artifact = _read_json(self.run_dir / "artifacts" / "runtime_pump" / "after_critic.json")
        self.assertEqual(artifact["processed"][0]["intent_id"], created["id"])

    def test_assets_task_applies_ui_schema_contract_update_before_completion(self):
        self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "input_analyst",
                "type": "assets_task",
                "payload": {
                    "kind": "ui_background",
                    "target": "status_panel",
                    "prompt": "quiet status panel",
                    "ui_schema": {
                        "postprocess_data_required": ["ui_extensions.status_panel"],
                    },
                    "postprocess_contract": {
                        "ui_extensions": {
                            "status_panel": {
                                "type": "object",
                                "required": ["mood"],
                            }
                        }
                    },
                },
            },
        )

        result = self.pump.run_pending_intents(
            self.card,
            self.run_dir,
            phase="after_critic",
        )

        self.assertEqual(result["processed"][0]["status"], "completed")
        self.assertTrue((self.card / "ui_manifest.json").exists())
        self.assertTrue((self.card / "postprocess_contract.json").exists())
        completed = self.intents.list_intents(self.run_dir, "completed")[0]
        update = completed["result"]["outputs"]["postprocess_contract_update"]
        self.assertEqual(update["ui_schema_status"], "applied")
        self.assertEqual(update["postprocess_contract_status"], "synced")

    def test_run_pending_intents_blocks_replay_plan_without_confirmation(self):
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "input_analyst",
                "type": "replay_plan",
                "payload": {
                    "schema_version": 1,
                    "plan_id": "plan-1",
                    "snapshot_id": "round-000001-20260623T000000000000Z-abc123def456",
                    "affected_rounds": ["round-000001"],
                    "requires_manual_confirmation": True,
                    "reason": "Retcon needs replay.",
                    "summary": "Replay one round.",
                },
            },
        )["intent"]

        result = self.pump.run_pending_intents(
            self.card,
            self.run_dir,
            phase="after_input_analysis",
        )

        self.assertEqual(result["blocked"][0]["intent_id"], created["id"])
        blocked = self.intents.list_intents(self.run_dir, "blocked")[0]
        self.assertEqual(blocked["result"]["reason"], "manual_confirmation_required")

    def test_run_pending_intents_blocks_system_request_without_authorization(self):
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "input_analyst",
                "type": "system_request",
                "payload": {
                    "summary": "Add export support.",
                    "authorization_gate": "allowSourceCodeSelfRepair",
                },
            },
        )["intent"]

        result = self.pump.run_pending_intents(
            self.card,
            self.run_dir,
            phase="after_input_analysis",
            runtime_settings={"allowSourceCodeSelfRepair": False},
        )

        self.assertEqual(result["blocked"][0]["intent_id"], created["id"])
        blocked = self.intents.list_intents(self.run_dir, "blocked")[0]
        self.assertEqual(blocked["result"]["reason"], "source_code_self_repair_not_authorized")

    def test_run_pending_intents_blocks_unknown_executor(self):
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "unknown_task",
                "payload": {"x": 1},
            },
        )["intent"]

        result = self.pump.run_pending_intents(
            self.card,
            self.run_dir,
            phase="after_input_analysis",
        )

        self.assertEqual(result["blocked"][0]["intent_id"], created["id"])
        blocked = self.intents.list_intents(self.run_dir, "blocked")[0]
        self.assertEqual(blocked["result"]["reason"], "executor_not_wired")


if __name__ == "__main__":
    unittest.main()
