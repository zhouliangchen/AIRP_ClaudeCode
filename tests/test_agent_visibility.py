import importlib.util
import json
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


def basis(mode, summary="visible proof", **extra):
    payload = {"mode": mode, "summary": summary}
    payload.update(extra)
    return payload


class AgentVisibilityTest(unittest.TestCase):
    def setUp(self):
        self.visibility = load_module("agent_visibility")

    def test_local_visual_event_does_not_reach_actor_in_another_location(self):
        event = {
            "type": "scene",
            "content": "A red lamp flickers beside the archive door.",
            "location": "archive",
            "sensory_channels": ["visual"],
            "visibility_basis": basis(
                "location",
                location="archive",
                sensory_channels=["visual"],
            ),
        }
        actor = {"name": "Ada", "location": "courtyard"}

        self.assertFalse(
            self.visibility.event_visible_to_actor(event, "character:Ada", actor)
        )

    def test_local_visual_event_reaches_actor_in_same_location(self):
        event = {
            "type": "scene",
            "content": "A red lamp flickers beside the archive door.",
            "location": "archive",
            "sensory_channels": ["visual"],
            "visibility_basis": basis(
                "location",
                location="archive",
                sensory_channels=["visual"],
            ),
        }
        actor = {"name": "Ada", "location": "archive"}

        self.assertTrue(
            self.visibility.event_visible_to_actor(event, "character:Ada", actor)
        )

    def test_location_basis_can_match_actor_scene_id_without_location(self):
        event = {
            "type": "scene",
            "content": "The classroom projector hums.",
            "sensory_channels": ["auditory"],
            "visibility_basis": basis(
                "location",
                scene_id="classroom-1",
                sensory_channels=["auditory"],
            ),
        }
        actor = {"name": "Ada", "scene_id": "classroom-1"}

        self.assertTrue(
            self.visibility.event_visible_to_actor(event, "character:Ada", actor)
        )

    def test_location_basis_matches_same_cjk_location(self):
        event = {
            "type": "scene",
            "content": "The classroom projector hums.",
            "visibility_basis": basis("location", location="\u6559\u5ba4"),
        }

        self.assertTrue(
            self.visibility.event_visible_to_actor(
                event,
                "character:Ada",
                {"location": "\u6559\u5ba4"},
            )
        )

    def test_location_basis_rejects_different_cjk_location(self):
        event = {
            "type": "scene",
            "content": "The classroom projector hums.",
            "visibility_basis": basis("location", location="\u6559\u5ba4"),
        }

        self.assertFalse(
            self.visibility.event_visible_to_actor(
                event,
                "character:Ada",
                {"location": "\u8d70\u5eca"},
            )
        )

    def test_location_basis_rejects_different_mixed_cjk_location(self):
        event = {
            "type": "scene",
            "content": "The classroom projector hums.",
            "visibility_basis": basis("location", location="room:\u6559\u5ba4"),
        }

        self.assertFalse(
            self.visibility.event_visible_to_actor(
                event,
                "character:Ada",
                {"location": "room:\u8d70\u5eca"},
            )
        )

    def test_location_basis_matches_and_rejects_cjk_scene_id(self):
        event = {
            "type": "scene",
            "content": "The classroom projector hums.",
            "visibility_basis": basis("location", scene_id="\u6559\u5ba4"),
        }
        mixed_event = {
            "type": "scene",
            "content": "The classroom projector hums.",
            "visibility_basis": basis("location", scene_id="room:\u6559\u5ba4"),
        }

        self.assertTrue(
            self.visibility.event_visible_to_actor(
                event,
                "character:Ada",
                {"scene_id": "\u6559\u5ba4"},
            )
        )
        self.assertFalse(
            self.visibility.event_visible_to_actor(
                mixed_event,
                "character:Ada",
                {"scene_id": "room:\u8d70\u5eca"},
            )
        )

    def test_location_sensory_channels_distinguish_cjk_channels(self):
        event = {
            "type": "scene",
            "content": "A red light flashes in the classroom.",
            "visibility_basis": basis(
                "location",
                location="\u6559\u5ba4",
                sensory_channels=["\u89c6\u89c9"],
            ),
        }

        self.assertFalse(
            self.visibility.event_visible_to_actor(
                event,
                "character:Ada",
                {
                    "location": "\u6559\u5ba4",
                    "sensory_channels": ["\u542c\u89c9"],
                },
            )
        )

    def test_location_sensory_channels_match_same_cjk_channel(self):
        event = {
            "type": "scene",
            "content": "A red light flashes in the classroom.",
            "visibility_basis": basis(
                "location",
                location="\u6559\u5ba4",
                sensory_channels=["\u89c6\u89c9"],
            ),
        }

        self.assertTrue(
            self.visibility.event_visible_to_actor(
                event,
                "character:Ada",
                {
                    "location": "\u6559\u5ba4",
                    "sensory_channels": ["\u89c6\u89c9"],
                },
            )
        )

    def test_public_broadcast_reaches_configured_recipient(self):
        event = {
            "type": "announcement",
            "content": "The bell rings across the school.",
            "visible_to": ["character:Ada", "character:SuLi"],
            "sensory_channels": ["auditory"],
            "visibility_basis": basis(
                "public",
                visible_to=["character:Ada", "character:SuLi"],
                sensory_channels=["auditory"],
            ),
        }

        self.assertTrue(
            self.visibility.event_visible_to_actor(event, "character:Ada", {"location": "archive"})
        )
        self.assertFalse(
            self.visibility.event_visible_to_actor(event, "character:Eve", {"location": "archive"})
        )

    def test_public_all_marker_reaches_any_actor(self):
        event = {
            "type": "announcement",
            "content": "A siren sounds through every corridor.",
            "visible_to": ["all"],
            "visibility_basis": basis("public", visible_to=["all"]),
        }

        self.assertTrue(
            self.visibility.event_visible_to_actor(event, "character:Eve", {"location": "courtyard"})
        )

    def test_public_mode_without_explicit_recipient_or_marker_fails_closed(self):
        event = {
            "type": "announcement",
            "content": "A bell rings somewhere off-screen.",
            "visibility_basis": basis("public"),
        }

        self.assertFalse(
            self.visibility.event_visible_to_actor(event, "character:Eve", {"location": "courtyard"})
        )

    def test_actor_call_public_basis_can_reach_actor_other_than_call_actor_id(self):
        actor_call = {
            "actor_id": "character:Ada",
            "prompt": "A bell rings across the school.",
            "visibility_basis": basis("public", visible_to=["all"]),
        }

        self.assertTrue(
            self.visibility.actor_call_visible_to_actor(
                actor_call,
                "character:Eve",
                {"location": "courtyard"},
            )
        )

    def test_actor_call_hidden_marker_in_prompt_blocks_visibility(self):
        actor_call = {
            "actor_id": "character:Ada",
            "prompt": "world_truth says the bell is fake.",
            "visibility_basis": basis("public", visible_to=["all"]),
        }

        self.assertFalse(
            self.visibility.actor_call_visible_to_actor(
                actor_call,
                "character:Eve",
                {"location": "courtyard"},
            )
        )

    def test_actor_call_nested_visibility_metadata_basis_proves_visibility(self):
        actor_call = {
            "actor_id": "character:Ada",
            "prompt": "A bell rings across the school.",
            "visibility_metadata": {
                "visibility_basis": basis("public", visible_to=["all"]),
            },
        }

        self.assertEqual(
            self.visibility.actor_call_basis(actor_call)["mode"],
            "public",
        )
        self.assertTrue(
            self.visibility.actor_call_visible_to_actor(
                actor_call,
                "character:Eve",
                {"location": "courtyard"},
            )
        )

    def test_direct_target_proof_distinguishes_cjk_actor_ids(self):
        event = {
            "type": "direct_prompt",
            "content": "苏黎 receives a direct cue.",
            "visibility_basis": basis(
                "direct",
                target_actor="character:苏黎",
            ),
        }

        self.assertTrue(
            self.visibility.event_visible_to_actor(event, "character:苏黎", {})
        )
        self.assertFalse(
            self.visibility.event_visible_to_actor(event, "character:艾达", {})
        )

    def test_visible_to_distinguishes_cjk_actor_ids(self):
        event = {
            "type": "announcement",
            "content": "Only 苏黎 receives the note.",
            "visibility_basis": basis(
                "public",
                visible_to=["character:苏黎"],
            ),
        }

        self.assertTrue(
            self.visibility.event_visible_to_actor(event, "character:苏黎", {})
        )
        self.assertFalse(
            self.visibility.event_visible_to_actor(event, "character:艾达", {})
        )

    def test_private_dialogue_reaches_speaker_target_and_explicit_witness_only(self):
        event = {
            "type": "dialogue",
            "content": "Stay close.",
            "source_actor": "character:Ada",
            "target_actor": "player",
            "visible_to": ["character:SuLi"],
            "visibility_basis": basis(
                "private_dialogue",
                source_actor="character:Ada",
                target_actor="player",
                visible_to=["character:SuLi"],
            ),
        }

        self.assertTrue(self.visibility.event_visible_to_actor(event, "character:Ada", {}))
        self.assertTrue(self.visibility.event_visible_to_actor(event, "player", {}))
        self.assertTrue(self.visibility.event_visible_to_actor(event, "character:SuLi", {}))
        self.assertFalse(self.visibility.event_visible_to_actor(event, "character:Eve", {}))

    def test_private_dialogue_distinguishes_cjk_source_target_and_witness(self):
        event = {
            "type": "dialogue",
            "content": "跟紧我。",
            "visibility_basis": basis(
                "private_dialogue",
                source_actor="character:苏黎",
                target_actor="character:艾达",
                visible_to=["character:旁观者"],
            ),
        }

        self.assertTrue(self.visibility.event_visible_to_actor(event, "character:苏黎", {}))
        self.assertTrue(self.visibility.event_visible_to_actor(event, "character:艾达", {}))
        self.assertTrue(self.visibility.event_visible_to_actor(event, "character:旁观者", {}))
        self.assertFalse(self.visibility.event_visible_to_actor(event, "character:路人", {}))

    def test_unproven_world_visible_event_fails_closed(self):
        event = {
            "actor": "gm",
            "type": "scene",
            "content": "The archive door opens.",
        }

        self.assertFalse(self.visibility.event_visible_to_actor(event, "character:Ada", {}))

    def test_world_visible_source_bucket_does_not_prove_actor_visibility(self):
        event = {
            "actor": "gm",
            "type": "scene",
            "content": "The archive door opens.",
        }

        self.assertFalse(
            self.visibility.event_visible_to_actor(
                event,
                "character:Ada",
                {},
                source_bucket_actor_id="world_visible",
            )
        )

    def test_nested_visibility_metadata_basis_proves_event_visibility(self):
        event = {
            "type": "trace_event",
            "content": "The archive lamp flickers.",
            "visibility_metadata": {
                "visibility_basis": basis(
                    "location",
                    location="archive",
                    sensory_channels=["visual"],
                ),
            },
        }
        actor = {"name": "Ada", "location": "archive"}

        self.assertTrue(
            self.visibility.event_visible_to_actor(event, "character:Ada", actor)
        )

    def test_actor_specific_bucket_is_visible_only_to_that_actor(self):
        event = {
            "actor": "gm",
            "type": "sound",
            "content": "You hear a hinge creak beside you.",
        }

        self.assertTrue(
            self.visibility.event_visible_to_actor(
                event,
                "character:Ada",
                {},
                source_bucket_actor_id="character:Ada",
            )
        )
        self.assertFalse(
            self.visibility.event_visible_to_actor(
                event,
                "character:SuLi",
                {},
                source_bucket_actor_id="character:Ada",
            )
        )

    def test_actor_specific_bucket_distinguishes_cjk_actor_ids(self):
        event = {
            "actor": "gm",
            "type": "sound",
            "content": "你听见身边的门轴轻响。",
        }

        self.assertTrue(
            self.visibility.event_visible_to_actor(
                event,
                "character:苏黎",
                {},
                source_bucket_actor_id="character:苏黎",
            )
        )
        self.assertFalse(
            self.visibility.event_visible_to_actor(
                event,
                "character:艾达",
                {},
                source_bucket_actor_id="character:苏黎",
            )
        )

    def test_hidden_markers_in_basis_make_event_invisible(self):
        event = {
            "type": "scene",
            "content": "The lamp flickers.",
            "visible_to": ["all"],
            "visibility_basis": {
                "mode": "public",
                "summary": "world_truth says the lamp is fake",
            },
        }

        self.assertFalse(self.visibility.event_visible_to_actor(event, "player", {}))

    def test_normalized_basis_is_json_safe_and_compact(self):
        normalized = self.visibility.normalize_visibility_basis({
            "mode": "location",
            "summary": "Ada can hear the bell.",
            "scene_id": 99,
            "location": "archive",
            "time_window": "current",
            "visible_to": ["character:Ada", 7],
            "sensory_channels": ["auditory", "visual"],
            "source_actor": "gm",
            "target_actor": "character:Ada",
            "extra": {"ignored": True},
        })

        self.assertEqual(
            normalized,
            {
                "mode": "location",
                "summary": "Ada can hear the bell.",
                "scene_id": "99",
                "location": "archive",
                "time_window": "current",
                "visible_to": ["character:Ada", "7"],
                "sensory_channels": ["auditory", "visual"],
                "source_actor": "gm",
                "target_actor": "character:Ada",
            },
        )
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
