import importlib.util
import json
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
    from tests.module_aliases import load_repo_module
    return load_repo_module(name)


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class AssetJobQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000003"
        self.run_dir.mkdir(parents=True)
        self.mod = _load("asset_job_queue")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_completed_candidate(self, batch_id, index, *, final_target_path="generated/characters/Ada/Ada.png"):
        candidate_id = f"{batch_id}-candidate-{index}"
        target_path = f"generated/tmp/{batch_id}/candidate-{index}.png"
        output = self.card / target_path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"candidate-{index}".encode("ascii"))
        job = {
            "schema_version": 1,
            "queue_type": "character_reference_candidate",
            "job_id": candidate_id,
            "batch_id": batch_id,
            "character_name": "Ada",
            "status": "completed",
            "target_path": target_path,
            "output_path": target_path,
            "final_target_path": final_target_path,
        }
        job_path = self.card / "generated" / "jobs" / f"{candidate_id}.json"
        job_path.parent.mkdir(parents=True, exist_ok=True)
        job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        return job

    def test_character_reference_job_uses_generated_characters_and_is_hidden(self):
        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "character_reference",
                    "job_id": "character-suli-reference",
                    "character_name": "苏黎",
                    "target_path": "generated/characters/苏黎/苏黎.png",
                    "prompt": "为苏黎绘制人设图",
                }
            ],
        }
        result = self.mod.apply_plan(self.card, self.run_dir, plan, image_settings_ready=False)

        self.assertEqual(result["status"], "deferred")
        job = _read_json(self.card / "generated" / "jobs" / "character-suli-reference.json")
        self.assertEqual(job["queue_type"], "character_reference")
        self.assertEqual(job["display_policy"], "hidden_reference")
        self.assertEqual(job["target_path"], "generated/characters/苏黎/苏黎.png")
        self.assertEqual(job["agent_plan_id"], "assets-round-000003")
        mirror = _read_json(
            self.run_dir / "artifacts" / "assets_ui" / "jobs" / "character-suli-reference.json"
        )
        self.assertEqual(mirror["job_id"], "character-suli-reference")
        self.assertEqual(mirror["target_path"], "generated/characters/苏黎/苏黎.png")

    def test_ready_jobs_are_submitted_in_one_batch(self):
        commands = []
        cwds = []

        def fake_run(command, **kwargs):
            commands.append(command)
            cwds.append(kwargs.get("cwd"))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-a",
                    "round_id": "round-000003",
                    "prompt": "无角色雨夜剧院氛围图",
                    "important_characters": [],
                    "characters": [],
                    "scene_mode": "atmosphere_only",
                    "display_policy": "story_inline",
                },
                {
                    "queue_type": "character_reference",
                    "job_id": "character-suli-reference",
                    "character_name": "苏黎",
                    "target_path": "generated/characters/苏黎/苏黎.png",
                    "prompt": "为苏黎绘制人设图",
                },
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(len(commands), 2)
        self.assertTrue(all("--async" in cmd for cmd in commands))
        self.assertTrue(all(Path(cmd[1]).resolve() == (ROOT / "skills" / "image_generate.py").resolve() for cmd in commands))
        self.assertTrue(all(Path(cwd).resolve() == ROOT.resolve() for cwd in cwds))
        scene_command = commands[0]
        self.assertIn("--round-id", scene_command)
        self.assertEqual(scene_command[scene_command.index("--round-id") + 1], "round-000003")
        self.assertNotIn("--round-id", commands[1])

    def test_ready_scene_job_passes_characters_to_worker(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-with-characters",
                    "round_id": "round-000003",
                    "prompt": "苏黎和林岚在雨中对话",
                    "characters": ["苏黎", "林岚"],
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(len(commands), 1)
        command = commands[0]
        suli_index = command.index("--character")
        self.assertEqual(command[suli_index + 1], "苏黎")
        linlan_index = command.index("--character", suli_index + 2)
        self.assertEqual(command[linlan_index + 1], "林岚")
        self.assertIn("--round-id", command)
        self.assertEqual(command[command.index("--round-id") + 1], "round-000003")

    def test_ready_character_reference_job_passes_character_name_to_worker(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "character_reference",
                    "job_id": "character-suli-reference",
                    "character_name": "苏黎",
                    "target_path": "generated/characters/苏黎/苏黎.png",
                    "prompt": "为苏黎绘制人设图",
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(len(commands), 1)
        command = commands[0]
        character_index = command.index("--character")
        self.assertEqual(command[character_index + 1], "苏黎")
        self.assertNotIn("--round-id", command)

    def test_control_queue_types_wait_without_image_worker(self):
        cases = [
            ("character_reference_selection", "waiting_on_critic", "critic_vision_not_available"),
            ("ui_patch_request", "deferred", "ui_patch_requires_claude_code"),
        ]
        for queue_type, expected_status, expected_reason in cases:
            with self.subTest(queue_type=queue_type):
                commands = []

                def fake_run(command, **kwargs):
                    commands.append(command)
                    return SimpleNamespace(returncode=0, stdout="", stderr="")

                plan = {
                    "schema_version": 1,
                    "plan_id": "assets-round-000003",
                    "jobs": [
                        {
                            "queue_type": queue_type,
                            "job_id": f"{queue_type}-job",
                            "prompt": "control task",
                        }
                    ],
                }

                result = self.mod.apply_plan(
                    self.card,
                    self.run_dir,
                    plan,
                    image_settings_ready=True,
                    run_command=fake_run,
                )

                self.assertEqual(commands, [])
                self.assertEqual(result["status"], expected_status)
                job_path = f"{queue_type.replace('_', '-')}-job.json"
                job = _read_json(self.card / "generated" / "jobs" / job_path)
                self.assertEqual(job["status"], expected_status)
                self.assertEqual(job["reason"], expected_reason)
                self.assertNotIn("command", job)

    def test_asset_rename_updates_character_assets_and_job_references(self):
        old_path = "generated/characters/无名少女/无名少女.png"
        new_path = "generated/characters/苏黎/苏黎.png"
        old_file = self.card / old_path
        old_file.parent.mkdir(parents=True)
        old_file.write_bytes(b"old portrait")
        old_scene_job = {
            "schema_version": 1,
            "queue_type": "scene_illustration",
            "job_id": "scene-with-old-reference",
            "status": "waiting_on_references",
            "reference_candidates": [{"path": old_path, "purpose": "character_reference"}],
            "resolved_references": [old_path],
            "missing_references": [old_path],
        }
        job_path = self.card / "generated" / "jobs" / "scene-with-old-reference.json"
        job_path.parent.mkdir(parents=True)
        job_path.write_text(json.dumps(old_scene_job, ensure_ascii=False), encoding="utf-8")
        mirror_path = self.run_dir / "artifacts" / "assets_ui" / "jobs" / "scene-with-old-reference.json"
        mirror_path.parent.mkdir(parents=True)
        mirror_path.write_text(json.dumps(old_scene_job, ensure_ascii=False), encoding="utf-8")
        (self.card / ".card_assets.json").write_text(
            json.dumps(
                {
                    "images": [
                        {
                            "path": old_path,
                            "source_job_id": "character-old-reference",
                            "references": [old_path],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.card / "ui_manifest.json").write_text(
            json.dumps(
                {
                    "asset_requirements": {
                        "scene_illustration_each_round": {
                            "reference_candidates": [{"path": old_path}],
                            "characters": ["无名少女"],
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "asset_rename",
                    "job_id": "rename-unnamed-girl",
                    "from_path": "generated/characters/无名少女",
                    "to_path": "generated/characters/苏黎",
                    "replacements": [{"from": old_path, "to": new_path}],
                }
            ],
        }

        result = self.mod.apply_plan(self.card, self.run_dir, plan, image_settings_ready=False)

        self.assertEqual(result["status"], "completed")
        self.assertTrue((self.card / new_path).is_file())
        self.assertFalse(old_file.exists())
        self.assertFalse((self.card / "generated" / "characters" / "无名少女").exists())
        self.assertFalse((self.card / "generated" / "characters" / "苏黎" / "无名少女.png").exists())

        persisted = _read_json(self.card / "generated" / "jobs" / "rename-unnamed-girl.json")
        self.assertEqual(persisted["status"], "completed")
        self.assertEqual(persisted["applied_replacements"], [{"from": old_path, "to": new_path}])
        self.assertNotIn("reason", persisted)

        updated_card_assets = _read_json(self.card / ".card_assets.json")
        self.assertEqual(updated_card_assets["images"][0]["path"], new_path)
        self.assertEqual(updated_card_assets["images"][0]["references"], [new_path])
        updated_manifest = _read_json(self.card / "ui_manifest.json")
        self.assertEqual(
            updated_manifest["asset_requirements"]["scene_illustration_each_round"]["reference_candidates"][0]["path"],
            new_path,
        )
        updated_job = _read_json(job_path)
        self.assertEqual(updated_job["reference_candidates"][0]["path"], new_path)
        self.assertEqual(updated_job["resolved_references"], [new_path])
        self.assertEqual(updated_job["missing_references"], [new_path])
        updated_mirror = _read_json(mirror_path)
        self.assertEqual(updated_mirror["reference_candidates"][0]["path"], new_path)

    def test_asset_rename_rewrites_only_exact_json_string_values(self):
        old_path = "generated/characters/旧/旧.png"
        new_path = "generated/characters/新/新.png"
        old_file = self.card / old_path
        old_file.parent.mkdir(parents=True)
        old_file.write_bytes(b"old portrait")
        job_payload = {
            "schema_version": 1,
            "queue_type": "scene_illustration",
            "job_id": "scene-prose-reference",
            "status": "waiting_on_references",
            "notes": "do not rewrite generated/characters/旧/旧.png inside prose",
            "description": f"embedded {old_path} path stays prose",
            "path": old_path,
            "resolved_references": [old_path],
            "reference_candidates": [{"path": old_path}],
        }
        job_path = self.card / "generated" / "jobs" / "scene-prose-reference.json"
        job_path.parent.mkdir(parents=True)
        job_path.write_text(json.dumps(job_payload, ensure_ascii=False), encoding="utf-8")
        (self.card / ".card_assets.json").write_text(
            json.dumps(
                {
                    "images": [
                        {
                            "path": old_path,
                            "notes": "do not rewrite generated/characters/旧/旧.png inside prose",
                            "references": [old_path],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "asset_rename",
                    "job_id": "rename-exact-values",
                    "from_path": "generated/characters/旧",
                    "to_path": "generated/characters/新",
                    "replacements": [{"from": old_path, "to": new_path}],
                }
            ],
        }

        result = self.mod.apply_plan(self.card, self.run_dir, plan, image_settings_ready=False)

        self.assertEqual(result["status"], "completed")
        updated_job = _read_json(job_path)
        self.assertEqual(updated_job["notes"], "do not rewrite generated/characters/旧/旧.png inside prose")
        self.assertEqual(updated_job["description"], f"embedded {old_path} path stays prose")
        self.assertEqual(updated_job["path"], new_path)
        self.assertEqual(updated_job["resolved_references"], [new_path])
        self.assertEqual(updated_job["reference_candidates"][0]["path"], new_path)
        updated_assets = _read_json(self.card / ".card_assets.json")
        self.assertEqual(updated_assets["images"][0]["notes"], "do not rewrite generated/characters/旧/旧.png inside prose")
        self.assertEqual(updated_assets["images"][0]["path"], new_path)
        self.assertEqual(updated_assets["images"][0]["references"], [new_path])

    def test_asset_rename_rejects_existing_target_without_deleting_assets(self):
        source_dir = self.card / "generated" / "characters" / "Old"
        target_dir = self.card / "generated" / "characters" / "New"
        source_file = source_dir / "Old.png"
        target_file = target_dir / "New.png"
        source_file.parent.mkdir(parents=True)
        target_file.parent.mkdir(parents=True)
        source_file.write_bytes(b"source portrait")
        target_file.write_bytes(b"existing target portrait")
        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "asset_rename",
                    "job_id": "rename-existing-target",
                    "from_path": "generated/characters/Old",
                    "to_path": "generated/characters/New",
                    "replacements": [
                        {
                            "from": "generated/characters/Old/Old.png",
                            "to": "generated/characters/New/New.png",
                        }
                    ],
                }
            ],
        }

        result = self.mod.apply_plan(self.card, self.run_dir, plan, image_settings_ready=False)

        self.assertEqual(result["status"], "failed")
        self.assertTrue(source_file.is_file())
        self.assertEqual(source_file.read_bytes(), b"source portrait")
        self.assertTrue(target_file.is_file())
        self.assertEqual(target_file.read_bytes(), b"existing target portrait")
        job = _read_json(self.card / "generated" / "jobs" / "rename-existing-target.json")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["reason"], "target_asset_exists")
        self.assertEqual(job["conflict_path"], "generated/characters/New")
        self.assertNotIn("applied_replacements", job)

    def test_asset_rename_rejects_existing_internal_replacement_target_without_moving_source(self):
        source_dir = self.card / "generated" / "tmp" / "old"
        source_file = source_dir / "portrait.png"
        replacement_target = self.card / "generated" / "characters" / "New" / "New.png"
        source_file.parent.mkdir(parents=True)
        replacement_target.parent.mkdir(parents=True)
        source_file.write_bytes(b"source portrait")
        replacement_target.write_bytes(b"existing replacement target")
        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "asset_rename",
                    "job_id": "rename-existing-internal-target",
                    "from_path": "generated/tmp/old",
                    "to_path": "generated/tmp/new",
                    "replacements": [
                        {
                            "from": "generated/tmp/old/portrait.png",
                            "to": "generated/characters/New/New.png",
                        }
                    ],
                }
            ],
        }

        result = self.mod.apply_plan(self.card, self.run_dir, plan, image_settings_ready=False)

        self.assertEqual(result["status"], "failed")
        self.assertTrue(source_file.is_file())
        self.assertEqual(source_file.read_bytes(), b"source portrait")
        self.assertFalse((self.card / "generated" / "tmp" / "new").exists())
        self.assertTrue(replacement_target.is_file())
        self.assertEqual(replacement_target.read_bytes(), b"existing replacement target")
        job = _read_json(self.card / "generated" / "jobs" / "rename-existing-internal-target.json")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["reason"], "target_asset_exists")
        self.assertEqual(job["conflict_path"], "generated/characters/New/New.png")
        self.assertNotIn("applied_replacements", job)

    def test_asset_rename_rejects_unsafe_replacement_path_without_moving_source(self):
        old_path = "generated/characters/无名少女/无名少女.png"
        old_file = self.card / old_path
        old_file.parent.mkdir(parents=True)
        old_file.write_bytes(b"old portrait")
        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "asset_rename",
                    "job_id": "rename-unsafe",
                    "from_path": "generated/characters/无名少女",
                    "to_path": "generated/characters/苏黎",
                    "replacements": [{"from": old_path, "to": "../escape.png"}],
                }
            ],
        }

        result = self.mod.apply_plan(self.card, self.run_dir, plan, image_settings_ready=False)

        self.assertEqual(result["status"], "failed")
        self.assertTrue(old_file.is_file())
        self.assertFalse((self.card / "generated" / "characters" / "苏黎").exists())
        job = _read_json(self.card / "generated" / "jobs" / "rename-unsafe.json")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["reason"], "invalid_asset_path")
        self.assertEqual(job["invalid_path"], "../escape.png")
        self.assertNotIn("applied_replacements", job)

    def test_asset_rename_rejects_empty_source_path(self):
        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "asset_rename",
                    "job_id": "rename-empty-source",
                    "from_path": "",
                    "to_path": "generated/characters/苏黎",
                    "replacements": [
                        {
                            "from": "generated/characters/无名少女/无名少女.png",
                            "to": "generated/characters/苏黎/苏黎.png",
                        }
                    ],
                }
            ],
        }

        result = self.mod.apply_plan(self.card, self.run_dir, plan, image_settings_ready=False)

        self.assertEqual(result["status"], "failed")
        job = _read_json(self.card / "generated" / "jobs" / "rename-empty-source.json")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["reason"], "invalid_asset_path")
        self.assertEqual(job["invalid_path"], "")

    def test_asset_rename_missing_source_fails_without_creating_target(self):
        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "asset_rename",
                    "job_id": "rename-missing-source",
                    "from_path": "generated/characters/不存在",
                    "to_path": "generated/characters/苏黎",
                    "replacements": [],
                }
            ],
        }

        result = self.mod.apply_plan(self.card, self.run_dir, plan, image_settings_ready=False)

        self.assertEqual(result["status"], "failed")
        job = _read_json(self.card / "generated" / "jobs" / "rename-missing-source.json")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["reason"], "source_asset_missing")
        self.assertFalse((self.card / "generated" / "characters" / "苏黎").exists())

    def test_required_scene_reference_waits_and_resolves_existing_references(self):
        existing = self.card / "generated" / "characters" / "苏黎" / "苏黎.png"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"fake image")
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-required-refs",
                    "round_id": "round-000003",
                    "prompt": "苏黎和林岚在剧院里对峙",
                    "reference_policy": "required",
                    "reference_candidates": [
                        {
                            "path": "generated/characters/苏黎/苏黎.png",
                            "purpose": "character_reference",
                        },
                        "generated/characters/林岚/林岚.png",
                    ],
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "waiting_on_references")
        self.assertEqual(commands, [])
        job = _read_json(self.card / "generated" / "jobs" / "scene-required-refs.json")
        self.assertEqual(job["status"], "waiting_on_references")
        self.assertEqual(job["reason"], "missing_character_reference")
        self.assertEqual(job["missing_references"], ["generated/characters/林岚/林岚.png"])
        self.assertEqual(job["resolved_references"], ["generated/characters/苏黎/苏黎.png"])

    def test_invalid_asset_paths_fail_before_worker(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "character_reference",
                    "job_id": "bad-character-path",
                    "character_name": "苏黎",
                    "target_path": "../escape.png",
                    "prompt": "为苏黎绘制人设图",
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(commands, [])
        job = _read_json(self.card / "generated" / "jobs" / "bad-character-path.json")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["reason"], "invalid_asset_path")
        self.assertEqual(job["invalid_path"], "../escape.png")
        self.assertNotIn("command", job)

    def test_missing_job_ids_are_stable_and_unique(self):
        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "round_id": "round-000003",
                    "prompt": "雨夜剧院外景",
                    "important_characters": [],
                },
                {
                    "queue_type": "scene_illustration",
                    "round_id": "round-000003",
                    "prompt": "剧院后台近景",
                    "important_characters": [],
                },
            ],
        }

        result = self.mod.apply_plan(self.card, self.run_dir, plan, image_settings_ready=False)

        job_ids = [job["job_id"] for job in result["jobs"]]
        self.assertEqual(
            job_ids,
            [
                "assets-round-000003-scene_illustration-1",
                "assets-round-000003-scene_illustration-2",
            ],
        )
        self.assertEqual(len(set(job_ids)), 2)
        files = sorted((self.card / "generated" / "jobs").glob("*.json"))
        self.assertEqual(
            [path.stem for path in files],
            [
                "assets-round-000003-scene-illustration-1",
                "assets-round-000003-scene-illustration-2",
            ],
        )

    def test_required_scene_missing_prefilled_resolved_reference_waits(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-prefilled-missing-ref",
                    "round_id": "round-000003",
                    "prompt": "Ada 站在舞台中央",
                    "reference_policy": "required",
                    "resolved_references": ["generated/characters/Ada/Ada.png"],
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "waiting_on_references")
        self.assertEqual(commands, [])
        job = _read_json(self.card / "generated" / "jobs" / "scene-prefilled-missing-ref.json")
        self.assertEqual(job["status"], "waiting_on_references")
        self.assertEqual(job["reason"], "missing_character_reference")
        self.assertEqual(job["missing_references"], ["generated/characters/Ada/Ada.png"])

    def test_required_scene_directory_reference_waits(self):
        directory_ref = self.card / "generated" / "characters" / "Ada" / "Ada.png"
        directory_ref.mkdir(parents=True)
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-directory-candidate-ref",
                    "round_id": "round-000003",
                    "prompt": "Ada 站在舞台中央",
                    "reference_policy": "required",
                    "reference_candidates": [
                        {
                            "path": "generated/characters/Ada/Ada.png",
                            "purpose": "character_reference",
                        }
                    ],
                },
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-directory-resolved-ref",
                    "round_id": "round-000003",
                    "prompt": "Ada 走向后台",
                    "reference_policy": "required",
                    "resolved_references": ["generated/characters/Ada/Ada.png"],
                },
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "waiting_on_references")
        self.assertEqual(commands, [])
        for job_id in ("scene-directory-candidate-ref", "scene-directory-resolved-ref"):
            job = _read_json(self.card / "generated" / "jobs" / f"{job_id}.json")
            self.assertEqual(job["status"], "waiting_on_references")
            self.assertEqual(job["missing_references"], ["generated/characters/Ada/Ada.png"])

    def test_optional_scene_resolved_references_do_not_pass_worker_references(self):
        ref = self.card / "generated" / "characters" / "Ada" / "Ada.png"
        ref.parent.mkdir(parents=True)
        ref.write_bytes(b"fake image")
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-optional-ref",
                    "round_id": "round-000003",
                    "prompt": "Ada 站在舞台中央",
                    "reference_policy": "optional",
                    "resolved_references": ["generated/characters/Ada/Ada.png"],
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(len(commands), 1)
        self.assertNotIn("--reference", commands[0])
        job = _read_json(self.card / "generated" / "jobs" / "scene-optional-ref.json")
        self.assertEqual(job["resolved_references"], ["generated/characters/Ada/Ada.png"])

    def test_scene_important_characters_are_prompt_only_without_required_policy(self):
        for policy in (None, "optional"):
            with self.subTest(reference_policy=policy):
                commands = []

                def fake_run(command, **kwargs):
                    commands.append(command)
                    return SimpleNamespace(returncode=0, stdout="", stderr="")

                job_id = "scene-important-no-policy" if policy is None else "scene-important-optional"
                job = {
                    "queue_type": "scene_illustration",
                    "job_id": job_id,
                    "round_id": "round-000003",
                    "prompt": "Ada walks through the market",
                    "important_characters": ["Ada"],
                }
                if policy is not None:
                    job["reference_policy"] = policy
                plan = {
                    "schema_version": 1,
                    "plan_id": "assets-round-000003",
                    "jobs": [job],
                }

                result = self.mod.apply_plan(
                    self.card,
                    self.run_dir,
                    plan,
                    image_settings_ready=True,
                    run_command=fake_run,
                )

                self.assertEqual(result["status"], "queued")
                self.assertEqual(len(commands), 1)
                self.assertNotIn("--reference", commands[0])
                persisted = _read_json(self.card / "generated" / "jobs" / f"{job_id}.json")
                self.assertEqual(persisted["status"], "queued")
                self.assertNotIn("missing_references", persisted)

    def test_required_scene_important_character_waits_for_generated_reference(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-important-required",
                    "round_id": "round-000003",
                    "prompt": "Ada walks through the market",
                    "important_characters": ["Ada"],
                    "reference_policy": "required",
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "waiting_on_references")
        self.assertEqual(commands, [])
        job = _read_json(self.card / "generated" / "jobs" / "scene-important-required.json")
        self.assertEqual(job["status"], "waiting_on_references")
        self.assertEqual(job["missing_references"], ["generated/characters/Ada/Ada.png"])

    def test_scene_waits_only_for_important_character_references(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-important-and-minor",
                    "round_id": "round-000003",
                    "prompt": "Ada and a passerby cross the market",
                    "characters": ["Ada", "Passerby"],
                    "important_characters": ["Ada"],
                    "reference_policy": "required",
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "waiting_on_references")
        self.assertEqual(commands, [])
        job = _read_json(self.card / "generated" / "jobs" / "scene-important-and-minor.json")
        self.assertEqual(job["status"], "waiting_on_references")
        self.assertEqual(job["missing_references"], ["generated/characters/Ada/Ada.png"])

    def test_scene_with_existing_important_reference_ignores_minor_character_reference(self):
        ref = self.card / "generated" / "characters" / "Ada" / "Ada.png"
        ref.parent.mkdir(parents=True)
        ref.write_bytes(b"fake image")
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-existing-important",
                    "round_id": "round-000003",
                    "prompt": "Ada and a passerby cross the market",
                    "characters": ["Ada", "Passerby"],
                    "important_characters": ["Ada"],
                    "reference_policy": "required",
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(len(commands), 1)
        job = _read_json(self.card / "generated" / "jobs" / "scene-existing-important.json")
        self.assertEqual(job["status"], "queued")
        self.assertNotIn("missing_references", job)

    def test_first_character_reference_without_style_creates_candidate_batch_and_blocks_others(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "style_state": {"has_style_reference": False, "style_reference_paths": []},
            "jobs": [
                {
                    "queue_type": "character_reference",
                    "job_id": "character-ada-reference",
                    "character_name": "Ada",
                    "prompt": "Create Ada reference",
                },
                {
                    "queue_type": "character_reference",
                    "job_id": "character-ben-reference",
                    "character_name": "Ben",
                    "prompt": "Create Ben reference",
                },
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(len(result["jobs"]), 4)
        self.assertEqual(len(commands), 3)
        candidate_ids = [job["job_id"] for job in result["jobs"][:3]]
        self.assertEqual(
            candidate_ids,
            [
                "character-ada-reference-candidate-1",
                "character-ada-reference-candidate-2",
                "character-ada-reference-candidate-3",
            ],
        )
        for index, job in enumerate(result["jobs"][:3], start=1):
            self.assertEqual(job["queue_type"], "character_reference_candidate")
            self.assertEqual(job["batch_id"], "character-ada-reference-candidates")
            self.assertEqual(job["display_policy"], "hidden_reference")
            self.assertEqual(
                job["target_path"],
                f"generated/tmp/character-ada-reference-candidates/candidate-{index}.png",
            )
            self.assertEqual(job["status"], "queued")
        blocked = result["jobs"][3]
        self.assertEqual(blocked["job_id"], "character-ben-reference")
        self.assertEqual(blocked["status"], "waiting_on_style_reference")
        self.assertEqual(blocked["reason"], "style_reference_not_selected")
        self.assertNotIn("command", blocked)

    def test_character_reference_with_existing_style_does_not_create_candidate_batch(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "style_state": {"has_style_reference": True, "style_reference_paths": []},
            "jobs": [
                {
                    "queue_type": "character_reference",
                    "job_id": "character-ada-reference",
                    "character_name": "Ada",
                    "prompt": "Create Ada reference",
                },
                {
                    "queue_type": "character_reference",
                    "job_id": "character-ben-reference",
                    "character_name": "Ben",
                    "prompt": "Create Ben reference",
                },
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "queued")
        self.assertEqual([job["queue_type"] for job in result["jobs"]], ["character_reference", "character_reference"])
        self.assertEqual(len(commands), 2)

    def test_character_candidate_batch_uses_safe_batch_path(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "style_state": {"has_style_reference": False, "style_reference_paths": []},
            "jobs": [
                {
                    "queue_type": "character_reference",
                    "job_id": "character:suli/reference",
                    "character_name": "Suli",
                    "prompt": "Create Suli reference",
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(len(commands), 3)
        for index, job in enumerate(result["jobs"], start=1):
            self.assertEqual(job["batch_id"], "character-suli-reference-candidates")
            self.assertEqual(
                job["target_path"],
                f"generated/tmp/character-suli-reference-candidates/candidate-{index}.png",
            )
            self.assertNotIn(":", job["target_path"])
            self.assertNotIn("\\", job["target_path"])
            self.assertNotIn("..", Path(job["target_path"]).parts)

    def test_completed_candidates_create_waiting_critic_selection_without_vision(self):
        batch_id = "character-ada-reference-candidates"
        for index in range(1, 4):
            self._write_completed_candidate(batch_id, index)

        result = self.mod.resume_waiting_jobs(self.card, self.run_dir)

        self.assertEqual(result["status"], "waiting_on_critic")
        self.assertEqual(len(result["jobs"]), 1)
        selection = result["jobs"][0]
        self.assertEqual(selection["queue_type"], "character_reference_selection")
        self.assertEqual(selection["job_id"], f"{batch_id}-selection")
        self.assertEqual(selection["batch_id"], batch_id)
        self.assertEqual(selection["status"], "waiting_on_critic")
        self.assertEqual(selection["reason"], "critic_vision_not_available")
        self.assertEqual(len(selection["candidates"]), 3)
        persisted = _read_json(self.card / "generated" / "jobs" / f"{batch_id}-selection.json")
        self.assertEqual(persisted["status"], "waiting_on_critic")
        mirror = _read_json(
            self.run_dir / "artifacts" / "assets_ui" / "jobs" / f"{batch_id}-selection.json"
        )
        self.assertEqual(mirror["reason"], "critic_vision_not_available")

    def test_critic_selection_moves_winner_and_rejected_candidates(self):
        batch_id = "character-ada-reference-candidates"
        for index in range(1, 4):
            self._write_completed_candidate(batch_id, index)
        calls = []

        def fake_critic(payload):
            calls.append(payload)
            return {
                "winner_candidate_id": f"{batch_id}-candidate-2",
                "notes": "candidate 2 has the cleanest character reference",
            }

        result = self.mod.resume_waiting_jobs(self.card, self.run_dir, critic_runner=fake_critic)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["batch_id"], batch_id)
        self.assertEqual([item["job_id"] for item in calls[0]["candidates"]], [
            f"{batch_id}-candidate-1",
            f"{batch_id}-candidate-2",
            f"{batch_id}-candidate-3",
        ])
        final_path = self.card / "generated" / "characters" / "Ada" / "Ada.png"
        self.assertEqual(final_path.read_bytes(), b"candidate-2")
        rejected_dir = self.card / "generated" / "tmp" / batch_id / "rejected"
        self.assertEqual((rejected_dir / "candidate-1.png").read_bytes(), b"candidate-1")
        self.assertEqual((rejected_dir / "candidate-3.png").read_bytes(), b"candidate-3")
        self.assertFalse((self.card / "generated" / "tmp" / batch_id / "candidate-1.png").exists())
        self.assertFalse((self.card / "generated" / "tmp" / batch_id / "candidate-2.png").exists())
        self.assertFalse((self.card / "generated" / "tmp" / batch_id / "candidate-3.png").exists())
        selection = _read_json(self.card / "generated" / "jobs" / f"{batch_id}-selection.json")
        self.assertEqual(selection["status"], "completed")
        self.assertEqual(selection["winner_candidate_id"], f"{batch_id}-candidate-2")
        self.assertEqual(selection["final_target_path"], "generated/characters/Ada/Ada.png")
        self.assertEqual(selection["critic_report"]["notes"], "candidate 2 has the cleanest character reference")

    def test_critic_selection_resumes_after_winner_was_already_moved(self):
        batch_id = "character-ada-reference-candidates"
        for index in range(1, 4):
            self._write_completed_candidate(batch_id, index)
        winner_source = self.card / "generated" / "tmp" / batch_id / "candidate-2.png"
        final_path = self.card / "generated" / "characters" / "Ada" / "Ada.png"
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(winner_source.read_bytes())
        winner_source.unlink()

        def fake_critic(payload):
            return {"winner_candidate_id": f"{batch_id}-candidate-2"}

        result = self.mod.resume_waiting_jobs(self.card, self.run_dir, critic_runner=fake_critic)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(final_path.read_bytes(), b"candidate-2")
        rejected_dir = self.card / "generated" / "tmp" / batch_id / "rejected"
        self.assertEqual((rejected_dir / "candidate-1.png").read_bytes(), b"candidate-1")
        self.assertEqual((rejected_dir / "candidate-3.png").read_bytes(), b"candidate-3")
        self.assertFalse((self.card / "generated" / "tmp" / batch_id / "candidate-1.png").exists())
        self.assertFalse((self.card / "generated" / "tmp" / batch_id / "candidate-2.png").exists())
        self.assertFalse((self.card / "generated" / "tmp" / batch_id / "candidate-3.png").exists())
        selection = _read_json(self.card / "generated" / "jobs" / f"{batch_id}-selection.json")
        self.assertEqual(selection["status"], "completed")
        self.assertEqual(selection["winner_candidate_id"], f"{batch_id}-candidate-2")
        self.assertEqual(selection["final_target_path"], "generated/characters/Ada/Ada.png")

    def test_critic_selection_records_missing_rejected_candidate_without_blocking(self):
        batch_id = "character-ada-reference-candidates"
        for index in range(1, 4):
            self._write_completed_candidate(batch_id, index)
        rejected_source = self.card / "generated" / "tmp" / batch_id / "candidate-1.png"
        rejected_source.unlink()

        def fake_critic(payload):
            return {"winner_candidate_id": f"{batch_id}-candidate-2"}

        result = self.mod.resume_waiting_jobs(self.card, self.run_dir, critic_runner=fake_critic)

        self.assertEqual(result["status"], "completed")
        self.assertEqual((self.card / "generated" / "characters" / "Ada" / "Ada.png").read_bytes(), b"candidate-2")
        self.assertFalse((self.card / "generated" / "tmp" / batch_id / "rejected" / "candidate-1.png").exists())
        self.assertEqual(
            (self.card / "generated" / "tmp" / batch_id / "rejected" / "candidate-3.png").read_bytes(),
            b"candidate-3",
        )
        selection = _read_json(self.card / "generated" / "jobs" / f"{batch_id}-selection.json")
        self.assertEqual(selection["status"], "completed")
        self.assertEqual(
            selection["missing_rejected_paths"],
            [f"generated/tmp/{batch_id}/candidate-1.png"],
        )

    def test_completed_critic_selection_is_not_overwritten_on_resume(self):
        batch_id = "character-ada-reference-candidates"
        for index in range(1, 4):
            self._write_completed_candidate(batch_id, index)

        def fake_critic(payload):
            return {"winner_candidate_id": f"{batch_id}-candidate-2"}

        first = self.mod.resume_waiting_jobs(self.card, self.run_dir, critic_runner=fake_critic)
        second = self.mod.resume_waiting_jobs(self.card, self.run_dir)

        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        selection = _read_json(self.card / "generated" / "jobs" / f"{batch_id}-selection.json")
        self.assertEqual(selection["status"], "completed")
        self.assertEqual(selection["winner_candidate_id"], f"{batch_id}-candidate-2")

    def test_critic_selection_unsafe_final_target_waits_without_moving_files(self):
        batch_id = "character-ada-reference-candidates"
        for index in range(1, 4):
            self._write_completed_candidate(batch_id, index)

        def fake_critic(payload):
            return {
                "winner_candidate_id": f"{batch_id}-candidate-2",
                "final_target_path": "../escape.png",
            }

        result = self.mod.resume_waiting_jobs(self.card, self.run_dir, critic_runner=fake_critic)

        self.assertEqual(result["status"], "waiting_on_critic")
        for index in range(1, 4):
            self.assertTrue((self.card / "generated" / "tmp" / batch_id / f"candidate-{index}.png").is_file())
        self.assertFalse((self.card / "generated" / "characters" / "Ada" / "Ada.png").exists())
        selection = _read_json(self.card / "generated" / "jobs" / f"{batch_id}-selection.json")
        self.assertEqual(selection["status"], "waiting_on_critic")
        self.assertEqual(selection["reason"], "invalid_final_target_path")
        self.assertEqual(selection["invalid_path"], "../escape.png")

    def test_preset_waiting_job_persists_without_worker_submission(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "character_reference",
                    "job_id": "character-ben-reference",
                    "character_name": "Ben",
                    "prompt": "Create Ben reference",
                    "status": "waiting_on_style_reference",
                    "reason": "style_reference_not_selected",
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "waiting_on_style_reference")
        self.assertEqual(commands, [])
        job = _read_json(self.card / "generated" / "jobs" / "character-ben-reference.json")
        self.assertEqual(job["status"], "waiting_on_style_reference")
        self.assertEqual(job["reason"], "style_reference_not_selected")
        self.assertNotIn("command", job)

    def test_preset_waiting_job_invalid_path_fails_before_persistence(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "character_reference",
                    "job_id": "character-invalid-waiting-reference",
                    "character_name": "Ben",
                    "target_path": "../escape.png",
                    "prompt": "Create Ben reference",
                    "status": "waiting_on_style_reference",
                    "reason": "style_reference_not_selected",
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(commands, [])
        job = _read_json(self.card / "generated" / "jobs" / "character-invalid-waiting-reference.json")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["reason"], "invalid_asset_path")
        self.assertEqual(job["invalid_path"], "../escape.png")
        self.assertNotIn("command", job)

    def test_optional_scene_invalid_reference_candidate_path_fails_before_worker(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-optional-invalid-candidate",
                    "round_id": "round-000003",
                    "prompt": "Ada 站在舞台中央",
                    "reference_policy": "optional",
                    "reference_candidates": ["../escape.png"],
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(commands, [])
        job = _read_json(self.card / "generated" / "jobs" / "scene-optional-invalid-candidate.json")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["reason"], "invalid_asset_path")
        self.assertEqual(job["invalid_path"], "../escape.png")

    def test_required_scene_resolved_references_pass_worker_references(self):
        ref = self.card / "generated" / "characters" / "Ada" / "Ada.png"
        ref.parent.mkdir(parents=True)
        ref.write_bytes(b"fake image")
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-required-ref",
                    "round_id": "round-000003",
                    "prompt": "Ada 走向后台",
                    "reference_policy": "required",
                    "resolved_references": ["generated/characters/Ada/Ada.png"],
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(len(commands), 1)
        self.assertIn("--reference", commands[0])
        ref_index = commands[0].index("--reference")
        self.assertEqual(commands[0][ref_index + 1], "generated/characters/Ada/Ada.png")
        self.assertIn("--round-id", commands[0])
        self.assertEqual(commands[0][commands[0].index("--round-id") + 1], "round-000003")

    def test_worker_structured_deferred_stdout_preserves_reason(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(
                returncode=1,
                stdout=json.dumps(
                    {
                        "status": "deferred",
                        "reason": "reference_image_not_supported",
                        "references": ["x.png"],
                    }
                ),
                stderr="",
            )

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-worker-deferred",
                    "round_id": "round-000003",
                    "prompt": "雨夜剧院",
                    "important_characters": [],
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "deferred")
        self.assertEqual(len(commands), 1)
        job = _read_json(self.card / "generated" / "jobs" / "scene-worker-deferred.json")
        self.assertEqual(job["status"], "deferred")
        self.assertEqual(job["reason"], "reference_image_not_supported")
        self.assertEqual(job["worker_references"], ["x.png"])

    def test_worker_structured_deferred_stdout_uses_error_as_reason(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(
                returncode=1,
                stdout=json.dumps(
                    {
                        "status": "deferred",
                        "error": "reference_image_not_supported",
                    }
                ),
                stderr="",
            )

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-worker-deferred-error",
                    "round_id": "round-000003",
                    "prompt": "Rainy theater",
                    "important_characters": [],
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "deferred")
        self.assertEqual(len(commands), 1)
        job = _read_json(self.card / "generated" / "jobs" / "scene-worker-deferred-error.json")
        self.assertEqual(job["status"], "deferred")
        self.assertEqual(job["reason"], "reference_image_not_supported")

    def test_worker_image_generation_failed_stdout_remains_resumable(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(
                returncode=1,
                stdout=json.dumps(
                    {
                        "status": "failed",
                        "reason": "image_generation_failed",
                    }
                ),
                stderr="",
            )

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-image-generation-failed",
                    "round_id": "round-000003",
                    "prompt": "Rainy theater",
                    "important_characters": [],
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "deferred")
        self.assertEqual(len(commands), 1)
        job = _read_json(self.card / "generated" / "jobs" / "scene-image-generation-failed.json")
        self.assertEqual(job["status"], "deferred")
        self.assertEqual(job["reason"], "image_generation_failed")

    def test_worker_completed_job_file_is_not_overwritten_by_parent_queue_state(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            completed = {
                "schema_version": 1,
                "queue_type": "scene_illustration",
                "job_id": "scene-worker-race",
                "agent_plan_id": "assets-round-000003",
                "status": "completed",
                "output_path": "generated/images/scene-worker-race.png",
            }
            job_path = self.card / "generated" / "jobs" / "scene-worker-race.json"
            job_path.parent.mkdir(parents=True)
            job_path.write_text(json.dumps(completed), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-worker-race",
                    "round_id": "round-000003",
                    "prompt": "Rainy theater",
                    "important_characters": [],
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(commands), 1)
        job = _read_json(self.card / "generated" / "jobs" / "scene-worker-race.json")
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["output_path"], "generated/images/scene-worker-race.png")
        mirror = _read_json(self.run_dir / "artifacts" / "assets_ui" / "jobs" / "scene-worker-race.json")
        self.assertEqual(mirror["status"], "completed")
        self.assertEqual(mirror["output_path"], "generated/images/scene-worker-race.png")

    def test_punctuation_job_id_uses_worker_compatible_filename_slug(self):
        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "a:b",
                    "round_id": "round-000003",
                    "prompt": "exterior",
                    "important_characters": [],
                }
            ],
        }

        result = self.mod.apply_plan(self.card, self.run_dir, plan, image_settings_ready=False)

        self.assertEqual(result["status"], "deferred")
        self.assertTrue((self.card / "generated" / "jobs" / "ab.json").is_file())
        self.assertFalse((self.card / "generated" / "jobs" / "a_b.json").exists())
        self.assertTrue(
            (self.run_dir / "artifacts" / "assets_ui" / "jobs" / "ab.json").is_file()
        )
        mirror = _read_json(self.run_dir / "artifacts" / "assets_ui" / "jobs" / "ab.json")
        self.assertEqual(mirror["job_id"], "a:b")

    def test_unknown_queue_type_fails_without_worker(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "ui_patch",
                    "job_id": "unknown-ui-patch",
                    "prompt": "更新状态栏",
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(commands, [])
        job = _read_json(self.card / "generated" / "jobs" / "unknown-ui-patch.json")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["reason"], "unsupported_queue_type")
        self.assertEqual(job["queue_type"], "ui_patch")

    def test_missing_queue_type_fails_without_worker(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "job_id": "missing-queue-type",
                    "prompt": "没有类型的任务",
                }
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(commands, [])
        job = _read_json(self.card / "generated" / "jobs" / "missing-queue-type.json")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["reason"], "invalid_queue_type")

    def test_worker_exception_persists_job_and_continues(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            raise RuntimeError("worker unavailable")

        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-worker-exception",
                    "round_id": "round-000003",
                    "prompt": "雨夜剧院",
                    "important_characters": [],
                },
                {
                    "queue_type": "scene_illustration",
                    "job_id": "scene-deferred-after-exception",
                    "round_id": "round-000003",
                    "prompt": "后台灯光",
                    "important_characters": [],
                },
            ],
        }

        result = self.mod.apply_plan(
            self.card,
            self.run_dir,
            plan,
            image_settings_ready=True,
            run_command=fake_run,
        )

        self.assertEqual(result["status"], "deferred")
        self.assertEqual(len(result["jobs"]), 2)
        self.assertEqual(len(calls), 2)
        first = _read_json(self.card / "generated" / "jobs" / "scene-worker-exception.json")
        second = _read_json(self.card / "generated" / "jobs" / "scene-deferred-after-exception.json")
        self.assertEqual(first["status"], "deferred")
        self.assertEqual(first["reason"], "asset_worker_start_failed")
        self.assertEqual(first["exception_type"], "RuntimeError")
        self.assertIn("worker unavailable", first["error"])
        self.assertEqual(second["status"], "deferred")
        self.assertEqual(second["reason"], "asset_worker_start_failed")

    def test_sanitized_explicit_job_id_collisions_are_disambiguated(self):
        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": [
                {
                    "queue_type": "scene_illustration",
                    "job_id": "a/b",
                    "round_id": "round-000003",
                    "prompt": "外景",
                    "important_characters": [],
                },
                {
                    "queue_type": "scene_illustration",
                    "job_id": "a:b",
                    "round_id": "round-000003",
                    "prompt": "内景",
                    "important_characters": [],
                },
            ],
        }

        result = self.mod.apply_plan(self.card, self.run_dir, plan, image_settings_ready=False)

        job_ids = [job["job_id"] for job in result["jobs"]]
        self.assertEqual(job_ids, ["a/b", "a:b-2"])
        self.assertEqual(result["jobs"][1]["source_job_id"], "a:b")
        files = sorted((self.card / "generated" / "jobs").glob("*.json"))
        self.assertEqual([path.name for path in files], ["ab-2.json", "ab.json"])

    def test_non_dict_plan_job_persists_failed_record(self):
        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": ["not a job"],
        }

        result = self.mod.apply_plan(self.card, self.run_dir, plan, image_settings_ready=False)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["jobs"][0]["status"], "failed")
        self.assertEqual(result["jobs"][0]["reason"], "invalid_job")
        job = _read_json(self.card / "generated" / "jobs" / "assets-round-000003-invalid-job-1.json")
        self.assertEqual(job["raw_job"], "not a job")

    def test_non_list_jobs_persists_failed_plan_shape_record(self):
        plan = {
            "schema_version": 1,
            "plan_id": "assets-round-000003",
            "jobs": {"queue_type": "scene_illustration"},
        }

        result = self.mod.apply_plan(self.card, self.run_dir, plan, image_settings_ready=False)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["jobs"][0]["status"], "failed")
        self.assertEqual(result["jobs"][0]["reason"], "invalid_jobs_shape")
        job = _read_json(
            self.card / "generated" / "jobs" / "assets-round-000003-invalid-jobs-shape-1.json"
        )
        self.assertEqual(job["raw_jobs_type"], "dict")
