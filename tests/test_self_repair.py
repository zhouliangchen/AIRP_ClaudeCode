import importlib.util
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("self_repair", ROOT / "skills" / "self_repair.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SelfRepairPolicyTest(unittest.TestCase):
    def setUp(self):
        self.self_repair = _load_module()

    def test_default_policy_is_limited_and_does_not_allow_source_code_edits(self):
        policy = self.self_repair.load_policy(settings={}, environ={})

        self.assertEqual(policy.mode, "limited")
        self.assertEqual(policy.story_preflight_attempts, 1)
        self.assertEqual(policy.delivery_repair_attempts, 1)
        self.assertFalse(policy.repair_critic_block)
        self.assertFalse(policy.repair_round_progression)
        self.assertFalse(policy.allow_source_code_self_repair)

    def test_chinese_mode_alias_and_env_override_are_normalized(self):
        policy = self.self_repair.load_policy(
            settings={"selfRepairMode": "完全修复"},
            environ={"AIRP_SELF_REPAIR_MODE": "仅分析定位"},
        )

        self.assertEqual(policy.mode, "analysis_only")
        self.assertEqual(policy.delivery_repair_attempts, 0)
        self.assertFalse(policy.can_auto_repair)

    def test_full_policy_can_repair_progression_but_source_code_requires_second_switch(self):
        policy = self.self_repair.load_policy(
            settings={"selfRepairMode": "full", "allowSourceCodeSelfRepair": True},
            environ={},
        )

        self.assertEqual(policy.mode, "full")
        self.assertEqual(policy.story_preflight_attempts, 3)
        self.assertEqual(policy.delivery_repair_attempts, 3)
        self.assertTrue(policy.repair_critic_block)
        self.assertTrue(policy.repair_round_progression)
        self.assertTrue(policy.allow_source_code_self_repair)

    def test_repair_routing_defaults_to_story_composition(self):
        routing = self.self_repair.normalize_repair_routing({})

        self.assertEqual(routing["stage"], "story_composition")
        self.assertEqual(routing["rollback"], "story_only")
        self.assertEqual(routing["target_agents"], ["story"])
        self.assertEqual(routing["risk"], "low")


if __name__ == "__main__":
    unittest.main()
