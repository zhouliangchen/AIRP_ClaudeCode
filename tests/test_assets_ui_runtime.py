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
        self.assertNotIn("--reference", command)
        self.assertEqual(len(result["outputs"]["jobs"][0]["resolved_references"]), 1)
        self.assertIn("--job-id", command)

    def test_process_assets_task_preserves_required_reference_worker_deferred_reason(self):
        self._configure_image_settings()
        reference = self.card / "characters" / "Ada" / "Ada.png"
        reference.parent.mkdir(parents=True)
        reference.write_bytes(b"png")

        def run_command(*args, **kwargs):
            return SimpleNamespace(
                returncode=1,
                stdout=json.dumps(
                    {
                        "ok": False,
                        "status": "deferred",
                        "error": "reference_image_not_supported",
                        "references": ["characters/Ada/Ada.png"],
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )

        result = self.mod.process_assets_task(
            self.card,
            self.run_dir,
            {
                "id": "intent-required-reference",
                "type": "assets_task",
                "payload": {"kind": "scene_illustration", "target": "scene_illustration", "prompt": "Ada scene"},
            },
            phase="after_critic",
            run_command=run_command,
            planner=lambda context: {
                "schema_version": 1,
                "scene_jobs": [
                    {
                        "job_id": "scene-required-reference",
                        "kind": "scene_illustration",
                        "target": "scene_illustration",
                        "prompt": "Ada scene",
                        "characters": ["Ada"],
                        "reference_policy": "required",
                        "reference_candidates": ["characters/Ada/Ada.png"],
                    }
                ],
            },
        )

        job = result["outputs"]["jobs"][0]
        self.assertEqual(job["status"], "deferred")
        self.assertEqual(job["reason"], "reference_image_not_supported")
        written = _read_json(self.card / "generated" / "jobs" / "scene-required-reference.json")
        self.assertEqual(written["reason"], "reference_image_not_supported")

    def test_process_assets_task_defers_worker_image_generation_failure(self):
        self._configure_image_settings()

        def run_command(*args, **kwargs):
            return SimpleNamespace(
                returncode=1,
                stdout=json.dumps(
                    {
                        "ok": False,
                        "status": "failed",
                        "reason": "image_generation_failed",
                        "error": "HTTP 404: Images API is not supported",
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )

        result = self.mod.process_assets_task(
            self.card,
            self.run_dir,
            {
                "id": "intent-image-api-failed",
                "type": "assets_task",
                "payload": {"kind": "scene_illustration", "target": "scene_illustration", "prompt": "scene"},
            },
            phase="after_critic",
            run_command=run_command,
            planner=lambda context: {
                "schema_version": 1,
                "scene_jobs": [
                    {
                        "job_id": "scene-image-api-failed",
                        "kind": "scene_illustration",
                        "target": "scene_illustration",
                        "prompt": "scene",
                    }
                ],
            },
        )

        job = result["outputs"]["jobs"][0]
        self.assertEqual(result["outputs"]["status"], "deferred")
        self.assertEqual(job["status"], "deferred")
        self.assertEqual(job["reason"], "image_generation_failed")
        written = _read_json(self.card / "generated" / "jobs" / "scene-image-api-failed.json")
        self.assertEqual(written["status"], "deferred")

    def test_process_persistent_requirements_noops_without_requirement(self):
        result = self.mod.process_persistent_requirements(
            self.card,
            self.run_dir,
            phase="after_critic",
            planner=lambda context: self.fail("planner should not run without requirements"),
        )

        self.assertEqual(result["status"], "not_required")
        self.assertEqual(result["jobs"], [])

    def test_process_assets_task_marks_invalid_planner_paths_as_failed_without_raising(self):
        unsafe_paths = [
            "C:tmp/foo.png",
            "C:/tmp/foo.png",
            "/tmp/foo.png",
            "../escape.png",
        ]
        for unsafe_path in unsafe_paths:
            with self.subTest(unsafe_path=unsafe_path):
                result = self.mod.process_assets_task(
                    self.card,
                    self.run_dir,
                    {
                        "id": f"intent-invalid-{unsafe_path}",
                        "type": "assets_task",
                        "payload": {
                            "kind": "scene_illustration",
                            "target": "scene_illustration",
                            "prompt": "bad path test",
                        },
                    },
                    phase="after_critic",
                    planner=lambda context, path=unsafe_path: {
                        "schema_version": 1,
                        "scene_jobs": [
                            {
                                "job_id": "scene-invalid-path",
                                "kind": "scene_illustration",
                                "target": "scene_illustration",
                                "prompt": "scene",
                                "reference_policy": "optional",
                                "reference_candidates": [path],
                            }
                        ],
                    },
                )

                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["outputs"]["status"], "failed")
                self.assertEqual(result["outputs"]["jobs"][0]["status"], "failed")
                self.assertEqual(result["outputs"]["jobs"][0]["reason"], "invalid_asset_path")
                self.assertEqual(result["outputs"]["jobs"][0]["invalid_path"], unsafe_path)

    def test_process_assets_task_ignores_unsafe_character_profile_names(self):
        captured = {}
        (self.card / "memory" / "escape").mkdir(parents=True)
        (self.card / "memory" / "escape" / "profile.md").write_text("SHOULD_NOT_LOAD", encoding="utf-8")

        def planner(context):
            captured["character_profiles"] = context["character_profiles"]
            return {"schema_version": 1, "scene_jobs": []}

        result = self.mod.process_assets_task(
            self.card,
            self.run_dir,
            {
                "id": "intent-unsafe-characters",
                "type": "assets_task",
                "payload": {
                    "kind": "scene_illustration",
                    "target": "scene_illustration",
                    "prompt": "character profile safety",
                    "characters": ["苏黎", "../escape", "C:bad", "nested/name"],
                },
            },
            phase="after_critic",
            planner=planner,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outputs"]["status"], "not_required")
        self.assertEqual(captured["character_profiles"], {"苏黎": "苏黎：银灰短发，黑色风衣，左眼下有细小泪痣。"})

    def test_process_assets_task_default_plan_waits_for_missing_required_character_references(self):
        result = self.mod.process_assets_task(
            self.card,
            self.run_dir,
            {
                "id": "intent-default-plan",
                "type": "assets_task",
                "payload": {
                    "kind": "scene_illustration",
                    "target": "scene_illustration",
                    "prompt": "苏黎站在雨中",
                    "characters": ["苏黎"],
                    "reference_policy": "required",
                },
            },
            phase="after_critic",
            planner=None,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outputs"]["status"], "waiting_on_references")
        self.assertEqual(result["outputs"]["jobs"][0]["kind"], "character_reference")
        self.assertEqual(result["outputs"]["jobs"][0]["status"], "deferred")
        self.assertEqual(result["outputs"]["jobs"][0]["target_path"], result["outputs"]["jobs"][1]["missing_references"][0])
        self.assertEqual(result["outputs"]["jobs"][1]["status"], "waiting_on_references")
        self.assertEqual(
            result["outputs"]["jobs"][1]["reference_candidates"],
            ["characters/苏黎/苏黎.png"],
        )
        self.assertEqual(
            result["outputs"]["jobs"][1]["missing_references"],
            ["characters/苏黎/苏黎.png"],
        )

    def test_default_plan_treats_reuse_character_references_as_required(self):
        result = self.mod.process_assets_task(
            self.card,
            self.run_dir,
            {
                "id": "intent-reuse-reference",
                "type": "assets_task",
                "payload": {
                    "kind": "scene_illustration",
                    "target": "scene_illustration",
                    "summary": "用户指令要求每轮提供一张带角色的剧情插图。",
                    "planner_hints": "雨蒙故作镇定回到教室，并观察苏黎是否注意到吊坠。",
                    "characters": ["雨蒙", "苏黎"],
                    "reference_policy": "reuse",
                },
            },
            phase="after_critic",
            planner=None,
        )

        outputs = result["outputs"]
        self.assertEqual(outputs["status"], "waiting_on_references")
        self.assertEqual(outputs["jobs"][0]["kind"], "character_reference")
        self.assertEqual(outputs["jobs"][1]["kind"], "character_reference")
        scene_job = outputs["jobs"][2]
        self.assertEqual(scene_job["status"], "waiting_on_references")
        self.assertEqual(scene_job["reference_policy"], "required")
        self.assertEqual(
            scene_job["reference_candidates"],
            ["characters/雨蒙/雨蒙.png", "characters/苏黎/苏黎.png"],
        )
        self.assertIn("剧情插图", scene_job["prompt"])
        self.assertIn("雨蒙故作镇定回到教室", scene_job["prompt"])
        self.assertIn("苏黎", scene_job["prompt"])
        self.assertNotEqual(scene_job["prompt"], "用户指令要求每轮提供一张带角色的剧情插图。")

    def test_default_plan_infers_art_style_when_save_has_no_reference_images(self):
        (self.run_dir / "story.output.json").write_text(
            json.dumps(
                {
                    "content": "废弃神社的夜雨里，纸灯笼映出潮湿石阶，少女握着裂开的御守回头。"
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = self.mod.process_assets_task(
            self.card,
            self.run_dir,
            {
                "id": "intent-style-infer",
                "type": "assets_task",
                "payload": {
                    "kind": "scene_illustration",
                    "target": "scene_illustration",
                    "prompt": "为当前剧情生成插图",
                },
            },
            phase="after_critic",
            planner=None,
        )

        scene_job = result["outputs"]["jobs"][0]
        self.assertIn("画风策略：当前存档没有可用参考图片", scene_job["prompt"])
        self.assertIn("根据剧情题材、时代、情绪、场景和角色状态智能匹配画风", scene_job["prompt"])
        self.assertIn("废弃神社的夜雨", scene_job["prompt"])

    def test_default_plan_uses_user_requested_art_style(self):
        result = self.mod.process_assets_task(
            self.card,
            self.run_dir,
            {
                "id": "intent-style-override",
                "type": "assets_task",
                "payload": {
                    "kind": "scene_illustration",
                    "target": "scene_illustration",
                    "prompt": "为当前剧情生成插图",
                    "art_style": "90年代赛璐璐动画画风，低饱和胶片颗粒",
                },
            },
            phase="after_critic",
            planner=None,
        )

        scene_job = result["outputs"]["jobs"][0]
        self.assertIn("用户指定画风：90年代赛璐璐动画画风，低饱和胶片颗粒", scene_job["prompt"])
        self.assertEqual(scene_job["art_style"], "90年代赛璐璐动画画风，低饱和胶片颗粒")

    def test_default_plan_requires_matching_character_appearance_reference(self):
        base_reference = self.card / "characters" / "苏黎" / "苏黎.png"
        base_reference.parent.mkdir(parents=True)
        base_reference.write_bytes(b"base portrait")

        result = self.mod.process_assets_task(
            self.card,
            self.run_dir,
            {
                "id": "intent-transformed-reference",
                "type": "assets_task",
                "payload": {
                    "kind": "scene_illustration",
                    "target": "scene_illustration",
                    "prompt": "苏黎展开半透明蝶翼，银白长发漂浮在光里。",
                    "characters": ["苏黎"],
                    "character_appearances": [
                        {
                            "name": "苏黎",
                            "appearance_state": "蝶化形态",
                            "description": "银白长发，半透明蝶翼，瞳孔泛蓝光。",
                        }
                    ],
                    "reference_policy": "required",
                },
            },
            phase="after_critic",
            planner=None,
        )

        outputs = result["outputs"]
        self.assertEqual(outputs["status"], "waiting_on_references")
        self.assertEqual(outputs["jobs"][0]["kind"], "character_reference")
        self.assertEqual(outputs["jobs"][0]["target_path"], "characters/苏黎/苏黎-蝶化形态.png")
        self.assertIn("蝶化形态", outputs["jobs"][0]["prompt"])
        scene_job = outputs["jobs"][1]
        self.assertEqual(scene_job["status"], "waiting_on_references")
        self.assertEqual(scene_job["reference_candidates"], ["characters/苏黎/苏黎-蝶化形态.png"])
        self.assertEqual(scene_job["missing_references"], ["characters/苏黎/苏黎-蝶化形态.png"])
        self.assertEqual(scene_job["character_appearances"][0]["appearance_state"], "蝶化形态")

    def test_character_reference_job_writes_to_target_path(self):
        self._configure_image_settings()
        calls = []

        def run_command(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")

        result = self.mod.process_assets_task(
            self.card,
            self.run_dir,
            {
                "id": "intent-reference-output-path",
                "type": "assets_task",
                "payload": {
                    "kind": "scene_illustration",
                    "target": "scene_illustration",
                    "prompt": "苏黎展开半透明蝶翼。",
                    "characters": ["苏黎"],
                    "character_appearances": [
                        {"name": "苏黎", "appearance_state": "蝶化形态"}
                    ],
                    "reference_policy": "required",
                },
            },
            phase="after_critic",
            run_command=run_command,
            planner=None,
        )

        self.assertEqual(result["outputs"]["jobs"][0]["status"], "queued")
        command = calls[0][0][0]
        self.assertIn("--output-path", command)
        self.assertIn("characters/苏黎/苏黎-蝶化形态.png", command)
        self.assertIn("--character", command)
        self.assertIn("苏黎", command)

    def test_persistent_requirement_reuses_characters_and_reference_policy(self):
        manifest = {
            "version": 1,
            "mode": "autonomous",
            "generated_assets": [],
            "asset_requirements": {
                "scene_illustration_each_round": {
                    "enabled": True,
                    "reason": "从本轮开始每轮必须提供剧情插图",
                    "prompt": "带雨蒙和苏黎的剧情插图",
                    "characters": ["雨蒙", "苏黎"],
                    "reference_policy": "required",
                }
            },
        }
        (self.card / "ui_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )

        result = self.mod.process_persistent_requirements(
            self.card,
            self.run_dir,
            phase="after_critic",
            planner=None,
        )

        self.assertEqual(result["outputs"]["status"], "waiting_on_references")
        scene_job = result["outputs"]["jobs"][2]
        self.assertEqual(scene_job["characters"], ["雨蒙", "苏黎"])
        self.assertEqual(scene_job["reference_policy"], "required")
        self.assertEqual(
            scene_job["reference_candidates"],
            ["characters/雨蒙/雨蒙.png", "characters/苏黎/苏黎.png"],
        )

    def test_persistent_requirement_infers_current_scene_characters_for_references(self):
        (self.card / "memory" / "characters" / "雨蒙").mkdir(parents=True)
        (self.card / "memory" / "characters" / "雨蒙" / "profile.md").write_text(
            "雨蒙：普通的高一男生，随身握着粉色花朵吊坠。",
            encoding="utf-8",
        )
        manifest = {
            "version": 1,
            "mode": "autonomous",
            "generated_assets": [],
            "asset_requirements": {
                "scene_illustration_each_round": {
                    "enabled": True,
                    "reason": "从本轮开始每轮必须提供剧情插图",
                    "prompt": "",
                }
            },
        }
        (self.card / "ui_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.run_dir / "player.context.json").write_text(
            json.dumps({"self_knowledge": {"name": "雨蒙"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.run_dir / "actor.outputs.json").write_text(
            json.dumps(
                {
                    "character:苏黎": [
                        {
                            "agent_id": "character:苏黎",
                            "character_name": "苏黎",
                            "natural_reply": "……认识。",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.run_dir / "story.output.json").write_text(
            json.dumps(
                {
                    "content": (
                        "雨蒙在教室里摊开掌心，苏黎看着发光的吊坠。\n"
                        "<character_dialogues>"
                        '[{"character_name": "苏黎", "content": "……认识。"}]'
                        "</character_dialogues>"
                    )
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = self.mod.process_persistent_requirements(
            self.card,
            self.run_dir,
            phase="after_critic",
            planner=None,
        )

        scene_job = result["outputs"]["jobs"][2]
        self.assertEqual(scene_job["characters"], ["雨蒙", "苏黎"])
        self.assertEqual(scene_job["reference_policy"], "required")
        self.assertEqual(
            scene_job["reference_candidates"],
            ["characters/雨蒙/雨蒙.png", "characters/苏黎/苏黎.png"],
        )
        self.assertIn("画面必须包含角色：雨蒙、苏黎", scene_job["prompt"])

    def test_process_assets_task_resumes_waiting_scene_job_when_references_exist(self):
        self._configure_image_settings()
        waiting_payload = {
            "schema_version": 1,
            "job_id": "scene-waiting",
            "kind": "scene_illustration",
            "target": "scene_illustration",
            "prompt": "scene after portrait arrives",
            "characters": ["Ada"],
            "reference_policy": "required",
            "reference_candidates": ["characters/Ada/Ada.png"],
            "resolved_references": [],
            "missing_references": ["characters/Ada/Ada.png"],
            "status": "waiting_on_references",
            "reason": "missing_character_reference",
        }
        jobs_dir = self.card / "generated" / "jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / "scene-waiting.json").write_text(
            json.dumps(waiting_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        reference = self.card / "characters" / "Ada" / "Ada.png"
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
                "id": "intent-resume-waiting",
                "type": "assets_task",
                "payload": {"kind": "scene_illustration", "target": "scene_illustration", "prompt": "noop"},
            },
            phase="after_critic",
            run_command=run_command,
            planner=lambda context: {"schema_version": 1, "scene_jobs": []},
        )

        self.assertEqual(result["outputs"]["resumed_jobs"][0]["job_id"], "scene-waiting")
        self.assertEqual(result["outputs"]["resumed_jobs"][0]["status"], "queued")
        command = calls[0][0][0]
        self.assertIn("--reference", command)
        self.assertIn("characters/Ada/Ada.png", command)
