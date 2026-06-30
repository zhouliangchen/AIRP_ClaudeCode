import copy
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    from tests.module_aliases import load_repo_module
    return load_repo_module(name)


class CapabilityRegistryTest(unittest.TestCase):
    def setUp(self):
        self.registry = _load("capability_registry")

    def test_normalizes_current_capability_request(self):
        request = {
            "id": "cap-001",
            "requested_by": "input_analyst",
            "target": "assets-ui",
            "capability": "assets.generate_image",
            "summary": "Create a rainy street image.",
            "reason": "Player requested a visual update.",
            "source_channel": "user_instruction",
            "risk": "low",
            "authorization_gate": "none",
            "payload": {"kind": "scene", "target": "scene_illustration", "prompt": "rainy street"},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "make an image"},
        }

        normalized = self.registry.normalize_capability_request(request)

        self.assertEqual(normalized["id"], "cap-001")
        self.assertEqual(normalized["capability"], "assets.generate_image")
        self.assertEqual(normalized["target"], "assets-ui")
        self.assertEqual(normalized["action"], "intent")
        self.assertEqual(normalized["intent_type"], "assets_task")
        self.assertEqual(normalized["status"], "recognized")

    def test_normalizes_assets_capability_name_used_as_target(self):
        request = {
            "id": "cap-assets-alias",
            "requested_by": "input_analyst",
            "target": "assets.generate_image",
            "capability": "assets.generate_image",
            "summary": "Create a classroom illustration.",
            "reason": "User requested a story image.",
            "source_channel": "user_instruction",
            "risk": "low",
            "authorization_gate": "none",
            "payload": {"asset_requirement": {"scene_illustration_each_round": True}},
            "evidence": {"raw_excerpt": "每一轮必须提供一张剧情插图"},
        }

        normalized = self.registry.normalize_capability_request(request)

        self.assertEqual(normalized["target"], "assets-ui")
        self.assertEqual(normalized["status"], "recognized")
        self.assertEqual(normalized["intent_type"], "assets_task")

    def test_character_rename_capability_allows_input_analyst_and_gm(self):
        base = {
            "id": "rename-player",
            "target": "memory",
            "capability": "character.rename",
            "summary": "Rename unnamed player placeholder to 雨蒙.",
            "reason": "The protagonist name became explicit.",
            "source_channel": "user_instruction",
            "risk": "medium",
            "authorization_gate": "none",
            "payload": {"from_name": "player", "to_name": "雨蒙", "actor_id": "player"},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "我的名字叫雨蒙"},
        }

        for requested_by, source_channel in (("input_analyst", "user_instruction"), ("gm", "gm_output")):
            with self.subTest(requested_by=requested_by):
                request = dict(base, requested_by=requested_by, source_channel=source_channel)
                normalized = self.registry.normalize_capability_request(request)

                self.assertEqual(normalized["status"], "recognized")
                self.assertEqual(normalized["action"], "intent")
                self.assertEqual(normalized["intent_type"], "character_rename")

    def test_unknown_capability_becomes_audit_action(self):
        request = {
            "id": "cap-unknown",
            "requested_by": "input_analyst",
            "target": "weather",
            "capability": "external.weather_lookup",
            "summary": "Look up weather.",
            "reason": "User asked for weather.",
            "source_channel": "user_instruction",
            "risk": "low",
            "authorization_gate": "none",
            "payload": {},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "weather"},
        }

        normalized = self.registry.normalize_capability_request(request)

        self.assertEqual(normalized["status"], "unsupported_capability")
        self.assertEqual(normalized["action"], "audit_only")
        self.assertEqual(normalized["capability"], "external.weather_lookup")

    def test_source_change_requires_authorization(self):
        request = {
            "id": "cap-source",
            "requested_by": "input_analyst",
            "target": "main-agent",
            "capability": "source.change_request",
            "summary": "Add save export.",
            "reason": "User explicitly requested source work.",
            "source_channel": "user_instruction",
            "risk": "high",
            "authorization_gate": "allowSourceCodeSelfRepair",
            "payload": {"feature": "save_export"},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "add export"},
        }

        blocked = self.registry.authorize_capability(
            self.registry.normalize_capability_request(request),
            runtime_settings={"allowSourceCodeSelfRepair": False},
        )
        allowed = self.registry.authorize_capability(
            self.registry.normalize_capability_request(request),
            runtime_settings={"allowSourceCodeSelfRepair": True},
        )

        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["status"], "authorization_required")
        self.assertTrue(allowed["allowed"])
        self.assertEqual(allowed["status"], "authorized")

    def test_manual_confirmation_cannot_be_authorized_by_runtime_setting(self):
        request = {
            "id": "cap-manual",
            "requested_by": "input_analyst",
            "target": "card-data",
            "capability": "card.patch_data",
            "summary": "Patch card data.",
            "reason": "User asked to adjust card metadata.",
            "source_channel": "user_instruction",
            "risk": "high",
            "authorization_gate": "manual_confirmation",
            "payload": {"field": "name"},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "patch card"},
        }

        result = self.registry.authorize_capability(
            self.registry.normalize_capability_request(request),
            runtime_settings={"manual_confirmation": True},
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["status"], "authorization_required")
        self.assertEqual(result["authorization_gate"], "manual_confirmation")

    def test_rejects_requester_risk_and_gate_mismatches_as_audit_only(self):
        requester = {
            "id": "cap-requester",
            "requested_by": "assets",
            "target": "assets-ui",
            "capability": "assets.generate_image",
            "summary": "Create image.",
            "reason": "Requester is not allowed.",
            "source_channel": "user_instruction",
            "risk": "low",
            "authorization_gate": "none",
            "payload": {},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "make image"},
        }
        risk = dict(requester, id="cap-risk", requested_by="input_analyst", risk="critical")
        gate = dict(
            requester,
            id="cap-gate",
            requested_by="input_analyst",
            capability="source.change_request",
            target="main-agent",
            authorization_gate="none",
            risk="high",
        )

        requester_result = self.registry.normalize_capability_request(requester)
        risk_result = self.registry.normalize_capability_request(risk)
        gate_result = self.registry.normalize_capability_request(gate)

        self.assertEqual(requester_result["status"], "requester_not_allowed")
        self.assertEqual(requester_result["action"], "audit_only")
        self.assertEqual(risk_result["status"], "risk_exceeds_capability")
        self.assertEqual(risk_result["action"], "audit_only")
        self.assertEqual(gate_result["status"], "authorization_gate_mismatch")
        self.assertEqual(gate_result["action"], "audit_only")

    def test_rejects_target_mismatch_as_audit_only(self):
        request = {
            "id": "cap-target",
            "requested_by": "input_analyst",
            "target": "main-agent",
            "capability": "assets.generate_image",
            "summary": "Create image.",
            "reason": "Target does not match capability.",
            "source_channel": "user_instruction",
            "risk": "low",
            "authorization_gate": "none",
            "payload": {},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "make image"},
        }

        normalized = self.registry.normalize_capability_request(request)

        self.assertEqual(normalized["status"], "target_mismatch")
        self.assertEqual(normalized["action"], "audit_only")
        self.assertEqual(normalized["expected_target"], "assets-ui")

    def test_normalization_does_not_mutate_or_share_request_payloads(self):
        request = {
            "id": "cap-copy",
            "requested_by": "input_analyst",
            "target": "assets-ui",
            "capability": "assets.generate_image",
            "summary": "Create a scene image.",
            "reason": "Player requested a visual update.",
            "source_channel": "user_instruction",
            "risk": "low",
            "authorization_gate": "none",
            "payload": {"nested": {"prompt": "rainy street"}},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "make an image"},
        }
        original = copy.deepcopy(request)

        normalized = self.registry.normalize_capability_request(request)
        normalized["payload"]["nested"]["prompt"] = "changed"

        self.assertEqual(request, original)


if __name__ == "__main__":
    unittest.main()
