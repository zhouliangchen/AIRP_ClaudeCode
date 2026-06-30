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
    from tests.module_aliases import load_repo_module
    return load_repo_module(name)


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

    def test_process_agent_asset_request_preserves_requester_and_operation_payload(self):
        request = {
            "id": "story-asset-update",
            "requested_by": "story",
            "target": "assets-ui",
            "capability": "assets.generate_image",
            "summary": "Update the ongoing scene illustration requirement.",
            "reason": "Story found a better visual focus for the round.",
            "source_channel": "story_output",
            "risk": "medium",
            "authorization_gate": "none",
            "payload": {
                "action": "modify",
                "kind": "scene",
                "target": "scene_illustration",
                "prompt": "focus on the lamp and threshold",
                "asset_requirement_key": "scene_illustration_each_round",
            },
            "evidence": {"raw_excerpt": "The lamp reveal is the visual focus."},
        }

        result = self.mod.process_capability_requests(
            self.run_dir,
            [request],
            runtime_settings={},
            source_intent_id="story",
        )

        self.assertEqual(result["created_intents_count"], 1)
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual(pending[0]["requested_by"], "story")
        self.assertEqual(pending[0]["type"], "assets_task")
        self.assertEqual(pending[0]["payload"]["action"], "modify")
        self.assertEqual(pending[0]["payload"]["asset_requirement_key"], "scene_illustration_each_round")

    def test_assets_capability_preserves_planning_payload_fields(self):
        request = {
            "id": "scene-image-rich",
            "requested_by": "input_analyst",
            "target": "assets-ui",
            "capability": "assets.generate_image",
            "summary": "Create a scene image with Su Li.",
            "reason": "The user requested persistent scene illustrations.",
            "source_channel": "user_instruction",
            "risk": "medium",
            "authorization_gate": "none",
            "payload": {
                "kind": "scene_illustration",
                "target": "scene_illustration",
                "prompt": "rainy classroom, Su Li near the window",
                "asset_requirement": {
                    "scene_illustration_each_round": True,
                    "reason": "从本轮开始每轮必须提供剧情插图",
                },
                "characters": ["苏黎"],
                "reference_policy": "required",
                "reference_candidates": ["generated/characters/苏黎/苏黎.png"],
                "art_style": "水彩绘本画风",
                "planner_hints": {"style": "consistent with previous scene"},
                "ui_schema": {"postprocess_data_required": ["ui_extensions.scene"]},
                "postprocess_contract": {
                    "ui_extensions": {"scene": {"type": "object", "required": ["caption"]}}
                },
            },
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "每轮必须提供剧情插图"},
        }

        result = self.mod.process_capability_requests(
            self.run_dir,
            [request],
            runtime_settings={},
            source_intent_id="input_analysis",
        )

        self.assertEqual(len(result["created_intents"]), 1)
        pending = self.intents.list_intents(self.run_dir, "pending")
        payload = pending[0]["payload"]
        self.assertEqual(payload["asset_requirement"]["scene_illustration_each_round"], True)
        self.assertEqual(payload["characters"], ["苏黎"])
        self.assertEqual(payload["reference_policy"], "required")
        self.assertEqual(payload["reference_candidates"], ["generated/characters/苏黎/苏黎.png"])
        self.assertEqual(payload["art_style"], "水彩绘本画风")
        self.assertEqual(payload["planner_hints"]["style"], "consistent with previous scene")
        self.assertIn("ui_schema", payload)
        self.assertIn("postprocess_contract", payload)

    def test_assets_capability_normalizes_top_level_scene_requirement(self):
        request = {
            "id": "scene-image-each-round",
            "requested_by": "input_analyst",
            "target": "assets-ui",
            "capability": "assets.generate_image",
            "summary": "用户指令要求每轮提供一张带角色的剧情插图。",
            "reason": "用户要求从本轮开始持续生成插图。",
            "source_channel": "user_instruction",
            "risk": "medium",
            "authorization_gate": "none",
            "payload": {
                "scene_illustration_each_round": True,
                "characters": ["雨蒙", "苏黎"],
                "character_appearances": [
                    {
                        "name": "苏黎",
                        "appearance_state": "蝶化形态",
                        "description": "银白长发，半透明蝶翼。",
                    }
                ],
                "reference_policy": "reuse",
                "planner_hints": "雨蒙回到教室并观察苏黎。",
            },
            "evidence": {"raw_excerpt": "本轮开始，每轮都需要提供一张带角色的剧情插图。"},
        }

        result = self.mod.process_capability_requests(
            self.run_dir,
            [request],
            runtime_settings={},
            source_intent_id="input_analysis",
        )

        self.assertEqual(result["created_intents_count"], 1)
        pending = self.intents.list_intents(self.run_dir, "pending")
        payload = pending[0]["payload"]
        self.assertEqual(payload["asset_requirement"]["scene_illustration_each_round"], True)
        self.assertEqual(payload["asset_requirement"]["reason"], "用户要求从本轮开始持续生成插图。")
        self.assertEqual(payload["characters"], ["雨蒙", "苏黎"])
        self.assertEqual(payload["character_appearances"][0]["appearance_state"], "蝶化形态")
        self.assertEqual(payload["reference_policy"], "reuse")
        self.assertEqual(payload["planner_hints"], "雨蒙回到教室并观察苏黎。")

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

    def test_replay_plan_with_none_gate_creates_replay_plan_intent(self):
        request = {
            "id": "cap-replay",
            "requested_by": "input_analyst",
            "target": "replay",
            "capability": "replay.plan",
            "summary": "Plan a replay from the previous round.",
            "reason": "Player reframed the previous answer as a dream.",
            "source_channel": "user_instruction",
            "risk": "high",
            "authorization_gate": "none",
            "payload": {
                "schema_version": 1,
                "scope": "single_round",
                "plan_id": "replay-001",
                "snapshot_id": "round-000001-20260623T000000000000Z-abc123def456",
                "affected_rounds": ["round-000001"],
            },
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "previous scene was a dream"},
        }

        result = self.mod.process_capability_requests(
            self.run_dir,
            [request],
            runtime_settings={"allowSourceCodeSelfRepair": False},
            source_intent_id="intent_000001",
        )

        self.assertEqual(result["created_intents_count"], 1)
        self.assertEqual(result["created_messages_count"], 1)
        self.assertEqual(result["results"][0]["status"], "queued")
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual(pending[0]["type"], "replay_plan")
        self.assertEqual(
            pending[0]["payload"],
            {
                "capability_request_id": "cap-replay",
                "capability": "replay.plan",
                "requested_by": "input_analyst",
                "payload": request["payload"],
                "policy": {
                    "source_channel": "user_instruction",
                    "risk": "high",
                    "authorization_gate": "none",
                },
            },
        )
        messages = self.messages.read_messages(self.run_dir)
        self.assertEqual(messages[0]["type"], "capability_request")
        self.assertEqual(messages[0]["payload"]["capability"], "replay.plan")
        artifact = _read_first_audit(self.run_dir, result)
        self.assertEqual(artifact["status"], "queued")
        self.assertEqual(artifact["authorization"]["authorization_gate"], "none")
        self.assertEqual(artifact["created_intent_ids"], [pending[0]["id"]])
        self.assertEqual(artifact["created_message_ids"], [messages[0]["id"]])

    def test_replay_execute_with_none_gate_creates_replay_execute_intent(self):
        request = {
            "id": "cap-replay-execute",
            "requested_by": "input_analyst",
            "target": "replay",
            "capability": "replay.execute",
            "summary": "Execute the planned replay.",
            "reason": "The replay plan has already been materialized.",
            "source_channel": "user_instruction",
            "risk": "high",
            "authorization_gate": "none",
            "payload": {"schema_version": 1, "plan_id": "replay-001", "resume": True},
            "evidence": {"semantic_unit_ids": ["u2"], "raw_excerpt": "run the replay"},
        }

        result = self.mod.process_capability_requests(
            self.run_dir,
            [request],
            runtime_settings={},
            source_intent_id="intent_000001",
        )

        self.assertEqual(result["created_intents_count"], 1)
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual(pending[0]["type"], "replay_execute")
        self.assertEqual(
            pending[0]["payload"],
            {
                "capability_request_id": "cap-replay-execute",
                "capability": "replay.execute",
                "requested_by": "input_analyst",
                "payload": request["payload"],
                "policy": {
                    "source_channel": "user_instruction",
                    "risk": "high",
                    "authorization_gate": "none",
                },
            },
        )
        self.assertEqual(result["results"][0]["status"], "queued")

    def test_legacy_routing_request_entrypoint_is_removed(self):
        self.assertFalse(hasattr(self.mod, "process_routing_requests"))
