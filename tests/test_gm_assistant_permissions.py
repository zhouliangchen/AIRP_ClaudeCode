import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = str(ROOT / "skills")


def load_module(name):
    if SKILLS_DIR not in sys.path:
        sys.path.insert(0, SKILLS_DIR)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def promotion(source_agent):
    return {
        "name": "Side NPC",
        "source_agent": source_agent,
        "reason": "The character now needs independent agency.",
        "profile_seed": "A recurring side character with a durable goal.",
        "visibility": "character_private_and_gm",
        "activation": "current_turn",
    }


def gm_output(source_agent):
    return {
        "agent": "gm",
        "scene_beats": [{"content": "The side corridor goes quiet."}],
        "events": [],
        "actor_calls": [],
        "parallel_groups": [],
        "world_state_delta": [],
        "character_promotions": [promotion(source_agent)],
        "decision_point": None,
        "stop_reason": "continue",
    }


class LegacyGmAssistantPermissionsTest(unittest.TestCase):
    def setUp(self):
        self.agent_schemas = load_module("agent_schemas")
        self.character_promotions = load_module("character_promotions")
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.card.mkdir()
        (self.card / ".card_data.json").write_text(
            json.dumps({"character_orchestration": {"major": []}}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_agent_schema_rejects_legacy_gm_assistant_character_promotion(self):
        with self.assertRaisesRegex(
            self.agent_schemas.ValidationError,
            "subGM.*promotion_request.*main GM",
        ):
            self.agent_schemas.validate_gm_output(gm_output("gm_assistant:thread-1"))

    def test_character_promotions_reject_legacy_gm_assistant_source(self):
        record = promotion("gm_assistant:thread-1")

        with self.assertRaisesRegex(
            self.character_promotions.CharacterPromotionError,
            "subGM.*promotion_request.*main GM",
        ):
            self.character_promotions.validate_promotion(record, "character_promotions[0]")

        with self.assertRaisesRegex(
            self.character_promotions.CharacterPromotionError,
            "subGM.*promotion_request.*main GM",
        ):
            self.character_promotions.apply_promotions(
                self.card,
                [record],
                round_id="round-000001",
            )

    def test_preprocess_and_gm_sources_remain_allowed_for_direct_promotions(self):
        records = [
            dict(promotion("preprocess"), name="PlayerDeclared"),
            dict(promotion("gm"), name="GmPromoted"),
        ]

        normalized = [
            self.character_promotions.validate_promotion(record, f"character_promotions[{index}]")
            for index, record in enumerate(records)
        ]
        self.assertEqual([record["source_agent"] for record in normalized], ["preprocess", "gm"])

        result = self.character_promotions.apply_promotions(
            self.card,
            records,
            round_id="round-000001",
        )
        self.assertEqual(result["registered"], ["PlayerDeclared", "GmPromoted"])

        gm_normalized = self.agent_schemas.validate_gm_output(gm_output("gm"))
        self.assertEqual(gm_normalized["character_promotions"][0]["source_agent"], "gm")

        with self.assertRaisesRegex(self.agent_schemas.ValidationError, "source_agent.*gm"):
            self.agent_schemas.validate_gm_output(gm_output("preprocess"))

    def test_policy_documents_subgm_request_only_record_and_legacy_rejection(self):
        policy = (ROOT / ".claude" / "skills" / "rp-gm-promotion-policy.md").read_text(encoding="utf-8")

        self.assertIn('"type": "promotion_request"', policy)
        self.assertIn('"candidate_name": "Side NPC"', policy)
        self.assertIn('"source_agent": "subGM:thread-1"', policy)
        self.assertIn("must not be applied directly", policy)
        self.assertIn("Only the main GM may turn it into a promotion record", policy)
        self.assertIn("Legacy `gm_assistant:*` sources", policy)


if __name__ == "__main__":
    unittest.main()
