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


def visibility_basis(actor_id="character:SuLi"):
    return {
        "mode": "direct",
        "summary": f"{actor_id} is present and can perceive the prompt.",
        "location": "classroom",
        "visible_to": [actor_id],
        "sensory_channels": ["visual"],
        "target_actor": actor_id,
    }


def minimal_subgm_output(actor_call=None):
    return {
        "agent": "subGM",
        "thread_id": "side-thread-1",
        "status": "running",
        "scene_beats": [],
        "events": [],
        "actor_calls": [
            actor_call
            or {
                "call_id": "call-suli-1",
                "actor_id": "character:SuLi",
                "prompt": "React to the classroom clue.",
                "reason": "SuLi is present in the side scene.",
                "visibility_basis": visibility_basis(),
            }
        ],
        "messages_to_gm": [],
        "world_state_delta": [],
        "character_usage": ["character:SuLi"],
        "promotion_requests": [],
        "boundary_requests": [],
        "notes_for_story": ["SuLi noticed the classroom clue."],
        "next_resume_point": "",
    }


class AgentSchemaTest(unittest.TestCase):
    def setUp(self):
        self.agent_schemas = _load_agent_schemas()

    def test_validate_gm_output_uses_interactive_event_contract(self):
        payload = {
            "agent": "gm",
            "scene_beats": [
                {
                    "content": "The classroom clock clicks once.",
                    "scene_id": "classroom-1",
                    "location": "classroom",
                    "time_window": "current",
                    "visible_to": ["all"],
                    "sensory_channels": ["auditory"],
                    "source_actor": "gm",
                    "visibility_basis": {
                        "mode": "public",
                        "summary": "Everyone in the classroom can hear the clock.",
                        "location": "classroom",
                        "visible_to": ["all"],
                        "sensory_channels": ["auditory"],
                    },
                }
            ],
            "events": [
                {
                    "type": "npc_action",
                    "target": "",
                    "content": "A student shuts the door.",
                    "scene_id": "classroom-1",
                    "location": "classroom",
                    "time_window": "current",
                    "visible_to": ["character:SuLi"],
                    "sensory_channels": ["visual"],
                    "source_actor": "character:ClassRep",
                    "target_actor": "character:SuLi",
                    "visibility_basis": visibility_basis(),
                }
            ],
            "actor_calls": [
                {
                    "call_id": "call-1",
                    "actor_id": "character:SuLi",
                    "prompt": "You notice the pendant in his hand.",
                    "reason": "SuLi can see the pendant.",
                    "scene_id": "classroom-1",
                    "location": "classroom",
                    "time_window": "current",
                    "visible_to": ["character:SuLi"],
                    "sensory_channels": ["visual"],
                    "source_actor": "gm",
                    "target_actor": "character:SuLi",
                    "visibility_basis": visibility_basis(),
                }
            ],
            "parallel_groups": [["character:SuLi", "character:ClassRep"]],
            "world_state_delta": [{"scope": "classroom", "fact": "The door is shut."}],
            "character_promotions": [
                {
                    "name": "ClassRep",
                    "source_agent": "gm",
                    "reason": "ClassRep now needs independent agency.",
                    "profile_seed": "Calm student monitor who tracks classroom rules.",
                    "visibility": "character_private_and_gm",
                    "activation": "current_turn",
                }
            ],
            "decision_point": None,
            "stop_reason": "continue",
        }

        normalized = self.agent_schemas.validate_gm_output(payload)

        self.assertEqual(normalized["agent"], "gm")
        self.assertEqual(normalized["scene_beats"], payload["scene_beats"])
        self.assertEqual(normalized["events"], payload["events"])
        self.assertEqual(normalized["actor_calls"][0]["actor_id"], "character:SuLi")
        self.assertEqual(normalized["actor_calls"][0]["scene_id"], "classroom-1")
        self.assertEqual(normalized["actor_calls"][0]["location"], "classroom")
        self.assertEqual(normalized["actor_calls"][0]["time_window"], "current")
        self.assertEqual(normalized["actor_calls"][0]["visible_to"], ["character:SuLi"])
        self.assertEqual(normalized["actor_calls"][0]["sensory_channels"], ["visual"])
        self.assertEqual(normalized["actor_calls"][0]["source_actor"], "gm")
        self.assertEqual(normalized["actor_calls"][0]["target_actor"], "character:SuLi")
        self.assertEqual(normalized["actor_calls"][0]["visibility_basis"], visibility_basis())
        self.assertEqual(normalized["parallel_groups"], payload["parallel_groups"])
        self.assertEqual(normalized["world_state_delta"], payload["world_state_delta"])
        self.assertEqual(normalized["character_promotions"][0]["name"], "ClassRep")
        self.assertIsNone(normalized["decision_point"])
        self.assertEqual(normalized["stop_reason"], "continue")

    def test_validate_gm_output_requires_actor_call_visibility_basis(self):
        payload = {
            "agent": "gm",
            "scene_beats": [],
            "events": [],
            "actor_calls": [
                {
                    "call_id": "call-1",
                    "actor_id": "player",
                    "prompt": "You look up.",
                    "reason": "The player is present.",
                }
            ],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "visibility_basis"):
            self.agent_schemas.validate_gm_output(payload)

    def test_validate_gm_output_rejects_hidden_marker_visibility_basis(self):
        payload = {
            "agent": "gm",
            "scene_beats": [],
            "events": [],
            "actor_calls": [
                {
                    "call_id": "call-1",
                    "actor_id": "player",
                    "prompt": "You look up.",
                    "reason": "The player is present.",
                    "visibility_basis": {"mode": "direct", "summary": "world_truth says this matters"},
                }
            ],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "visibility_basis"):
            self.agent_schemas.validate_gm_output(payload)

    def test_validate_gm_output_rejects_hidden_fact_actor_call_visibility_basis(self):
        payload = {
            "agent": "gm",
            "scene_beats": [],
            "events": [],
            "actor_calls": [
                {
                    "call_id": "call-1",
                    "actor_id": "player",
                    "prompt": "You look up.",
                    "reason": "The player is present.",
                    "visibility_basis": {
                        "mode": "direct",
                        "summary": "The player can perceive the cue.",
                        "hidden_fact": "GM-only cause.",
                    },
                }
            ],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "visibility_basis"):
            self.agent_schemas.validate_gm_output(payload)

    def test_validate_gm_output_rejects_hidden_fact_actor_call_visibility_field(self):
        payload = {
            "agent": "gm",
            "scene_beats": [],
            "events": [],
            "actor_calls": [
                {
                    "call_id": "call-1",
                    "actor_id": "player",
                    "prompt": "You look up.",
                    "reason": "The player is present.",
                    "visible_to": ["player", "hidden_fact"],
                    "visibility_basis": visibility_basis("player"),
                }
            ],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "visible_to"):
            self.agent_schemas.validate_gm_output(payload)

    def test_validate_gm_output_rejects_hidden_fact_optional_scene_visibility_basis(self):
        payload = {
            "agent": "gm",
            "scene_beats": [
                {
                    "content": "The clock ticks.",
                    "visibility_basis": {
                        "mode": "public",
                        "summary": "Everyone can hear the clock.",
                        "hidden_fact": "GM-only cause.",
                    },
                }
            ],
            "events": [],
            "actor_calls": [],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, r"scene_beats\[0\].visibility_basis"):
            self.agent_schemas.validate_gm_output(payload)

    def test_validate_gm_output_rejects_hidden_fact_optional_event_visibility_basis(self):
        payload = {
            "agent": "gm",
            "scene_beats": [],
            "events": [
                {
                    "type": "npc_action",
                    "content": "Ada shuts the door.",
                    "visibility_basis": {
                        "mode": "direct",
                        "summary": "The player can see Ada move.",
                        "visible_to": ["player", "hidden_fact"],
                    },
                }
            ],
            "actor_calls": [],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, r"events\[0\].visibility_basis"):
            self.agent_schemas.validate_gm_output(payload)

    def test_validate_gm_output_defaults_character_promotions_to_empty_list(self):
        payload = {
            "agent": "gm",
            "scene_beats": [{"content": "The room goes quiet."}],
            "events": [],
            "actor_calls": [],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }

        normalized = self.agent_schemas.validate_gm_output(payload)

        self.assertEqual(normalized["character_promotions"], [])

    def test_validate_gm_output_rejects_subgm_character_promotions(self):
        payload = {
            "agent": "gm",
            "scene_beats": [{"content": "The room goes quiet."}],
            "events": [],
            "actor_calls": [],
            "parallel_groups": [],
            "world_state_delta": [],
            "character_promotions": [
                {
                    "name": "Side NPC",
                    "source_agent": "subGM:thread-1",
                    "reason": "subGM wants promotion.",
                    "profile_seed": "Not allowed.",
                    "visibility": "character_private_and_gm",
                    "activation": "future_turn",
                }
            ],
            "decision_point": None,
            "stop_reason": "continue",
        }

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "subGM"):
            self.agent_schemas.validate_gm_output(payload)

    def test_validate_gm_output_rejects_preprocess_character_promotions(self):
        payload = {
            "agent": "gm",
            "scene_beats": [{"content": "The room goes quiet."}],
            "events": [],
            "actor_calls": [],
            "parallel_groups": [],
            "world_state_delta": [],
            "character_promotions": [
                {
                    "name": "ClassRep",
                    "source_agent": "preprocess",
                    "reason": "Spoofed stronger source.",
                    "profile_seed": "Spoofed profile should not pass through GM output.",
                    "visibility": "character_private_and_gm",
                    "activation": "current_turn",
                }
            ],
            "decision_point": None,
            "stop_reason": "continue",
        }

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "source_agent.*gm"):
            self.agent_schemas.validate_gm_output(payload)

    def test_validate_gm_output_rejects_malformed_scene_beats(self):
        payload = {
            "agent": "gm",
            "scene_beats": [{"metadata": {"beat": 1}}],
            "events": [],
            "actor_calls": [],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "scene_beats"):
            self.agent_schemas.validate_gm_output(payload)

    def test_validate_gm_output_rejects_malformed_events(self):
        payload = {
            "agent": "gm",
            "scene_beats": [{"content": "The room goes quiet."}],
            "events": [{"type": "npc_action", "content": 123}],
            "actor_calls": [],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "events"):
            self.agent_schemas.validate_gm_output(payload)

    def test_validate_gm_output_rejects_malformed_actor_calls(self):
        payload = {
            "agent": "gm",
            "scene_beats": [{"content": "The room goes quiet."}],
            "events": [],
            "actor_calls": [{"call_id": "call-1", "actor_id": "player", "prompt": "You look up."}],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "actor_calls"):
            self.agent_schemas.validate_gm_output(payload)

    def test_validate_gm_output_rejects_hidden_marker_actor_ids(self):
        base_call = {
            "call_id": "call-1",
            "actor_id": "player",
            "prompt": "You look up.",
            "reason": "The player is present.",
        }
        payload = {
            "agent": "gm",
            "scene_beats": [{"content": "The room goes quiet."}],
            "events": [],
            "actor_calls": [base_call],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }

        for actor_id, expected in (
            ("character:gmOnly", "gm_only"),
            ("character:gm-only", "gm_only"),
            ("character:gm only", "gm_only"),
            ("character:gm_only", "gm_only"),
            ("character:worldTruth", "world_truth"),
            ("character:hiddenNote", "hidden_note"),
        ):
            with self.subTest(actor_id=actor_id):
                call = dict(base_call)
                call["actor_id"] = actor_id
                payload["actor_calls"] = [call]

                with self.assertRaisesRegex(self.agent_schemas.ValidationError, expected):
                    self.agent_schemas.validate_gm_output(payload)

    def test_validate_subgm_output_requires_actor_call_visibility_basis(self):
        payload = minimal_subgm_output(
            {
                "call_id": "call-suli-1",
                "actor_id": "character:SuLi",
                "prompt": "React to the classroom clue.",
                "reason": "SuLi is present in the side scene.",
            }
        )

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "visibility_basis"):
            self.agent_schemas.validate_subgm_output(payload)

    def test_validate_subgm_output_rejects_hidden_marker_visibility_basis(self):
        payload = minimal_subgm_output(
            {
                "call_id": "call-suli-1",
                "actor_id": "character:SuLi",
                "prompt": "React to the classroom clue.",
                "reason": "SuLi is present in the side scene.",
                "visibility_basis": {"mode": "direct", "summary": "gm_only side-scene signal"},
            }
        )

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "visibility_basis"):
            self.agent_schemas.validate_subgm_output(payload)

    def test_validate_actor_output_requires_events_protocol(self):
        payload = {
            "agent": "character",
            "agent_id": "character:ada",
            "character_name": "Ada",
            "events": [
                {"type": "perceive_request", "content": "I listen for steps beyond the door."},
                {"type": "dialogue", "target": "player", "content": "Stay close."},
                {"type": "action", "target": "", "content": "I lift the lamp and wait by the threshold."},
                {"type": "memory_delta", "target": "self", "content": "I saw the archive door open."},
                {"type": "goal_update", "target": "self", "content": "Keep the player away from danger."},
                {"type": "wait_for_gm", "target": "", "content": "I wait for what the lamp reveals."},
                {"type": "stop_for_player_decision", "target": "player", "content": "The player must choose whether to enter."},
            ],
            "stop_reason": "continue",
        }

        normalized = self.agent_schemas.validate_actor_output(payload)

        self.assertEqual(normalized["agent"], "character")
        self.assertEqual(normalized["agent_id"], "character:ada")
        self.assertEqual(normalized["events"][0]["type"], "perceive_request")
        self.assertEqual(normalized["events"][0]["target"], "")
        self.assertEqual(normalized["events"][0]["metadata"], {})
        self.assertEqual(normalized["events"][1]["target"], "player")
        self.assertEqual(normalized["stop_reason"], "continue")

    def test_validate_actor_output_rejects_character_agent_with_player_id(self):
        payload = {
            "agent": "character",
            "agent_id": "player",
            "character_name": "Ada",
            "events": [
                {
                    "type": "action",
                    "target": "",
                    "content": "I lift the lamp.",
                    "metadata": {},
                }
            ],
            "stop_reason": "continue",
        }

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "agent_id"):
            self.agent_schemas.validate_actor_output(payload)

    def test_validate_actor_output_rejects_empty_character_agent_id_suffix(self):
        payload = {
            "agent": "character",
            "agent_id": "character:",
            "character_name": "",
            "events": [
                {
                    "type": "action",
                    "target": "",
                    "content": "I lift the lamp.",
                    "metadata": {},
                }
            ],
            "stop_reason": "continue",
        }

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "agent_id"):
            self.agent_schemas.validate_actor_output(payload)

    def test_validate_actor_output_rejects_player_agent_with_character_id(self):
        payload = {
            "agent": "player",
            "agent_id": "character:Ada",
            "events": [
                {
                    "type": "action",
                    "target": "",
                    "content": "I open the archive door.",
                    "metadata": {},
                }
            ],
            "stop_reason": "continue",
        }

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "agent_id"):
            self.agent_schemas.validate_actor_output(payload)

    def test_validate_actor_output_rejects_legacy_single_action_protocol(self):
        payload = {
            "agent": "player",
            "agent_id": "player",
            "action": "I walk forward.",
            "dialogue": [],
            "perception": [],
            "memory_delta": [],
        }

        with self.assertRaises(self.agent_schemas.ValidationError):
            self.agent_schemas.validate_actor_output(payload)

    def test_validate_actor_output_rejects_unknown_event_type(self):
        payload = {
            "agent": "player",
            "agent_id": "player",
            "events": [{"type": "thought", "target": "", "content": "I keep thinking about the answer."}],
            "stop_reason": "continue",
        }

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "allowed actor event type"):
            self.agent_schemas.validate_actor_output(payload)

    def test_validate_actor_output_rejects_unknown_event_fields(self):
        base_event = {
            "type": "memory_delta",
            "target": "self",
            "content": "I saw the archive door open.",
            "metadata": {"tone": "cautious"},
        }
        payload = {
            "agent": "player",
            "agent_id": "player",
            "events": [base_event],
            "stop_reason": "continue",
        }

        normalized = self.agent_schemas.validate_actor_output(payload)
        self.assertEqual(normalized["events"][0]["metadata"], {"tone": "cautious"})

        for extra_key, extra_value in (("source", "gm_only"), ("confidence", "high")):
            with self.subTest(extra_key=extra_key):
                event = dict(base_event)
                event[extra_key] = extra_value
                payload["events"] = [event]
                with self.assertRaisesRegex(self.agent_schemas.ValidationError, extra_key):
                    self.agent_schemas.validate_actor_output(payload)

    def test_actor_output_rejects_omniscient_or_control_fields(self):
        base = {
            "agent": "player",
            "agent_id": "player",
            "events": [
                {
                    "type": "action",
                    "target": "",
                    "content": "I step closer to the door.",
                    "metadata": {},
                }
            ],
            "stop_reason": "continue",
        }

        for forbidden_key in ("gm_notes", "player_name", "world_truth"):
            with self.subTest(forbidden_key=forbidden_key):
                payload = dict(base)
                payload["events"] = [
                    {
                        "type": "action",
                        "target": "",
                        "content": "I step closer to the door.",
                        "metadata": {"nested": {forbidden_key: "hidden fact"}},
                    }
                ]
                with self.assertRaisesRegex(self.agent_schemas.ValidationError, forbidden_key):
                    self.agent_schemas.validate_actor_output(payload)

    def test_actor_output_rejects_documented_hidden_markers_in_nested_metadata(self):
        base = {
            "agent": "player",
            "agent_id": "player",
            "events": [
                {
                    "type": "action",
                    "target": "",
                    "content": "I step closer to the door.",
                    "metadata": {},
                }
            ],
            "stop_reason": "continue",
        }

        for forbidden_key in ("gm_only", "omniscient", "hidden_note", "out_of_character"):
            with self.subTest(forbidden_key=forbidden_key):
                payload = dict(base)
                payload["events"] = [
                    {
                        "type": "action",
                        "target": "",
                        "content": "I step closer to the door.",
                        "metadata": {"nested": {"audit": {forbidden_key: "hidden fact"}}},
                    }
                ]
                with self.assertRaisesRegex(self.agent_schemas.ValidationError, forbidden_key):
                    self.agent_schemas.validate_actor_output(payload)

    def test_actor_output_rejects_hidden_marker_values_in_metadata(self):
        base = {
            "agent": "player",
            "agent_id": "player",
            "events": [
                {
                    "type": "memory_delta",
                    "target": "self",
                    "content": "I remember the archive door.",
                    "metadata": {"tone": "cautious"},
                }
            ],
            "stop_reason": "continue",
        }

        normalized = self.agent_schemas.validate_actor_output(base)
        self.assertEqual(normalized["events"][0]["metadata"], {"tone": "cautious"})

        for metadata, expected in (
            ({"source": "gm_only"}, "gm_only"),
            ({"nested": {"note": "world_truth"}}, "world_truth"),
        ):
            with self.subTest(metadata=metadata):
                payload = dict(base)
                event = dict(base["events"][0])
                event["metadata"] = metadata
                payload["events"] = [event]

                with self.assertRaisesRegex(self.agent_schemas.ValidationError, expected):
                    self.agent_schemas.validate_actor_output(payload)

    def test_actor_output_rejects_camel_hidden_markers_in_metadata_and_content(self):
        base = {
            "agent": "player",
            "agent_id": "player",
            "events": [
                {
                    "type": "memory_delta",
                    "target": "self",
                    "content": "I remember the archive door.",
                    "metadata": {},
                }
            ],
            "stop_reason": "continue",
        }

        for marker, expected in (
            ("gmOnly", "gm_only"),
            ("worldTruth", "world_truth"),
            ("hiddenNote", "hidden_note"),
        ):
            with self.subTest(marker=marker, location="metadata_value"):
                payload = dict(base)
                event = dict(base["events"][0])
                event["metadata"] = {"source": marker}
                payload["events"] = [event]

                with self.assertRaisesRegex(self.agent_schemas.ValidationError, expected):
                    self.agent_schemas.validate_actor_output(payload)

            with self.subTest(marker=marker, location="metadata_key"):
                payload = dict(base)
                event = dict(base["events"][0])
                event["metadata"] = {marker: "hidden fact"}
                payload["events"] = [event]

                with self.assertRaisesRegex(self.agent_schemas.ValidationError, "forbidden"):
                    self.agent_schemas.validate_actor_output(payload)

            with self.subTest(marker=marker, location="content"):
                payload = dict(base)
                event = dict(base["events"][0])
                event["content"] = f"I should not persist {marker} knowledge."
                payload["events"] = [event]

                with self.assertRaisesRegex(self.agent_schemas.ValidationError, expected):
                    self.agent_schemas.validate_actor_output(payload)

    def test_actor_output_rejects_explicit_hidden_marker_variants_as_values(self):
        base = {
            "agent": "player",
            "agent_id": "player",
            "events": [
                {
                    "type": "memory_delta",
                    "target": "self",
                    "content": "I remember the archive door.",
                    "metadata": {},
                }
            ],
            "stop_reason": "continue",
        }

        for marker, expected in (
            ("gmOnly", "gm_only"),
            ("gm-only", "gm_only"),
            ("gm only", "gm_only"),
            ("gm_only", "gm_only"),
            ("worldTruth", "world_truth"),
            ("hiddenNote", "hidden_note"),
        ):
            with self.subTest(marker=marker):
                payload = dict(base)
                event = dict(base["events"][0])
                event["metadata"] = {"source": marker}
                payload["events"] = [event]

                with self.assertRaisesRegex(self.agent_schemas.ValidationError, expected):
                    self.agent_schemas.validate_actor_output(payload)

    def test_actor_output_allows_marker_words_inside_normal_prose(self):
        payload = {
            "agent": "player",
            "agent_id": "player",
            "events": [
                {
                    "type": "memory_delta",
                    "target": "self",
                    "content": "I met a player named Ada",
                    "metadata": {},
                },
                {
                    "type": "memory_delta",
                    "target": "self",
                    "content": "I found a hidden notebook",
                    "metadata": {},
                },
                {
                    "type": "memory_delta",
                    "target": "self",
                    "content": "The world truthfully felt smaller.",
                    "metadata": {},
                },
            ],
            "stop_reason": "continue",
        }

        try:
            normalized = self.agent_schemas.validate_actor_output(payload)
        except self.agent_schemas.ValidationError as exc:
            self.fail(f"normal prose should be accepted: {exc}")

        self.assertEqual(
            [event["content"] for event in normalized["events"]],
            [
                "I met a player named Ada",
                "I found a hidden notebook",
                "The world truthfully felt smaller.",
            ],
        )

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
