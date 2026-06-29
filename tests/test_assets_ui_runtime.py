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

    def test_process_assets_task_requires_assets_ui_agent_in_production_path(self):
        calls = []
        plan = {
            "schema_version": 1,
            "plan_id": "fake-agent-plan",
            "jobs": [],
            "ui_patch_requests": [],
            "rename_operations": [],
        }
        original_planner = self.mod.assets_ui_agent.plan_assets_task

        def fake_planner(context):
            calls.append(context)
            return plan

        self.mod.assets_ui_agent.plan_assets_task = fake_planner
        try:
            result = self.mod.process_assets_task(
                self.card,
                self.run_dir,
                {
                    "id": "intent-agent-required",
                    "type": "assets_task",
                    "payload": {"kind": "scene_illustration", "target": "scene_illustration", "prompt": "scene"},
                },
                phase="after_critic",
            )
        finally:
            self.mod.assets_ui_agent.plan_assets_task = original_planner

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["payload"]["prompt"], "scene")
        self.assertEqual(result["outputs"]["plan"], plan)
        audit = _read_json(self.run_dir / "artifacts" / "assets_ui" / "intent-agent-required.json")
        self.assertEqual(audit["plan"], plan)

    def test_process_assets_task_does_not_default_plan_when_agent_fails(self):
        original_planner = self.mod.assets_ui_agent.plan_assets_task

        def failing_planner(_context):
            raise RuntimeError("agent unavailable")

        self.mod.assets_ui_agent.plan_assets_task = failing_planner
        try:
            result = self.mod.process_assets_task(
                self.card,
                self.run_dir,
                {
                    "id": "intent-agent-fails",
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
            )
        finally:
            self.mod.assets_ui_agent.plan_assets_task = original_planner

        outputs = result["outputs"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(outputs["status"], "deferred")
        self.assertEqual(outputs["reason"], "assets_ui_agent_failed")
        self.assertEqual(outputs["jobs"], [])
        self.assertEqual(outputs["resumed_jobs"], [])
        self.assertEqual(outputs["plan"], {})
        self.assertFalse((self.card / "generated" / "jobs").exists())
        audit = _read_json(self.run_dir / "artifacts" / "assets_ui" / "intent-agent-fails.json")
        self.assertEqual(audit["outputs"]["reason"], "assets_ui_agent_failed")

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
                "jobs": [
                    {
                        "queue_type": "character_reference",
                        "job_id": "character-suli-reference",
                        "character_name": "苏黎",
                        "target_path": "generated/characters/苏黎/苏黎.png",
                        "prompt": "professional character sheet for Su Li",
                    },
                    {
                        "queue_type": "scene_illustration",
                        "job_id": "scene-round-000004",
                        "kind": "scene_illustration",
                        "target": "scene_illustration",
                        "prompt": "cinematic rainy classroom with Su Li",
                        "characters": ["苏黎"],
                        "reference_policy": "required",
                        "reference_candidates": ["generated/characters/苏黎/苏黎.png"],
                    }
                ],
            },
        )

        self.assertEqual(result["status"], "completed")
        outputs = result["outputs"]
        self.assertEqual(outputs["status"], "waiting_on_references")
        self.assertEqual(outputs["jobs"][0]["queue_type"], "character_reference")
        self.assertEqual(outputs["jobs"][1]["status"], "waiting_on_references")
        self.assertEqual(outputs["jobs"][1]["missing_references"], ["generated/characters/苏黎/苏黎.png"])
        manifest = _read_json(self.card / "ui_manifest.json")
        self.assertTrue(manifest["asset_requirements"]["scene_illustration_each_round"]["enabled"])
        self.assertTrue((self.card / "generated" / "jobs" / "scene-round-000004.json").exists())
        audit = _read_json(self.run_dir / "artifacts" / "assets_ui" / "intent-assets-1.json")
        self.assertEqual(audit["intent_id"], "intent-assets-1")

    def test_planner_scene_job_inherits_payload_characters_and_references_when_omitted(self):
        self._configure_image_settings()
        for rel_path in (
            "generated/characters/雨蒙/雨蒙.png",
            "generated/characters/苏黎/苏黎.png",
            "generated/images/scene-0003.png",
        ):
            path = self.card / rel_path
            path.parent.mkdir(parents=True)
            path.write_bytes(b"png")
        (self.card / ".card_assets.json").write_text(
            json.dumps(
                {
                    "images": [
                        {
                            "id": "scene-0003",
                            "kind": "scene",
                            "path": "generated/images/scene-0003.png",
                            "status": "completed",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        calls = []

        def run_command(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        intent = {
            "id": "intent-assets-planner-metadata",
            "type": "assets_task",
            "payload": {
                "kind": "scene_illustration",
                "target": "scene_illustration",
                "prompt": "教室里雨蒙和苏黎互相试探。",
                "characters": ["雨蒙", "苏黎"],
                "character_appearances": [
                    {
                        "name": "雨蒙",
                        "description": "普通高一男生，抱着书包遮住口袋。",
                    },
                    {
                        "name": "苏黎",
                        "description": "漂亮冷淡的靠窗女同学。",
                    },
                ],
                "reference_policy": "required",
            },
        }

        result = self.mod.process_assets_task(
            self.card,
            self.run_dir,
            intent,
            phase="after_critic",
            run_command=run_command,
            planner=lambda context: {
                "schema_version": 1,
                "jobs": [
                    {
                        "job_id": "scene-round-000004",
                        "kind": "scene_illustration",
                        "target": "scene_illustration",
                        "prompt": "cinematic classroom scene",
                    }
                ],
            },
        )

        scene_job = result["outputs"]["jobs"][0]
        self.assertEqual(scene_job["status"], "queued")
        self.assertEqual(scene_job["characters"], ["雨蒙", "苏黎"])
        self.assertEqual(
            scene_job["reference_candidates"],
            [
                "generated/characters/雨蒙/雨蒙.png",
                "generated/characters/苏黎/苏黎.png",
                "generated/images/scene-0003.png",
            ],
        )
        self.assertEqual(scene_job["resolved_references"], scene_job["reference_candidates"])
        command = calls[0][0][0]
        self.assertIn("--reference", command)
        self.assertIn("generated/characters/雨蒙/雨蒙.png", command)
        self.assertIn("generated/characters/苏黎/苏黎.png", command)
        self.assertIn("generated/images/scene-0003.png", command)
        self.assertIn("--character", command)
        self.assertIn("雨蒙", command)
        self.assertIn("苏黎", command)

    def test_process_assets_task_debug_logs_assets_ui_planner_call(self):
        intent = {
            "id": "intent-assets-debug",
            "type": "assets_task",
            "payload": {
                "kind": "scene_illustration",
                "target": "scene_illustration",
                "prompt": "debug scene prompt",
            },
        }

        result = self.mod.process_assets_task(
            self.card,
            self.run_dir,
            intent,
            phase="after_critic",
            runtime_settings={"modelDebugMode": True},
            planner=lambda context: {
                "schema_version": 1,
                "jobs": [
                    {
                        "job_id": "scene-debug",
                        "kind": "scene_illustration",
                        "target": "scene_illustration",
                        "prompt": "debug cinematic prompt",
                    }
                ],
            },
        )

        self.assertEqual(result["status"], "completed")
        index_path = self.card / "debug" / "model_calls" / "index.jsonl"
        index_lines = index_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(index_lines), 1)
        index_item = json.loads(index_lines[0])
        self.assertEqual(index_item["agent_key"], "assets-ui")
        record = _read_json(self.card / index_item["relative_path"])
        self.assertIn("intent-assets-debug", record["raw_input"]["prompt"])
        self.assertIn("after_critic", record["raw_input"]["prompt"])
        self.assertIn("scene-debug", record["raw_output"]["stdout"])

    def test_process_assets_task_starts_scene_job_when_references_exist(self):
        self._configure_image_settings()
        reference = self.card / "generated" / "characters" / "苏黎" / "苏黎.png"
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
                "jobs": [
                    {
                        "job_id": "scene-round-000004",
                        "kind": "scene_illustration",
                        "target": "scene_illustration",
                        "prompt": "cinematic rainy scene",
                        "characters": ["苏黎"],
                        "reference_policy": "optional",
                        "reference_candidates": ["generated/characters/苏黎/苏黎.png"],
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
        reference = self.card / "generated" / "characters" / "Ada" / "Ada.png"
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
                        "references": ["generated/characters/Ada/Ada.png"],
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
                "jobs": [
                    {
                        "job_id": "scene-required-reference",
                        "kind": "scene_illustration",
                        "target": "scene_illustration",
                        "prompt": "Ada scene",
                        "characters": ["Ada"],
                        "reference_policy": "required",
                        "reference_candidates": ["generated/characters/Ada/Ada.png"],
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
                "jobs": [
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
                        "jobs": [
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
            return {"schema_version": 1, "jobs": []}

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
            planner=lambda context: self.mod._default_plan(context),
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outputs"]["status"], "waiting_on_references")
        self.assertEqual(result["outputs"]["jobs"][0]["queue_type"], "character_reference")
        self.assertEqual(result["outputs"]["jobs"][0]["status"], "deferred")
        self.assertEqual(result["outputs"]["jobs"][0]["target_path"], result["outputs"]["jobs"][1]["missing_references"][0])
        self.assertEqual(result["outputs"]["jobs"][1]["status"], "waiting_on_references")
        self.assertEqual(
            result["outputs"]["jobs"][1]["reference_candidates"],
            ["generated/characters/苏黎/苏黎.png"],
        )
        self.assertEqual(
            result["outputs"]["jobs"][1]["missing_references"],
            ["generated/characters/苏黎/苏黎.png"],
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
            planner=lambda context: self.mod._default_plan(context),
        )

        outputs = result["outputs"]
        self.assertEqual(outputs["status"], "waiting_on_references")
        self.assertEqual(outputs["jobs"][0]["queue_type"], "character_reference")
        self.assertEqual(outputs["jobs"][1]["queue_type"], "character_reference")
        scene_job = outputs["jobs"][2]
        self.assertEqual(scene_job["status"], "waiting_on_references")
        self.assertEqual(scene_job["reference_policy"], "required")
        self.assertEqual(
            scene_job["reference_candidates"],
            ["generated/characters/雨蒙/雨蒙.png", "generated/characters/苏黎/苏黎.png"],
        )
        self.assertIn("剧情插图", scene_job["prompt"])
        self.assertIn("雨蒙故作镇定回到教室", scene_job["prompt"])
        self.assertIn("苏黎", scene_job["prompt"])
        self.assertNotEqual(scene_job["prompt"], "用户指令要求每轮提供一张带角色的剧情插图。")

    def test_scene_prompt_is_cinematic_and_describes_reference_purposes(self):
        su_li = self.card / "generated" / "characters" / "苏黎" / "苏黎.png"
        su_li.parent.mkdir(parents=True)
        su_li.write_bytes(b"portrait")
        prior_scene = self.card / "generated" / "images" / "scene-0001.png"
        prior_scene.parent.mkdir(parents=True)
        prior_scene.write_bytes(b"style")
        (self.run_dir / "story.output.json").write_text(
            json.dumps(
                {
                    "content": (
                        "预备铃即将响起，雨蒙把数学练习册半合在桌面，只露出粉色水印的一角。"
                        "苏黎坐在靠窗后排，指尖压着旧封皮笔记，窗外晨光斜切过课桌。"
                        "两人都装作在对题，实际上都在观察那枚粉色吊坠与便签的反光。"
                    )
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = self.mod.process_assets_task(
            self.card,
            self.run_dir,
            {
                "id": "intent-cinematic-reference-prompt",
                "type": "assets_task",
                "payload": {
                    "kind": "scene_illustration",
                    "target": "scene_illustration",
                    "characters": ["苏黎"],
                    "reference_policy": "required",
                    "reference_candidates": [
                        "generated/characters/苏黎/苏黎.png",
                        "generated/images/scene-0001.png",
                    ],
                    "planner_hints": "突出二人表面普通对题、暗中试探的紧张感。",
                },
            },
            phase="after_critic",
            planner=lambda context: self.mod._default_plan(context),
        )

        scene_job = result["outputs"]["jobs"][0]
        prompt = scene_job["prompt"]
        self.assertIn("镜头设计：", prompt)
        self.assertIn("构图与主体：", prompt)
        self.assertIn("光线与氛围：", prompt)
        self.assertIn("参考图用途：", prompt)
        self.assertIn("generated/characters/苏黎/苏黎.png", prompt)
        self.assertIn("文件名：苏黎.png", prompt)
        self.assertIn("角色人设参考：苏黎", prompt)
        self.assertIn("generated/images/scene-0001.png", prompt)
        self.assertIn("文件名：scene-0001.png", prompt)
        self.assertIn("画风参考", prompt)
        self.assertNotIn("剧情依据：", prompt)

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
            planner=lambda context: self.mod._default_plan(context),
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
            planner=lambda context: self.mod._default_plan(context),
        )

        scene_job = result["outputs"]["jobs"][0]
        self.assertIn("用户指定画风：90年代赛璐璐动画画风，低饱和胶片颗粒", scene_job["prompt"])
        self.assertEqual(scene_job["art_style"], "90年代赛璐璐动画画风，低饱和胶片颗粒")

    def test_default_plan_requires_matching_character_appearance_reference(self):
        base_reference = self.card / "generated" / "characters" / "苏黎" / "苏黎.png"
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
            planner=lambda context: self.mod._default_plan(context),
        )

        outputs = result["outputs"]
        self.assertEqual(outputs["status"], "waiting_on_references")
        self.assertEqual(outputs["jobs"][0]["queue_type"], "character_reference")
        self.assertEqual(outputs["jobs"][0]["target_path"], "generated/characters/苏黎/苏黎-蝶化形态.png")
        self.assertIn("蝶化形态", outputs["jobs"][0]["prompt"])
        scene_job = outputs["jobs"][1]
        self.assertEqual(scene_job["status"], "waiting_on_references")
        self.assertEqual(scene_job["reference_candidates"], ["generated/characters/苏黎/苏黎-蝶化形态.png"])
        self.assertEqual(scene_job["missing_references"], ["generated/characters/苏黎/苏黎-蝶化形态.png"])
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
            planner=lambda context: self.mod._default_plan(context),
        )

        self.assertEqual(result["outputs"]["jobs"][0]["status"], "queued")
        command = calls[0][0][0]
        self.assertIn("--output-path", command)
        self.assertIn("generated/characters/苏黎/苏黎-蝶化形态.png", command)
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
            planner=lambda context: self.mod._default_plan(context),
        )

        self.assertEqual(result["outputs"]["status"], "waiting_on_references")
        scene_job = result["outputs"]["jobs"][2]
        self.assertEqual(scene_job["characters"], ["雨蒙", "苏黎"])
        self.assertEqual(scene_job["reference_policy"], "required")
        self.assertEqual(
            scene_job["reference_candidates"],
            ["generated/characters/雨蒙/雨蒙.png", "generated/characters/苏黎/苏黎.png"],
        )

    def test_asset_requirement_delete_removes_existing_requirement_without_jobs(self):
        manifest = {
            "version": 1,
            "mode": "autonomous",
            "generated_assets": [],
            "asset_requirements": {
                "scene_illustration_each_round": {
                    "enabled": True,
                    "reason": "old requirement",
                    "prompt": "old scene",
                }
            },
        }
        (self.card / "ui_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        intent = {
            "id": "delete-scene-requirement",
            "type": "assets_task",
            "payload": {
                "action": "delete",
                "asset_requirement_key": "scene_illustration_each_round",
            },
        }

        result = self.mod.process_assets_task(
            self.card,
            self.run_dir,
            intent,
            phase="after_critic",
            planner=lambda _context: {
                "asset_requirement_update": {
                    "action": "delete",
                    "asset_requirement_key": "scene_illustration_each_round",
                },
                "jobs": [],
            },
        )

        updated = json.loads((self.card / "ui_manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("scene_illustration_each_round", updated["asset_requirements"])
        self.assertEqual(result["outputs"]["asset_requirement_update"]["action"], "delete")
        self.assertEqual(result["outputs"]["asset_requirement_update"]["applied"], True)
        self.assertEqual(result["outputs"]["jobs"], [])

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
            planner=lambda context: self.mod._default_plan(context),
        )

        scene_job = result["outputs"]["jobs"][2]
        self.assertEqual(scene_job["characters"], ["雨蒙", "苏黎"])
        self.assertEqual(scene_job["reference_policy"], "required")
        self.assertEqual(
            scene_job["reference_candidates"],
            ["generated/characters/雨蒙/雨蒙.png", "generated/characters/苏黎/苏黎.png"],
        )
        self.assertIn("画面必须包含角色：雨蒙、苏黎", scene_job["prompt"])

    def test_process_assets_task_merges_resumed_queue_jobs(self):
        jobs_dir = self.card / "generated" / "jobs"
        jobs_dir.mkdir(parents=True)
        for index in range(1, 4):
            target = self.card / "generated" / "tmp" / "ada-reference-candidates" / f"candidate-{index}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"candidate-{index}".encode("ascii"))
            candidate = {
                "schema_version": 1,
                "queue_type": "character_reference_candidate",
                "job_id": f"ada-reference-candidate-{index}",
                "batch_id": "ada-reference-candidates",
                "character_name": "Ada",
                "target_path": f"generated/tmp/ada-reference-candidates/candidate-{index}.png",
                "final_target_path": "generated/characters/Ada/Ada.png",
                "prompt": "Ada reference",
                "status": "completed",
            }
            (jobs_dir / f"ada-reference-candidate-{index}.json").write_text(
                json.dumps(candidate, ensure_ascii=False),
                encoding="utf-8",
            )

        calls = []
        original_runner = self.mod.llm_runner.run_llm_agent

        def fake_runner(agent_key, prompt, cwd):
            calls.append((agent_key, prompt, cwd))
            return json.dumps(
                {
                    "winner_candidate_id": "ada-reference-candidate-2",
                    "scores": {
                        "ada-reference-candidate-1": {
                            "style_fit": 6,
                            "character_fit": 7,
                            "highlights": "轮廓清楚但风格偏离。",
                        },
                        "ada-reference-candidate-2": {
                            "style_fit": 9,
                            "character_fit": 9,
                            "highlights": "最贴合角色背景和画风。",
                        },
                        "ada-reference-candidate-3": {
                            "style_fit": 7,
                            "character_fit": 6,
                            "highlights": "表情可用但辨识度不足。",
                        },
                    },
                },
                ensure_ascii=False,
            )

        self.mod.llm_runner.run_llm_agent = fake_runner
        try:
            result = self.mod.process_assets_task(
                self.card,
                self.run_dir,
                {
                    "id": "intent-resume-waiting",
                    "type": "assets_task",
                    "payload": {"kind": "scene_illustration", "target": "scene_illustration", "prompt": "noop"},
                },
                phase="after_critic",
                planner=lambda context: {"schema_version": 1, "jobs": []},
            )
        finally:
            self.mod.llm_runner.run_llm_agent = original_runner

        self.assertEqual(calls[0][0], "critic")
        self.assertIn("画风贴合度", calls[0][1])
        self.assertIn("人设吻合度", calls[0][1])
        self.assertIn("其他亮点", calls[0][1])
        self.assertIn("generated/tmp/ada-reference-candidates/candidate-2.png", calls[0][1])
        self.assertEqual(result["outputs"]["status"], "completed")
        self.assertEqual(result["outputs"]["resumed_jobs"][0]["job_id"], "ada-reference-candidates-selection")
        self.assertEqual(result["outputs"]["resumed_jobs"][0]["status"], "completed")
        self.assertEqual(result["outputs"]["resumed_jobs"][0]["winner_candidate_id"], "ada-reference-candidate-2")
        self.assertEqual((self.card / "generated" / "characters" / "Ada" / "Ada.png").read_bytes(), b"candidate-2")
        self.assertEqual(
            (self.card / "generated" / "tmp" / "ada-reference-candidates" / "rejected" / "candidate-1.png").read_bytes(),
            b"candidate-1",
        )
        self.assertEqual(
            (self.card / "generated" / "tmp" / "ada-reference-candidates" / "rejected" / "candidate-3.png").read_bytes(),
            b"candidate-3",
        )

    def test_process_assets_task_waits_when_candidate_critic_runner_fails(self):
        jobs_dir = self.card / "generated" / "jobs"
        jobs_dir.mkdir(parents=True)
        for index in range(1, 4):
            target = self.card / "generated" / "tmp" / "ada-reference-candidates" / f"candidate-{index}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"candidate-{index}".encode("ascii"))
            candidate = {
                "schema_version": 1,
                "queue_type": "character_reference_candidate",
                "job_id": f"ada-reference-candidate-{index}",
                "batch_id": "ada-reference-candidates",
                "character_name": "Ada",
                "target_path": f"generated/tmp/ada-reference-candidates/candidate-{index}.png",
                "final_target_path": "generated/characters/Ada/Ada.png",
                "prompt": "Ada reference",
                "status": "completed",
            }
            (jobs_dir / f"ada-reference-candidate-{index}.json").write_text(
                json.dumps(candidate, ensure_ascii=False),
                encoding="utf-8",
            )

        original_runner = self.mod.llm_runner.run_llm_agent

        def failing_runner(_agent_key, _prompt, _cwd):
            raise RuntimeError("critic offline")

        self.mod.llm_runner.run_llm_agent = failing_runner
        try:
            result = self.mod.process_assets_task(
                self.card,
                self.run_dir,
                {
                    "id": "intent-resume-waiting",
                    "type": "assets_task",
                    "payload": {"kind": "scene_illustration", "target": "scene_illustration", "prompt": "noop"},
                },
                phase="after_critic",
                planner=lambda context: {"schema_version": 1, "jobs": []},
            )
        finally:
            self.mod.llm_runner.run_llm_agent = original_runner

        self.assertEqual(result["outputs"]["status"], "waiting_on_critic")
        self.assertEqual(result["outputs"]["resumed_jobs"][0]["job_id"], "ada-reference-candidates-selection")
        self.assertEqual(result["outputs"]["resumed_jobs"][0]["status"], "waiting_on_critic")
        self.assertEqual(result["outputs"]["resumed_jobs"][0]["reason"], "critic_runner_failed")

    def test_process_assets_task_prioritizes_waiting_critic_over_new_deferred_jobs(self):
        jobs_dir = self.card / "generated" / "jobs"
        jobs_dir.mkdir(parents=True)
        for index in range(1, 4):
            target = self.card / "generated" / "tmp" / "ada-reference-candidates" / f"candidate-{index}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"candidate-{index}".encode("ascii"))
            candidate = {
                "schema_version": 1,
                "queue_type": "character_reference_candidate",
                "job_id": f"ada-reference-candidate-{index}",
                "batch_id": "ada-reference-candidates",
                "character_name": "Ada",
                "target_path": f"generated/tmp/ada-reference-candidates/candidate-{index}.png",
                "final_target_path": "generated/characters/Ada/Ada.png",
                "prompt": "Ada reference",
                "status": "completed",
            }
            (jobs_dir / f"ada-reference-candidate-{index}.json").write_text(
                json.dumps(candidate, ensure_ascii=False),
                encoding="utf-8",
            )

        original_runner = self.mod.llm_runner.run_llm_agent

        def failing_runner(_agent_key, _prompt, _cwd):
            raise RuntimeError("critic offline")

        self.mod.llm_runner.run_llm_agent = failing_runner
        try:
            result = self.mod.process_assets_task(
                self.card,
                self.run_dir,
                {
                    "id": "intent-mixed-status",
                    "type": "assets_task",
                    "payload": {"kind": "scene_illustration", "target": "scene_illustration", "prompt": "noop"},
                },
                phase="after_critic",
                planner=lambda context: {
                    "schema_version": 1,
                    "jobs": [
                        {
                            "queue_type": "scene_illustration",
                            "job_id": "scene-deferred",
                            "prompt": "scene waits for image settings",
                        }
                    ],
                },
            )
        finally:
            self.mod.llm_runner.run_llm_agent = original_runner

        self.assertEqual(result["outputs"]["jobs"][0]["status"], "deferred")
        self.assertEqual(result["outputs"]["resumed_jobs"][0]["status"], "waiting_on_critic")
        self.assertEqual(result["outputs"]["resumed_jobs"][0]["reason"], "critic_runner_failed")
        self.assertEqual(result["outputs"]["status"], "waiting_on_critic")
