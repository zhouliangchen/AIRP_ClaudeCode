import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
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
