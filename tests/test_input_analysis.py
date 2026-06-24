import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_module(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "skills" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_input_analysis():
    return _load_module("input_analysis")


class InputAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.mod = _load_input_analysis()
        self.role = "我尝试将吊坠扔掉。"
        self.instruction = "用于长期剧情引导：吊坠是变身器。"
        self.raw = self.role + "\n\n[USER_INSTRUCTION]\n" + self.instruction

    def _analysis(self):
        return {
            "schema_version": 1,
            "round_id": "round-000002",
            "analysis_mode": "ai",
            "source_integrity": {
                "raw_text_sha256": self.mod.sha256_text(self.raw),
                "role_text_sha256": self.mod.sha256_text(self.role),
                "user_instruction_text_sha256": self.mod.sha256_text(self.instruction),
                "raw_preserved": True,
            },
            "semantic_units": [
                {
                    "id": "u1",
                    "source_channel": "role_input",
                    "type": "action",
                    "raw_excerpt": self.role,
                    "derived_summary": "玩家尝试丢弃吊坠。",
                    "confidence": 0.92,
                    "visibility": "player_pov",
                    "persist": False,
                },
                {
                    "id": "u2",
                    "source_channel": "user_instruction",
                    "type": "hidden_setting",
                    "raw_excerpt": self.instruction,
                    "derived_summary": "吊坠真实用途暂不公开。",
                    "confidence": 0.95,
                    "visibility": "gm_only",
                    "persist": True,
                },
            ],
            "world_updates": {
                "hidden_facts": [
                    {
                        "id": "hidden-1",
                        "text": self.instruction,
                        "visibility": "gm_only",
                        "status": "active",
                    }
                ],
                "public_facts": [],
                "important_characters": [],
                "retcon_requests": [],
            },
            "narrative_directives": {
                "rewrite_previous_output": False,
                "expand_synopsis_before_continue": False,
                "continue_after_player_action": True,
                "must_stop_for_player_decision": False,
            },
            "routing": {
                "role_channel": self.role,
                "user_instruction_channel": self.instruction,
                "gm": True,
                "player": True,
                "characters": [],
            },
            "routing_requests": [],
            "risks": [],
        }

    def _safe_fallback_analysis(self):
        data = self._analysis()
        data["analysis_mode"] = "fallback"
        data["semantic_units"] = [
            {
                "id": "fallback-role-1",
                "source_channel": "role_input",
                "type": "action",
                "raw_excerpt": self.role,
                "derived_summary": "Fallback preserved role input.",
                "confidence": 0.0,
                "visibility": "player_pov",
                "persist": False,
            }
        ]
        data["world_updates"] = {
            "hidden_facts": [],
            "public_facts": [],
            "important_characters": [],
            "retcon_requests": [],
        }
        data["routing_requests"] = []
        data["risks"] = ["fallback: persistence blocked"]
        return data

    def _validate(self, data):
        return self.mod.validate_input_analysis(
            data,
            raw_text=self.raw,
            role_text=self.role,
            user_instruction_text=self.instruction,
        )

    def test_validate_accepts_ai_analysis_with_matching_hashes(self):
        result = self._validate(self._analysis())
        self.assertEqual(result["analysis_mode"], "ai")
        self.assertEqual(result["semantic_units"][0]["type"], "action")

    def test_validate_accepts_world_update_record_safe_status_variants(self):
        for status in ("active", "superseded", "retracted"):
            with self.subTest(status=status):
                important_status = "active"
                data = self._analysis()
                data["world_updates"] = {
                    "hidden_facts": [
                        {
                            "id": "hidden-1",
                            "text": "GM fact.",
                            "visibility": "gm_only",
                            "status": status,
                        }
                    ],
                    "public_facts": [
                        {
                            "id": "public-1",
                            "text": "Public fact.",
                            "visibility": "public_world",
                            "status": status,
                        }
                    ],
                    "important_characters": [
                        {
                            "name": "Suli",
                            "text": "Suli is a transfer student.",
                            "visibility": "character_private_and_gm",
                            "status": important_status,
                        }
                    ],
                    "retcon_requests": [
                        {
                            "id": "retcon-1",
                            "text": "Treat the prior answer as a dream.",
                            "visibility": "gm_only",
                            "status": status,
                        }
                    ],
                }

                self._validate(data)

    def test_validate_rejects_non_active_important_character_status(self):
        for status in ("superseded", "retracted"):
            with self.subTest(status=status):
                data = self._analysis()
                data["world_updates"]["important_characters"] = [
                    {
                        "name": "Suli",
                        "text": "Suli is a transfer student.",
                        "visibility": "character_private_and_gm",
                        "status": status,
                    }
                ]

                with self.assertRaisesRegex(
                    self.mod.InputAnalysisError,
                    r"important_characters\[0\]\.status",
                ):
                    self._validate(data)

    def test_validate_rejects_missing_blank_or_invalid_world_update_status(self):
        base_records = {
            "hidden_facts": {
                "id": "hidden-1",
                "text": "GM fact.",
                "visibility": "gm_only",
            },
            "public_facts": {
                "id": "public-1",
                "text": "Public fact.",
                "visibility": "public_world",
            },
            "important_characters": {
                "name": "Suli",
                "text": "Suli is a transfer student.",
                "visibility": "character_private_and_gm",
            },
            "retcon_requests": {
                "id": "retcon-1",
                "text": "Treat the prior answer as a dream.",
                "visibility": "gm_only",
            },
        }
        bad_statuses = {
            "missing": None,
            "blank": " ",
            "none": None,
            "invalid": "deleted",
        }

        for key, base_record in base_records.items():
            for case, status in bad_statuses.items():
                with self.subTest(key=key, case=case):
                    data = self._analysis()
                    record = dict(base_record)
                    if case != "missing":
                        record["status"] = status
                    data["world_updates"][key] = [record]

                    with self.assertRaisesRegex(
                        self.mod.InputAnalysisError,
                        rf"{key}\[0\]\.status",
                    ):
                        self._validate(data)

    def test_validate_rejects_invalid_world_update_records(self):
        cases = [
            ("hidden_facts", "not-object", r"hidden_facts\[0\] must be an object"),
            (
                "hidden_facts",
                {"id": "", "text": "GM fact.", "visibility": "gm_only", "status": "active"},
                r"hidden_facts\[0\]\.id",
            ),
            (
                "hidden_facts",
                {"id": "hidden-1", "text": "GM fact.", "visibility": "public_world", "status": "active"},
                r"hidden_facts\[0\]\.visibility",
            ),
            (
                "public_facts",
                {"id": "public-1", "text": "", "visibility": "public_world", "status": "active"},
                r"public_facts\[0\]\.text",
            ),
            (
                "public_facts",
                {"id": "public-1", "text": "Public fact.", "visibility": "gm_only", "status": "active"},
                r"public_facts\[0\]\.visibility",
            ),
            (
                "important_characters",
                {"name": "Suli", "text": " ", "visibility": "character_private_and_gm", "status": "active"},
                r"important_characters\[0\]\.text",
            ),
            (
                "important_characters",
                {"name": "Suli", "text": "Profile.", "visibility": "gm_only", "status": "active"},
                r"important_characters\[0\]\.visibility",
            ),
            (
                "retcon_requests",
                {"id": "retcon-1", "text": "Retcon.", "visibility": "character_pov", "status": "active"},
                r"retcon_requests\[0\]\.visibility",
            ),
        ]

        for key, record, pattern in cases:
            with self.subTest(key=key, record=record):
                data = self._analysis()
                data["world_updates"][key] = [record]

                with self.assertRaisesRegex(self.mod.InputAnalysisError, pattern):
                    self._validate(data)

    def test_validate_accepts_each_allowed_semantic_unit_type(self):
        allowed_types = (
            "action",
            "synopsis",
            "omniscient_setting",
            "hidden_setting",
            "character_declaration",
            "edit_request",
            "system_command",
            "style_guidance",
            "unclear",
        )

        for unit_type in allowed_types:
            with self.subTest(unit_type=unit_type):
                data = self._analysis()
                data["semantic_units"][0]["type"] = unit_type

                self._validate(data)

    def test_validate_rejects_unknown_semantic_unit_type(self):
        data = self._analysis()
        data["semantic_units"][0]["type"] = "dialogue"

        with self.assertRaises(self.mod.InputAnalysisError):
            self._validate(data)

    def test_validate_accepts_each_allowed_visibility(self):
        allowed_visibilities = (
            "gm_only",
            "public_world",
            "player_pov",
            "character_pov",
            "specific_characters",
        )

        for visibility in allowed_visibilities:
            with self.subTest(visibility=visibility):
                data = self._analysis()
                data["semantic_units"][0]["visibility"] = visibility

                self._validate(data)

    def test_validate_rejects_unknown_visibility(self):
        data = self._analysis()
        data["semantic_units"][0]["visibility"] = "public"

        with self.assertRaises(self.mod.InputAnalysisError):
            self._validate(data)

    def test_validate_rejects_hash_mismatch(self):
        data = self._analysis()
        data["source_integrity"]["raw_text_sha256"] = hashlib.sha256(b"wrong").hexdigest()
        with self.assertRaises(self.mod.InputAnalysisError):
            self._validate(data)

    def test_validate_rejects_missing_integrity_hash(self):
        for key in (
            "raw_text_sha256",
            "role_text_sha256",
            "user_instruction_text_sha256",
        ):
            with self.subTest(key=key):
                data = self._analysis()
                del data["source_integrity"][key]

                with self.assertRaises(self.mod.InputAnalysisError):
                    self._validate(data)

    def test_validate_rejects_missing_semantic_unit_required_field(self):
        for key in (
            "id",
            "source_channel",
            "type",
            "raw_excerpt",
            "derived_summary",
            "confidence",
            "visibility",
            "persist",
        ):
            with self.subTest(key=key):
                data = self._analysis()
                del data["semantic_units"][0][key]

                with self.assertRaises(self.mod.InputAnalysisError):
                    self._validate(data)

    def test_validate_rejects_non_bool_semantic_unit_persist(self):
        data = self._analysis()
        data["semantic_units"][0]["persist"] = "false"

        with self.assertRaises(self.mod.InputAnalysisError):
            self._validate(data)

    def test_validate_rejects_missing_or_mistyped_routing_keys(self):
        required_keys = (
            "role_channel",
            "user_instruction_channel",
            "gm",
            "player",
            "characters",
        )
        bad_values = {
            "role_channel": None,
            "user_instruction_channel": 7,
            "gm": "true",
            "player": 1,
            "characters": "npc",
        }

        for key in required_keys:
            with self.subTest(case="missing", key=key):
                data = self._analysis()
                del data["routing"][key]

                with self.assertRaises(self.mod.InputAnalysisError):
                    self._validate(data)

        for key, value in bad_values.items():
            with self.subTest(case="bad_type", key=key):
                data = self._analysis()
                data["routing"][key] = value

                with self.assertRaises(self.mod.InputAnalysisError):
                    self._validate(data)

    def test_validate_accepts_user_requested_routing_requests(self):
        data = self._analysis()
        data["routing_requests"] = [
            {
                "id": "route-001",
                "type": "assets_ui_task",
                "source_channel": "user_instruction",
                "summary": "Create a rainy street image for this scene.",
                "target": "assets-ui",
                "payload": {
                    "kind": "scene",
                    "target": "scene_illustration",
                    "prompt": "rainy street at midnight",
                },
                "requires_authorization": False,
                "authorization_gate": "none",
                "evidence": {
                    "semantic_unit_ids": ["u2"],
                    "raw_excerpt": self.instruction,
                },
            },
            {
                "id": "route-002",
                "type": "source_feature_request",
                "source_channel": "user_instruction",
                "summary": "Implement a new save export feature.",
                "target": "main_agent",
                "payload": {
                    "feature": "save_export",
                    "requested_behavior": "Add an export button for current save data.",
                },
                "requires_authorization": True,
                "authorization_gate": "allowSourceCodeSelfRepair",
                "evidence": {
                    "semantic_unit_ids": ["u2"],
                    "raw_excerpt": self.instruction,
                },
            },
        ]

        result = self._validate(data)

        self.assertEqual(len(result["routing_requests"]), 2)
        self.assertEqual(result["routing_requests"][0]["type"], "assets_ui_task")
        self.assertEqual(
            result["routing_requests"][1]["authorization_gate"],
            "allowSourceCodeSelfRepair",
        )

    def test_validate_accepts_capability_requests(self):
        data = self._analysis()
        data["routing_requests"] = []
        data["capability_requests"] = [
            {
                "id": "cap-001",
                "requested_by": "input_analyst",
                "target": "assets-ui",
                "capability": "assets.generate_image",
                "summary": "Create a pendant illustration.",
                "reason": "The instruction channel explicitly requested an image.",
                "source_channel": "user_instruction",
                "risk": "low",
                "authorization_gate": "none",
                "payload": {"prompt": "silver pendant"},
                "evidence": {
                    "semantic_unit_ids": ["u2"],
                    "raw_excerpt": self.instruction,
                },
            }
        ]

        validated = self._validate(data)

        self.assertEqual(
            validated["capability_requests"][0]["capability"],
            "assets.generate_image",
        )

    def test_validate_accepts_unknown_capability_request_for_later_audit(self):
        data = self._analysis()
        data["routing_requests"] = []
        data["capability_requests"] = [
            {
                "id": "cap-weather",
                "requested_by": "input_analyst",
                "target": "weather",
                "capability": "external.weather_lookup",
                "summary": "Look up weather.",
                "reason": "The instruction channel explicitly requested weather.",
                "source_channel": "user_instruction",
                "risk": "low",
                "authorization_gate": "none",
                "payload": {},
                "evidence": {
                    "semantic_unit_ids": ["u2"],
                    "raw_excerpt": self.instruction,
                },
            }
        ]

        validated = self._validate(data)

        self.assertEqual(
            validated["capability_requests"][0]["capability"],
            "external.weather_lookup",
        )

    def test_validate_rejects_capability_request_without_evidence_excerpt(self):
        data = self._analysis()
        data["capability_requests"] = [
            {
                "id": "cap-bad",
                "requested_by": "input_analyst",
                "target": "assets-ui",
                "capability": "assets.generate_image",
                "summary": "Create image.",
                "reason": "User asked for image.",
                "source_channel": "user_instruction",
                "risk": "low",
                "authorization_gate": "none",
                "payload": {},
                "evidence": {"semantic_unit_ids": ["u2"], "raw_excerpt": ""},
            }
        ]

        with self.assertRaisesRegex(
            self.mod.InputAnalysisError,
            r"capability_requests\[0\]\.evidence\.raw_excerpt",
        ):
            self._validate(data)

    def test_validate_rejects_duplicate_capability_request_ids(self):
        data = self._analysis()
        data["capability_requests"] = [
            {
                "id": "cap-dup",
                "requested_by": "input_analyst",
                "target": "assets-ui",
                "capability": "assets.generate_image",
                "summary": "Create image.",
                "reason": "User asked for image.",
                "source_channel": "user_instruction",
                "risk": "low",
                "authorization_gate": "none",
                "payload": {},
                "evidence": {"raw_excerpt": self.instruction},
            },
            {
                "id": "cap-dup",
                "requested_by": "input_analyst",
                "target": "weather",
                "capability": "external.weather_lookup",
                "summary": "Unsupported request for audit.",
                "reason": "User asked for weather.",
                "source_channel": "user_instruction",
                "risk": "low",
                "authorization_gate": "none",
                "payload": {},
                "evidence": {"raw_excerpt": self.instruction},
            },
        ]

        with self.assertRaisesRegex(
            self.mod.InputAnalysisError,
            r"capability_requests\[1\]\.id",
        ):
            self._validate(data)

    def test_validate_rejects_unknown_routing_request_type(self):
        data = self._analysis()
        data["routing_requests"] = [
            {
                "id": "route-001",
                "type": "unknown_request",
                "source_channel": "user_instruction",
                "summary": "Invalid request.",
                "target": "main_agent",
                "payload": {},
                "requires_authorization": False,
                "authorization_gate": "none",
                "evidence": {"raw_excerpt": self.instruction},
            }
        ]

        with self.assertRaisesRegex(
            self.mod.InputAnalysisError,
            r"routing_requests\[0\]\.type",
        ):
            self._validate(data)

    def test_validate_rejects_source_request_without_source_gate(self):
        data = self._analysis()
        data["routing_requests"] = [
            {
                "id": "route-001",
                "type": "source_feature_request",
                "source_channel": "user_instruction",
                "summary": "Implement a source change.",
                "target": "main_agent",
                "payload": {"feature": "demo"},
                "requires_authorization": False,
                "authorization_gate": "none",
                "evidence": {"raw_excerpt": self.instruction},
            }
        ]

        with self.assertRaisesRegex(
            self.mod.InputAnalysisError,
            r"source_feature_request",
        ):
            self._validate(data)

    def test_validate_rejects_non_source_request_with_source_gate(self):
        data = self._analysis()
        data["routing_requests"] = [
            {
                "id": "route-001",
                "type": "assets_ui_task",
                "source_channel": "user_instruction",
                "summary": "Create a scene image.",
                "target": "assets-ui",
                "payload": {"prompt": "rain"},
                "requires_authorization": True,
                "authorization_gate": "allowSourceCodeSelfRepair",
                "evidence": {"raw_excerpt": self.instruction},
            }
        ]

        with self.assertRaisesRegex(
            self.mod.InputAnalysisError,
            r"authorization_gate",
        ):
            self._validate(data)

    def test_validate_rejects_missing_or_mistyped_narrative_directive_keys(self):
        required_keys = (
            "rewrite_previous_output",
            "expand_synopsis_before_continue",
            "continue_after_player_action",
            "must_stop_for_player_decision",
        )

        for key in required_keys:
            with self.subTest(case="missing", key=key):
                data = self._analysis()
                del data["narrative_directives"][key]

                with self.assertRaises(self.mod.InputAnalysisError):
                    self._validate(data)

        for key in required_keys:
            with self.subTest(case="bad_type", key=key):
                data = self._analysis()
                data["narrative_directives"][key] = "false"

                with self.assertRaises(self.mod.InputAnalysisError):
                    self._validate(data)

    def test_routing_preserves_explicit_dual_channel_text(self):
        result = self.mod.analysis_to_routed_input(
            self._analysis(),
            explicit_payload={
                "input_schema": "dual_channel_v1",
                "role_text": self.role,
                "user_instruction_text": self.instruction,
            },
        )
        self.assertEqual(result["role_channel"], self.role)
        self.assertEqual(result["user_instruction_channel"], self.instruction)
        self.assertEqual(result["input_schema"], "analysis_v1")
        self.assertEqual(result["analysis_mode"], "ai")
        self.assertEqual(
            result["components"],
            [
                {"channel": "role", "text": self.role},
                {"channel": "user_instruction", "text": self.instruction},
            ],
        )

    def test_routing_uses_analysis_channels_without_explicit_payload(self):
        result = self.mod.analysis_to_routed_input(self._analysis())

        self.assertEqual(result["role_channel"], self.role)
        self.assertEqual(result["user_instruction_channel"], self.instruction)
        self.assertEqual(result["input_schema"], "analysis_v1")
        self.assertEqual(result["analysis_mode"], "ai")
        self.assertEqual(
            result["components"],
            [
                {"channel": "role", "text": self.role},
                {"channel": "user_instruction", "text": self.instruction},
            ],
        )

    def test_validate_rejects_non_explicit_routing_text_not_in_raw_input(self):
        data = self._analysis()
        data["routing"]["role_channel"] = "I do something the player never wrote."
        data["routing"]["user_instruction_channel"] = self.instruction

        with self.assertRaisesRegex(self.mod.InputAnalysisError, "routing.role_channel"):
            self._validate(data)

    def test_validate_accepts_non_explicit_routing_substrings_from_raw_input(self):
        data = self._analysis()
        data["routing"]["role_channel"] = self.role
        data["routing"]["user_instruction_channel"] = self.instruction

        self._validate(data)

    def test_validate_allows_explicit_payload_routing_override_text(self):
        data = self._analysis()
        data["routing"]["role_channel"] = "analysis rewrite"
        data["routing"]["user_instruction_channel"] = "analysis instruction rewrite"
        explicit_role = "explicit role text not in raw"
        explicit_instruction = "explicit instruction text not in raw"
        data["source_integrity"]["role_text_sha256"] = self.mod.sha256_text(explicit_role)
        data["source_integrity"]["user_instruction_text_sha256"] = self.mod.sha256_text(explicit_instruction)

        self.mod.validate_input_analysis(
            data,
            raw_text=self.raw,
            role_text=explicit_role,
            user_instruction_text=explicit_instruction,
            explicit_payload={
                "input_schema": "dual_channel_v1",
                "role_text": explicit_role,
                "user_instruction_text": explicit_instruction,
            },
        )

    def test_routing_converts_channel_values_to_strings(self):
        data = self._analysis()
        data["routing"]["role_channel"] = None
        data["routing"]["user_instruction_channel"] = 42

        result = self.mod.analysis_to_routed_input(data)

        self.assertEqual(result["role_channel"], "")
        self.assertEqual(result["user_instruction_channel"], "42")
        self.assertEqual(
            result["components"],
            [{"channel": "user_instruction", "text": "42"}],
        )

        explicit_result = self.mod.analysis_to_routed_input(
            self._analysis(),
            explicit_payload={
                "input_schema": "dual_channel_v1",
                "role_text": 7,
                "user_instruction_text": None,
            },
        )

        self.assertEqual(explicit_result["role_channel"], "7")
        self.assertEqual(explicit_result["user_instruction_channel"], "")
        self.assertEqual(
            explicit_result["components"],
            [{"channel": "role", "text": "7"}],
        )

    def test_fallback_blocks_high_risk_persistence(self):
        fallback = self.mod.build_fallback_analysis(
            raw_text=self.raw,
            role_text=self.role,
            user_instruction_text=self.instruction,
            round_id="round-000002",
        )
        self.assertEqual(fallback["analysis_mode"], "fallback")
        self.assertEqual(fallback["world_updates"]["hidden_facts"], [])
        self.assertEqual(fallback["world_updates"]["important_characters"], [])
        self.assertIn("fallback", fallback["risks"][0])

    def test_build_fallback_analysis_outputs_empty_routing_requests(self):
        result = self.mod.build_fallback_analysis(
            raw_text=self.raw,
            role_text=self.role,
            user_instruction_text=self.instruction,
            round_id="round-000002",
        )

        self.assertEqual(result["routing_requests"], [])
        self.assertEqual(result["capability_requests"], [])

    def test_validate_rejects_fallback_world_update_persistence(self):
        blocked_updates = {
            "hidden_facts": [
                {"text": self.instruction, "visibility": "gm_only", "status": "active"}
            ],
            "important_characters": [{"name": "神秘少女"}],
            "retcon_requests": [{"text": "重写上一轮输出。"}],
        }

        for key, value in blocked_updates.items():
            with self.subTest(key=key):
                data = self._safe_fallback_analysis()
                data["world_updates"][key] = value

                with self.assertRaises(self.mod.InputAnalysisError):
                    self._validate(data)

    def test_validate_rejects_fallback_persisted_high_risk_unit(self):
        data = self._safe_fallback_analysis()
        data["semantic_units"].append(
            {
                "id": "fallback-hidden-1",
                "source_channel": "user_instruction",
                "type": "hidden_setting",
                "raw_excerpt": self.instruction,
                "derived_summary": "Fallback must not persist hidden settings.",
                "confidence": 0.0,
                "visibility": "gm_only",
                "persist": True,
            }
        )

        with self.assertRaises(self.mod.InputAnalysisError):
            self._validate(data)


class InputAnalysisApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.card = Path(self.tmp.name) / "card"
        self.card.mkdir()
        self.agent_packets = _load_module("agent_packets")
        self.input_analysis = _load_module("input_analysis")
        self.apply_mod = _load_module("input_analysis_apply")
        self.InputAnalysisError = self.apply_mod.input_analysis.InputAnalysisError

        self.role_text = "I press the cracked pendant into my palm."
        self.hidden_text = "The pendant can only be destroyed by moon fire."
        self.important_text = "Suli is a transfer student who secretly knows the pendant's history."
        self.raw_text = (
            self.role_text
            + "\n\n[USER_INSTRUCTION]\n"
            + self.hidden_text
            + "\n"
            + self.important_text
        )
        self.input_payload = {
            "id": "input-analysis-apply-1",
            "created_at": "2026-06-17T06:00:00Z",
            "source": "player",
            "input_schema": "dual_channel_v1",
            "raw_text": self.raw_text,
            "display_text": self.role_text,
            "role_text": self.role_text,
            "user_instruction_text": self.hidden_text + "\n" + self.important_text,
        }
        self.card_data = {
            "mode": "blank_bootstrap",
            "source_type": "blank",
            "character_orchestration": {
                "major": [],
                "minor_policy": "main_agent",
                "max_parallel_subagents": 3,
            },
        }
        (self.card / ".card_data.json").write_text(
            json.dumps(self.card_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="heuristic fallback text should not win",
            chat_log=[{"index": 1, "summary": "Previous turn."}],
            card_data=self.card_data,
            character_contexts={"characters": []},
            turn_index=0,
            input_payload=self.input_payload,
        )
        self.run_dir = Path(result["run_dir"])

    def tearDown(self):
        self.tmp.cleanup()

    def _analysis(self):
        return {
            "schema_version": 1,
            "round_id": self.run_dir.name,
            "analysis_mode": "fixture",
            "source_integrity": {
                "raw_text_sha256": self.input_analysis.sha256_text(self.raw_text),
                "role_text_sha256": self.input_analysis.sha256_text(self.role_text),
                "user_instruction_text_sha256": self.input_analysis.sha256_text(
                    self.input_payload["user_instruction_text"]
                ),
                "raw_preserved": True,
            },
            "semantic_units": [
                {
                    "id": "unit-action-1",
                    "source_channel": "role_input",
                    "type": "action",
                    "raw_excerpt": self.role_text,
                    "derived_summary": "The player grips the pendant.",
                    "confidence": 0.93,
                    "visibility": "player_pov",
                    "persist": False,
                },
                {
                    "id": "unit-hidden-1",
                    "source_channel": "user_instruction",
                    "type": "hidden_setting",
                    "raw_excerpt": self.hidden_text,
                    "derived_summary": "The destruction condition is GM-only.",
                    "confidence": 0.98,
                    "visibility": "gm_only",
                    "persist": True,
                },
                {
                    "id": "unit-character-1",
                    "source_channel": "user_instruction",
                    "type": "character_declaration",
                    "raw_excerpt": self.important_text,
                    "derived_summary": "Suli should be tracked as an important character.",
                    "confidence": 0.96,
                    "visibility": "gm_only",
                    "persist": True,
                },
            ],
            "world_updates": {
                "hidden_facts": [
                    {
                        "id": "hidden-moon-fire",
                        "text": self.hidden_text,
                        "visibility": "gm_only",
                        "status": "active",
                    }
                ],
                "public_facts": [],
                "important_characters": [
                    {
                        "name": "Suli",
                        "text": self.important_text,
                        "visibility": "character_private_and_gm",
                        "status": "active",
                    }
                ],
                "retcon_requests": [],
            },
            "narrative_directives": {
                "rewrite_previous_output": False,
                "expand_synopsis_before_continue": False,
                "continue_after_player_action": True,
                "must_stop_for_player_decision": False,
            },
            "routing": {
                "role_channel": "analysis rewrite must not override explicit role",
                "user_instruction_channel": "analysis rewrite must not override explicit instruction",
                "gm": True,
                "player": True,
                "characters": ["Suli"],
            },
            "routing_requests": [],
            "risks": [],
        }

    def test_apply_current_run_persists_analysis_updates_and_rebuilds_packets(self):
        (self.run_dir / "input_analysis.output.json").write_text(
            json.dumps(self._analysis(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        result = self.apply_mod.apply_current_run(self.card)

        hidden_entries = [
            json.loads(line)
            for line in (self.card / "memory" / "gm_only_hidden_truths.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        card_data = json.loads((self.card / ".card_data.json").read_text(encoding="utf-8"))
        profile_md = self.card / "memory" / "characters" / "Suli" / "profile.md"
        profile_json = self.card / "memory" / "characters" / "Suli" / "profile.json"
        input_json = json.loads((self.run_dir / "input.json").read_text(encoding="utf-8"))
        gm_packet = json.loads((self.run_dir / "gm.context.json").read_text(encoding="utf-8"))
        player_packet = json.loads((self.run_dir / "player.context.json").read_text(encoding="utf-8"))
        character_packet = json.loads((self.run_dir / "characters" / "Suli.context.json").read_text(encoding="utf-8"))
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(result["stage"], "analysis_applied")
        self.assertEqual(hidden_entries[0]["text"], self.hidden_text)
        self.assertEqual(hidden_entries[0]["source_input_id"], "input-analysis-apply-1")
        self.assertIn("Suli", card_data["character_orchestration"]["major"])
        self.assertTrue(profile_md.exists())
        self.assertTrue(profile_json.exists())
        self.assertIn(self.important_text, profile_md.read_text(encoding="utf-8"))
        profile = json.loads(profile_json.read_text(encoding="utf-8"))
        self.assertEqual(profile["source"], "input_analysis")
        self.assertEqual(profile["source_agent"], "preprocess")
        self.assertEqual(profile["history"][-1]["source_agent"], "preprocess")
        self.assertEqual(profile["authoritative_setting"], self.important_text)
        self.assertEqual(input_json["raw_text"], self.raw_text)
        self.assertEqual(input_json["routed_input"]["role_channel"], self.role_text)
        self.assertEqual(
            input_json["routed_input"]["user_instruction_channel"],
            self.input_payload["user_instruction_text"],
        )
        self.assertEqual(input_json["input_analysis"]["analysis_mode"], "fixture")
        self.assertIn(self.hidden_text, json.dumps(gm_packet, ensure_ascii=False))
        self.assertNotIn(self.hidden_text, json.dumps(player_packet, ensure_ascii=False))
        self.assertEqual(character_packet["actor_id"], "character:Suli")
        self.assertEqual(character_packet["self_knowledge"]["name"], "Suli")
        self.assertIn(self.important_text, json.dumps(character_packet["memory"], ensure_ascii=False))
        self.assertNotIn(self.role_text, json.dumps(character_packet, ensure_ascii=False))
        self.assertNotIn("raw_text", gm_packet["input_analysis_request"])
        self.assertEqual(manifest["stage"], "analysis_applied")
        self.assertEqual(manifest["expected_outputs"]["input_analysis"], "input_analysis.output.json")

    def test_apply_current_run_does_not_persist_actor_unaware_hidden_truth_as_profile(self):
        analysis = self._analysis()
        unaware_hidden_text = (
            "雨蒙是被选中的人，粉色花朵吊坠是魔法少女变身器。"
            "战斗将燃烧男性身份与记忆转化为魔力。本人目前不知这些真相。"
        )
        analysis["world_updates"]["hidden_facts"] = [
            {
                "id": "hidden-yumeng-truth",
                "text": unaware_hidden_text,
                "visibility": "gm_only",
                "status": "active",
            }
        ]
        analysis["world_updates"]["important_characters"] = [
            {
                "name": "雨蒙",
                "authoritative_setting": unaware_hidden_text,
                "visibility": "character_private_and_gm",
                "status": "active",
            }
        ]
        (self.run_dir / "input_analysis.output.json").write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        result = self.apply_mod.apply_current_run(self.card)

        hidden_text = (self.card / "memory" / "gm_only_hidden_truths.jsonl").read_text(encoding="utf-8")
        self.assertIn(unaware_hidden_text, hidden_text)
        self.assertEqual(result["important_characters_persisted"], [])
        self.assertEqual(result["important_characters_skipped"], ["雨蒙"])
        self.assertFalse((self.card / "memory" / "characters" / "雨蒙" / "profile.md").exists())

    def test_apply_current_run_normalizes_legacy_semantic_units_before_validation(self):
        analysis = self._analysis()
        analysis["semantic_units"] = [
            {
                "type": "action",
                "visibility": "player_pov",
                "content": "The player grips the pendant.",
            },
            {
                "type": "hidden_setting",
                "visibility": "gm_only",
                "content": "The pendant has a secret destruction condition.",
            },
        ]
        self._write_analysis(analysis)

        self.apply_mod.apply_current_run(self.card)

        normalized = json.loads(
            (self.run_dir / "input_analysis.output.json").read_text(encoding="utf-8")
        )
        self.assertEqual(normalized["semantic_units"][0]["id"], "unit-001")
        self.assertEqual(normalized["semantic_units"][0]["source_channel"], "role_input")
        self.assertEqual(normalized["semantic_units"][0]["raw_excerpt"], self.role_text)
        self.assertEqual(
            normalized["semantic_units"][0]["derived_summary"],
            "The player grips the pendant.",
        )
        self.assertFalse(normalized["semantic_units"][0]["persist"])
        self.assertEqual(
            normalized["semantic_units"][1]["source_channel"],
            "user_instruction",
        )
        self.assertEqual(
            normalized["semantic_units"][1]["raw_excerpt"],
            self.input_payload["user_instruction_text"],
        )

    def test_apply_current_run_normalizes_missing_request_lists_to_empty_lists(self):
        analysis = self._analysis()
        analysis.pop("routing_requests", None)
        analysis.pop("capability_requests", None)
        (self.run_dir / "input_analysis.output.json").write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        result = self.apply_mod.apply_current_run(self.card, self.root)

        normalized = json.loads((self.run_dir / "input_analysis.output.json").read_text(encoding="utf-8"))
        self.assertEqual(normalized["routing_requests"], [])
        self.assertEqual(normalized["capability_requests"], [])
        self.assertEqual(result["routing_requests"], [])
        self.assertEqual(result["capability_requests"], [])

    def test_apply_current_run_maps_legacy_routing_requests_to_capability_requests(self):
        analysis = self._analysis()
        analysis.pop("capability_requests", None)
        analysis["routing_requests"] = [
            {
                "id": "route-legacy-assets",
                "type": "assets_ui_task",
                "source_channel": "user_instruction",
                "summary": "Create a pendant image.",
                "target": "assets-ui",
                "payload": {
                    "kind": "scene",
                    "target": "scene_illustration",
                    "prompt": "silver pendant",
                },
                "requires_authorization": False,
                "authorization_gate": "none",
                "evidence": {
                    "semantic_unit_ids": ["unit-hidden-1"],
                    "raw_excerpt": self.hidden_text,
                },
            }
        ]
        self._write_analysis(analysis)

        result = self.apply_mod.apply_current_run(self.card, self.root)

        normalized = json.loads((self.run_dir / "input_analysis.output.json").read_text(encoding="utf-8"))
        self.assertEqual(
            normalized["capability_requests"][0]["capability"],
            "assets.generate_image",
        )
        self.assertEqual(
            normalized["capability_requests"][0]["legacy_type"],
            "assets_ui_task",
        )
        self.assertEqual(result["capability_requests"], normalized["capability_requests"])

    def test_apply_current_run_wraps_malformed_legacy_routing_request_error_without_rewrite(self):
        analysis = self._analysis()
        analysis.pop("capability_requests", None)
        analysis["routing_requests"] = [{"id": "route-bad"}]
        original = json.dumps(analysis, ensure_ascii=False, indent=2)
        (self.run_dir / "input_analysis.output.json").write_text(
            original,
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            self.InputAnalysisError,
            r"routing_requests\[0\]\.type",
        ):
            self.apply_mod.apply_current_run(self.card, self.root)

        self.assertEqual(
            (self.run_dir / "input_analysis.output.json").read_text(encoding="utf-8"),
            original,
        )

    def test_apply_current_run_rejects_unknown_legacy_routing_type_without_rewrite(self):
        analysis = self._analysis()
        analysis.pop("capability_requests", None)
        analysis["routing_requests"] = [
            {
                "id": "route-unknown",
                "type": "unknown_request",
                "source_channel": "user_instruction",
                "summary": "Unknown legacy route.",
                "target": "main_agent",
                "payload": {},
                "requires_authorization": False,
                "authorization_gate": "none",
                "evidence": {"raw_excerpt": self.hidden_text},
            }
        ]
        original = json.dumps(analysis, ensure_ascii=False, indent=2)
        (self.run_dir / "input_analysis.output.json").write_text(
            original,
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            self.InputAnalysisError,
            r"routing_requests\[0\]\.type",
        ):
            self.apply_mod.apply_current_run(self.card, self.root)

        self.assertEqual(
            (self.run_dir / "input_analysis.output.json").read_text(encoding="utf-8"),
            original,
        )

    def test_apply_current_run_includes_existing_routed_character_without_profile_creation(self):
        card_data = {
            "mode": "story",
            "source_type": "imported",
            "character_orchestration": {
                "major": [],
                "minor_policy": "main_agent",
                "max_parallel_subagents": 3,
            },
        }
        (self.card / ".card_data.json").write_text(
            json.dumps(card_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ada_dir = self.card / "memory" / "characters" / "Ada"
        ada_dir.mkdir(parents=True)
        (ada_dir / "profile.md").write_text("Ada is already known.", encoding="utf-8")
        analysis = self._analysis()
        analysis["world_updates"]["hidden_facts"] = []
        analysis["world_updates"]["important_characters"] = []
        analysis["routing"]["characters"] = ["Ada"]
        self._write_analysis(analysis)

        result = self.apply_mod.apply_current_run(self.card)

        character_packet_path = self.run_dir / "characters" / "Ada.context.json"
        character_packet = json.loads(character_packet_path.read_text(encoding="utf-8"))
        card_data_after = json.loads((self.card / ".card_data.json").read_text(encoding="utf-8"))

        self.assertEqual(result["routed_input"]["characters"], ["Ada"])
        self.assertEqual(character_packet["actor_id"], "character:Ada")
        self.assertEqual(character_packet["self_knowledge"]["name"], "Ada")
        self.assertIn("Ada is already known.", json.dumps(character_packet["memory"], ensure_ascii=False))
        self.assertNotIn(self.role_text, json.dumps(character_packet, ensure_ascii=False))
        self.assertFalse((ada_dir / "profile.json").exists())
        self.assertEqual(card_data_after["character_orchestration"]["major"], [])

    def test_apply_current_run_includes_routed_character_from_existing_run_context_only(self):
        card_data = {
            "mode": "story",
            "source_type": "imported",
            "character_orchestration": {
                "major": [],
                "minor_policy": "main_agent",
                "max_parallel_subagents": 3,
            },
        }
        (self.card / ".card_data.json").write_text(
            json.dumps(card_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        existing_context = {
            "actor_id": "character:Ada",
            "agent": "character",
            "visibility": "first_person_character",
            "self_knowledge": {
                "name": "Ada",
                "identity": "",
                "role": "",
                "body_state": {},
                "relationships": {},
            },
            "memory": {
                "long_term": ["Ada exists only in the prepared run context."],
                "recent": [],
                "goals": [],
            },
            "visible_events": [],
            "role_channel_anchor": "",
        }
        (self.run_dir / "characters").mkdir(exist_ok=True)
        (self.run_dir / "characters" / "Ada.context.json").write_text(
            json.dumps(existing_context, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        analysis = self._analysis()
        analysis["world_updates"]["hidden_facts"] = []
        analysis["world_updates"]["important_characters"] = []
        analysis["routing"]["characters"] = ["Ada", "Unknown"]
        self._write_analysis(analysis)

        result = self.apply_mod.apply_current_run(self.card)

        ada_packet_path = self.run_dir / "characters" / "Ada.context.json"
        unknown_packet_path = self.run_dir / "characters" / "Unknown.context.json"
        ada_packet = json.loads(ada_packet_path.read_text(encoding="utf-8"))
        card_data_after = json.loads((self.card / ".card_data.json").read_text(encoding="utf-8"))

        self.assertEqual(result["routed_input"]["characters"], ["Ada", "Unknown"])
        self.assertEqual(ada_packet["actor_id"], "character:Ada")
        self.assertEqual(ada_packet["self_knowledge"]["name"], "Ada")
        self.assertIn(
            "prepared run context",
            json.dumps(ada_packet["memory"], ensure_ascii=False),
        )
        self.assertNotIn(self.role_text, json.dumps(ada_packet, ensure_ascii=False))
        self.assertFalse(unknown_packet_path.exists())
        self.assertFalse((self.card / "memory" / "characters" / "Ada").exists())
        self.assertFalse((self.card / "memory" / "characters" / "Unknown").exists())
        self.assertEqual(card_data_after["character_orchestration"]["major"], [])

    def test_apply_current_run_rejects_after_story_ready_without_overwriting_manifest(self):
        self._write_analysis()
        original_manifest = self._set_manifest_stage("story_ready")

        with self.assertRaisesRegex(self.InputAnalysisError, "story_ready"):
            self.apply_mod.apply_current_run(self.card)

        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "story_ready")
        self.assertEqual(manifest["retry_count"], original_manifest["retry_count"])
        self.assertEqual(
            manifest["critic_retry_count"],
            original_manifest["critic_retry_count"],
        )

    def test_apply_current_run_rejects_after_blocked_without_overwriting_manifest(self):
        self._write_analysis()
        original_manifest = self._set_manifest_stage("blocked")

        with self.assertRaisesRegex(self.InputAnalysisError, "blocked"):
            self.apply_mod.apply_current_run(self.card)

        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["retry_count"], original_manifest["retry_count"])
        self.assertEqual(
            manifest["critic_retry_count"],
            original_manifest["critic_retry_count"],
        )

    def test_apply_current_run_rejects_non_active_important_character_without_promotion(self):
        for status in ("superseded", "retracted"):
            with self.subTest(status=status):
                analysis = self._analysis()
                analysis["world_updates"]["hidden_facts"] = []
                analysis["world_updates"]["important_characters"] = [
                    {
                        "name": "Suli",
                        "text": self.important_text,
                        "visibility": "character_private_and_gm",
                        "status": status,
                    }
                ]
                self._write_analysis(analysis)

                with self.assertRaisesRegex(self.InputAnalysisError, "status"):
                    self.apply_mod.apply_current_run(self.card)

                card_data = json.loads((self.card / ".card_data.json").read_text(encoding="utf-8"))
                self.assertNotIn(
                    "Suli",
                    card_data.get("character_orchestration", {}).get("major", []),
                )
                self.assertFalse((self.card / "memory" / "characters" / "Suli").exists())

    def test_apply_current_run_rejects_gm_only_important_character_without_profile(self):
        analysis = self._analysis()
        analysis["world_updates"]["important_characters"] = [
            {
                "name": "苏黎",
                "setting_text": "秘密",
                "visibility": "gm_only",
                "status": "active",
            }
        ]
        self._write_analysis(analysis)

        with self.assertRaisesRegex(self.InputAnalysisError, "gm_only"):
            self.apply_mod.apply_current_run(self.card)

        self.assertFalse((self.card / "memory" / "characters" / "苏黎").exists())

    def test_apply_current_run_rejects_missing_important_character_visibility_without_profile(self):
        analysis = self._analysis()
        analysis["world_updates"]["important_characters"] = [
            {
                "name": "Suli",
                "setting_text": "private setting",
                "status": "active",
            }
        ]
        self._write_analysis(analysis)

        with self.assertRaisesRegex(self.InputAnalysisError, "visibility"):
            self.apply_mod.apply_current_run(self.card)

        self.assertFalse((self.card / "memory" / "characters" / "Suli").exists())

    def test_apply_current_run_rejects_blank_important_character_visibility_without_profile(self):
        analysis = self._analysis()
        analysis["world_updates"]["important_characters"] = [
            {
                "name": "Suli",
                "setting_text": "private setting",
                "visibility": "  ",
                "status": "active",
            }
        ]
        self._write_analysis(analysis)

        with self.assertRaisesRegex(self.InputAnalysisError, "visibility"):
            self.apply_mod.apply_current_run(self.card)

        self.assertFalse((self.card / "memory" / "characters" / "Suli").exists())

    def test_apply_current_run_rejects_invalid_card_data_without_overwriting_file(self):
        self._write_analysis()
        invalid_text = "{not valid json"
        (self.card / ".card_data.json").write_text(invalid_text, encoding="utf-8")

        with self.assertRaisesRegex(self.InputAnalysisError, r"\.card_data\.json"):
            self.apply_mod.apply_current_run(self.card)

        self.assertEqual(
            (self.card / ".card_data.json").read_text(encoding="utf-8"),
            invalid_text,
        )
        self.assertFalse((self.card / "memory" / "gm_only_hidden_truths.jsonl").exists())
        self.assertFalse((self.card / "memory" / "characters" / "Suli").exists())

    def _write_analysis(self, analysis=None):
        (self.run_dir / "input_analysis.output.json").write_text(
            json.dumps(analysis or self._analysis(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _set_manifest_stage(self, stage):
        manifest_path = self.run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["stage"] = stage
        manifest["retry_count"] = 2
        manifest["critic_retry_count"] = 3
        manifest["delivery"] = {"stage": "locked"}
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return manifest


if __name__ == "__main__":
    unittest.main()
