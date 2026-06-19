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

    def test_unproven_world_visible_event_fails_closed(self):
        event = {
            "actor": "gm",
            "type": "scene",
            "content": "The archive door opens.",
        }

        self.assertFalse(self.visibility.event_visible_to_actor(event, "character:Ada", {}))

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
