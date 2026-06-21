import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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

    def test_actor_skills_forbid_profile_edits(self):
        combined = "\n".join([
            self.read(".claude/skills/rp-player-agent.md"),
            self.read(".claude/skills/rp-character-agent.md"),
        ])
        self.assertIn("may update memory and goals", combined)
        self.assertIn("must not modify profile, background, personality, body facts, or authoritative settings", combined)

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

    def test_gm_routing_requires_perception_response_closure(self):
        text = self.read(".claude/skills/rp-gm-actor-routing.md")
        self.assertIn("pending_perception_requests[]", text)
        self.assertIn("perception_responses[]", text)
        self.assertIn('status: "answered"', text)
        self.assertIn('status: "closed"', text)

    def test_gm_routing_documents_structured_dialogue_transfer_fields(self):
        text = self.read(".claude/skills/rp-gm-actor-routing.md")
        self.assertIn("exact_visible_words", text)
        self.assertIn("delivery_channel", text)
        self.assertIn("visible_tone_or_action", text)
        self.assertIn("Never transfer private intent", text)

    def test_visibility_policy_documents_perception_feedback_boundary(self):
        text = self.read(".claude/skills/rp-gm-visibility-policy.md")
        self.assertIn("Perception Feedback", text)
        self.assertIn("perception_responses[].content", text)
        self.assertIn("visibility_basis", text)
        self.assertIn("must not reveal hidden causality", text)

    def test_story_skill_forbids_invented_important_character_replies(self):
        text = self.read(".claude/skills/rp-story-agent.md")
        self.assertIn("dialogue_transfer", text)
        self.assertIn("must not invent", text)
        self.assertTrue(
            "important character" in text or "core character" in text,
            "story skill must name important/core character reply constraints",
        )

    def test_actor_skills_define_bounded_custom_action(self):
        combined = "\n".join([
            self.read(".claude/skills/rp-player-agent.md"),
            self.read(".claude/skills/rp-character-agent.md"),
        ])
        self.assertIn("custom_action", combined)
        self.assertIn('top-level `target`', combined)
        self.assertIn("metadata.visible_content", combined)
        self.assertIn("risk_level", combined)
        self.assertIn("high or critical", combined)

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
