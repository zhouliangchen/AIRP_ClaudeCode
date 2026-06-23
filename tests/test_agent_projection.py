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


def _basis(mode, summary="visible proof", **extra):
    payload = {"mode": mode, "summary": summary}
    payload.update(extra)
    return payload


def _public_event(content, **extra):
    event = {
        "actor": "gm",
        "type": "scene",
        "content": content,
        "visible_to": ["all"],
        "visibility_basis": _basis("public", visible_to=["all"]),
    }
    event.update(extra)
    return event


class AgentProjectionTest(unittest.TestCase):
    def setUp(self):
        self.agent_projection = _load_agent_projection()

    def test_projection_drops_unproven_world_visible_event(self):
        packet = self.agent_projection.project_actor_context(
            "character:Ada",
            {"visible_events": [{"actor": "gm", "type": "scene", "content": "The archive opens."}]},
            {"name": "Ada", "location": "courtyard"},
            "You wait outside.",
            {"mode": "direct", "summary": "Ada is addressed by GM.", "target_actor": "character:Ada"},
        )

        self.assertEqual(packet["visible_events"], [])

    def test_projection_drops_private_type_even_with_public_basis(self):
        packet = self.agent_projection.project_actor_context(
            "character:Ada",
            {
                "visible_events": [
                    {
                        "actor": "gm",
                        "type": "private",
                        "content": "The archive hides a moon base.",
                        "visible_to": ["all"],
                        "visibility_basis": _basis("public", visible_to=["all"]),
                    }
                ]
            },
            {"name": "Ada", "location": "courtyard"},
            "You wait outside.",
            {"mode": "direct", "summary": "Ada is addressed by GM.", "target_actor": "character:Ada"},
        )

        self.assertEqual(packet["visible_events"], [])

    def test_projection_keeps_location_proven_event_for_same_location_only(self):
        world = {
            "visible_events": [
                {
                    "actor": "gm",
                    "type": "scene",
                    "content": "A red lamp flickers in the archive.",
                    "location": "archive",
                    "sensory_channels": ["visual"],
                    "visibility_basis": _basis(
                        "location",
                        location="archive",
                        sensory_channels=["visual"],
                    ),
                }
            ]
        }

        ada = self.agent_projection.project_actor_context(
            "character:Ada",
            world,
            {"name": "Ada", "location": "archive"},
            "You see the lamp.",
            {"mode": "direct", "summary": "Ada is addressed by GM.", "target_actor": "character:Ada"},
        )
        eve = self.agent_projection.project_actor_context(
            "character:Eve",
            world,
            {"name": "Eve", "location": "courtyard"},
            "You stand elsewhere.",
            {"mode": "direct", "summary": "Eve is addressed by GM.", "target_actor": "character:Eve"},
        )

        self.assertEqual(len(ada["visible_events"]), 1)
        self.assertEqual(eve["visible_events"], [])

    def test_projection_includes_actor_call_visibility_basis(self):
        basis = {
            "mode": "direct",
            "summary": "Ada is addressed because she sees the player's hand close.",
            "location": "classroom",
            "visible_to": ["character:Ada"],
            "target_actor": "character:Ada",
        }

        packet = self.agent_projection.project_actor_context(
            "character:Ada",
            {},
            {"name": "Ada"},
            "You see the player's hand close.",
            basis,
        )

        self.assertEqual(packet["gm_visibility_basis"], basis)

    def test_player_projection_removes_hidden_context_and_keeps_prompt_anchor(self):
        world = {
            "role_channel": "I hide the pendant under my sleeve.",
            "user_instruction_channel": "Hidden truth: the pendant burns identity.",
            "gm_only_hidden_settings": [{"fact": "The pendant burns identity."}],
            "hidden_facts": {"pendant": "It is a soul furnace."},
            "recent_chat": [{"summary": "GM-only foreshadowing about the pendant."}],
            "visible_events": [_public_event("The classroom is noisy.")],
            "sensory_context": {"sight": "A teacher writes on the board."},
        }
        actor = {
            "name": "Yumeng",
            "identity": "transfer student",
            "body_state": {"hands": "tense"},
            "relationships": {"SuLi": "classmate"},
            "memory": {
                "long_term": ["I woke up on the road."],
                "key_memories": ["I first noticed the pendant near the school road."],
                "short_term": ["I reached the classroom."],
                "goals": ["Reach school."],
            },
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
        self.assertEqual(sorted(packet["memory"]), ["goals", "key_memories", "long_term", "short_term"])
        self.assertIn("You stand near your desk", packet["gm_prompt"])
        self.assertEqual(packet["role_channel_anchor"], "I hide the pendant under my sleeve.")
        self.assertIn("I woke up on the road.", serialized)
        self.assertIn("I first noticed the pendant", serialized)
        self.assertIn("I reached the classroom.", serialized)
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

    def test_projection_scrubs_shared_hidden_markers_from_actor_packet_fields(self):
        actor = {
            "name": "Ada",
            "body_state": {"hands": "steady", "gm_only_text": "pulse reveals the lock"},
            "relationships": {
                "player": {
                    "status": "trusted",
                    "private_notes": "knows the forbidden pact",
                }
            },
            "misconceptions": [
                "The hallway is safe.",
                "hidden_fact: the floor will collapse",
            ],
            "sensory_context": {
                "sight": "chalk dust in the light",
                "hidden_text": "secret sigil under the desk",
                "private_notes": "GM sees the concealed scar",
            },
        }
        world = {
            "visible_events": [
                _public_event("The bell rings."),
                _public_event(
                    "A chalk line glows.",
                    metadata={"private_notes": "route to the vault"},
                ),
            ]
        }

        actor_packet = self.agent_projection.project_actor_context(
            "character:Ada",
            world,
            actor,
            "You hear the bell.",
        )
        actor_serialized = _packet_json(actor_packet)

        self.assertIn("steady", actor_serialized)
        self.assertIn("trusted", actor_serialized)
        self.assertIn("chalk dust in the light", actor_serialized)
        self.assertIn("The bell rings.", actor_serialized)
        self.assertNotIn("A chalk line glows.", actor_serialized)
        for hidden in (
            "misconceptions",
            "private_notes",
            "hidden_text",
            "gm_only_text",
            "hidden_fact",
            "The hallway is safe.",
            "pulse reveals the lock",
            "forbidden pact",
            "floor will collapse",
            "secret sigil",
            "concealed scar",
            "route to the vault",
        ):
            self.assertNotIn(hidden, actor_serialized)

        world_sensory_packet = self.agent_projection.project_actor_context(
            "player",
            {
                "sensory_context": {
                    "sound": "students whisper nearby",
                    "hidden_text": "the bell is a trap",
                    "private_notes": "GM-only route marker",
                }
            },
            {},
            "You hear the classroom.",
        )
        world_sensory_serialized = _packet_json(world_sensory_packet)

        self.assertIn("students whisper nearby", world_sensory_serialized)
        for hidden in (
            "hidden_text",
            "private_notes",
            "bell is a trap",
            "GM-only route marker",
        ):
            self.assertNotIn(hidden, world_sensory_serialized)

    def test_projection_scrubs_compact_hidden_markers_from_actor_packet_fields(self):
        actor = {
            "name": "Ada",
            "memory": {
                "long_term": ["I remember the archive.", "worldtruthactor"],
                "key_memories": ["hiddenfactwitness"],
                "short_term": ["The hallway is quiet.", "outofcharacternote"],
                "goals": ["gmonlyroom"],
            },
            "sensory_context": {
                "sound": "students whisper nearby",
                "hiddenfactwitness": "drop compact hidden key",
                "smell": "outofcharacternote",
            },
            "misconceptions": ["The hallway is safe.", "gmonlyroom"],
        }
        world = {
            "role_channel": "I keep walking.",
            "sensory_context": {
                "sight": "chalk dust in the light",
                "note": "worldtruthactor",
            },
            "visible_events": [
                _public_event("The bell rings."),
                _public_event("hiddenfactwitness"),
            ],
        }

        packet = self.agent_projection.project_actor_context(
            "character:Ada",
            world,
            actor,
            "You see chalk dust. gmonlyroom says the door is false. You should not see this.",
        )
        serialized = _packet_json(packet).lower()

        self.assertIn("students whisper nearby", _packet_json(packet))
        self.assertIn("The bell rings.", _packet_json(packet))
        self.assertEqual(packet["gm_prompt"], "You see chalk dust.")
        for hidden in (
            "misconceptions",
            "the hallway is safe.",
            "gmonlyroom",
            "worldtruthactor",
            "hiddenfactwitness",
            "outofcharacternote",
            "drop compact hidden key",
            "door is false",
            "You should not see this.",
        ):
            self.assertNotIn(hidden.lower(), serialized)

    def test_character_projection_keeps_own_memory_goals_and_visible_events_only(self):
        world = {
            "role_channel": "I hide the pendant.",
            "user_instruction_channel": "SuLi is secretly a former magical girl.",
            "private_events": [{"actor": "player", "type": "thought", "content": "I am scared."}],
            "visible_events": [
                _public_event(
                    "He closes his hand around something pink.",
                    actor="player",
                    type="action",
                ),
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
                "key_memories": ["I once sealed a ritual note in my desk."],
                "short_term": ["I saw a flash of pink."],
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
        self.assertIn("ritual note", serialized)
        self.assertIn("Avoid attention", serialized)
        self.assertIn("He closes his hand", serialized)
        self.assertIn("chair beside you", serialized)
        self.assertIn("You are SuLi.", packet["immersive_context"])
        self.assertNotIn("misconceptions", serialized)
        self.assertNotIn("former magical girl", serialized)
        self.assertNotIn("I am scared", serialized)
        self.assertNotIn("betray SuLi", serialized)
        self.assertNotIn("audit", packet)
        self.assertNotIn("forbidden_removed", packet)
        self.assertNotIn("hidden_identity_facts", serialized)
        self.assertNotIn("private_events", serialized)
        self.assertNotIn("user_instruction_channel", serialized)

    def test_projection_ignores_old_actor_memory_aliases_and_bare_memory(self):
        world = {"visible_events": [{"actor": "gm", "type": "scene", "content": "The lamp flickers."}]}
        old_alias_actor = {
            "name": "Ada",
            "memory": {
                "long_term_memory": ["old long-term alias"],
                "recent": ["old recent alias"],
                "recent_memory": ["old recent-memory alias"],
                "short_term_memory": ["old short-term alias"],
                "current_goals": ["old current-goals alias"],
                "memories": ["old memories alias"],
                "key_memory": ["old key-memory alias"],
            },
        }
        bare_memory_actor = {
            "name": "Ada",
            "memory": ["old bare memory list"],
        }

        old_alias_packet = self.agent_projection.project_actor_context(
            "character:Ada",
            world,
            old_alias_actor,
            "You see the lamp flicker.",
        )
        bare_memory_packet = self.agent_projection.project_actor_context(
            "character:Ada",
            world,
            bare_memory_actor,
            "You see the lamp flicker.",
        )
        serialized = _packet_json([old_alias_packet["memory"], bare_memory_packet["memory"]])

        self.assertEqual(
            old_alias_packet["memory"],
            {"long_term": [], "key_memories": [], "short_term": [], "goals": []},
        )
        self.assertEqual(
            bare_memory_packet["memory"],
            {"long_term": [], "key_memories": [], "short_term": [], "goals": []},
        )
        self.assertNotIn("old long-term alias", serialized)
        self.assertNotIn("old recent alias", serialized)
        self.assertNotIn("old recent-memory alias", serialized)
        self.assertNotIn("old short-term alias", serialized)
        self.assertNotIn("old current-goals alias", serialized)
        self.assertNotIn("old memories alias", serialized)
        self.assertNotIn("old key-memory alias", serialized)
        self.assertNotIn("old bare memory list", serialized)

    def test_projection_handles_missing_inputs_with_stable_defaults(self):
        packet = self.agent_projection.project_actor_context("character:Missing", None, None, "")

        self.assertEqual(
            packet,
            {
                "actor_id": "character:Missing",
                "agent": "character",
                "visibility": "first_person_character",
                "gm_prompt": "",
                "gm_visibility_basis": {},
                "address_mode": "second_person_gm_narration",
                "immersive_context": "You are Missing.",
                "self_knowledge": {
                    "name": "",
                    "identity": "",
                    "role": "",
                    "body_state": {},
                    "relationships": {},
                },
                "memory": {
                    "long_term": [],
                    "key_memories": [],
                    "short_term": [],
                    "goals": [],
                },
                "sensory_context": {},
                "visible_events": [],
                "role_channel_anchor": "",
            },
        )
        _packet_json(packet)

    def test_projection_does_not_mutate_input_dictionaries_or_lists(self):
        world = {
            "role_channel": "I look at SuLi.",
            "user_instruction_channel": "Keep the curse hidden.",
            "visible_events": [_public_event("The player looks up.", actor="player")],
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

    def test_gm_prompt_drops_projection_control_segments(self):
        packet = self.agent_projection.project_actor_context(
            "character:Ada",
            {},
            {"name": "Ada", "memory": {"long_term": ["I distrust the old crown."]}},
            (
                "You see the market gate open. "
                "projection_control: retry the actor packet with stricter labels. "
                "You should not see this continuation. "
                "control_note: audit this branch. "
                "You should not see this either."
            ),
        )
        serialized = _packet_json(packet).lower()

        self.assertEqual(packet["gm_prompt"], "You see the market gate open.")
        self.assertIn("I distrust the old crown.", packet["immersive_context"])
        self.assertNotIn("projection_control", serialized)
        self.assertNotIn("projection control", serialized)
        self.assertNotIn("control_note", serialized)
        self.assertNotIn("control note", serialized)
        self.assertNotIn("stricter labels", serialized)
        self.assertNotIn("audit this branch", serialized)
        self.assertNotIn("You should not see this", serialized)

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
                    _public_event("Rain taps the glass.", type="sound"),
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
                    "key_memories": [{"content": "I held the lamp steady."}],
                    "short_term": [{"out_of_character": "debug"}, {"content": "The player arrived."}],
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
                    _public_event("The door is closed."),
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
                _public_event("The door is closed."),
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
                    _public_event(
                        "The room tilts.",
                        metadata={"angle": float("nan"), "distance": float("inf")},
                    )
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
        self.assertEqual(packet["visible_events"][0]["metadata"], {"angle": None, "distance": None})


if __name__ == "__main__":
    unittest.main()
