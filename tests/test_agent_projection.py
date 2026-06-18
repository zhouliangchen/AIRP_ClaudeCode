import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_projection():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_projection", ROOT / "skills" / "agent_projection.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentProjectionTest(unittest.TestCase):
    def setUp(self):
        self.agent_projection = _load_agent_projection()

    def test_player_projection_removes_hidden_context_and_keeps_prompt_anchor(self):
        world = {
            "role_channel": "I hide the pendant under my sleeve.",
            "user_instruction_channel": "Hidden truth: the pendant burns identity.",
            "gm_only_hidden_settings": [{"fact": "The pendant burns identity."}],
            "hidden_facts": {"pendant": "It is a soul furnace."},
            "recent_chat": [{"summary": "GM-only foreshadowing about the pendant."}],
            "visible_events": [{"actor": "gm", "type": "scene", "content": "The classroom is noisy."}],
            "sensory_context": {"sight": "A teacher writes on the board."},
        }
        actor = {
            "name": "Yumeng",
            "identity": "transfer student",
            "body_state": {"hands": "tense"},
            "relationships": {"SuLi": "classmate"},
            "memory": ["I woke up on the road."],
            "recent_memory": ["I reached the classroom."],
            "goals": ["Reach school."],
        }

        packet = self.agent_projection.project_actor_context(
            "player",
            world,
            actor,
            "You stand near your desk with the pendant in your palm.",
        )
        serialized = json.dumps(packet, ensure_ascii=False, sort_keys=True)

        self.assertEqual(packet["actor_id"], "player")
        self.assertEqual(packet["agent"], "player")
        self.assertEqual(packet["visibility"], "first_person_player")
        self.assertIn("You stand near your desk", packet["gm_prompt"])
        self.assertEqual(packet["role_channel_anchor"], "I hide the pendant under my sleeve.")
        self.assertIn("I woke up on the road.", serialized)
        self.assertIn("The classroom is noisy.", serialized)
        self.assertNotIn("user_instruction_channel", packet)
        self.assertNotIn("burns identity", serialized)
        self.assertNotIn("soul furnace", serialized)
        self.assertNotIn("GM-only foreshadowing", serialized)
        self.assertEqual(
            packet["forbidden_removed"],
            ["gm_only_hidden_settings", "hidden_facts", "recent_chat", "user_instruction_channel"],
        )

    def test_character_projection_keeps_own_memory_goals_and_visible_events_only(self):
        world = {
            "role_channel": "I hide the pendant.",
            "user_instruction_channel": "SuLi is secretly a former magical girl.",
            "private_events": [{"actor": "player", "type": "thought", "content": "I am scared."}],
            "visible_events": [
                {"actor": "player", "type": "action", "content": "He closes his hand around something pink."},
                {"actor": "character:Other", "type": "internal", "content": "Other plans to betray SuLi."},
            ],
            "actor_visible_events": {
                "character:SuLi": [
                    {"actor": "gm", "type": "sound", "content": "The chair beside you scrapes softly."}
                ],
            },
            "hidden_identity_facts": {"character:SuLi": "former magical girl"},
        }
        actor = {
            "name": "SuLi",
            "role": "quiet classmate",
            "memory": {
                "long_term": ["I know old rituals."],
                "recent": ["I saw a flash of pink."],
                "goals": ["Avoid attention."],
            },
            "hidden_identity": "former magical girl",
            "misconceptions": ["The pendant is only a trinket."],
        }

        packet = self.agent_projection.project_actor_context(
            "character:SuLi",
            world,
            actor,
            "You notice his hand close around something pink.",
        )
        serialized = json.dumps(packet, ensure_ascii=False, sort_keys=True)

        self.assertEqual(packet["agent"], "character")
        self.assertEqual(packet["visibility"], "first_person_character")
        self.assertEqual(packet["role_channel_anchor"], "")
        self.assertIn("old rituals", serialized)
        self.assertIn("Avoid attention", serialized)
        self.assertIn("He closes his hand", serialized)
        self.assertIn("chair beside you", serialized)
        self.assertIn("only a trinket", serialized)
        self.assertNotIn("former magical girl", serialized)
        self.assertNotIn("I am scared", serialized)
        self.assertNotIn("betray SuLi", serialized)
        self.assertEqual(packet["forbidden_removed"], ["hidden_identity_facts", "private_events", "user_instruction_channel"])

    def test_projection_handles_missing_inputs_with_stable_defaults(self):
        packet = self.agent_projection.project_actor_context("character:Missing", None, None, "")

        self.assertEqual(
            packet,
            {
                "actor_id": "character:Missing",
                "agent": "character",
                "visibility": "first_person_character",
                "gm_prompt": "",
                "address_mode": "second_person_gm_narration",
                "self_knowledge": {
                    "name": "",
                    "identity": "",
                    "role": "",
                    "body_state": {},
                    "relationships": {},
                },
                "memory": {
                    "long_term": [],
                    "recent": [],
                    "goals": [],
                },
                "sensory_context": {},
                "visible_events": [],
                "misconceptions": [],
                "role_channel_anchor": "",
                "forbidden_removed": [],
            },
        )
        json.dumps(packet, ensure_ascii=False, sort_keys=True)

    def test_projection_does_not_mutate_input_dictionaries_or_lists(self):
        world = {
            "role_channel": "I look at SuLi.",
            "user_instruction_channel": "Keep the curse hidden.",
            "visible_events": [{"actor": "player", "content": "The player looks up."}],
            "actor_visible_events": {
                "player": [{"actor": "gm", "content": "A bell rings."}],
            },
        }
        actor = {
            "name": "Yumeng",
            "memory": ["I remember the road."],
            "relationships": {"SuLi": {"status": "unknown"}},
            "misconceptions": ["The day is normal."],
        }
        original_world = copy.deepcopy(world)
        original_actor = copy.deepcopy(actor)

        packet = self.agent_projection.project_actor_context(
            "player",
            world,
            actor,
            "You hear the bell ring.",
        )
        packet["visible_events"][0]["content"] = "mutated packet"
        packet["memory"]["long_term"].append("mutated packet")
        packet["self_knowledge"]["relationships"]["SuLi"]["status"] = "mutated packet"

        self.assertEqual(world, original_world)
        self.assertEqual(actor, original_actor)


if __name__ == "__main__":
    unittest.main()
