import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_schemas():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_schemas", ROOT / "skills" / "agent_schemas.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentSchemaTest(unittest.TestCase):
    def setUp(self):
        self.agent_schemas = _load_agent_schemas()

    def test_valid_gm_output_is_normalized(self):
        payload = {
            "agent": "gm",
            "narration": "The archive door opens from the inside.",
            "npc_events": [{"actor": "clerk", "event": "raises a lamp"}],
            "world_state_delta": [{"scope": "location", "fact": "the archive is lit"}],
            "handoff": {"scene_goal": "let the player choose whether to enter"},
        }

        normalized = self.agent_schemas.validate_gm_output(payload)

        self.assertEqual(normalized["agent"], "gm")
        self.assertEqual(normalized["narration"], payload["narration"])
        self.assertEqual(normalized["npc_events"], payload["npc_events"])
        self.assertEqual(normalized["world_state_delta"], payload["world_state_delta"])
        self.assertEqual(normalized["handoff"], payload["handoff"])

    def test_valid_actor_output_is_first_person_only(self):
        payload = {
            "agent": "character",
            "agent_id": "character:ada",
            "character_name": "Ada",
            "action": "I lift the lamp and wait by the threshold.",
            "dialogue": [{"target": "player", "text": "Stay close."}],
            "perception": ["I smell dust beyond the door."],
            "memory_delta": [{"text": "I saw the archive door open.", "source": "perceived"}],
        }

        normalized = self.agent_schemas.validate_actor_output(payload)

        self.assertEqual(normalized["agent"], "character")
        self.assertEqual(normalized["agent_id"], "character:ada")
        self.assertEqual(normalized["dialogue"][0]["text"], "Stay close.")
        self.assertEqual(normalized["memory_delta"][0]["source"], "perceived")

    def test_actor_output_rejects_omniscient_or_control_fields(self):
        base = {
            "agent": "player",
            "agent_id": "player",
            "action": "I step closer to the door.",
            "dialogue": [],
            "perception": [],
            "memory_delta": [],
        }

        for forbidden_key in ("gm_notes", "player_name", "world_truth"):
            with self.subTest(forbidden_key=forbidden_key):
                payload = dict(base)
                payload[forbidden_key] = "hidden fact"
                with self.assertRaisesRegex(self.agent_schemas.ValidationError, forbidden_key):
                    self.agent_schemas.validate_actor_output(payload)

    def test_valid_story_output_preserves_character_dialogue_metadata(self):
        payload = {
            "content": "Ada lifted the lamp. \"Stay close,\" she said.",
            "character_dialogues": [
                {"character": "Ada", "text": "Stay close.", "source_agent": "character:ada"}
            ],
            "metadata": {"round_id": "round-000001"},
        }

        normalized = self.agent_schemas.validate_story_output(payload)

        self.assertEqual(normalized["content"], payload["content"])
        self.assertEqual(normalized["character_dialogues"][0]["source_agent"], "character:ada")

    def test_valid_critic_report_supports_all_decisions(self):
        for decision in ("pass", "revise", "block"):
            with self.subTest(decision=decision):
                payload = {
                    "decision": decision,
                    "hard_failures": [],
                    "soft_issues": [],
                    "repair_instruction": "",
                    "system_iteration_suggestion": "Tighten critic retry prompts.",
                }

                normalized = self.agent_schemas.validate_critic_report(payload)

                self.assertEqual(normalized["decision"], decision)
                self.assertEqual(normalized["hard_failures"], [])
                self.assertEqual(normalized["system_iteration_suggestion"], "Tighten critic retry prompts.")

    def test_load_json_checked_applies_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "critic.report.json"
            path.write_text(json.dumps({"decision": "pass"}), encoding="utf-8")

            normalized = self.agent_schemas.load_json_checked(
                path,
                self.agent_schemas.validate_critic_report,
            )

        self.assertEqual(normalized["decision"], "pass")


if __name__ == "__main__":
    unittest.main()
