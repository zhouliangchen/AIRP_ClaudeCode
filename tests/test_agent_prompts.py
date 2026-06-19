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


class AgentPromptsTest(unittest.TestCase):
    def setUp(self):
        self.agent_prompts = load_module("agent_prompts")

    def test_subgm_prompt_references_skill_and_forbids_gm_authority(self):
        prompt = self.agent_prompts.subgm_prompt_text({"thread_id": "side_a"})

        self.assertIn(".claude/skills/rp-subgm-agent.md", prompt)
        self.assertIn('"thread_id": "side_a"', prompt)
        self.assertIn("no `character_promotions`", prompt)
        self.assertIn("no `subgm_commands`", prompt)
        self.assertIn("no player participation", prompt)


if __name__ == "__main__":
    unittest.main()
