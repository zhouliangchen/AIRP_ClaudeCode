import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_round_prepare():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("round_prepare", ROOT / "skills" / "round_prepare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RoundPrepareHelperTest(unittest.TestCase):
    def test_grep_reference_section_returns_indexed_section_content(self):
        round_prepare = _load_round_prepare()
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp)
            memory = card / "memory"
            memory.mkdir()
            (memory / "reference.md").write_text(
                "# Reference\n\n## 佐天泪子\nline one\nline two\n\n## 初春饰利\nline three\n",
                encoding="utf-8",
            )

            sections = round_prepare._load_reference_sections(card)

        self.assertEqual(round_prepare.grep_reference_section(sections, "佐天泪子"), "line one\nline two")
        self.assertEqual(round_prepare.grep_reference_section(sections, "## 初春饰利"), "line three")
        self.assertEqual(round_prepare.grep_reference_section(sections, "missing"), "")

    def test_build_character_contexts_keeps_all_explicit_major_characters_when_parallel_cap_is_two(self):
        round_prepare = _load_round_prepare()
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp)
            for name in ("Ada", "Bert", "Cora"):
                char_dir = card / "memory" / "characters" / name
                char_dir.mkdir(parents=True)
                (char_dir / "profile.md").write_text(f"{name} profile.", encoding="utf-8")

            result = round_prepare.build_character_contexts(
                card,
                {
                    "character_orchestration": {
                        "major": ["Ada", "Bert", "Cora"],
                        "max_parallel_subagents": 2,
                    }
                },
                {},
                [],
                "Ada looks toward the door.",
            )

        self.assertEqual(
            [item["name"] for item in result["characters"]],
            ["Ada", "Bert", "Cora"],
        )
        self.assertEqual(result["characters"][0]["scene_relevance"], "normal")
        self.assertEqual(result["characters"][1]["scene_relevance"], "normal")
        self.assertEqual(result["characters"][2]["profile_summary"], "Cora profile.")

    def test_build_character_contexts_keeps_blank_self_and_all_explicit_major_characters(self):
        round_prepare = _load_round_prepare()
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp)
            result = round_prepare.build_character_contexts(
                card,
                {
                    "mode": "blank_bootstrap",
                    "character_orchestration": {
                        "major": ["Ada", "Bert", "Cora"],
                        "max_parallel_subagents": 2,
                    },
                },
                {},
                [],
                "Bert listens.",
            )

        self.assertEqual(
            [item["name"] for item in result["characters"]],
            ["_self", "Ada", "Bert", "Cora"],
        )
        self.assertEqual(result["characters"][0]["scene_relevance"], "high")
        self.assertEqual(result["characters"][2]["scene_relevance"], "normal")

    def test_build_character_contexts_keeps_passive_card_structure_fallback_small(self):
        round_prepare = _load_round_prepare()
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp)
            result = round_prepare.build_character_contexts(
                card,
                {"character_orchestration": {"max_parallel_subagents": 2}},
                {"characters": {"Ada": {}, "Bert": {}, "Cora": {}}},
                [],
                "",
            )

        self.assertEqual(
            [item["name"] for item in result["characters"]],
            ["Ada", "Bert"],
        )
