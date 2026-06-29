import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


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
        self.capability_executors = _load("capability_executors")
        self.llm_settings = importlib.import_module("llm_settings")
        self.original_frontend_settings_path = self.llm_settings.DEFAULT_FRONTEND_SETTINGS_PATH
        self.original_local_settings_path = self.llm_settings.DEFAULT_LOCAL_SETTINGS_PATH
        self.original_environ = os.environ.copy()
        for key in (
            "OPENAI_API_KEY",
            "AIRP_IMAGE_GENERATION_API_KEY",
            "AIRP_IMAGE_GENERATION_BASE_URL",
            "AIRP_IMAGE_GENERATION_MODEL",
        ):
            os.environ.pop(key, None)

    def tearDown(self):
        self.llm_settings.DEFAULT_FRONTEND_SETTINGS_PATH = self.original_frontend_settings_path
        self.llm_settings.DEFAULT_LOCAL_SETTINGS_PATH = self.original_local_settings_path
        os.environ.clear()
        os.environ.update(self.original_environ)
        self.tmp.cleanup()

    def _scene_asset_plan(self, context):
        round_id = Path(context.get("run_dir") or self.run_dir).name
        return {
            "schema_version": 1,
            "plan_id": "test-assets-plan",
            "style_state": {"has_style_reference": False, "style_reference_paths": [], "art_style": ""},
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": f"scene-{round_id}",
                    "round_id": round_id,
                    "kind": "scene_illustration",
                    "target": "scene_illustration",
                    "prompt": "rainy street",
                    "display_policy": "story_inline",
                    "camera_perspective": "third_person_camera",
                    "scene_mode": "story_scene",
                }
            ],
            "ui_patch_requests": [],
            "rename_operations": [],
        }

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

        with mock.patch.object(
            self.capability_executors.assets_ui_runtime.assets_ui_agent,
            "plan_assets_task",
            side_effect=self._scene_asset_plan,
        ):
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
        self.assertEqual(completed["result"]["outputs"]["jobs"][0]["reason"], "asset_worker_not_configured")
        artifact = _read_json(self.run_dir / "artifacts" / "runtime_pump" / "after_critic.json")
        self.assertEqual(artifact["processed"][0]["intent_id"], created["id"])

    def test_run_pending_intents_starts_assets_task_without_waiting_when_async_enabled(self):
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
        original_execute = self.pump.execute_intent

        def slow_execute(*args, **kwargs):
            time.sleep(0.2)
            return {"status": "completed", "outputs": {"status": "deferred", "jobs": []}}

        self.pump.execute_intent = slow_execute
        try:
            started_at = time.perf_counter()
            result = self.pump.run_pending_intents(
                self.card,
                self.run_dir,
                phase="after_critic",
                async_asset_tasks=True,
            )
            elapsed = time.perf_counter() - started_at

            self.assertLess(elapsed, 0.15)
            self.assertEqual(result["processed"][0]["intent_id"], created["id"])
            self.assertEqual(result["processed"][0]["status"], "started")
            self.assertTrue(result["processed"][0]["outputs"]["nonblocking"])

            deadline = time.time() + 2
            completed = []
            while time.time() < deadline:
                completed = self.intents.list_intents(self.run_dir, "completed")
                if completed:
                    break
                time.sleep(0.02)
            self.assertEqual(completed[0]["id"], created["id"])
            self.assertEqual(completed[0]["result"]["outputs"]["status"], "deferred")
        finally:
            self.pump.execute_intent = original_execute

    def test_assets_task_executor_delegates_to_assets_ui_runtime(self):
        intent = {
            "id": "intent-assets-1",
            "type": "assets_task",
            "payload": {
                "kind": "scene_illustration",
                "target": "scene_illustration",
                "prompt": "rainy night street",
            },
        }

        result = self.capability_executors.execute_assets_task(
            self.card,
            self.run_dir,
            intent,
            phase="after_critic",
        )

        self.assertEqual(result["status"], "completed")
        self.assertIn("jobs", result["outputs"])
        self.assertTrue((self.run_dir / "artifacts" / "assets_ui" / "intent-assets-1.json").exists())

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

    def test_assets_task_ignores_legacy_image_config_for_worker_start(self):
        frontend_path = Path(self.tmp.name) / "styles" / "llm_settings.frontend.json"
        local_path = Path(self.tmp.name) / "styles" / "llm_settings.local.json"
        frontend_path.parent.mkdir(parents=True)
        frontend_path.write_text(
            json.dumps({"image_generation": {"api_key": ""}}, ensure_ascii=False),
            encoding="utf-8",
        )
        local_path.write_text(
            json.dumps({"image_generation": {"api_key": ""}}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.llm_settings.DEFAULT_FRONTEND_SETTINGS_PATH = frontend_path
        self.llm_settings.DEFAULT_LOCAL_SETTINGS_PATH = local_path
        (self.card / "image_config.local.json").write_text(
            json.dumps({"api_key": "legacy-key"}, ensure_ascii=False),
            encoding="utf-8",
        )
        intent = {
            "id": "intent-assets-1",
            "type": "assets_task",
            "payload": {"kind": "scene", "target": "rain", "prompt": "rainy street"},
        }
        calls = []

        def run_command(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")

        with mock.patch.object(
            self.capability_executors.assets_ui_runtime.assets_ui_agent,
            "plan_assets_task",
            side_effect=self._scene_asset_plan,
        ):
            result = self.capability_executors.execute_assets_task(
                self.card,
                self.run_dir,
                intent,
                phase="after_critic",
                run_command=run_command,
            )

        self.assertEqual(result["outputs"]["status"], "deferred")
        self.assertEqual(result["outputs"]["jobs"][0]["reason"], "asset_worker_not_configured")
        self.assertEqual(calls, [])

    def test_run_pending_intents_materializes_replay_plan_without_confirmation(self):
        backup_id = "round-000001-20260623T000000000000Z-abc123def456"
        backup_dir = self.card / "backup" / backup_id
        backup_dir.mkdir(parents=True)
        (backup_dir / "backup.json").write_text("{}", encoding="utf-8")
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "input_analyst",
                "type": "replay_plan",
                "payload": {
                    "capability_request_id": "cap-replay",
                    "capability": "replay.plan",
                    "requested_by": "input_analyst",
                    "payload": {
                        "schema_version": 1,
                        "scope": "single_round",
                        "plan_id": "replay-001",
                        "backup_id": backup_id,
                        "affected_inputs": [
                            {
                                "round_id": "round-000001",
                                "input_id": "input-1",
                                "role_text": "first role",
                                "user_instruction_text": "",
                            }
                        ],
                    },
                    "policy": {
                        "source_channel": "user_instruction",
                        "risk": "high",
                        "authorization_gate": "none",
                    },
                },
            },
        )["intent"]

        result = self.pump.run_pending_intents(
            self.card,
            self.run_dir,
            phase="after_input_analysis",
        )

        self.assertEqual([item["intent_id"] for item in result["processed"]], [created["id"]])
        self.assertEqual([item["status"] for item in result["processed"]], ["completed"])
        completed = self.intents.list_intents(self.run_dir, "completed")[0]
        self.assertEqual(completed["result"]["outputs"]["session_id"], "replay-001")
        self.assertTrue((self.card / ".replay" / "sessions" / "replay-001" / "plan.json").exists())
        self.assertTrue((self.card / ".replay" / "sessions" / "replay-001" / "status.json").exists())

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
