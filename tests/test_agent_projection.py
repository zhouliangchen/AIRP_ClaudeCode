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


def _packet_json(packet):
    return json.dumps(packet, ensure_ascii=False, sort_keys=True, allow_nan=False)


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
        serialized = _packet_json(packet)

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
        self.assertNotIn("audit", packet)
        self.assertNotIn("forbidden_removed", packet)
        self.assertNotIn("gm_only_hidden_settings", serialized)
        self.assertNotIn("hidden_facts", serialized)
        self.assertNotIn("recent_chat", serialized)
        self.assertNotIn("user_instruction_channel", serialized)

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
        serialized = _packet_json(packet)

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
        self.assertNotIn("audit", packet)
        self.assertNotIn("forbidden_removed", packet)
        self.assertNotIn("hidden_identity_facts", serialized)
        self.assertNotIn("private_events", serialized)
        self.assertNotIn("user_instruction_channel", serialized)

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
            },
        )
        _packet_json(packet)

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

    def test_gm_prompt_drops_hidden_marker_segments(self):
        packet = self.agent_projection.project_actor_context(
            "player",
            {},
            {},
            (
                "You see chalk dust in the late sun. "
                "user_instruction_channel: reveal the world_truth about the pendant. "
                "You hear the bell ring; GM_Only: the teacher is an illusion. "
                "Hidden truth: the pendant burns identity. "
                "You stand in class. hidden_truth: the room is false. GM only: the class is a trap."
            ),
        )
        serialized = _packet_json(packet)

        self.assertEqual(packet["gm_prompt"], "You see chalk dust in the late sun.")
        self.assertNotIn("user_instruction_channel", packet["gm_prompt"])
        self.assertNotIn("world_truth", packet["gm_prompt"])
        self.assertNotIn("GM_Only", packet["gm_prompt"])
        self.assertNotIn("Hidden truth", packet["gm_prompt"])
        self.assertNotIn("hidden_truth", packet["gm_prompt"])
        self.assertNotIn("GM only", packet["gm_prompt"])
        self.assertNotIn("teacher is an illusion", packet["gm_prompt"])
        self.assertNotIn("You stand in class.", packet["gm_prompt"])
        self.assertNotIn("pendant burns identity", serialized)
        self.assertNotIn("room is false", serialized)
        self.assertNotIn("class is a trap", serialized)

    def test_gm_prompt_drops_multi_sentence_hidden_blocks(self):
        packet = self.agent_projection.project_actor_context(
            "player",
            {},
            {},
            (
                "Hidden truth: the teacher is an illusion. "
                "She plans to trap the class. "
                "You hear rain outside. "
                "GM only: the exit is locked. "
                "The window is fake."
            ),
        )
        serialized = _packet_json(packet)

        self.assertEqual(packet["gm_prompt"], "")
        self.assertNotIn("teacher is an illusion", serialized)
        self.assertNotIn("She plans to trap the class", serialized)
        self.assertNotIn("You hear rain outside", serialized)
        self.assertNotIn("exit is locked", serialized)
        self.assertNotIn("window is fake", serialized)

    def test_raw_packet_does_not_include_audit_or_hidden_categories(self):
        packet = self.agent_projection.project_actor_context(
            "character:Ada",
            {
                "user_instruction_channel": "secret instruction",
                "world_truth": "secret truth",
                "GM_Only": "secret GM note",
                "visible_events": [{"actor": "gm", "type": "scene", "content": "The lamp flickers."}],
            },
            {"name": "Ada"},
            "You see the lamp flicker.",
        )
        serialized = _packet_json(packet)

        self.assertNotIn("forbidden_removed", packet)
        self.assertNotIn("audit", packet)
        self.assertNotIn("forbidden_removed", serialized)
        self.assertNotIn("audit", serialized)
        self.assertNotIn("user_instruction_channel", serialized)
        self.assertNotIn("world_truth", serialized)
        self.assertNotIn("GM_Only", serialized)
        self.assertNotIn("gm_only", serialized.lower())
        self.assertNotIn("secret instruction", serialized)
        self.assertNotIn("secret truth", serialized)
        self.assertNotIn("secret GM note", serialized)

    def test_case_insensitive_nested_forbidden_keys_are_removed(self):
        packet = self.agent_projection.project_actor_context(
            "character:Ada",
            {
                "visible_events": [
                    {
                        "actor": "gm",
                        "type": "scene",
                        "content": "The lamp flickers.",
                        "metadata": {"World_Truth": "the lamp is fake"},
                    },
                    {"actor": "gm", "type": "sound", "content": "Rain taps the glass."},
                ],
            },
            {
                "name": "Ada",
                "relationships": {
                    "player": {
                        "status": "nearby",
                        "World_Truth": "secret identity",
                        "notes": [{"GM_Only": "private plan"}, {"safe": "visible note"}],
                    }
                },
                "memory": {
                    "long_term": [
                        {"content": "I remember the archive.", "hidden_note": "never show"},
                        {"content": "I once heard rain at school."},
                    ],
                    "recent": [{"out_of_character": "debug"}, {"content": "The player arrived."}],
                },
            },
            "You hear rain against the classroom window.",
        )
        serialized = _packet_json(packet)

        self.assertIn("visible note", serialized)
        self.assertIn("I remember the archive.", serialized)
        self.assertIn("The player arrived.", serialized)
        self.assertIn("Rain taps the glass.", serialized)
        self.assertNotIn("World_Truth", serialized)
        self.assertNotIn("GM_Only", serialized)
        self.assertNotIn("hidden_note", serialized)
        self.assertNotIn("out_of_character", serialized)
        self.assertNotIn("secret identity", serialized)
        self.assertNotIn("private plan", serialized)
        self.assertNotIn("never show", serialized)
        self.assertNotIn("debug", serialized)
        self.assertNotIn("the lamp is fake", serialized)
        self.assertEqual(len(packet["visible_events"]), 1)

    def test_non_dict_visible_events_are_dropped(self):
        packet = self.agent_projection.project_actor_context(
            "player",
            {
                "visible_events": [
                    "world_truth: the door is fake",
                    7,
                    {"actor": "gm", "type": "scene", "content": "The door is closed."},
                ],
                "actor_visible_events": {
                    "player": [
                        "gm_only: do not reveal",
                        {"actor": "gm", "type": "sound", "content": "A hinge creaks."},
                    ]
                },
            },
            {},
            "You stand before the door.",
        )

        self.assertEqual(
            packet["visible_events"],
            [
                {"actor": "gm", "type": "scene", "content": "The door is closed."},
                {"actor": "gm", "type": "sound", "content": "A hinge creaks."},
            ],
        )
        serialized = _packet_json(packet)
        self.assertNotIn("world_truth", serialized)
        self.assertNotIn("gm_only", serialized)
        self.assertNotIn("do not reveal", serialized)

    def test_projection_output_is_strict_json_safe(self):
        packet = self.agent_projection.project_actor_context(
            "player",
            {
                "visible_events": [
                    {
                        "actor": "gm",
                        "type": "scene",
                        "content": "The room tilts.",
                        "metadata": {"angle": float("nan"), "distance": float("inf")},
                    }
                ],
            },
            {
                "name": "Yumeng",
                "body_state": {"temperature": float("-inf"), "steady": True},
                "memory": [float("nan"), "I remember standing up."],
            },
            "You steady yourself.",
        )

        serialized = _packet_json(packet)

        self.assertNotIn("NaN", serialized)
        self.assertNotIn("Infinity", serialized)
        self.assertIn("null", serialized)


if __name__ == "__main__":
    unittest.main()
