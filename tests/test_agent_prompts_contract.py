import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentPromptsContractTest(unittest.TestCase):
    def test_input_analyst_prompt_requires_player_self_name_declaration_and_rename(self):
        prompts = _load_module("agent_prompts")

        text = prompts._input_analyst_prompt({})

        self.assertIn("current protagonist names themself", text)
        self.assertIn("semantic_units[].type: \"character_declaration\"", text)
        self.assertIn("capability: \"character.rename\"", text)
        self.assertIn("from_name: \"player\"", text)

    def test_story_prompt_contract_requires_derived_content_edits_for_retcon(self):
        prompts = _load_module("agent_prompts")

        text = prompts._story_prompt({
            "story_input": {
                "input_analysis": {
                    "narrative_directives": {
                        "rewrite_previous_output": True,
                    },
                },
            },
        })

        self.assertIn('"derived_content_edits": []', text)
        self.assertIn("rewrite_previous_output", text)
        self.assertIn("must include a non-empty `derived_content_edits` array", text)
        self.assertIn("turn_index", text)
        self.assertIn('"ai"', text)


if __name__ == "__main__":
    unittest.main()
