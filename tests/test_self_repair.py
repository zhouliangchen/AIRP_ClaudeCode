import importlib.util
import json
import os
import sys
import tempfile
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
        self.assertFalse(hasattr(policy, "story_" + "pre" + "flight_attempts"))
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
        self.assertFalse(hasattr(policy, "story_" + "pre" + "flight_attempts"))
        self.assertEqual(policy.delivery_repair_attempts, 3)
        self.assertTrue(policy.repair_critic_block)
        self.assertTrue(policy.repair_round_progression)
        self.assertTrue(policy.allow_source_code_self_repair)

    def test_policy_settings_file_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            payload = json.dumps(
                {"selfRepairMode": "full", "allowSourceCodeSelfRepair": True},
                ensure_ascii=False,
            )
            settings_path.write_bytes(payload.encode("utf-8-sig"))

            policy = self.self_repair.load_policy(settings_path, environ={})

        self.assertEqual(policy.mode, "full")
        self.assertTrue(policy.repair_round_progression)
        self.assertTrue(policy.allow_source_code_self_repair)

    def test_repair_routing_defaults_to_story_composition(self):
        routing = self.self_repair.normalize_repair_routing({})

        self.assertEqual(routing["stage"], "story_composition")
        self.assertEqual(routing["rollback"], "story_only")
        self.assertEqual(routing["target_agents"], ["story"])
        self.assertEqual(routing["risk"], "low")

    def test_delivery_result_word_count_reason_is_not_delivery_gate(self):
        routing = self.self_repair.routing_from_delivery_result(
            {"action": "retry", "reason": "word_count"}
        )

        self.assertEqual(routing["stage"], "story_composition")
        self.assertEqual(routing["rollback"], "story_only")
        self.assertEqual(routing["target_agents"], ["story"])

    def test_delivery_result_mechanical_reason_routes_to_delivery_gate(self):
        routing = self.self_repair.routing_from_delivery_result(
            {"action": "retry", "reason": "agent_outputs"}
        )

        self.assertEqual(routing["stage"], "delivery_gate")
        self.assertEqual(routing["rollback"], "story_only")
        self.assertEqual(routing["target_agents"], ["story"])


if __name__ == "__main__":
    unittest.main()
