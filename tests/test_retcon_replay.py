import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    from tests.module_aliases import load_repo_module
    return load_repo_module(name)


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class RetconReplayShimTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.card.mkdir()
        self.retcon_replay = _load_module("retcon_replay")

    def tearDown(self):
        self.tmp.cleanup()

    def test_legacy_prepare_is_deprecated_noop_and_does_not_create_state(self):
        run_dir = self.card / ".agent_runs" / "round-000001"
        run_dir.mkdir(parents=True)
        _write_json(
            run_dir / "input_analysis.output.json",
            {
                "narrative_directives": {"rewrite_previous_output": True},
                "capability_requests": [],
            },
        )

        result = self.retcon_replay.prepare_replay_from_current_run(self.card, run_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "not_required")
        self.assertTrue(result["deprecated"])
        self.assertFalse((self.card / ".retcon_replay.json").exists())

    def test_active_constraint_is_empty_for_legacy_state(self):
        _write_json(
            self.card / ".retcon_replay.json",
            {
                "schema_version": 1,
                "status": "active",
                "active_input_index": 0,
                "records": [{"id": "input-1"}],
            },
        )

        constraint = self.retcon_replay.active_constraint_for_pending(self.card, {"id": "input-1"})

        self.assertEqual(constraint, {})

    def test_advance_after_delivery_is_deprecated_noop(self):
        _write_json(
            self.card / ".retcon_replay.json",
            {
                "schema_version": 1,
                "status": "active",
                "active_input_index": 0,
                "records": [{"id": "input-1"}, {"id": "input-2"}],
            },
        )

        result = self.retcon_replay.advance_after_delivery(self.card)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "not_required")
        self.assertTrue(result["deprecated"])
        self.assertFalse((self.card / ".pending_user_turn.json").exists())


if __name__ == "__main__":
    unittest.main()
