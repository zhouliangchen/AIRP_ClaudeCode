import importlib.util
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

    def test_subgm_prompt_contract_requires_actor_call_visibility_basis(self):
        prompt = self.agent_prompts.subgm_prompt_text({"thread_id": "side_a"})

        self.assertIn('"visibility_basis"', prompt)
        self.assertIn('"mode": "direct"', prompt)
        self.assertIn('"summary": "why this actor can perceive or receive this side prompt"', prompt)
        self.assertIn("Every `actor_calls[]` item must include valid per-call `visibility_basis.mode` and `visibility_basis.summary`", prompt)

    def test_gm_prompt_contract_requires_actor_call_visibility_basis_mode(self):
        prompt = self.agent_prompts._gm_prompt({})

        self.assertIn('"visibility_basis"', prompt)
        self.assertIn('"mode": "direct"', prompt)
        self.assertIn('"summary": "why this actor can perceive or receive this prompt"', prompt)
        self.assertIn("Every `actor_calls[]` item must include valid per-call `visibility_basis.mode` and `visibility_basis.summary`", prompt)

    def test_gm_prompt_forbids_complete_with_unresolved_active_subgm_threads(self):
        prompt = self.agent_prompts._gm_prompt({})

        self.assertIn("must not set `stop_reason` to `complete` while active subGM side threads remain", prompt)
        self.assertIn("message, accelerate, pause, merge, or close", prompt)

    def test_postprocess_prompt_defines_frontend_data_contract(self):
        prompt = self.agent_prompts.build_postprocess_prompt({
            "postprocess_context": {
                "story.input.json": {"round_id": "round-000001"},
                "current_state": {"quest": "Old goal"},
            }
        })

        self.assertIn("postprocess.output.json", prompt)
        self.assertIn("core.summary", prompt)
        self.assertIn("core.options", prompt)
        self.assertIn("core.current_goal", prompt)
        self.assertIn("mvu.commands", prompt)
        self.assertIn("Do not rewrite story prose", prompt)
        self.assertIn("Do not write progress.json", prompt)
        self.assertIn("Runtime Input JSON", prompt)
        self.assertIn('"quest": "Old goal"', prompt)

    def test_story_prompt_assigns_frontend_data_to_postprocess(self):
        prompt = self.agent_prompts._story_prompt({})

        self.assertIn("Do not write `<summary>` or `<options>` in `story.output.json`", prompt)
        self.assertIn("postprocess owns summary, options, current goal, and frontend data", prompt)
        self.assertIn("Do not write `<UpdateVariable>`", prompt)
        self.assertIn("postprocess owns MVU variable update commands", prompt)
        self.assertIn("do not narrate that action as actually performed", prompt)

    def test_critic_prompt_does_not_require_story_summary_or_options_tags(self):
        prompt = self.agent_prompts._critic_prompt({})

        self.assertIn("does not require `<summary>` or `<options>`", prompt)
        self.assertIn("postprocess owns `core.summary` and `core.options`", prompt)
        self.assertIn(
            "Do not require `<summary>` or `<options>` in `story.output.json`; postprocess owns frontend summary/options",
            prompt,
        )
        self.assertIn("Explicit player-action polarity", prompt)
        self.assertIn("hard-fail if `story_output.content` narrates a player action as completed", prompt)

    def test_write_round_prompts_compacts_gm_prompt_raw_channel_duplicates(self):
        role = "我叫雨蒙，一名普通的高一男生。至少在今天早上之前"
        instruction = "作品基调：日式轻小说风格，青春活力，严肃和欢乐交织的校园非日常喜剧。"
        gm_packet = {
            "agent": "gm",
            "role_channel": role,
            "user_instruction_channel": instruction,
            "components": [
                {"channel": "role", "text": role},
                {"channel": "user_instruction", "text": instruction},
            ],
            "world_state": {
                "role_channel": role,
                "user_instruction_channel": instruction,
                "components": [
                    {"channel": "role", "text": role},
                    {"channel": "user_instruction", "text": instruction},
                ],
                "input_analysis": {
                    "schema_version": 1,
                    "round_id": "round-000001",
                    "analysis_mode": "ai",
                    "source_integrity": {"raw_text_sha256": "hash"},
                    "semantic_units": [
                        {
                            "id": "su-001",
                            "type": "style_guidance",
                            "text": instruction,
                            "raw_excerpt": instruction,
                            "derived_summary": "Use a lively school light-novel tone.",
                            "visibility": "gm_only",
                        }
                    ],
                    "routing": {
                        "role_channel": role,
                        "user_instruction_channel": instruction,
                    },
                    "world_updates": {},
                    "narrative_directives": {},
                },
                "runtime_settings": {"style": "轻松活泼", "wordCount": 4000},
                "style_profile": {"name": "轻松活泼", "content": "short"},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "round-000001"
            self.agent_prompts.write_round_prompts(
                run_dir,
                gm_packet,
                {"agent": "player"},
                {},
                runtime_settings_payload={"style": "轻松活泼", "wordCount": 4000},
            )
            prompt = (run_dir / "prompts" / "gm.prompt.md").read_text(encoding="utf-8")

        self.assertEqual(prompt.count(role), 1)
        self.assertEqual(prompt.count(instruction), 1)
        self.assertNotIn('"components"', prompt)
        self.assertNotIn('"raw_excerpt"', prompt)
        self.assertNotIn('"text": "' + instruction, prompt)
        self.assertIn("Use a lively school light-novel tone.", prompt)

    def test_projection_prompt_contains_contract_context_and_no_reveal_rule(self):
        prompt = self.agent_prompts.projection_prompt_text(
            {
                "target_actor_id": "character:Bob",
                "source_call_id": "call-bob-1",
                "requested_actor_message": "You learn Alice is secretly a vampire.",
                "actor_context": (
                    "You only saw Alice avoid the sun and heard townsfolk call her dangerous."
                ),
            }
        )

        self.assertIn("Projection Agent Prompt", prompt)
        self.assertIn('"decision"', prompt)
        self.assertIn('"final_actor_message"', prompt)
        self.assertIn("character:Bob", prompt)
        self.assertIn("call-bob-1", prompt)
        self.assertIn("avoid the sun", prompt)
        self.assertIn("Do not reveal objective truth to the target actor", prompt)
        self.assertIn("Do not tell the actor that a belief is false", prompt)
        self.assertIn("Only the natural-language `final_actor_message` can be delivered to the actor", prompt)
        self.assertIn("never deliver context packets", prompt)
        self.assertNotIn("false_belief", prompt)

    def test_projection_prompt_includes_existing_skill_body(self):
        prompt = self.agent_prompts.projection_prompt_text(
            {
                "target_actor_id": "character:Bob",
                "source_call_id": "call-bob-1",
            }
        )

        self.assertIn(".claude/skills/rp-projection-agent.md", prompt)
        self.assertNotIn("(missing skill file:", prompt)

    def test_input_analyst_prompt_prefers_capability_requests(self):
        prompt = self.agent_prompts._input_analyst_prompt({
            "round_id": "round-000001",
            "source_integrity": {
                "raw_text_sha256": "raw",
                "role_text_sha256": "role",
                "user_instruction_text_sha256": "instruction",
            },
        })

        self.assertIn('"capability_requests": []', prompt)
        self.assertIn("capability_requests[]", prompt)
        self.assertIn("assets.generate_image", prompt)
        self.assertIn("source.change_request -> main-agent", prompt)
        self.assertIn("assets.generate_image -> assets-ui", prompt)
        self.assertIn("card.patch_data -> card-data", prompt)
        self.assertNotIn("Allowed `routing_requests[].type` values", prompt)

    def test_actor_prompts_keep_role_guidance_immersive_and_first_person(self):
        player_prompt = self.agent_prompts._player_prompt(
            {
                "actor_id": "player",
                "immersive_context": "我是当前扮演的角色。\n我记得：雨声越来越近。",
            }
        )
        character_prompt = self.agent_prompts._character_prompt(
            {
                "actor_id": "character:Ada",
                "character_name": "Ada",
                "immersive_context": "我是 Ada。\n我现在想要：守住门。",
            }
        )

        for prompt in (player_prompt, character_prompt):
            self.assertIn("我是", prompt)
            self.assertIn("我只用自然语言回复", prompt)
            self.assertIn("我的行动准则", prompt)
            for forbidden in (
                '"events"',
                "custom_action",
                "perceive_request",
                "visible_content",
                "stop_for_player_decision",
                "Required Output Contract",
                "JSON 输出契约",
            ):
                self.assertNotIn(forbidden, prompt)
            self.assertNotIn("Skill reference", prompt)
            self.assertNotIn(".claude/skills/", prompt)
            self.assertNotIn("actor.outputs.json", prompt)
            self.assertNotIn("Claude Code", prompt)
            self.assertNotIn("file mailbox", prompt)
            self.assertNotIn("runtime loop", prompt)
            self.assertNotIn("GM resolution", prompt)
            self.assertNotIn("prompts, files", prompt)

    def test_character_prompt_uses_self_knowledge_display_name(self):
        prompt = self.agent_prompts._character_prompt(
            {
                "actor_id": "character:Ada_Zero_",
                "self_knowledge": {"name": "Ada/Zero?"},
                "immersive_context": "我是 Ada/Zero?。\n我正在听门后的动静。",
            }
        )

        self.assertIn("# 我的行动提示：Ada/Zero?", prompt)
        self.assertIn("我是 Ada/Zero?。", prompt)
        self.assertNotIn("character:Ada_Zero_", prompt)


if __name__ == "__main__":
    unittest.main()
