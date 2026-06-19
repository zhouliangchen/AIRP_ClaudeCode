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


def promotion(source_agent):
    return {
        "name": "Side NPC",
        "source_agent": source_agent,
        "reason": "subGM suggestion",
        "profile_seed": "not allowed",
        "visibility": "character_private_and_gm",
        "activation": "current_turn",
    }


def gm_output_with_promotion(source_agent):
    return {
        "agent": "gm",
        "scene_beats": [],
        "events": [],
        "actor_calls": [],
        "parallel_groups": [],
        "world_state_delta": [],
        "character_promotions": [promotion(source_agent)],
        "decision_point": None,
        "stop_reason": "continue",
    }


class SubgmPermissionsTest(unittest.TestCase):
    def setUp(self):
        self.schemas = load_module("agent_schemas")
        self.promotions = load_module("character_promotions")

    def test_schema_rejects_subgm_character_promotion_source(self):
        with self.assertRaisesRegex(self.schemas.ValidationError, "subGM"):
            self.schemas.validate_gm_output(gm_output_with_promotion("subGM:thread-1"))

    def test_character_promotions_reject_subgm_source(self):
        with self.assertRaisesRegex(self.promotions.CharacterPromotionError, "subGM"):
            self.promotions.validate_promotion(promotion("subGM:thread-1"), "character_promotions[0]")

    def test_old_gm_assistant_source_is_rejected_with_current_subgm_wording(self):
        with self.assertRaisesRegex(self.promotions.CharacterPromotionError, "subGM"):
            self.promotions.validate_promotion(promotion("gm_assistant:thread-1"), "character_promotions[0]")

    def test_preprocess_and_gm_sources_remain_allowed(self):
        self.assertEqual(
            self.promotions.validate_promotion(promotion("preprocess"), "character_promotions[0]")["source_agent"],
            "preprocess",
        )
        gm_normalized = self.schemas.validate_gm_output(gm_output_with_promotion("gm"))
        self.assertEqual(gm_normalized["character_promotions"][0]["source_agent"], "gm")


if __name__ == "__main__":
    unittest.main()
