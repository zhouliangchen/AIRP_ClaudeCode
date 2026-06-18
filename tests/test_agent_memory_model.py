import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_memory_model():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_memory_model", ROOT / "skills" / "agent_memory_model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_update():
    return {
        "agent_id": "character:SuLi",
        "character_name": "SuLi",
        "source": "self",
        "visibility": "actor",
        "long_term": {
            "self_understanding": ["I keep my worries private around classmates."],
            "stable_beliefs": ["The archive is safer when entered slowly."],
            "relationship_models": ["I trust Ada to notice small sounds."],
        },
        "key_memories": [
            {
                "content": "I watched Ada raise her lamp before the archive door opened.",
                "importance": "high",
                "details": ["The air smelled like old paper.", "The player hesitated at the threshold."],
            }
        ],
        "short_term": [
            {
                "content": "I am still standing near the archive threshold.",
                "expires_after": "scene_end",
            }
        ],
        "goals": {
            "active": ["Keep Ada within sight."],
            "paused": ["Ask about the sealed index later."],
            "resolved": ["Reach the archive door."],
        },
    }


class AgentMemoryModelTest(unittest.TestCase):
    def setUp(self):
        self.model = _load_agent_memory_model()

    def test_validate_memory_update_accepts_structured_actor_payload(self):
        update = self.model.validate_memory_update(_valid_update())

        self.assertEqual(update["agent_id"], "character:SuLi")
        self.assertEqual(update["source"], "self")
        self.assertEqual(update["visibility"], "actor")
        self.assertEqual(
            update["long_term"]["self_understanding"],
            ["I keep my worries private around classmates."],
        )
        self.assertEqual(update["key_memories"][0]["importance"], "high")
        self.assertEqual(update["short_term"][0]["expires_after"], "scene_end")
        self.assertEqual(update["goals"]["active"], ["Keep Ada within sight."])

        long_term = self.model.render_long_term_markdown(update)
        key_memories = self.model.render_key_memories_markdown(update)
        short_term = self.model.render_short_term_markdown(update)
        goals = self.model.render_goals_json(update)

        self.assertIn("Self Understanding", long_term)
        self.assertIn("I trust Ada", long_term)
        self.assertIn("[high] I watched Ada raise her lamp", key_memories)
        self.assertIn("The player hesitated", key_memories)
        self.assertIn("scene_end", short_term)
        self.assertEqual(goals["goals"]["active"], ["Keep Ada within sight."])
        json.dumps(goals, ensure_ascii=False)

    def test_validate_memory_update_rejects_forbidden_actor_profile_edit_keys(self):
        for key in (
            "profile",
            "background",
            "personality",
            "body_facts",
            "authoritative_setting",
            "character_sheet",
        ):
            with self.subTest(key=key):
                payload = _valid_update()
                payload[key] = "actors may not edit this"

                with self.assertRaisesRegex(self.model.AgentMemoryModelError, key):
                    self.model.validate_memory_update(payload)

    def test_validate_memory_update_rejects_nested_profile_edit_keys(self):
        payload = _valid_update()
        payload["key_memories"][0]["details"] = [{"character_sheet": "new body facts"}]

        with self.assertRaisesRegex(self.model.AgentMemoryModelError, "character_sheet"):
            self.model.validate_memory_update(payload)

    def test_validate_memory_update_rejects_hidden_markers(self):
        for marker, expected in (
            ("gm_only", "gm_only"),
            ("gmOnly", "gm_only"),
            ("world-truth", "world_truth"),
            ("hidden note", "hidden_note"),
            ("out-of-character", "out_of_character"),
        ):
            with self.subTest(marker=marker):
                payload = _valid_update()
                payload["long_term"]["stable_beliefs"] = [f"I should not persist {marker} knowledge."]

                with self.assertRaisesRegex(self.model.AgentMemoryModelError, expected):
                    self.model.validate_memory_update(payload)

    def test_validate_memory_update_rejects_non_self_or_non_actor_payloads(self):
        for field, value in (("source", "gm"), ("visibility", "gm_only")):
            with self.subTest(field=field):
                payload = _valid_update()
                payload[field] = value

                with self.assertRaises(self.model.AgentMemoryModelError):
                    self.model.validate_memory_update(payload)


if __name__ == "__main__":
    unittest.main()
