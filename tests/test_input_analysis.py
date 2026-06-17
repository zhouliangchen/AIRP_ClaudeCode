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
                    {"text": self.instruction, "visibility": "gm_only", "status": "active"}
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
        self.assertIn(self.important_text, json.dumps(character_packet, ensure_ascii=False))
        self.assertNotIn("raw_text", gm_packet["input_analysis_request"])
        self.assertEqual(manifest["stage"], "analysis_applied")
        self.assertEqual(manifest["expected_outputs"]["input_analysis"], "input_analysis.output.json")

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

    def test_apply_current_run_rejects_gm_only_important_character_without_profile(self):
        analysis = self._analysis()
        analysis["world_updates"]["important_characters"] = [
            {
                "name": "苏黎",
                "setting_text": "秘密",
                "visibility": "gm_only",
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
