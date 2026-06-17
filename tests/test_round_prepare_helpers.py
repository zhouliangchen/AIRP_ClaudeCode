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
