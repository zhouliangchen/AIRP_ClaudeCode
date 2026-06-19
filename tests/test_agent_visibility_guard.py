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

    def test_sanitize_gm_output_redacts_visibility_basis(self):
        sanitized = self.guard.sanitize_gm_output(
            {
                "agent": "gm",
                "scene_beats": [],
                "events": [],
                "actor_calls": [
                    {
                        "call_id": "call-player-1",
                        "actor_id": "player",
                        "prompt": "You feel heat.",
                        "reason": "Visible touch.",
                        "visible_to": ["player", "hidden_fact"],
                        "visibility_basis": {
                            "mode": "direct",
                            "summary": "The pendant burns identity.",
                            "target_actor": "player",
                            "visible_to": ["player", "hidden_fact"],
                            "hidden_fact": "GM-only cause.",
                        },
                    }
                ],
                "parallel_groups": [],
                "world_state_delta": [],
                "decision_point": None,
                "stop_reason": "continue",
            },
            {"hidden_facts": [{"fact": "The pendant burns identity."}]},
        )

        self.assertEqual(
            sanitized["actor_calls"][0]["visibility_basis"]["summary"],
            "[redacted]",
        )
        self.assertNotIn("hidden_fact", repr(sanitized).lower())
        self.assertIn("[redacted]", sanitized["actor_calls"][0]["visible_to"])
        self.assertIn("[redacted]", sanitized["actor_calls"][0]["visibility_basis"]["visible_to"])

    def test_sanitize_gm_output_redacts_compact_hidden_markers(self):
        sanitized = self.guard.sanitize_gm_output(
            {
                "agent": "gm",
                "scene_beats": [],
                "events": [],
                "actor_calls": [
                    {
                        "call_id": "call-player-1",
                        "actor_id": "player",
                        "prompt": "gmonlyroom",
                        "reason": "worldtruthactor",
                        "metadata": {
                            "hiddenfactwitness": "drop this key",
                            "note": "outofcharacternote",
                        },
                        "location": "gmonlyroom",
                        "visible_to": ["player", "hiddenfactwitness"],
                        "source_actor": "worldtruthactor",
                        "visibility_basis": {
                            "mode": "direct",
                            "summary": "outofcharacternote",
                            "source_actor": "worldtruthactor",
                            "target_actor": "player",
                            "visible_to": ["player", "hiddenfactwitness"],
                        },
                    }
                ],
                "parallel_groups": [],
                "world_state_delta": [],
                "decision_point": None,
                "stop_reason": "continue",
            },
            {},
        )

        serialized = repr(sanitized).lower()
        for marker in (
            "gmonlyroom",
            "worldtruthactor",
            "hiddenfactwitness",
            "outofcharacternote",
        ):
            self.assertNotIn(marker, serialized)
        self.assertIn("[redacted]", serialized)

    def test_redacts_hidden_phrase_and_marker_from_character_promotions(self):
        input_payload = {
            "routed_input": {
                "user_instruction_channel": "Hidden truth: the class rep reports to the secret council.",
            },
        }
        gm_output = {
            "agent": "gm",
            "scene_beats": [],
            "events": [],
            "actor_calls": [],
            "parallel_groups": [],
            "world_state_delta": [],
            "character_promotions": [{
                "name": "ClassRep",
                "source_agent": "gm",
                "reason": "world_truth: ClassRep now needs independent agency.",
                "profile_seed": "Rule-bound monitor; the class rep reports to the secret council.",
                "visibility": "character_private_and_gm",
                "activation": "current_turn",
            }],
            "decision_point": None,
            "stop_reason": "continue",
        }

        sanitized = self.guard.sanitize_gm_output(gm_output, input_payload)
        text = repr(sanitized).lower()

        self.assertNotIn("secret council", text)
        self.assertNotIn("world_truth", text)
        self.assertIn("[redacted]", text)
        self.assertEqual(sanitized["character_promotions"][0]["source_agent"], "gm")

    def test_redacts_camel_case_hidden_markers_from_character_promotions(self):
        markers = [
            "gmOnly",
            "WorldTruth",
            "hiddenNote",
            "privateMemory",
            "outOfCharacter",
        ]
        for marker in markers:
            with self.subTest(marker=marker):
                gm_output = {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "character_promotions": [{
                        "name": "ClassRep",
                        "source_agent": "gm",
                        "reason": f"{marker}: ClassRep now needs independent agency.",
                        "profile_seed": f"{marker}: hidden seed for the class rep.",
                        "visibility": "character_private_and_gm",
                        "activation": "current_turn",
                    }],
                    "decision_point": None,
                    "stop_reason": "continue",
                }

                sanitized = self.guard.sanitize_gm_output(gm_output, {})
                promotion = sanitized["character_promotions"][0]

                self.assertEqual(promotion["reason"], "[redacted]")
                self.assertEqual(promotion["profile_seed"], "[redacted]")
                self.assertNotIn(marker.lower(), repr(promotion).lower())

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

    def test_sanitize_gm_output_redacts_event_source_call_id_and_target(self):
        input_payload = {
            "routed_input": {
                "user_instruction_channel": "Hidden truth: moon base archive.",
            },
            "hidden_facts": [{"fact": "moon base archive"}],
        }
        gm_output = {
            "agent": "gm",
            "scene_beats": [],
            "events": [
                {
                    "type": "npc_action",
                    "target": "player",
                    "source_call_id": "hiddenfactevent",
                    "content": "Ada raises a hand.",
                },
                {
                    "type": "npc_action",
                    "target": "worldtruthsource",
                    "source_call_id": "moon base archive",
                    "content": "Ada reads the public signal.",
                },
            ],
            "actor_calls": [],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }

        sanitized = self.guard.sanitize_gm_output(gm_output, input_payload)
        serialized = repr(sanitized).lower()

        self.assertEqual(sanitized["events"][0]["target"], "player")
        self.assertEqual(sanitized["events"][0]["source_call_id"], "[redacted]")
        self.assertEqual(sanitized["events"][1]["target"], "[redacted]")
        self.assertEqual(sanitized["events"][1]["source_call_id"], "[redacted]")
        self.assertNotIn("hiddenfactevent", serialized)
        self.assertNotIn("moon base archive", serialized)
        self.assertNotIn("worldtruthsource", serialized)

    def test_sanitize_gm_output_redacts_decision_point_and_separator_variants(self):
        input_payload = {
            "routed_input": {
                "user_instruction_channel": "Hidden truth: moon base archive.",
            },
            "hidden_facts": [{"fact": "moon base archive"}],
        }
        gm_output = {
            "agent": "gm",
            "scene_beats": [],
            "events": [
                {
                    "type": "npc_action",
                    "target": "moon-base-archive",
                    "source_call_id": "moon_base_archive",
                    "content": "Ada reads the public signal.",
                },
            ],
            "actor_calls": [{
                "call_id": "call-player-1",
                "actor_id": "player",
                "prompt": "React without saying moon.base.archive.",
                "reason": "Visible prompt.",
                "visibility_basis": {
                    "mode": "direct",
                    "summary": "The player is directly addressed.",
                    "target_actor": "player",
                    "visible_to": ["player"],
                },
            }],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": {
                "reason": "Choose whether to reveal moon-base-archive.",
                "options": ["ask about moon_base_archive", "walk away"],
            },
            "stop_reason": "moon.base.archive",
        }

        sanitized = self.guard.sanitize_gm_output(gm_output, input_payload)
        serialized = repr(sanitized).lower()

        self.assertEqual(sanitized["events"][0]["target"], "[redacted]")
        self.assertEqual(sanitized["events"][0]["source_call_id"], "[redacted]")
        self.assertIn("[redacted]", sanitized["actor_calls"][0]["prompt"])
        self.assertIn("[redacted]", sanitized["decision_point"]["reason"])
        self.assertIn("[redacted]", sanitized["decision_point"]["options"][0])
        self.assertEqual(sanitized["decision_point"]["options"][1], "walk away")
        self.assertEqual(sanitized["stop_reason"], "[redacted]")
        self.assertNotIn("moon-base-archive", serialized)
        self.assertNotIn("moon_base_archive", serialized)
        self.assertNotIn("moon.base.archive", serialized)

    def test_sanitize_gm_output_preserves_valid_stop_reason_matching_hidden_phrase_tokens(self):
        input_payload = {
            "hidden_facts": [{"fact": "player decision"}],
        }
        gm_output = {
            "agent": "gm",
            "scene_beats": [],
            "events": [],
            "actor_calls": [],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "player_decision",
        }

        sanitized = self.guard.sanitize_gm_output(gm_output, input_payload)

        self.assertEqual(sanitized["stop_reason"], "player_decision")

    def test_drops_camel_case_hidden_marker_keys_from_gm_metadata(self):
        input_payload = {
            "hidden_facts": ["The mirror names traitors."],
        }
        gm_output = {
            "agent": "gm",
            "scene_beats": [{
                "content": "The classroom quiets.",
                "metadata": {
                    "hiddenNote": "drop this scene key",
                    "playerName": "drop this scene player-name key",
                    "safeNote": "The mirror names traitors.",
                    "nested": {
                        "WorldTruth": "drop this nested key",
                        "playerName": "drop this nested scene player-name key",
                        "ordinary": "visible",
                    },
                    "items": [{
                        "gmOnly": "drop this listed key",
                        "ordinary": "kept",
                    }],
                },
            }],
            "events": [{
                "type": "npc_action",
                "content": "The class rep writes a note.",
                "metadata": {
                    "worldTruth": "drop this event key",
                    "playerName": "drop this event player-name key",
                    "public": {
                        "privateMemory": "drop this nested event key",
                        "playerName": "drop this nested event player-name key",
                        "safe": "The mirror names traitors.",
                    },
                },
            }],
            "actor_calls": [{
                "call_id": "call-player-1",
                "actor_id": "player",
                "prompt": "React to the class rep.",
                "reason": "The player can respond.",
                "metadata": {
                    "gmOnly": "drop this actor-call key",
                    "playerName": "drop this actor-call player-name key",
                    "public": [{
                        "outOfCharacter": "drop this nested actor-call key",
                        "playerName": "drop this nested actor-call player-name key",
                        "safe": "visible",
                    }],
                },
            }],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        }

        sanitized = self.guard.sanitize_gm_output(gm_output, input_payload)
        scene_metadata = sanitized["scene_beats"][0]["metadata"]
        event_metadata = sanitized["events"][0]["metadata"]
        call_metadata = sanitized["actor_calls"][0]["metadata"]
        serialized = repr([scene_metadata, event_metadata, call_metadata]).lower()

        for marker in ("hiddennote", "worldtruth", "gmonly", "privatememory", "outofcharacter", "playername"):
            self.assertNotIn(marker, serialized)
        self.assertIn("[redacted]", repr(scene_metadata["safeNote"]))
        self.assertIn("[redacted]", repr(event_metadata["public"]["safe"]))
        self.assertEqual(scene_metadata["nested"]["ordinary"], "visible")
        self.assertEqual(scene_metadata["items"][0]["ordinary"], "kept")
        self.assertEqual(call_metadata["public"][0]["safe"], "visible")

    def test_redacts_cjk_hidden_phrase_with_punctuation_and_spaces(self):
        phrases = self.guard.hidden_phrases({
            "hidden_facts": ["门后是梦境"],
        })

        redacted = self.guard.redact_text("你知道门 后，是 梦境，但只能说门后有光。", phrases)

        self.assertNotIn("门 后，是 梦境", redacted)
        self.assertIn("[redacted]", redacted)
        self.assertIn("门后有光", redacted)

    def test_hidden_phrases_ignore_visible_recent_chat_ai_text(self):
        visible_story = "清晨的教室里，预备铃响起时，你还坐在自己的座位上。"
        phrases = self.guard.hidden_phrases({
            "recent_chat": [{"ai": visible_story, "summary": "你记录了粉色云。"}],
            "routed_input": {
                "user_instruction_channel": "隐藏事实：吊坠会燃烧身份。",
            },
        })

        self.assertNotIn(visible_story, phrases)
        self.assertIn("吊坠会燃烧身份", phrases)

    def test_hidden_phrases_drop_short_and_oversized_cjk_fragments(self):
        long_visible_like_text = "清晨的教室里" * 80
        phrases = self.guard.hidden_phrases({
            "hidden_facts": [
                "当然，记忆、身份都会燃烧。门后是梦境。",
                long_visible_like_text,
            ],
        })

        self.assertNotIn("当然", phrases)
        self.assertNotIn("记忆", phrases)
        self.assertNotIn(long_visible_like_text, phrases)
        self.assertIn("门后是梦境", phrases)
        self.assertTrue(all(len(phrase) <= 160 for phrase in phrases))


if __name__ == "__main__":
    unittest.main()
