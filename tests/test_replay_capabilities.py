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


class ReplayCapabilitiesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "card" / ".agent_runs" / "round-000003"
        self.run_dir.mkdir(parents=True)
        self.mod = _load("replay_capabilities")

    def tearDown(self):
        self.tmp.cleanup()

    def _valid_plan(self):
        return {
            "schema_version": 1,
            "plan_id": "replay-001",
            "scope": "single_round",
            "backup_id": "round-000003-20260623T000000000000Z-abc123def456",
            "affected_rounds": ["round-000003"],
            "preserved_player_input_ids": [
                {
                    "round_id": "round-000003",
                    "input_id": "input-3",
                    "raw_text": "Treat the previous round as a dream and continue after waking.",
                }
            ],
            "discard_ai_artifacts": [
                "gm.output.json",
                "actor.outputs.json",
                "interaction.trace.json",
                "story.input.json",
            ],
            "requires_manual_confirmation": True,
        }

    def test_validate_replay_plan_normalizes_without_mutating_input(self):
        plan = self._valid_plan()
        original = json.loads(json.dumps(plan, ensure_ascii=False))

        normalized = self.mod.validate_replay_plan(plan)

        self.assertEqual(normalized["schema_version"], 1)
        self.assertEqual(normalized["plan_id"], "replay-001")
        self.assertEqual(normalized["scope"], "single_round")
        self.assertNotIn("mode", normalized)
        self.assertEqual(normalized["backup_id"], plan["backup_id"])
        self.assertEqual(
            normalized["affected_inputs"],
            [
                {
                    "round_id": "round-000003",
                    "input_id": "round-000003",
                    "role_text": "",
                    "user_instruction_text": "",
                }
            ],
        )
        self.assertNotIn("snapshot_id", normalized)
        self.assertNotIn("affected_rounds", normalized)
        self.assertNotIn("preserved_player_input_ids", normalized)
        self.assertNotIn("discard_artifacts", normalized)
        self.assertIs(normalized["requires_manual_confirmation"], True)
        normalized["affected_inputs"][0]["role_text"] = "changed"
        self.assertEqual(plan, original)

    def test_validate_replay_plan_rejects_unknown_scope_with_readable_error(self):
        plan = self._valid_plan()
        plan["scope"] = "all_rounds"

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "single_round or multi_round"):
            self.mod.validate_replay_plan(plan)

    def test_validate_replay_plan_accepts_multi_round_backup_plan(self):
        plan = self._valid_plan()
        plan["scope"] = "multi_round"
        plan["affected_inputs"] = [
            {"round_id": "round-000001", "input_id": "input-1", "role_text": "first", "user_instruction_text": ""},
            {"round_id": "round-000002", "input_id": "input-2", "role_text": "second", "user_instruction_text": "guide"},
        ]
        plan.pop("affected_rounds", None)

        normalized = self.mod.validate_replay_plan(plan)

        self.assertEqual(normalized["scope"], "multi_round")
        self.assertEqual(normalized["backup_id"], plan["backup_id"])
        self.assertEqual([item["input_id"] for item in normalized["affected_inputs"]], ["input-1", "input-2"])
        self.assertNotIn("snapshot_id", normalized)

    def test_validate_replay_execute_requires_plan_id(self):
        result = self.mod.validate_replay_execute({"schema_version": 1, "plan_id": "replay-001", "resume": True})

        self.assertEqual(result["plan_id"], "replay-001")
        self.assertTrue(result["resume"])

    def test_validate_replay_plan_rejects_empty_affected_inputs(self):
        plan = self._valid_plan()
        plan["scope"] = "multi_round"
        plan["affected_inputs"] = []
        plan.pop("affected_rounds", None)

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "affected_inputs"):
            self.mod.validate_replay_plan(plan)

    def test_validate_replay_plan_rejects_invalid_affected_rounds_alias(self):
        for affected_rounds in (None, "", "round-000003", []):
            with self.subTest(affected_rounds=affected_rounds):
                plan = self._valid_plan()
                plan["affected_rounds"] = affected_rounds

                with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "affected_rounds"):
                    self.mod.validate_replay_plan(plan)

    def test_validate_replay_plan_rejects_invalid_affected_rounds_alias_even_with_affected_inputs(self):
        for affected_rounds in (None, "", "round-000003", []):
            with self.subTest(affected_rounds=affected_rounds):
                plan = self._valid_plan()
                plan["affected_inputs"] = [
                    {"round_id": "round-000003", "input_id": "input-3", "role_text": "", "user_instruction_text": ""}
                ]
                plan["affected_rounds"] = affected_rounds

                with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "affected_rounds"):
                    self.mod.validate_replay_plan(plan)

    def test_validate_replay_plan_normalizes_manual_confirmation_to_bool(self):
        plan = self._valid_plan()
        plan["requires_manual_confirmation"] = False

        normalized = self.mod.validate_replay_plan(plan)

        self.assertIs(normalized["requires_manual_confirmation"], False)

    def test_validate_replay_plan_requires_schema_version(self):
        plan = self._valid_plan()
        del plan["schema_version"]

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "schema_version must be 1"):
            self.mod.validate_replay_plan(plan)

    def test_validate_replay_plan_rejects_invalid_backup_id(self):
        for backup_id in ("..", "not-a-backup"):
            with self.subTest(backup_id=backup_id):
                plan = self._valid_plan()
                plan["backup_id"] = backup_id

                with self.assertRaisesRegex(self.mod.ReplayCapabilityError, r"backup_id.*backup id"):
                    self.mod.validate_replay_plan(plan)

    def test_validate_replay_plan_rejects_non_object_affected_inputs(self):
        plan = self._valid_plan()
        plan["affected_inputs"] = [123]
        plan.pop("affected_rounds", None)

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "affected_inputs"):
            self.mod.validate_replay_plan(plan)

    def test_validate_replay_plan_rejects_invalid_affected_input_round_id(self):
        plan = self._valid_plan()
        plan["affected_inputs"] = [
            {"round_id": "not-a-round", "input_id": "input-1", "role_text": "", "user_instruction_text": ""}
        ]
        plan.pop("affected_rounds", None)

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "round_id"):
            self.mod.validate_replay_plan(plan)

    def test_validate_replay_plan_rejects_blank_affected_input_id(self):
        plan = self._valid_plan()
        plan["affected_inputs"] = [
            {"round_id": "round-000003", "input_id": "   ", "role_text": "", "user_instruction_text": ""}
        ]
        plan.pop("affected_rounds", None)

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "input_id"):
            self.mod.validate_replay_plan(plan)

    def test_validate_replay_execute_rejects_blank_plan_id(self):
        plan = self._valid_plan()

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "plan_id"):
            self.mod.validate_replay_execute({"schema_version": 1, "plan_id": "   "})

    def test_validate_replay_plan_rejects_reserved_or_path_plan_id(self):
        for plan_id in (".", "..", "a/b", "a\\b"):
            with self.subTest(plan_id=plan_id):
                plan = self._valid_plan()
                plan["plan_id"] = plan_id

                with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "plan_id"):
                    self.mod.validate_replay_plan(plan)

    def test_validate_replay_execute_rejects_reserved_or_path_plan_id(self):
        for plan_id in (".", "..", "a/b", "a\\b"):
            with self.subTest(plan_id=plan_id):
                payload = {"schema_version": 1, "plan_id": plan_id}

                with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "plan_id"):
                    self.mod.validate_replay_execute(payload)

    def test_validate_replay_plan_preserves_input_text_exactly(self):
        plan = self._valid_plan()
        plan["affected_inputs"] = [
            {
                "round_id": "round-000003",
                "input_id": "input-3",
                "role_text": "  exact role text  ",
                "user_instruction_text": "  exact guide text  ",
            }
        ]
        plan.pop("affected_rounds", None)

        normalized = self.mod.validate_replay_plan(plan)

        self.assertEqual(normalized["affected_inputs"][0]["role_text"], "  exact role text  ")
        self.assertEqual(normalized["affected_inputs"][0]["user_instruction_text"], "  exact guide text  ")

    def test_validate_replay_plan_accepts_aliases_but_normalizes_output(self):
        plan = self._valid_plan()
        plan["mode"] = plan.pop("scope")
        plan["snapshot_id"] = plan.pop("backup_id")

        normalized = self.mod.validate_replay_plan(plan)

        self.assertEqual(normalized["scope"], "single_round")
        self.assertEqual(normalized["backup_id"], plan["snapshot_id"])
        self.assertEqual(normalized["affected_inputs"][0]["round_id"], "round-000003")
        self.assertEqual(normalized["affected_inputs"][0]["input_id"], "round-000003")
        self.assertNotIn("affected_rounds", normalized)

    def test_materialize_replay_plan_writes_artifact_inside_run_without_side_effects(self):
        plan = self._valid_plan()
        backup_dir = self.run_dir.parent.parent / "backup" / plan["backup_id"]
        backup_dir.mkdir(parents=True)
        (backup_dir / "backup.json").write_text("{}", encoding="utf-8")
        existing_artifact = self.run_dir / "artifacts" / "story.input.json"
        existing_artifact.parent.mkdir(parents=True)
        existing_artifact.write_text('{"keep": true}', encoding="utf-8")

        result = self.mod.materialize_replay_plan(self.run_dir, plan)

        self.assertEqual(result["artifact_path"], "artifacts/replay_plans/replay-001.json")
        self.assertEqual(result["plan"]["plan_id"], "replay-001")
        artifact_path = self.run_dir / result["artifact_path"]
        self.assertTrue(artifact_path.is_file())
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(payload, result["plan"])
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["scope"], "single_round")
        self.assertEqual(payload["backup_id"], plan["backup_id"])
        self.assertEqual(payload["affected_inputs"][0]["input_id"], "round-000003")
        self.assertNotIn("snapshot_id", payload)
        self.assertNotIn("affected_rounds", payload)
        self.assertTrue(backup_dir.is_dir())
        self.assertEqual(existing_artifact.read_text(encoding="utf-8"), '{"keep": true}')

    def test_materialize_replay_plan_writes_session_files(self):
        plan = self._valid_plan()
        plan["scope"] = "multi_round"
        plan["affected_inputs"] = [
            {"round_id": "round-000001", "input_id": "input-1", "role_text": "first", "user_instruction_text": ""},
            {"round_id": "round-000002", "input_id": "input-2", "role_text": "second", "user_instruction_text": ""},
        ]
        plan.pop("affected_rounds", None)
        backup_dir = self.run_dir.parent.parent / "backup" / plan["backup_id"]
        backup_dir.mkdir(parents=True)
        (backup_dir / "backup.json").write_text("{}", encoding="utf-8")

        result = self.mod.materialize_replay_plan(self.run_dir, plan)

        card = self.run_dir.parent.parent
        session = card / ".replay" / "sessions" / "replay-001"
        self.assertEqual(result["session_id"], "replay-001")
        self.assertTrue((card / ".replay" / "active.json").exists())
        self.assertTrue((session / "plan.json").exists())
        self.assertTrue((session / "status.json").exists())
        plan_payload = json.loads((session / "plan.json").read_text(encoding="utf-8"))
        active = json.loads((card / ".replay" / "active.json").read_text(encoding="utf-8"))
        status = json.loads((session / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(plan_payload, result["plan"])
        self.assertEqual(active, {"session_id": "replay-001"})
        self.assertEqual(
            status,
            {
                "schema_version": 1,
                "session_id": "replay-001",
                "status": "planned",
                "active_round_index": 0,
                "completed_rounds": [],
                "failed_round": {},
                "last_error": "",
            },
        )

    def test_materialize_replay_plan_allocates_monotonic_save_replay_index(self):
        backup_dir = self.run_dir.parent.parent / "backup" / self._valid_plan()["backup_id"]
        backup_dir.mkdir(parents=True)
        (backup_dir / "backup.json").write_text("{}", encoding="utf-8")
        first_plan = self._valid_plan()
        second_plan = self._valid_plan()
        second_plan["plan_id"] = "replay-002"

        first = self.mod.materialize_replay_plan(self.run_dir, first_plan)
        second = self.mod.materialize_replay_plan(self.run_dir, second_plan)

        card = self.run_dir.parent.parent
        counter = json.loads((card / ".replay" / "replay_counter.json").read_text(encoding="utf-8"))
        self.assertEqual(first["plan"]["replay_index"], 1)
        self.assertEqual(second["plan"]["replay_index"], 2)
        self.assertEqual(counter["last_replay_index"], 2)
        self.assertEqual(
            json.loads((card / ".replay" / "sessions" / "replay-002" / "plan.json").read_text(encoding="utf-8"))[
                "replay_index"
            ],
            2,
        )

    def test_materialize_replay_plan_requires_existing_backup(self):
        plan = self._valid_plan()

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "backup"):
            self.mod.materialize_replay_plan(self.run_dir, plan)

    def test_materialize_replay_plan_requires_backup_metadata_file(self):
        plan = self._valid_plan()
        backup_dir = self.run_dir.parent.parent / "backup" / plan["backup_id"]
        backup_dir.mkdir(parents=True)

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "backup"):
            self.mod.materialize_replay_plan(self.run_dir, plan)

    def test_materialize_replay_plan_rejects_existing_session_with_readable_error(self):
        plan = self._valid_plan()
        backup_dir = self.run_dir.parent.parent / "backup" / plan["backup_id"]
        backup_dir.mkdir(parents=True)
        (backup_dir / "backup.json").write_text("{}", encoding="utf-8")
        self.mod.materialize_replay_plan(self.run_dir, plan)

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "session.*exists|exists.*session"):
            self.mod.materialize_replay_plan(self.run_dir, plan)


if __name__ == "__main__":
    unittest.main()
