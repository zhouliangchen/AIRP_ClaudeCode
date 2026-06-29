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


class AssetsUiAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000003"
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "artifacts").mkdir(parents=True)
        (self.run_dir / "artifacts" / "story.output.json").write_text(
            json.dumps({"content": "你在雨夜剧院看见苏黎站在舞台边。"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.card / "memory" / "characters" / "苏黎").mkdir(parents=True)
        (self.card / "memory" / "characters" / "苏黎" / "profile.md").write_text(
            "苏黎：银灰短发，黑色风衣，左眼下有细小泪痣。",
            encoding="utf-8",
        )
        self.mod = _load("assets_ui_agent")

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_assets_task_calls_assets_ui_agent_and_validates_scene_job(self):
        calls = []

        def fake_runner(agent_key, prompt, cwd):
            calls.append((agent_key, prompt, cwd))
            return json.dumps(
                {
                    "schema_version": 1,
                    "plan_id": "assets-round-000003",
                    "style_state": {"has_style_reference": False, "style_reference_paths": [], "art_style": ""},
                    "jobs": [
                        {
                            "queue_type": "scene_illustration",
                            "job_id": "scene-round-000003",
                            "round_id": "round-000003",
                            "prompt": "画面目标：雨夜剧院的剧情插图。镜头：第三人称摄像机视角。",
                            "characters": ["苏黎"],
                            "important_characters": ["苏黎"],
                            "character_appearances": [{"name": "苏黎", "appearance_state": "", "description": "黑色风衣"}],
                            "camera_perspective": "third_person_camera",
                            "perspective_reason": "需要同时呈现角色与舞台空间。",
                            "scene_mode": "story_scene",
                            "reference_candidates": [
                                {"path": "generated/characters/苏黎/苏黎.png", "purpose": "character_reference"}
                            ],
                            "display_policy": "story_inline",
                        }
                    ],
                    "ui_patch_requests": [],
                    "rename_operations": [],
                },
                ensure_ascii=False,
            )

        context = {
            "card_path": self.card,
            "run_dir": self.run_dir,
            "phase": "after_critic",
            "payload": {"characters": ["苏黎"], "reference_policy": "required"},
            "story_output": {"content": "你在雨夜剧院看见苏黎站在舞台边。"},
            "character_profiles": {"苏黎": "苏黎：银灰短发，黑色风衣。"},
        }
        plan = self.mod.plan_assets_task(context, llm_run=fake_runner)

        self.assertEqual(calls[0][0], "assets-ui")
        self.assertIn("严格 JSON", calls[0][1])
        self.assertIn("generated/characters", calls[0][1])
        self.assertEqual(plan["jobs"][0]["queue_type"], "scene_illustration")
        self.assertEqual(plan["jobs"][0]["display_policy"], "story_inline")

    def test_plan_assets_task_rejects_invalid_json_without_fallback(self):
        context = {"card_path": self.card, "run_dir": self.run_dir, "payload": {}}

        with self.assertRaisesRegex(self.mod.AssetsUiAgentError, "invalid_json"):
            self.mod.plan_assets_task(context, llm_run=lambda agent_key, prompt, cwd: "not json")

    def test_plan_assets_task_rejects_ui_patch_request_outside_card_scope(self):
        payload = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "style_state": {"has_style_reference": True, "style_reference_paths": [], "art_style": ""},
            "jobs": [],
            "ui_patch_requests": [{"scope": "global", "target_card": "card", "acceptance": ["bad"]}],
            "rename_operations": [],
        }
        context = {"card_path": self.card, "run_dir": self.run_dir, "payload": {}}

        with self.assertRaisesRegex(self.mod.AssetsUiAgentError, "ui_patch_scope"):
            self.mod.plan_assets_task(
                context,
                llm_run=lambda agent_key, prompt, cwd: json.dumps(payload, ensure_ascii=False),
            )

    def test_validate_plan_rejects_missing_required_top_level_lists(self):
        with self.assertRaisesRegex(self.mod.AssetsUiAgentError, "invalid_jobs"):
            self.mod.validate_plan({"schema_version": 1})

    def test_validate_plan_rejects_string_schema_version(self):
        payload = {
            "schema_version": "1",
            "jobs": [],
            "ui_patch_requests": [],
            "rename_operations": [],
        }

        with self.assertRaisesRegex(self.mod.AssetsUiAgentError, "invalid_schema_version"):
            self.mod.validate_plan(payload)

    def test_validate_plan_rejects_bool_schema_version(self):
        payload = {
            "schema_version": True,
            "jobs": [],
            "ui_patch_requests": [],
            "rename_operations": [],
        }

        with self.assertRaisesRegex(self.mod.AssetsUiAgentError, "invalid_schema_version"):
            self.mod.validate_plan(payload)

    def test_validate_plan_rejects_character_reference_selection_story_inline(self):
        payload = {
            "schema_version": 1,
            "jobs": [
                {
                    "queue_type": "character_reference_selection",
                    "display_policy": "story_inline",
                }
            ],
            "ui_patch_requests": [],
            "rename_operations": [],
        }

        with self.assertRaisesRegex(self.mod.AssetsUiAgentError, "invalid_display_policy"):
            self.mod.validate_plan(payload)

    def test_validate_plan_rejects_ui_patch_job_outside_card_scope(self):
        payload = {
            "schema_version": 1,
            "jobs": [
                {
                    "queue_type": "ui_patch_request",
                    "scope": "global",
                }
            ],
            "ui_patch_requests": [],
            "rename_operations": [],
        }

        with self.assertRaisesRegex(self.mod.AssetsUiAgentError, "ui_patch_scope"):
            self.mod.validate_plan(payload)
