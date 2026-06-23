import importlib.util
import json
import sys
import tempfile
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


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class InputRoutingRequestsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "card" / ".agent_runs" / "round-000001"
        self.run_dir.mkdir(parents=True)
        self.mod = _load("input_routing_requests")
        self.intents = _load("agent_intents")

    def tearDown(self):
        self.tmp.cleanup()

    def test_process_assets_ui_task_creates_assets_intent_and_audit_artifact(self):
        request = {
            "id": "route-001",
            "type": "assets_ui_task",
            "source_channel": "user_instruction",
            "summary": "Create a rainy street image.",
            "target": "assets-ui",
            "payload": {"kind": "scene", "target": "scene_illustration", "prompt": "rainy street"},
            "requires_authorization": False,
            "authorization_gate": "none",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "make an image"},
        }

        result = self.mod.process_routing_requests(
            self.run_dir,
            [request],
            runtime_settings={"selfRepairMode": "off", "allowSourceCodeSelfRepair": False},
            source_intent_id="intent_000001",
        )

        self.assertEqual(result["created_intents_count"], 1)
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual(pending[0]["type"], "assets_task")
        self.assertEqual(pending[0]["payload"]["prompt"], "rainy street")
        artifact = _read_json(self.run_dir / "artifacts" / "input_routing_requests" / "route-001.json")
        self.assertEqual(artifact["status"], "queued")

    def test_assets_ui_task_default_source_intent_policy_keeps_empty_source_id(self):
        request = {
            "id": "route-default",
            "type": "assets_ui_task",
            "source_channel": "user_instruction",
            "summary": "Create a rainy street image.",
            "target": "assets-ui",
            "payload": {"prompt": "rainy street"},
            "requires_authorization": False,
            "authorization_gate": "none",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "make an image"},
        }

        self.mod.process_routing_requests(
            self.run_dir,
            [request],
            runtime_settings={"selfRepairMode": "off", "allowSourceCodeSelfRepair": False},
        )

        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual(
            pending[0]["policy"],
            {"source_intent_id": "", "routing_request_id": "route-default"},
        )

    def test_source_request_without_gate_writes_authorization_required_without_system_intent(self):
        request = {
            "id": "route-002",
            "type": "source_feature_request",
            "source_channel": "user_instruction",
            "summary": "Add save export.",
            "target": "main_agent",
            "payload": {"feature": "save_export"},
            "requires_authorization": True,
            "authorization_gate": "allowSourceCodeSelfRepair",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "add export"},
        }

        result = self.mod.process_routing_requests(
            self.run_dir,
            [request],
            runtime_settings={"selfRepairMode": "full", "allowSourceCodeSelfRepair": False},
            source_intent_id="intent_000001",
        )

        self.assertEqual(result["created_intents_count"], 0)
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        artifact = _read_json(self.run_dir / "artifacts" / "input_routing_requests" / "route-002.json")
        self.assertEqual(artifact["status"], "authorization_required")
        self.assertFalse(artifact["authorization"]["allowSourceCodeSelfRepair"])

    def test_source_request_with_gate_creates_system_request_even_when_self_repair_off(self):
        request = {
            "id": "route-003",
            "type": "source_feature_request",
            "source_channel": "user_instruction",
            "summary": "Add save export.",
            "target": "main_agent",
            "payload": {"feature": "save_export"},
            "requires_authorization": True,
            "authorization_gate": "allowSourceCodeSelfRepair",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "add export"},
        }

        result = self.mod.process_routing_requests(
            self.run_dir,
            [request],
            runtime_settings={"selfRepairMode": "off", "allowSourceCodeSelfRepair": True},
            source_intent_id="intent_000001",
        )

        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual(result["created_intents_count"], 1)
        self.assertEqual(pending[0]["type"], "system_request")
        self.assertEqual(pending[0]["payload"]["reason"], "user_requested_source_feature")
        self.assertFalse(pending[0]["payload"]["selfRepairMode_required"])

    def test_card_data_edit_is_audit_only_and_does_not_write_card_files(self):
        request = {
            "id": "route-004",
            "type": "card_data_edit",
            "source_channel": "user_instruction",
            "summary": "Change a character title.",
            "target": "character:Ada",
            "payload": {"field": "title", "value": "Captain"},
            "requires_authorization": False,
            "authorization_gate": "none",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "Ada is Captain"},
        }

        result = self.mod.process_routing_requests(
            self.run_dir,
            [request],
            runtime_settings={"selfRepairMode": "full", "allowSourceCodeSelfRepair": True},
            source_intent_id="intent_000001",
        )

        self.assertEqual(result["created_intents_count"], 0)
        artifact = _read_json(self.run_dir / "artifacts" / "input_routing_requests" / "route-004.json")
        self.assertEqual(artifact["status"], "audit_only")
        self.assertFalse((self.run_dir.parents[1] / "memory" / "characters" / "Ada").exists())
