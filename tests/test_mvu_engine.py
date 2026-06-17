import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_mvu_engine():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("mvu_engine", ROOT / "skills" / "mvu_engine.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MvuEngineTest(unittest.TestCase):
    def setUp(self):
        self.mvu = _load_mvu_engine()

    def test_extract_update_variable_allows_omitted_jsonpatch_closing_tag(self):
        text = """
<content><p>scene</p></content>
<UpdateVariable>
<Analysis>
- scene advanced
</Analysis>
<JSONPatch>
[
  {"op":"replace","path":"/世界/时间","value":"清晨"},
  {"op":"replace","path":"/同桌/当前状况","value":"正在吐槽"}
]
</UpdateVariable>
"""

        commands = self.mvu.extract_commands(text)

        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0].type, "set")
        self.assertEqual(commands[0].args[0], "世界.时间")
        self.assertEqual(commands[1].args[0], "同桌.当前状况")


if __name__ == "__main__":
    unittest.main()
