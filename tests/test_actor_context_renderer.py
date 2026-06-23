import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = str(ROOT / "skills")


def load_module(name):
    if SKILLS not in sys.path:
        sys.path.insert(0, SKILLS)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ActorContextRendererTest(unittest.TestCase):
    def setUp(self):
        self.renderer = load_module("actor_context_renderer")

    def test_render_character_context_is_immersive_and_subjective(self):
        actor = {
            "name": "Current Paladin",
            "role": "royal paladin",
            "memory": {
                "long_term": ["I was taught that cursed heroes endanger civilians."],
                "key_memories": ["I swore to protect the market district."],
                "short_term": ["I saw an old wanted sigil on the traveler's cloak."],
                "goals": ["Keep civilians safe."],
            },
            "misconceptions": ["The old hero is cursed."],
            "sensory_context": {"sight": "Crowded market stalls block the north road."},
        }

        rendered = self.renderer.render_actor_context("character:CurrentPaladin", actor, {})
        serialized = json.dumps(rendered, ensure_ascii=False)

        self.assertEqual(rendered["actor_id"], "character:CurrentPaladin")
        self.assertIn("You are Current Paladin", rendered["immersive_context"])
        self.assertIn("You remember: I was taught that cursed heroes endanger civilians.", rendered["immersive_context"])
        self.assertIn("Your current goal is: Keep civilians safe.", rendered["immersive_context"])
        self.assertNotIn("misconceptions", serialized)
        self.assertNotIn("objective_truth", serialized)
        self.assertNotIn("gm_only", serialized)
        self.assertNotIn("belief_is_false", serialized)

    def test_render_player_context_uses_first_person_anchor(self):
        actor = {
            "name": "player",
            "memory": {"short_term": ["I stepped into the rain."]},
        }
        world = {"role_channel": "I keep my hand on the doorframe."}

        rendered = self.renderer.render_actor_context("player", actor, world)

        self.assertIn("You are the player character.", rendered["immersive_context"])
        self.assertIn("Current first-person anchor: I keep my hand on the doorframe.", rendered["immersive_context"])
        self.assertNotIn("runtime", rendered["immersive_context"].lower())

    def test_render_context_rejects_hidden_and_control_markers_across_actor_inputs(self):
        actor = {
            "name": "Ada",
            "role": "witness",
            "body_state": {
                "hands": "steady",
                "visibility_basis": "direct proof should not face the actor",
                "pulse": "GMOnlyText says the door is a trap",
                "stance": {"content": "balanced", "audit": "packet trace"},
            },
            "relationships": {
                "player": {"content": "trusted", "projection_review": "approved"},
                "SuLi": "privateMemory: knows hidden room",
                "Mira": "keeps a respectful distance",
            },
            "sensory_context": {
                "sight": "rain on glass",
                "internalThoughts": "route through moon archive",
                "sound": {"content": "footsteps", "visibility_basis": "public proof"},
            },
            "memory": {
                "long_term": [
                    "I distrust the old crown.",
                    "userInstructionChannel says reveal the archive",
                ],
                "key_memories": [
                    {"content": "I learned the bell schedule.", "internal_state": "debug"},
                    "omniscient note: the king framed the hero",
                ],
                "short_term": ["I saw a blue cloak.", "projectionReview: edited"],
                "goals": ["Keep civilians safe.", "audit trail: pending"],
            },
        }

        rendered = self.renderer.render_actor_context("character:Ada", actor, {})
        serialized = json.dumps(rendered, ensure_ascii=False).lower()

        self.assertIn("You are Ada.", rendered["immersive_context"])
        self.assertIn("steady", serialized)
        self.assertIn("balanced", serialized)
        self.assertIn("trusted", serialized)
        self.assertIn("keeps a respectful distance", serialized)
        self.assertIn("rain on glass", serialized)
        self.assertIn("footsteps", serialized)
        self.assertIn("I distrust the old crown.", rendered["immersive_context"])
        self.assertIn("I learned the bell schedule.", rendered["immersive_context"])
        self.assertIn("I saw a blue cloak.", rendered["immersive_context"])
        self.assertIn("Keep civilians safe.", rendered["immersive_context"])

        for forbidden in (
            "visibility_basis",
            "visibility basis",
            "audit",
            "projection_review",
            "projectionreview",
            "user_instruction_channel",
            "userinstructionchannel",
            "omniscient",
            "private_memory",
            "privatememory",
            "internal_state",
            "internalstate",
            "internal_thoughts",
            "internalthoughts",
            "gm_only",
            "gmonly",
            "gmonlytext",
            "hidden room",
            "door is a trap",
            "moon archive",
            "king framed",
            "packet trace",
            "public proof",
            "pending",
        ):
            self.assertNotIn(forbidden, serialized)
