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
            "snapshot_id": "round-000003-20260623T000000000000Z-abc123def456",
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
        normalized["preserved_player_input_ids"][0]["raw_text"] = "changed"

        self.assertEqual(plan, original)
        self.assertEqual(normalized["schema_version"], 1)
        self.assertEqual(normalized["plan_id"], "replay-001")
        self.assertEqual(normalized["scope"], "single_round")
        self.assertNotIn("mode", normalized)
        self.assertEqual(normalized["snapshot_id"], plan["snapshot_id"])
        self.assertEqual(normalized["affected_rounds"], ["round-000003"])
        self.assertEqual(
            normalized["discard_ai_artifacts"],
            ["gm.output.json", "actor.outputs.json", "interaction.trace.json", "story.input.json"],
        )
        self.assertNotIn("discard_artifacts", normalized)
        self.assertIs(normalized["requires_manual_confirmation"], True)

    def test_validate_replay_plan_rejects_multi_round_with_readable_error(self):
        plan = self._valid_plan()
        plan["scope"] = "multi_round"

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "multi_round replay is plan-only"):
            self.mod.validate_replay_plan(plan)

    def test_validate_replay_plan_requires_manual_confirmation_true(self):
        plan = self._valid_plan()
        plan["requires_manual_confirmation"] = False

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "requires_manual_confirmation must be true"):
            self.mod.validate_replay_plan(plan)

    def test_validate_replay_plan_rejects_unsafe_artifact_paths(self):
        plan = self._valid_plan()
        plan["discard_ai_artifacts"] = ["../story.output.json"]

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "discard_ai_artifacts"):
            self.mod.validate_replay_plan(plan)

    def test_validate_replay_plan_rejects_artifacts_outside_allowlist(self):
        plan = self._valid_plan()
        plan["discard_ai_artifacts"] = ["debug/model_calls/index.jsonl"]

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "unsupported artifact"):
            self.mod.validate_replay_plan(plan)

    def test_validate_replay_plan_rejects_invalid_preserved_input_entries(self):
        plan = self._valid_plan()
        plan["preserved_player_input_ids"] = [123]

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "preserved_player_input_ids"):
            self.mod.validate_replay_plan(plan)

    def test_validate_replay_plan_accepts_aliases_but_normalizes_output(self):
        plan = self._valid_plan()
        plan["mode"] = plan.pop("scope")
        plan["preserved_player_inputs"] = plan.pop("preserved_player_input_ids")
        plan["discard_artifacts"] = plan.pop("discard_ai_artifacts")

        normalized = self.mod.validate_replay_plan(plan)

        self.assertEqual(normalized["scope"], "single_round")
        self.assertIn("preserved_player_input_ids", normalized)
        self.assertIn("discard_ai_artifacts", normalized)
        self.assertNotIn("preserved_player_inputs", normalized)
        self.assertNotIn("discard_artifacts", normalized)

    def test_materialize_replay_plan_writes_artifact_inside_run_without_side_effects(self):
        plan = self._valid_plan()
        snapshot_dir = self.run_dir.parent / "snapshots" / plan["snapshot_id"]
        snapshot_dir.mkdir(parents=True)
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
        self.assertIn("preserved_player_input_ids", payload)
        self.assertIn("discard_ai_artifacts", payload)
        self.assertNotIn("preserved_player_inputs", payload)
        self.assertNotIn("discard_artifacts", payload)
        self.assertTrue(snapshot_dir.is_dir())
        self.assertEqual(existing_artifact.read_text(encoding="utf-8"), '{"keep": true}')


if __name__ == "__main__":
    unittest.main()
