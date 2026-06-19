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
        self.assertNotIn("metadata only", text.lower())
