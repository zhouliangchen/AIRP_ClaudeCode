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
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

        def fake_run(command, **kwargs):
            commands.append(command)
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
        self.assertEqual([path.stem for path in files], job_ids)

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
        self.assertEqual([path.name for path in files], ["a_b-2.json", "a_b.json"])

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
        job = _read_json(self.card / "generated" / "jobs" / "assets-round-000003-invalid_job-1.json")
        self.assertEqual(job["raw_job"], "not a job")
