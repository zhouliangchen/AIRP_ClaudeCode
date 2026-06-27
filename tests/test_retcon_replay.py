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
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class RetconReplayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.card.mkdir()
        self.agent_snapshots = _load_module("agent_snapshots")
        self.retcon_replay = _load_module("retcon_replay")

    def tearDown(self):
        self.tmp.cleanup()

    def _append_player_input(self, input_id, role_text, instruction_text=""):
        raw_text = role_text
        if instruction_text:
            raw_text += "\n\n[USER_INSTRUCTION]\n" + instruction_text
        payload = {
            "id": input_id,
            "created_at": "2026-06-27T00:00:00Z",
            "source": "player",
            "raw_text": raw_text,
            "display_text": role_text,
            "input_schema": "dual_channel_v1",
            "role_text": role_text,
            "user_instruction_text": instruction_text,
        }
        with (self.card / ".player_inputs.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def test_prepare_replay_restores_rollback_snapshot_and_sets_first_pending_input(self):
        first = self._append_player_input("input-1", "I wake in class.")
        second = self._append_player_input("input-2", "That class scene was a dream.")
        _write_json(self.card / "chat_log.json", [])
        created = self.agent_snapshots.create_snapshot(
            self.card,
            "round-000001",
            reason="before_round_prepare",
        )

        _write_json(self.card / "chat_log.json", [{"user": first["display_text"], "ai": "old class scene"}])
        run_dir = self.card / ".agent_runs" / "round-000002"
        run_dir.mkdir(parents=True)
        (self.card / ".agent_runs" / "current").write_text(str(run_dir.resolve()), encoding="utf-8")
        _write_json(
            run_dir / "input.raw.json",
            {
                "explicit_payload": {"id": second["id"]},
                "raw_text": second["raw_text"],
            },
        )
        _write_json(
            run_dir / "input.json",
            {
                "recent_chat": [{"user": first["display_text"], "ai": "old class scene"}],
            },
        )
        _write_json(
            run_dir / "input_analysis.output.json",
            {
                "narrative_directives": {"rewrite_previous_output": True},
                "semantic_units": [{"type": "edit_request"}],
            },
        )

        result = self.retcon_replay.prepare_replay_from_current_run(self.card, run_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "retcon_replay_prepared")
        self.assertEqual(result["rollback_turn_index"], 0)
        self.assertEqual(result["snapshot"]["snapshot_id"], created["snapshot_id"])
        self.assertEqual(json.loads((self.card / "chat_log.json").read_text(encoding="utf-8")), [])
        pending = json.loads((self.card / ".pending_user_turn.json").read_text(encoding="utf-8"))
        self.assertEqual(pending["id"], "input-1")
        self.assertEqual(pending["role_text"], "I wake in class.")
        state = json.loads((self.card / ".retcon_replay.json").read_text(encoding="utf-8"))
        self.assertEqual(state["input_ids"], ["input-1", "input-2"])
        self.assertEqual(state["active_input_index"], 0)
        self.assertEqual(state["records"][1]["role_text"], "That class scene was a dream.")

    def test_active_constraint_and_advance_queue_next_input(self):
        first = self._append_player_input("input-1", "I wake in class.")
        second = self._append_player_input("input-2", "That class scene was a dream.")
        _write_json(
            self.card / ".retcon_replay.json",
            {
                "schema_version": 1,
                "status": "active",
                "rollback_turn_index": 0,
                "active_input_index": 0,
                "input_ids": ["input-1", "input-2"],
                "records": [first, second],
            },
        )
        pending = {
            "id": "input-1",
            "role_text": "I wake in class.",
        }

        constraint = self.retcon_replay.active_constraint_for_pending(self.card, pending)

        self.assertEqual(constraint["current_input_id"], "input-1")
        self.assertEqual(constraint["next_input"]["id"], "input-2")
        self.assertIn("connect to that next player input", constraint["instruction"])

        advanced = self.retcon_replay.advance_after_delivery(self.card)

        self.assertEqual(advanced["action"], "queued_next_input")
        self.assertEqual(advanced["input_id"], "input-2")
        queued = json.loads((self.card / ".pending_user_turn.json").read_text(encoding="utf-8"))
        self.assertEqual(queued["id"], "input-2")
        self.assertEqual(queued["role_text"], "That class scene was a dream.")

    def test_prepare_replay_does_not_nest_while_replay_is_active(self):
        first = self._append_player_input("input-1", "I wake in class.")
        second = self._append_player_input("input-2", "That class scene was a dream.")
        _write_json(
            self.card / ".retcon_replay.json",
            {
                "schema_version": 1,
                "status": "active",
                "active_input_index": 1,
                "input_ids": ["input-1", "input-2"],
                "records": [first, second],
            },
        )
        run_dir = self.card / ".agent_runs" / "round-000002"
        run_dir.mkdir(parents=True)
        _write_json(
            run_dir / "input_analysis.output.json",
            {
                "narrative_directives": {"rewrite_previous_output": True},
                "semantic_units": [{"type": "edit_request"}],
            },
        )

        result = self.retcon_replay.prepare_replay_from_current_run(self.card, run_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "replay_already_active")
        state = json.loads((self.card / ".retcon_replay.json").read_text(encoding="utf-8"))
        self.assertEqual(state["input_ids"], ["input-1", "input-2"])
        self.assertEqual(state["active_input_index"], 1)


if __name__ == "__main__":
    unittest.main()
