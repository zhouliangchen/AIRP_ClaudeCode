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

    def test_process_capability_request_creates_assets_intent_and_capability_audit(self):
        request = {
            "id": "cap-assets",
            "requested_by": "input_analyst",
            "target": "assets-ui",
            "capability": "assets.generate_image",
            "summary": "Create a rainy street image.",
            "reason": "User asked for a visual update.",
            "source_channel": "user_instruction",
            "risk": "low",
            "authorization_gate": "none",
            "payload": {"kind": "scene", "target": "scene_illustration", "prompt": "rainy street"},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "make an image"},
        }

        result = self.mod.process_capability_requests(
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
        self.assertEqual(artifact["capability"], "assets.generate_image")
        self.assertEqual(artifact["status"], "queued")

    def test_process_character_rename_capability_creates_rename_intent(self):
        request = {
            "id": "rename-player",
            "requested_by": "input_analyst",
            "target": "memory",
            "capability": "character.rename",
            "summary": "Rename player placeholder to 雨蒙.",
            "reason": "The protagonist name became explicit.",
            "source_channel": "user_instruction",
            "risk": "medium",
            "authorization_gate": "none",
            "payload": {"from_name": "player", "to_name": "雨蒙", "actor_id": "player"},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "我的名字叫雨蒙"},
        }

        result = self.mod.process_capability_requests(
            self.run_dir,
            [request],
            runtime_settings={},
            source_intent_id="input_analysis",
        )

        self.assertEqual(result["created_intents_count"], 1)
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual(pending[0]["type"], "character_rename")
        self.assertEqual(pending[0]["payload"]["from_name"], "player")
        self.assertEqual(pending[0]["payload"]["to_name"], "雨蒙")
        self.assertEqual(pending[0]["payload"]["actor_id"], "player")

    def test_unknown_capability_writes_audit_and_message_without_intent(self):
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

        result = self.mod.process_capability_requests(
            self.run_dir,
            [request],
            runtime_settings={"allowSourceCodeSelfRepair": False},
            source_intent_id="intent_000001",
        )

        self.assertEqual(result["created_intents_count"], 0)
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        messages = self.messages.read_messages(self.run_dir)
        self.assertEqual(messages[0]["type"], "unsupported_capability")
        artifact = _read_first_audit(self.run_dir, result)
        self.assertEqual(artifact["status"], "unsupported_capability")

    def test_registry_mismatch_statuses_are_preserved_in_audit_and_message(self):
        requests = [
            {
                "id": "cap-target-mismatch",
                "requested_by": "input_analyst",
                "target": "gm",
                "capability": "assets.generate_image",
                "summary": "Create a rainy street image.",
                "reason": "User asked for a visual update.",
                "source_channel": "user_instruction",
                "risk": "low",
                "authorization_gate": "none",
                "payload": {"prompt": "rainy street"},
                "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "make an image"},
            },
            {
                "id": "cap-gate-mismatch",
                "requested_by": "input_analyst",
                "target": "main-agent",
                "capability": "source.change_request",
                "summary": "Add save export.",
                "reason": "User asked for source work.",
                "source_channel": "user_instruction",
                "risk": "high",
                "authorization_gate": "none",
                "payload": {"feature": "save_export"},
                "evidence": {"semantic_unit_ids": ["u2"], "raw_excerpt": "add export"},
            },
        ]

        result = self.mod.process_capability_requests(
            self.run_dir,
            requests,
            runtime_settings={"allowSourceCodeSelfRepair": True},
            source_intent_id="intent_000001",
        )

        self.assertEqual(result["created_intents_count"], 0)
        self.assertEqual([item["status"] for item in result["results"]], [
            "target_mismatch",
            "authorization_gate_mismatch",
        ])
        messages = self.messages.read_messages(self.run_dir)
        self.assertEqual([item["type"] for item in messages], [
            "target_mismatch",
            "authorization_gate_mismatch",
        ])
        for item in result["results"]:
            artifact = _read_json(Path(self.run_dir) / item["artifact"])
            self.assertEqual(artifact["status"], item["status"])

    def test_replay_plan_requires_manual_confirmation_and_does_not_create_intent_without_it(self):
        request = {
            "id": "cap-replay",
            "requested_by": "input_analyst",
            "target": "replay",
            "capability": "replay.plan",
            "summary": "Plan a replay from the previous round.",
            "reason": "Player reframed the previous answer as a dream.",
            "source_channel": "user_instruction",
            "risk": "high",
            "authorization_gate": "manual_confirmation",
            "payload": {
                "snapshot_id": "round-000001-20260623T000000000000Z-abc123def456",
                "affected_rounds": ["round-000001"],
                "preserved_player_input_ids": ["input-1"],
                "discard_ai_artifacts": ["gm.output.json", "story.input.json"],
            },
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "previous scene was a dream"},
        }

        result = self.mod.process_capability_requests(
            self.run_dir,
            [request],
            runtime_settings={"allowSourceCodeSelfRepair": False},
            source_intent_id="intent_000001",
        )

        self.assertEqual(result["created_intents_count"], 0)
        self.assertEqual(result["created_messages_count"], 1)
        self.assertEqual(result["results"][0]["status"], "authorization_required")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        messages = self.messages.read_messages(self.run_dir)
        self.assertEqual(messages[0]["type"], "authorization_required")
        self.assertEqual(messages[0]["payload"]["capability"], "replay.plan")
        artifact = _read_first_audit(self.run_dir, result)
        self.assertEqual(artifact["status"], "authorization_required")
        self.assertEqual(artifact["authorization"]["authorization_gate"], "manual_confirmation")
        self.assertEqual(artifact["created_intent_ids"], [])
        self.assertEqual(artifact["created_message_ids"], [messages[0]["id"]])

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
            {"source_intent_id": "", "capability_request_id": "route-default"},
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
            item for item in self.messages.read_messages(self.run_dir) if item.get("type") == "capability_request"
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
            item for item in self.messages.read_messages(self.run_dir) if item.get("type") == "capability_request"
        ]
        self.assertEqual(len(routing_messages), 3)
        artifact_files = sorted((self.run_dir / "artifacts" / "capability_requests").glob("*.json"))
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
        artifact_files = list((self.run_dir / "artifacts" / "capability_requests").glob("*.json"))
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

        self.assertEqual(list((self.run_dir / "artifacts" / "capability_requests").glob("*.json")), [])
        pending_assets = [item for item in self.intents.list_intents(self.run_dir, "pending") if item["type"] == "assets_task"]
        self.assertEqual(len(pending_assets), 1)
        routing_messages = [
            item for item in self.messages.read_messages(self.run_dir) if item.get("type") == "capability_request"
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
            item for item in self.messages.read_messages(self.run_dir) if item.get("type") == "capability_request"
        ]
        self.assertEqual(len(pending_assets_after_retry), 1)
        self.assertEqual(len(routing_messages_after_retry), 1)
        artifact = _read_first_audit(self.run_dir, result)
        self.assertEqual(artifact["created_intent_ids"], [pending_assets[0]["id"]])
        self.assertEqual(artifact["created_message_ids"], [routing_messages[0]["id"]])
        self.assertEqual(pending_assets_after_retry[0]["source_message_id"], routing_messages[0]["id"])

    def test_retry_after_audit_write_failure_reuses_side_effects_across_source_intent_ids(self):
        request = {
            "id": "route-recreated-analysis",
            "type": "assets_ui_task",
            "source_channel": "user_instruction",
            "summary": "Create a rainy street image.",
            "target": "assets-ui",
            "payload": {"kind": "scene", "target": "scene_illustration", "prompt": "rainy street"},
            "requires_authorization": False,
            "authorization_gate": "none",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "make an image"},
        }
        original_write_json = self.mod.agent_run.write_json
        failure_injected = {"done": False}

        def fail_first_audit_write(path, payload):
            if not failure_injected["done"] and "capability_requests" in str(path):
                failure_injected["done"] = True
                raise OSError("injected audit failure")
            return original_write_json(path, payload)

        self.mod.agent_run.write_json = fail_first_audit_write
        try:
            with self.assertRaisesRegex(OSError, "injected audit failure"):
                self.mod.process_routing_requests(
                    self.run_dir,
                    [request],
                    runtime_settings={"selfRepairMode": "off", "allowSourceCodeSelfRepair": False},
                    source_intent_id="intent_000001",
                )
        finally:
            self.mod.agent_run.write_json = original_write_json

        self.assertEqual(list((self.run_dir / "artifacts" / "capability_requests").glob("*.json")), [])
        pending_assets = [item for item in self.intents.list_intents(self.run_dir, "pending") if item["type"] == "assets_task"]
        self.assertEqual(len(pending_assets), 1)
        routing_messages = [
            item for item in self.messages.read_messages(self.run_dir) if item.get("type") == "capability_request"
        ]
        self.assertEqual(len(routing_messages), 1)

        result = self.mod.process_routing_requests(
            self.run_dir,
            [request],
            runtime_settings={"selfRepairMode": "off", "allowSourceCodeSelfRepair": False},
            source_intent_id="intent_000002",
        )

        pending_assets_after_retry = [
            item for item in self.intents.list_intents(self.run_dir, "pending") if item["type"] == "assets_task"
        ]
        routing_messages_after_retry = [
            item for item in self.messages.read_messages(self.run_dir) if item.get("type") == "capability_request"
        ]
        self.assertEqual(len(pending_assets_after_retry), 1)
        self.assertEqual(len(routing_messages_after_retry), 1)
        artifact = _read_first_audit(self.run_dir, result)
        self.assertEqual(artifact["created_intent_ids"], [pending_assets[0]["id"]])
        self.assertEqual(artifact["created_message_ids"], [routing_messages[0]["id"]])

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

    def test_card_data_edit_requires_authorization_and_does_not_write_card_files(self):
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
        self.assertEqual(artifact["status"], "authorization_required")
        self.assertFalse((self.run_dir.parents[1] / "memory" / "characters" / "Ada").exists())
