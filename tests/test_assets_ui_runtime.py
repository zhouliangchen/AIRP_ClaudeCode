import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


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


class AssetsUiRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000004"
        self.run_dir.mkdir(parents=True)
        (self.card / "memory" / "characters" / "苏黎").mkdir(parents=True)
        (self.card / "memory" / "characters" / "苏黎" / "profile.md").write_text(
            "苏黎：银灰短发，黑色风衣，左眼下有细小泪痣。", encoding="utf-8"
        )
        self.mod = _load("assets_ui_runtime")
        self.llm_settings = self.mod.llm_settings
        self.original_frontend_settings_path = self.llm_settings.DEFAULT_FRONTEND_SETTINGS_PATH
        self.original_local_settings_path = self.llm_settings.DEFAULT_LOCAL_SETTINGS_PATH
        self.original_environ = os.environ.copy()
        for key in (
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

    def _configure_image_settings(self):
        frontend = Path(self.tmp.name) / "styles" / "llm_settings.frontend.json"
        local = Path(self.tmp.name) / "styles" / "llm_settings.local.json"
        frontend.parent.mkdir(parents=True)
        frontend.write_text(
            json.dumps(
                {
                    "image_generation": {
                        "base_url": "https://image.example/v1",
                        "api_key": "image-secret",
                        "model": "image-model",
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        local.write_text(json.dumps({"image_generation": {}}, ensure_ascii=False), encoding="utf-8")
        self.llm_settings.DEFAULT_FRONTEND_SETTINGS_PATH = frontend
        self.llm_settings.DEFAULT_LOCAL_SETTINGS_PATH = local

    def test_process_assets_task_persists_requirement_and_waits_for_missing_character_reference(self):
        intent = {
            "id": "intent-assets-1",
            "type": "assets_task",
            "payload": {
                "kind": "scene_illustration",
                "target": "scene_illustration",
                "prompt": "苏黎站在雨夜教室窗边",
                "asset_requirement": {
                    "scene_illustration_each_round": True,
                    "reason": "从本轮开始每轮必须提供剧情插图",
                },
                "characters": ["苏黎"],
                "reference_policy": "required",
            },
        }

        result = self.mod.process_assets_task(
            self.card,
            self.run_dir,
            intent,
            phase="after_critic",
            planner=lambda context: {
                "schema_version": 1,
                "asset_requirement_update": {
                    "scene_illustration_each_round": True,
                    "reason": "从本轮开始每轮必须提供剧情插图",
                },
                "character_reference_jobs": [
                    {
                        "job_id": "character-suli-reference",
                        "character_name": "苏黎",
                        "target_path": "characters/苏黎/苏黎.png",
                        "prompt": "professional character sheet for Su Li",
                    }
                ],
                "scene_jobs": [
                    {
                        "job_id": "scene-round-000004",
                        "kind": "scene_illustration",
                        "target": "scene_illustration",
                        "prompt": "cinematic rainy classroom with Su Li",
                        "characters": ["苏黎"],
                        "reference_policy": "required",
                        "reference_candidates": ["characters/苏黎/苏黎.png"],
                    }
                ],
            },
        )

        self.assertEqual(result["status"], "completed")
        outputs = result["outputs"]
        self.assertEqual(outputs["status"], "waiting_on_references")
        self.assertEqual(outputs["jobs"][0]["kind"], "character_reference")
        self.assertEqual(outputs["jobs"][1]["status"], "waiting_on_references")
        self.assertEqual(outputs["jobs"][1]["missing_references"], ["characters/苏黎/苏黎.png"])
        manifest = _read_json(self.card / "ui_manifest.json")
        self.assertTrue(manifest["asset_requirements"]["scene_illustration_each_round"]["enabled"])
        self.assertTrue((self.card / "generated" / "jobs" / "scene-round-000004.json").exists())
        audit = _read_json(self.run_dir / "artifacts" / "assets_ui" / "intent-assets-1.json")
        self.assertEqual(audit["intent_id"], "intent-assets-1")

    def test_process_assets_task_starts_scene_job_when_references_exist(self):
        self._configure_image_settings()
        reference = self.card / "characters" / "苏黎" / "苏黎.png"
        reference.parent.mkdir(parents=True)
        reference.write_bytes(b"png")
        calls = []

        def run_command(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")

        result = self.mod.process_assets_task(
            self.card,
            self.run_dir,
            {
                "id": "intent-assets-2",
                "type": "assets_task",
                "payload": {"kind": "scene_illustration", "target": "scene_illustration", "prompt": "苏黎在雨中"},
            },
            phase="after_critic",
            run_command=run_command,
            planner=lambda context: {
                "schema_version": 1,
                "scene_jobs": [
                    {
                        "job_id": "scene-round-000004",
                        "kind": "scene_illustration",
                        "target": "scene_illustration",
                        "prompt": "cinematic rainy scene",
                        "characters": ["苏黎"],
                        "reference_policy": "optional",
                        "reference_candidates": ["characters/苏黎/苏黎.png"],
                    }
                ],
            },
        )

        self.assertEqual(result["outputs"]["jobs"][0]["status"], "queued")
        command = calls[0][0][0]
        self.assertIn("--reference", command)
        self.assertIn("characters/苏黎/苏黎.png", command)
        self.assertIn("--job-id", command)

    def test_process_persistent_requirements_noops_without_requirement(self):
        result = self.mod.process_persistent_requirements(
            self.card,
            self.run_dir,
            phase="after_critic",
            planner=lambda context: self.fail("planner should not run without requirements"),
        )

        self.assertEqual(result["status"], "not_required")
        self.assertEqual(result["jobs"], [])
