import copy
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentVisibilityGuardTest(unittest.TestCase):
    def setUp(self):
        self.guard = load_module("agent_visibility_guard")

    def test_redacts_hidden_phrase_from_actor_call_prompt_reason_and_metadata(self):
        input_payload = {
            "routed_input": {
                "user_instruction_channel": "Hidden truth: the pendant burns identity.",
            },
            "gm_only_hidden_settings": [
                {"fact": "the pendant burns identity"},
            ],
        }
        gm_output = {
            "agent": "gm",
            "scene_beats": [],
            "events": [],
            "actor_calls": [{
                "call_id": "call-player-1",
                "actor_id": "player",
                "prompt": "You feel that the pendant burns identity.",
                "reason": "The pendant burns identity, so test the player.",
                "metadata": {"note": "pendant burns identity"},
            }],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }
        sanitized = self.guard.sanitize_gm_output(gm_output, input_payload)
        text = repr(sanitized).lower()
        self.assertNotIn("burns identity", text)
        self.assertIn("[redacted]", text)

    def test_sanitize_gm_output_does_not_mutate_originals(self):
        input_payload = {
            "hidden_facts": ["The mirror names traitors."],
        }
        gm_output = {
            "agent": "gm",
            "scene_beats": [{"content": "The mirror names traitors.", "metadata": {"note": "visible"}}],
            "events": [],
            "actor_calls": [],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }
        original_input = copy.deepcopy(input_payload)
        original_gm = copy.deepcopy(gm_output)

        sanitized = self.guard.sanitize_gm_output(gm_output, input_payload)

        self.assertEqual(input_payload, original_input)
        self.assertEqual(gm_output, original_gm)
        self.assertNotEqual(sanitized, gm_output)

    def test_sanitizes_scene_beat_event_content_and_metadata(self):
        input_payload = {
            "world_truth": "The clock remembers blood.",
            "private_recent_chat": [{"content": "The hallway eats names."}],
        }
        gm_output = {
            "agent": "gm",
            "scene_beats": [{
                "content": "The clock remembers blood.",
                "metadata": {"hint": "The hallway eats names."},
            }],
            "events": [{
                "type": "npc_action",
                "content": "The hallway eats names.",
                "metadata": {"truth": "The clock remembers blood."},
            }],
            "actor_calls": [],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }

        sanitized = self.guard.sanitize_gm_output(gm_output, input_payload)
        text = repr(sanitized).lower()

        self.assertNotIn("clock remembers blood", text)
        self.assertNotIn("hallway eats names", text)
        self.assertEqual(text.count("[redacted]"), 4)

    def test_redacts_cjk_hidden_phrase_with_punctuation_and_spaces(self):
        phrases = self.guard.hidden_phrases({
            "hidden_facts": ["门后是梦境"],
        })

        redacted = self.guard.redact_text("你知道门 后，是 梦境，但只能说门后有光。", phrases)

        self.assertNotIn("门 后，是 梦境", redacted)
        self.assertIn("[redacted]", redacted)
        self.assertIn("门后有光", redacted)


if __name__ == "__main__":
    unittest.main()
