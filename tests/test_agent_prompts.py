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
        self.assertIn("Only `final_actor_message` can be delivered to the actor", prompt)
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
        self.assertNotIn("Allowed `routing_requests[].type` values", prompt)


if __name__ == "__main__":
    unittest.main()
