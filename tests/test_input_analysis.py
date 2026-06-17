import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_input_analysis():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(
        "input_analysis", ROOT / "skills" / "input_analysis.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def test_validate_accepts_ai_analysis_with_matching_hashes(self):
        result = self.mod.validate_input_analysis(
            self._analysis(),
            raw_text=self.raw,
            role_text=self.role,
            user_instruction_text=self.instruction,
        )
        self.assertEqual(result["analysis_mode"], "ai")
        self.assertEqual(result["semantic_units"][0]["type"], "action")

    def test_validate_rejects_hash_mismatch(self):
        data = self._analysis()
        data["source_integrity"]["raw_text_sha256"] = hashlib.sha256(b"wrong").hexdigest()
        with self.assertRaises(self.mod.InputAnalysisError):
            self.mod.validate_input_analysis(
                data,
                raw_text=self.raw,
                role_text=self.role,
                user_instruction_text=self.instruction,
            )

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

    def test_routing_uses_analysis_channels_without_explicit_payload(self):
        result = self.mod.analysis_to_routed_input(self._analysis())

        self.assertEqual(result["role_channel"], self.role)
        self.assertEqual(result["user_instruction_channel"], self.instruction)
        self.assertEqual(result["input_schema"], "analysis_v1")
        self.assertEqual(result["analysis_mode"], "ai")

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
                    self.mod.validate_input_analysis(
                        data,
                        raw_text=self.raw,
                        role_text=self.role,
                        user_instruction_text=self.instruction,
                    )

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
            self.mod.validate_input_analysis(
                data,
                raw_text=self.raw,
                role_text=self.role,
                user_instruction_text=self.instruction,
            )


if __name__ == "__main__":
    unittest.main()
