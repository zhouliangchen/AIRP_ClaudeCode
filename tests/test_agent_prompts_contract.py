import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    from tests.module_aliases import load_repo_module
    return load_repo_module(name)


class AgentPromptsContractTest(unittest.TestCase):
    def test_runtime_prompt_contracts_live_under_agent_packages(self):
        prompts = _load_module("agent_prompts")

        self.assertFalse(hasattr(prompts, "SKILL_PATHS"))
        contract_paths = prompts.PROMPT_CONTRACT_PATHS
        for agent_key in ("input_analyst", "gm", "subgm", "projection", "story", "critic", "postprocess"):
            with self.subTest(agent_key=agent_key):
                relative = contract_paths[agent_key]
                self.assertTrue(relative.startswith("skills/agents/"), relative)
                self.assertNotIn(".claude/skills", relative)
                self.assertTrue((ROOT / relative).exists(), relative)

        self.assertTrue((ROOT / ".claude" / "skills" / "rp.md").exists())
        self.assertTrue((ROOT / ".claude" / "skills" / "rp-orchestrator.md").exists())
        self.assertFalse((ROOT / ".claude" / "skills" / "rp-gm-agent.md").exists())
        self.assertFalse((ROOT / ".claude" / "skills" / "rp-story-agent.md").exists())

    def test_gm_prompt_explicitly_includes_policy_contracts(self):
        prompts = _load_module("agent_prompts")

        text = prompts._gm_prompt({})

        self.assertIn("Prompt contract reference: `skills/agents/gm/prompts/contract.md`", text)
        self.assertIn("## Prompt Contract", text)
        self.assertIn("## Additional GM Policy Contracts", text)
        self.assertIn("appearance-level or belief-level label", text)
        self.assertIn("Executable Parallel Groups", text)
        self.assertIn("subGM agents must not create or promote important characters", text)
        self.assertNotIn("## Skill Body", text)
        self.assertNotIn("You are a Claude Code subagent", text)

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
        self.assertIn("character_appearances", text)
        self.assertIn("art_style", text)
        self.assertIn("scene_illustration_each_round", text)

    def test_postprocess_prompt_uses_chinese_confirmation_prefix(self):
        prompts = _load_module("agent_prompts")

        text = prompts.build_postprocess_prompt({})

        self.assertIn('"label": "确认行动：玩家已经明确提出的可见行动"', text)
        self.assertNotIn('"label": "Confirm action: visible player action"', text)

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
        subgm_text = prompts._subgm_prompt({})
        story_text = prompts._story_prompt({"story_input": {"loop_outputs": {"actors": {}}}})
        critic_text = prompts._critic_prompt({
            "story_input": {"loop_outputs": {"actors": {}}},
            "story_output": {"content": ""},
        })

        self.assertIn("Important actor authority", gm_text)
        self.assertIn("must not compose direct dialogue", gm_text)
        self.assertIn("Use `actor_calls[].prompt` only for the selected second-person disclosure", gm_text)
        self.assertIn("source-backed by `story_input.loop_outputs.actors`", story_text)
        self.assertIn("GM `scene_beats`, GM `events`, and GM actor-call prompts are not actor sources", story_text)
        self.assertIn("Unsupported important-actor dialogue/action must be omitted", story_text)
        self.assertIn("hard failure that requires story revision", critic_text)
        self.assertIn("not a soft issue", critic_text)

        self.assertIn("Special scene memory disclosure", gm_text)
        self.assertIn("GM temporarily portrays every important character appearing in the special scene", gm_text)
        self.assertIn("unless the plot explicitly requires mind infiltration or direct mental perception", gm_text)
        self.assertIn("judge each appearing important character separately", gm_text)
        self.assertIn("clear memory, vague impression, or no disclosure", gm_text)
        self.assertIn("other person's dream", gm_text)
        self.assertIn("Special scene memory disclosure", subgm_text)
        self.assertIn("report disclosure recommendations to GM", subgm_text)
        self.assertIn("special-scene temporary GM/subGM portrayal is an allowed source", story_text)

    def test_gm_subgm_story_critic_prompts_describe_asset_requests_authority(self):
        prompts = _load_module("agent_prompts")

        gm_text = prompts._gm_prompt({})
        subgm_text = prompts._subgm_prompt({})
        story_text = prompts._story_prompt({"story_input": {}})
        critic_text = prompts._critic_prompt({"story_input": {}, "story_output": {"content": ""}})

        for text in (gm_text, subgm_text, story_text, critic_text):
            self.assertIn("asset_requests[]", text)
            self.assertIn("normally keep it to no more than two images", text)
            self.assertIn("highlight beat", text)
            self.assertIn("character-focused visual study", text)

        self.assertIn("create, modify, or delete assets-ui task requirements", gm_text)
        self.assertIn("create, modify, or delete assets-ui task requirements", story_text)
        self.assertIn("create, modify, or delete assets-ui task requirements", critic_text)
        self.assertIn("subGM may emit top-level `asset_requests[]` only with", subgm_text)
        self.assertIn("subGM must not modify or delete", subgm_text)
        self.assertIn("outside that special scene", story_text)
        self.assertIn("dream-forgotten or another-character dream content", critic_text)


if __name__ == "__main__":
    unittest.main()
