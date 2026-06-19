import importlib.util
import json
import sys
import tempfile
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


class CharacterPromotionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.card.mkdir()
        (self.card / ".card_data.json").write_text(json.dumps({
            "character_orchestration": {"major": []}
        }, ensure_ascii=False), encoding="utf-8")
        self.promotions = load_module("character_promotions")

    def tearDown(self):
        self.tmp.cleanup()

    def test_gm_promotion_creates_profile_and_major_registration(self):
        result = self.promotions.apply_promotions(self.card, [{
            "name": "SuLi",
            "source_agent": "gm",
            "reason": "She now drives the pendant scene.",
            "profile_seed": "Cold classmate with occult expertise.",
            "visibility": "character_private_and_gm",
            "activation": "current_turn",
        }], round_id="round-000003")
        self.assertEqual(result["promoted"], ["SuLi"])
        data = json.loads((self.card / ".card_data.json").read_text(encoding="utf-8"))
        self.assertIn("SuLi", data["character_orchestration"]["major"])
        self.assertTrue((self.card / "memory" / "characters" / "SuLi" / "profile.json").exists())
        profile_md = (self.card / "memory" / "characters" / "SuLi" / "profile.md").read_text(encoding="utf-8")
        self.assertIn("## GM-Originated Promotion Seed", profile_md)
        self.assertIn("- source: character_promotion", profile_md)
        self.assertIn("- player_authoritative: false", profile_md)
        self.assertNotIn("## Authoritative Player Setting", profile_md)

    def test_subgm_promotion_is_rejected(self):
        with self.assertRaisesRegex(self.promotions.CharacterPromotionError, "subGM"):
            self.promotions.apply_promotions(self.card, [{
                "name": "Side NPC",
                "source_agent": "subGM:thread-1",
                "reason": "subGM wants promotion",
                "profile_seed": "not allowed",
                "visibility": "character_private_and_gm",
            }], round_id="round-000003")

    def test_gm_promotion_preserves_existing_preprocess_profile(self):
        preprocess = self.promotions.apply_promotions(self.card, [{
            "name": "SuLi",
            "source_agent": "preprocess",
            "reason": "Player declared her as central.",
            "profile_seed": "Player-authored authoritative profile.",
            "visibility": "character_private_and_gm",
            "activation": "current_turn",
        }], round_id="round-000002")
        self.assertEqual(preprocess["promoted"], ["SuLi"])
        profile_path = self.card / "memory" / "characters" / "SuLi" / "profile.json"
        profile_md_path = self.card / "memory" / "characters" / "SuLi" / "profile.md"
        before = json.loads(profile_path.read_text(encoding="utf-8"))
        before_md = profile_md_path.read_text(encoding="utf-8")
        self.assertIn("## Authoritative Player Setting", before_md)
        self.assertIn("- source: input_analysis", before_md)

        gm = self.promotions.apply_promotions(self.card, [{
            "name": "SuLi",
            "source_agent": "gm",
            "reason": "GM wants a thinner profile.",
            "profile_seed": "Weaker GM-only summary.",
            "visibility": "character_private_and_gm",
            "activation": "current_turn",
        }], round_id="round-000003")

        after = json.loads(profile_path.read_text(encoding="utf-8"))
        after_md = profile_md_path.read_text(encoding="utf-8")
        self.assertEqual(gm["promoted"], [])
        self.assertEqual(after["authoritative_setting"], before["authoritative_setting"])
        self.assertEqual(after.get("source_agent"), "preprocess")
        self.assertEqual(after_md, before_md)

    def test_gm_promotion_preserves_existing_player_profile(self):
        char_dir = self.card / "memory" / "characters" / "SuLi"
        char_dir.mkdir(parents=True)
        profile = {
            "name": "SuLi",
            "source": "player",
            "source_agent": "player",
            "authoritative_setting": "Player-authored profile.",
        }
        (char_dir / "profile.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (char_dir / "profile.md").write_text("Player-authored profile.", encoding="utf-8")

        self.promotions.apply_promotions(self.card, [{
            "name": "SuLi",
            "source_agent": "gm",
            "reason": "GM wants a thinner profile.",
            "profile_seed": "Weaker GM-only summary.",
            "visibility": "character_private_and_gm",
            "activation": "current_turn",
        }], round_id="round-000003")

        after = json.loads((char_dir / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(after["authoritative_setting"], "Player-authored profile.")
        self.assertEqual(after["source_agent"], "player")


if __name__ == "__main__":
    unittest.main()
