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

    def test_input_analysis_prompt_describes_assets_ui_planning_payload(self):
        prompts = _load_module("agent_prompts")

        text = prompts._input_analyst_prompt({})

        self.assertIn("assets.generate_image", text)
        self.assertIn("asset_requirement", text)
        self.assertIn("reference_policy", text)
        self.assertIn("characters", text)
        self.assertIn("scene_illustration_each_round", text)

    def test_input_analyst_prompt_advertises_replay_capability_gates(self):
        prompts = _load_module("agent_prompts")

        text = prompts._input_analyst_prompt({})

        self.assertIn("`replay.execute`", text)
        self.assertIn("`replay.execute -> replay`", text)
        self.assertIn(
            "`replay.plan` and `replay.execute` must use `authorization_gate: \"none\"`",
            text,
        )
        self.assertIn("continuity retcon / rollback replay", text)
        self.assertIn(
            "must not rely only on `narrative_directives.rewrite_previous_output`",
            text,
        )
        self.assertIn("emit `replay.plan`", text)
        self.assertIn("paired `replay.execute`", text)
        self.assertIn("same `plan_id`", text)
        self.assertIn("`backup_id`, `affected_inputs`, and `plan_id`", text)
        self.assertIn("`replay.plan` materializes a `.replay` session", text)
        self.assertIn("`replay.execute` executes or resumes", text)
        self.assertIn("`retcon.consult` remains consultation-only", text)
        self.assertNotIn("blocked/not-wired", text)
        self.assertNotIn(
            "`replay.plan` and `card.patch_data` require `manual_confirmation`",
            text,
        )

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

    def test_prompts_enforce_important_actor_source_authority(self):
        prompts = _load_module("agent_prompts")

        gm_text = prompts._gm_prompt({})
        story_text = prompts._story_prompt({"story_input": {"loop_outputs": {"actors": {}}}})
        critic_text = prompts._critic_prompt({
            "story_input": {"loop_outputs": {"actors": {}}},
            "story_output": {"content": ""},
        })

        self.assertIn("Important actor authority", gm_text)
        self.assertIn("must not compose direct dialogue", gm_text)
        self.assertIn("complete second-person recap through `actor_calls[].prompt`", gm_text)
        self.assertIn("source-backed by `story_input.loop_outputs.actors`", story_text)
        self.assertIn("GM `scene_beats`, GM `events`, and GM actor-call prompts are not actor sources", story_text)
        self.assertIn("Unsupported important-actor dialogue/action must be omitted", story_text)
        self.assertIn("hard failure that requires story revision", critic_text)
        self.assertIn("not a soft issue", critic_text)


if __name__ == "__main__":
    unittest.main()
