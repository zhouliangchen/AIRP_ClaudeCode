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
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def test_maps_legacy_assets_route_to_capability_request(self):
        legacy = {
            "id": "route-001",
            "type": "assets_ui_task",
            "source_channel": "user_instruction",
            "summary": "Create a rainy street image.",
            "target": "assets-ui",
            "payload": {"prompt": "rainy street"},
            "requires_authorization": False,
            "authorization_gate": "none",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "make an image"},
        }

        mapped = self.registry.legacy_routing_request_to_capability(legacy)

        self.assertEqual(mapped["id"], "route-001")
        self.assertEqual(mapped["requested_by"], "input_analyst")
        self.assertEqual(mapped["capability"], "assets.generate_image")
        self.assertEqual(mapped["authorization_gate"], "none")

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
