import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_prompts():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(
        "agent_prompts",
        ROOT / "skills" / "agent_prompts.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GmSkillContractsTest(unittest.TestCase):
    def read(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_gm_agent_references_required_subskills(self):
        text = self.read(".claude/skills/rp-gm-agent.md")
        for name in (
            "rp-gm-visibility-policy",
            "rp-gm-actor-routing",
            "rp-gm-promotion-policy",
        ):
            self.assertIn(name, text)

    def test_visibility_policy_blocks_hidden_fact_hints_in_actor_calls(self):
        text = self.read(".claude/skills/rp-gm-visibility-policy.md")
        self.assertIn("actor_calls[].prompt", text)
        self.assertIn("actor_calls[].reason", text)
        self.assertIn("must not contain hidden facts, foreshadowing hints, or euphemistic substitutes", text)
        self.assertIn("second-person visible situation only", text)

    def test_visibility_policy_requires_structured_visibility_proof(self):
        text = self.read(".claude/skills/rp-gm-visibility-policy.md")
        self.assertIn("actor_calls[].visibility_basis", text)
        for field in (
            "scene_id",
            "location",
            "time_window",
            "visible_to",
            "sensory_channels",
            "source_actor",
            "target_actor",
            "visibility_basis",
        ):
            self.assertIn(field, text)
        self.assertIn("If visibility cannot be proven", text)

    def test_promotion_policy_allows_only_preprocess_and_gm_sources(self):
        text = self.read(".claude/skills/rp-gm-promotion-policy.md")
        self.assertIn("Allowed promotion sources: preprocess, gm", text)
        self.assertIn("subGM agents must not create or promote important characters", text)

    def test_actor_prompts_forbid_profile_edits(self):
        agent_prompts = _load_agent_prompts()
        combined = "\n".join([
            agent_prompts._player_prompt({"immersive_context": "我是当前角色。"}),
            agent_prompts._character_prompt({"character_name": "Ada", "immersive_context": "我是 Ada。"}),
        ])
        self.assertIn("我可以自然地说出自己想记住的事或当前目标", combined)
        self.assertIn("不修改人设、背景、人格、身体事实或权威设定", combined)

    def test_actor_routing_skill_defines_executable_parallel_groups(self):
        text = self.read(".claude/skills/rp-gm-actor-routing.md")
        self.assertIn("Executable Parallel Groups", text)
        self.assertIn("The runtime scheduler may execute safe groups concurrently", text)
        self.assertIn("downgrade unsafe groups to serial routing", text)
        self.assertIn("rejected before batching", text)
        self.assertNotIn("`parallel_groups` is metadata only", text.lower())

    def test_actor_routing_requires_per_call_visibility_basis(self):
        text = self.read(".claude/skills/rp-gm-actor-routing.md")
        self.assertIn("visibility_basis", text)
        self.assertIn("per call", text)

    def test_gm_routing_uses_natural_language_perception_continuation(self):
        text = self.read(".claude/skills/rp-gm-actor-routing.md")
        self.assertIn("natural-language `actor_calls[].prompt`", text)
        self.assertIn("visible sensory feedback only", text)
        self.assertIn("Do not use `perceive_request`", text)
        self.assertIn("pending perception fields", text)

    def test_gm_routing_documents_natural_language_dialogue_transfer(self):
        text = self.read(".claude/skills/rp-gm-actor-routing.md")
        self.assertIn("next `actor_calls[].prompt` as natural language", text)
        self.assertIn("Do not use dialogue-transfer metadata fields", text)
        self.assertIn("quote the visible spoken words", text)
        self.assertIn("Never transfer private intent", text)

    def test_visibility_policy_documents_perception_feedback_boundary(self):
        text = self.read(".claude/skills/rp-gm-visibility-policy.md")
        self.assertIn("Perception Feedback", text)
        self.assertIn("natural-language `actor_calls[].prompt`", text)
        self.assertIn("do not use `perceive_request`", text)
        self.assertIn("must not reveal hidden causality", text)

    def test_story_skill_forbids_invented_important_character_replies(self):
        text = self.read(".claude/skills/rp-story-agent.md")
        self.assertIn("actor-authored natural-language dialogue", text)
        self.assertIn("must not invent", text)
        self.assertTrue(
            "important character" in text or "core character" in text,
            "story skill must name important/core character reply constraints",
        )

    def test_actor_prompts_require_natural_language_reply(self):
        agent_prompts = _load_agent_prompts()
        combined = "\n".join([
            agent_prompts._player_prompt({"immersive_context": "我是当前角色。"}),
            agent_prompts._character_prompt({"character_name": "Ada", "immersive_context": "我是 Ada。"}),
        ])
        self.assertIn("我直接用自然语言", combined)
        self.assertIn("不写 JSON", combined)
        for forbidden in (
            "custom_action",
            "perceive_request",
            "visible_content",
            "stop_for_player_decision",
            "role_channel",
        ):
            self.assertNotIn(forbidden, combined)

    def test_context_projector_preserves_false_beliefs_as_immersive_memory(self):
        text = self.read(".claude/skills/rp-context-projector.md")

        self.assertIn("immersive_context", text)
        self.assertIn("subjective_memory", text)
        self.assertIn(
            "Never tell an actor that a belief is a misconception. Preserve false beliefs as in-world subjective memory.",
            text,
        )
        self.assertNotIn("own misconceptions", text)
        self.assertNotIn("- `misconceptions`", text)

    def test_gm_actor_requests_use_immersive_second_person_labels(self):
        text = self.read(".claude/skills/rp-gm-agent.md")

        self.assertIn("immersive second-person natural language", text)
        self.assertIn("objective world truth", text)
        self.assertIn("target actor memory, perception, training, and in-world reports", text)
        self.assertIn("appearance-level or belief-level label", text)

    def test_subgm_actor_requests_use_immersive_second_person_labels(self):
        text = self.read(".claude/skills/rp-subgm-agent.md")

        self.assertIn("immersive second-person natural language", text)
        self.assertIn("objective world truth", text)
        self.assertIn("target actor memory, perception, training, and in-world reports", text)
        self.assertIn("appearance-level or belief-level label", text)

    def test_projection_docs_preserve_final_actor_message_boundary(self):
        readme = self.read("README.md")
        projector = self.read(".claude/skills/rp-context-projector.md")
        projection_agent = self.read(".claude/skills/rp-projection-agent.md")

        self.assertIn("final_actor_message", projection_agent)
        self.assertIn("final_actor_message", readme)
        self.assertIn("final_actor_message", projector)
        self.assertIn("agent_projection.project_actor_context", readme)
        self.assertIn("actor context rendering", readme)
        self.assertIn("projection/rendering", projector)

    def test_actor_prompts_do_not_receive_misconceptions_label(self):
        agent_prompts = _load_agent_prompts()
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp)
            for actor_name, profile in (
                ("雨蒙", "我只能相信自己感知到的事。\n"),
                ("Ada", "我有自己的记忆、信念和感官。\n"),
            ):
                actor_dir = card / "characters" / actor_name
                actor_dir.mkdir(parents=True)
                (actor_dir / "profile.md").write_text(profile, encoding="utf-8")
                (actor_dir / "long_term_memories.md").write_text("", encoding="utf-8")
                (actor_dir / "key_memories.json").write_text('{"memories":[]}', encoding="utf-8")
                (actor_dir / "short_term_memories.md").write_text("", encoding="utf-8")
            (card / "characters" / "player.md").write_text(
                "name: 雨蒙\npath: characters/雨蒙\n",
                encoding="utf-8",
            )
            combined = "\n".join([
                agent_prompts._player_prompt({"card_folder": str(card), "actor_id": "player"}),
                agent_prompts._character_prompt(
                    {
                        "card_folder": str(card),
                        "actor_id": "character:Ada",
                        "character_name": "Ada",
                    }
                ),
            ])

        self.assertNotIn("misconceptions", combined)
        self.assertIn("我只能相信自己感知到的事。", combined)
        self.assertIn("我有自己的记忆、信念和感官。", combined)
        self.assertIn("我是 雨蒙。", combined)
        self.assertNotIn("我是 _self。", combined)
        self.assertIn("我不把不可感知的设定", combined)

    def test_delivery_skill_documents_post_round_memory_jobs(self):
        text = self.read(".claude/skills/rp-delivery.md")
        self.assertIn("post-round actor memory jobs", text)
        self.assertIn("degraded_memory_state", text)
        self.assertIn("must not remove already delivered prose", text)

    def test_gm_and_subgm_schema_examples_require_actor_call_visibility_basis(self):
        combined = "\n".join([
            self.read(".claude/skills/rp-gm-agent.md"),
            self.read(".claude/skills/rp-subgm-agent.md"),
        ])

        self.assertIn('"visibility_basis"', combined)
        self.assertIn('"mode": "direct"', combined)
        self.assertIn('"summary": "why this actor can perceive or receive this prompt"', combined)
        self.assertIn('"summary": "why this actor can perceive or receive this side prompt"', combined)
        self.assertIn('"target_actor": "character:Ada"', combined)

    def test_gm_actor_call_visibility_can_use_character_private_self_knowledge(self):
        text = self.read(".claude/skills/rp-gm-agent.md")

        self.assertIn("character private self-knowledge", text)
        self.assertIn("角色私有自知", text)
        self.assertIn("not public world knowledge", text)

    def test_gm_agent_forbids_repeated_passive_observation_calls(self):
        text = self.read(".claude/skills/rp-gm-agent.md")

        self.assertIn("Do not repeatedly call the same actor", text)
        self.assertIn("passive observation", text)
        self.assertIn("new visible stimulus", text)
        self.assertIn("stop_reason", text)

    def test_gm_agent_requires_player_actor_before_player_decision(self):
        text = self.read(".claude/skills/rp-gm-agent.md")

        self.assertIn("never act as the player agent", text)
        self.assertIn("never act as any important character agent", text)
        self.assertIn("Before the player actor has responded in this loop", text)
        self.assertIn("GM may serve as the actor's senses", text)
        self.assertIn("pain, itch, dizziness", text)
        self.assertIn("Do not write the actor's new voluntary actions", text)
        self.assertIn("do not write their seat as empty", text)
        self.assertIn("Do not set `stop_reason: \"player_decision\"` in the same GM output", text)
        self.assertIn("player_decision requires a prior player actor response", text)
        self.assertIn("actor_id\": \"player\"", text)
        self.assertIn("send it to postprocess as one of the player-facing action options", text)

    def test_actor_routing_stop_reasons_match_schema(self):
        text = self.read(".claude/skills/rp-gm-actor-routing.md")
        for reason in (
            "`continue`",
            "`player_decision`",
            "`word_target`",
            "`complete`",
            "`max_steps`",
        ):
            self.assertIn(reason, text)
        self.assertNotIn("`blocked`", text)

    def test_postprocess_skill_owns_frontend_data_not_progress(self):
        text = self.read(".claude/skills/rp-postprocess-agent.md")

        self.assertIn("You generate frontend support data after the critic has approved the story.", text)
        self.assertIn("Do not rewrite story prose.", text)
        self.assertIn("Do not review prose quality.", text)
        self.assertIn("Do not write progress.json.", text)
        for input_name in (
            "story.input.json",
            "story.output.json",
            "critic.report.json",
            "interaction.trace.json",
            "ui_manifest.json",
            "generated asset metadata",
            "pending postprocess repair queue",
            "current state.js values",
        ):
            self.assertIn(input_name, text)
        for field in (
            "schema_version",
            "core.summary",
            "core.options",
            "core.current_goal",
            "core.state_patch",
            "ui_extensions",
            "ui_extension_status",
            "repair_requests",
            "metadata",
        ):
            self.assertIn(field, text)
        self.assertIn("source=player_agent_critical_action", text)
        self.assertIn("requires_confirmation=true", text)
        self.assertIn("must not leak hidden facts", text)
        self.assertIn("must not write `<content>`, `<summary>`, or `<options>` tags", text)

    def test_story_and_critic_skills_do_not_own_postprocess_fields(self):
        story = self.read(".claude/skills/rp-story-agent.md")
        critic = self.read(".claude/skills/rp-critic-agent.md")
        delivery = self.read(".claude/skills/rp-delivery.md")
        orchestrator = self.read(".claude/skills/rp-orchestrator.md")

        self.assertIn("Do not emit `<summary>`; postprocess owns summary.", story)
        self.assertIn("Do not emit `<options>`; postprocess owns action options.", story)
        self.assertNotIn("<summary>...</summary><options>...</options>", story)
        self.assertIn("Frontend data is out of critic scope.", critic)
        self.assertIn("Do not review, request, generate, or repair summary, options, current_goal, state patches, status panels, or UI extension data.", critic)
        self.assertIn("Critic reviews the story body and source-backed character dialogue only.", critic)
        self.assertIn("Postprocess is required before delivery.", delivery)
        self.assertIn("valid `postprocess.output.json.core`", delivery)
        self.assertIn("Invalid UI extension data is nonblocking only when a repair record is written", delivery)
        self.assertIn("After critic `pass`, dispatch `run_postprocess`.", orchestrator)
        self.assertIn("Only after postprocess core validates should dispatcher create `deliver_round`.", orchestrator)
