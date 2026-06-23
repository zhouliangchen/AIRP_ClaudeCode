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


def _read_first_audit(run_dir, result):
    return _read_json(Path(run_dir) / result["results"][0]["artifact"])


class InputRoutingRequestsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "card" / ".agent_runs" / "round-000001"
        self.run_dir.mkdir(parents=True)
        self.mod = _load("input_routing_requests")
        self.intents = _load("agent_intents")
        self.messages = _load("agent_messages")

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
        artifact = _read_first_audit(self.run_dir, result)
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

    def test_process_assets_ui_task_is_idempotent_for_existing_audit_artifact(self):
        request = {
            "id": "route-repeat",
            "type": "assets_ui_task",
            "source_channel": "user_instruction",
            "summary": "Create a rainy street image.",
            "target": "assets-ui",
            "payload": {"kind": "scene", "target": "scene_illustration", "prompt": "rainy street"},
            "requires_authorization": False,
            "authorization_gate": "none",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "make an image"},
        }

        first = self.mod.process_routing_requests(
            self.run_dir,
            [request],
            runtime_settings={"selfRepairMode": "off", "allowSourceCodeSelfRepair": False},
            source_intent_id="intent_000001",
        )
        second = self.mod.process_routing_requests(
            self.run_dir,
            [request],
            runtime_settings={"selfRepairMode": "off", "allowSourceCodeSelfRepair": False},
            source_intent_id="intent_000001",
        )

        pending_assets = [item for item in self.intents.list_intents(self.run_dir, "pending") if item["type"] == "assets_task"]
        self.assertEqual(len(pending_assets), 1)
        self.assertEqual(second["created_intents"], [pending_assets[0]["id"]])
        self.assertEqual(second["results"][0]["created_intents"], [pending_assets[0]["id"]])
        routing_messages = [
            item for item in self.messages.read_messages(self.run_dir) if item.get("type") == "routing_request"
        ]
        self.assertEqual(len(routing_messages), 1)
        artifact = _read_first_audit(self.run_dir, first)
        self.assertEqual(artifact["created_intent_ids"], first["created_intents"])

    def test_path_shaping_request_ids_do_not_collide_with_literal_underscore_ids(self):
        requests = [
            {
                "id": "route/x",
                "type": "assets_ui_task",
                "source_channel": "user_instruction",
                "summary": "Create a bridge image.",
                "target": "assets-ui",
                "payload": {"kind": "scene", "target": "bridge", "prompt": "misty bridge"},
                "requires_authorization": False,
                "authorization_gate": "none",
                "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "draw bridge"},
            },
            {
                "id": "route_x",
                "type": "assets_ui_task",
                "source_channel": "user_instruction",
                "summary": "Create a street image.",
                "target": "assets-ui",
                "payload": {"kind": "scene", "target": "street", "prompt": "rainy street"},
                "requires_authorization": False,
                "authorization_gate": "none",
                "evidence": {"semantic_unit_ids": ["u2"], "raw_excerpt": "draw street"},
            },
            {
                "id": "route_x002F_x",
                "type": "assets_ui_task",
                "source_channel": "user_instruction",
                "summary": "Create a tower image.",
                "target": "assets-ui",
                "payload": {"kind": "scene", "target": "tower", "prompt": "silver tower"},
                "requires_authorization": False,
                "authorization_gate": "none",
                "evidence": {"semantic_unit_ids": ["u3"], "raw_excerpt": "draw tower"},
            },
        ]

        result = self.mod.process_routing_requests(
            self.run_dir,
            requests,
            runtime_settings={"selfRepairMode": "off", "allowSourceCodeSelfRepair": False},
            source_intent_id="intent_000001",
        )

        pending_assets = [item for item in self.intents.list_intents(self.run_dir, "pending") if item["type"] == "assets_task"]
        self.assertEqual(len(pending_assets), 3)
        routing_messages = [
            item for item in self.messages.read_messages(self.run_dir) if item.get("type") == "routing_request"
        ]
        self.assertEqual(len(routing_messages), 3)
        artifact_files = sorted((self.run_dir / "artifacts" / "input_routing_requests").glob("*.json"))
        self.assertEqual(len(artifact_files), 3)
        self.assertEqual(len({item.name for item in artifact_files}), 3)
        self.assertEqual(result["created_intents_count"], 3)

    def test_long_request_id_uses_bounded_audit_filename_and_remains_idempotent(self):
        request = {
            "id": "route-" + "a" * 260,
            "type": "assets_ui_task",
            "source_channel": "user_instruction",
            "summary": "Create a panorama.",
            "target": "assets-ui",
            "payload": {"kind": "scene", "target": "panorama", "prompt": "wide canyon"},
            "requires_authorization": False,
            "authorization_gate": "none",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "draw panorama"},
        }

        first = self.mod.process_routing_requests(
            self.run_dir,
            [request],
            runtime_settings={"selfRepairMode": "off", "allowSourceCodeSelfRepair": False},
            source_intent_id="intent_000001",
        )
        second = self.mod.process_routing_requests(
            self.run_dir,
            [request],
            runtime_settings={"selfRepairMode": "off", "allowSourceCodeSelfRepair": False},
            source_intent_id="intent_000001",
        )

        pending_assets = [item for item in self.intents.list_intents(self.run_dir, "pending") if item["type"] == "assets_task"]
        self.assertEqual(len(pending_assets), 1)
        artifact_files = list((self.run_dir / "artifacts" / "input_routing_requests").glob("*.json"))
        self.assertEqual(len(artifact_files), 1)
        self.assertLess(len(artifact_files[0].name), 120)
        self.assertEqual(second["created_intents"], first["created_intents"])
        self.assertEqual(second["created_intents"], [pending_assets[0]["id"]])

    def test_retry_after_attach_failure_reuses_pre_audit_intent_and_message(self):
        request = {
            "id": "route-attach-retry",
            "type": "assets_ui_task",
            "source_channel": "user_instruction",
            "summary": "Create a rainy street image.",
            "target": "assets-ui",
            "payload": {"kind": "scene", "target": "scene_illustration", "prompt": "rainy street"},
            "requires_authorization": False,
            "authorization_gate": "none",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "make an image"},
        }
        original_attach = self.mod.agent_intents.attach_source_message

        def fail_attach(_run_dir, _intent_id, _message_id):
            return {"ok": False, "reason": "injected_failure"}

        self.mod.agent_intents.attach_source_message = fail_attach
        try:
            with self.assertRaisesRegex(RuntimeError, "injected_failure"):
                self.mod.process_routing_requests(
                    self.run_dir,
                    [request],
                    runtime_settings={"selfRepairMode": "off", "allowSourceCodeSelfRepair": False},
                    source_intent_id="intent_000001",
                )
        finally:
            self.mod.agent_intents.attach_source_message = original_attach

        self.assertEqual(list((self.run_dir / "artifacts" / "input_routing_requests").glob("*.json")), [])
        pending_assets = [item for item in self.intents.list_intents(self.run_dir, "pending") if item["type"] == "assets_task"]
        self.assertEqual(len(pending_assets), 1)
        routing_messages = [
            item for item in self.messages.read_messages(self.run_dir) if item.get("type") == "routing_request"
        ]
        self.assertEqual(len(routing_messages), 1)

        result = self.mod.process_routing_requests(
            self.run_dir,
            [request],
            runtime_settings={"selfRepairMode": "off", "allowSourceCodeSelfRepair": False},
            source_intent_id="intent_000001",
        )

        pending_assets_after_retry = [
            item for item in self.intents.list_intents(self.run_dir, "pending") if item["type"] == "assets_task"
        ]
        routing_messages_after_retry = [
            item for item in self.messages.read_messages(self.run_dir) if item.get("type") == "routing_request"
        ]
        self.assertEqual(len(pending_assets_after_retry), 1)
        self.assertEqual(len(routing_messages_after_retry), 1)
        artifact = _read_first_audit(self.run_dir, result)
        self.assertEqual(artifact["created_intent_ids"], [pending_assets[0]["id"]])
        self.assertEqual(artifact["created_message_ids"], [routing_messages[0]["id"]])
        self.assertEqual(pending_assets_after_retry[0]["source_message_id"], routing_messages[0]["id"])

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
        artifact = _read_first_audit(self.run_dir, result)
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
        artifact = _read_first_audit(self.run_dir, result)
        self.assertEqual(artifact["status"], "audit_only")
        self.assertFalse((self.run_dir.parents[1] / "memory" / "characters" / "Ada").exists())
