import copy
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
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InputAnalysisApplyTest(unittest.TestCase):
    def test_rewrite_previous_output_requires_explicit_replay_capability(self):
        mod = _load_module("input_analysis_apply")
        input_analysis = mod.input_analysis
        role_text = "The previous scene was actually a dream. I wake up before school."
        instruction_text = "Keep the pendant setting hidden for long-term plot guidance."
        raw_text = role_text + "\n\n[USER_INSTRUCTION]\n" + instruction_text
        integrity = {
            "raw_text_sha256": input_analysis.sha256_text(raw_text),
            "role_text_sha256": input_analysis.sha256_text(role_text),
            "user_instruction_text_sha256": input_analysis.sha256_text(instruction_text),
            "raw_preserved": True,
        }
        analysis = {
            "schema_version": 1,
            "round_id": "round-000002",
            "analysis_mode": "fixture",
            "source_integrity": integrity,
            "semantic_units": [
                {
                    "id": "su-001",
                    "type": "edit_request",
                    "visibility": "gm_only",
                    "raw_excerpt": "The previous scene was actually a dream.",
                    "derived_summary": "Previous output must be retconned into a dream.",
                    "source_channel": "role_input",
                    "confidence": 0.8,
                    "persist": False,
                }
            ],
            "world_updates": {
                "hidden_facts": [],
                "public_facts": [],
                "important_characters": [],
                "retcon_requests": [],
            },
            "narrative_directives": {
                "rewrite_previous_output": True,
                "expand_synopsis_before_continue": True,
                "continue_after_player_action": True,
            },
            "routing": {
                "role_channel": role_text,
                "role_action_channel": "",
                "narrative_guidance_channel": role_text,
                "user_instruction_channel": instruction_text,
                "gm": True,
                "player": True,
                "characters": [],
            },
            "routing_requests": [],
            "capability_requests": [],
            "risks": [],
        }

        with self.assertRaisesRegex(
            input_analysis.InputAnalysisError,
            "rewrite_previous_output requires replay.plan",
        ):
            mod._validate_structured_retcon_has_replay_or_replay_context(
                analysis,
                {"explicit_payload": {}},
            )

    def test_structured_retcon_adds_replay_plan_and_execute_from_round_backup(self):
        mod = _load_module("input_analysis_apply")
        input_analysis = mod.input_analysis
        agent_run = _load_module("agent_run")
        role_text = "The previous classroom scene was a dream. I wake up before school."
        instruction_text = "Keep the pendant transformation setting hidden."
        raw_text = role_text + "\n\n[USER_INSTRUCTION]\n" + instruction_text
        backup_id = "round-000002-20260628T010203040506Z-abcdef123456"
        input_id = "input-retcon-001"
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            card.mkdir()
            (card / ".card_data.json").write_text(
                json.dumps(
                    {
                        "mode": "blank_bootstrap",
                        "source_type": "blank",
                        "name": "雨蒙",
                        "data": {"name": "雨蒙"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (card / "chat_log.json").write_text(
                json.dumps(
                    [
                        {
                            "index": 1,
                            "user": "I am 雨蒙.",
                            "ai": "Classroom scene.",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            run_dir = agent_run.create_run_dir(card, turn_index=1)
            integrity = {
                "raw_text_sha256": input_analysis.sha256_text(raw_text),
                "role_text_sha256": input_analysis.sha256_text(role_text),
                "user_instruction_text_sha256": input_analysis.sha256_text(instruction_text),
                "raw_preserved": True,
            }
            (run_dir / "input.raw.json").write_text(
                json.dumps(
                    {
                        "round_id": "round-000002",
                        "raw_text": raw_text,
                        "role_text": role_text,
                        "user_instruction_text": instruction_text,
                        "source_integrity": integrity,
                        "explicit_payload": {
                            "id": input_id,
                            "input_schema": "dual_channel_v1",
                            "role_text": role_text,
                            "user_instruction_text": instruction_text,
                            "snapshot": {
                                "ok": True,
                                "backup_id": backup_id,
                                "round_id": "round-000002",
                                "reason": "before_round_prepare",
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (run_dir / "input_analysis.output.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "round_id": "round-000002",
                        "analysis_mode": "fixture",
                        "source_integrity": integrity,
                        "semantic_units": [
                            {
                                "id": "su-001",
                                "type": "edit_request",
                                "visibility": "gm_only",
                                "raw_excerpt": "previous classroom scene was a dream",
                                "derived_summary": "Previous AI output must be treated as a dream.",
                                "source_channel": "role_input",
                                "confidence": 0.9,
                                "persist": False,
                            }
                        ],
                        "world_updates": {
                            "hidden_facts": [],
                            "public_facts": [],
                            "important_characters": [],
                            "retcon_requests": [
                                {
                                    "id": "rr-001",
                                    "text": "Previous AI output must be treated as a dream.",
                                    "visibility": "gm_only",
                                    "status": "active",
                                }
                            ],
                        },
                        "narrative_directives": {
                            "rewrite_previous_output": True,
                            "expand_synopsis_before_continue": True,
                            "continue_after_player_action": True,
                        },
                        "routing": {
                            "role_channel": role_text,
                            "role_action_channel": "",
                            "narrative_guidance_channel": role_text,
                            "user_instruction_channel": instruction_text,
                            "gm": True,
                            "player": True,
                            "characters": [],
                        },
                        "routing_requests": [],
                        "capability_requests": [],
                        "risks": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = mod.apply_current_run(card, ROOT)

            capabilities = result["capability_requests"]
            self.assertEqual(
                [item["capability"] for item in capabilities],
                ["replay.plan", "replay.execute"],
            )
            plan = capabilities[0]["payload"]
            self.assertEqual(plan["backup_id"], backup_id)
            self.assertEqual(plan["affected_inputs"][0]["input_id"], input_id)
            self.assertEqual(capabilities[1]["payload"]["plan_id"], plan["plan_id"])

    def test_blank_player_character_declaration_updates_player_mapping(self):
        mod = _load_module("input_analysis_apply")
        input_analysis = mod.input_analysis
        agent_run = _load_module("agent_run")
        role_text = "我叫雨蒙，一名普通的高一男生。再回过神来时，我坐在教室里。"
        instruction_text = "作品基调：日式轻小说风格。"
        raw_text = role_text + "\n\n[USER_INSTRUCTION]\n" + instruction_text
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            card.mkdir()
            (card / ".card_data.json").write_text(
                json.dumps(
                    {
                        "mode": "blank_bootstrap",
                        "source_type": "blank",
                        "name": "未命名角色",
                        "data": {"name": "未命名角色"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (card / "chat_log.json").write_text("[]", encoding="utf-8")
            run_dir = agent_run.create_run_dir(card, turn_index=0)
            integrity = {
                "raw_text_sha256": input_analysis.sha256_text(raw_text),
                "role_text_sha256": input_analysis.sha256_text(role_text),
                "user_instruction_text_sha256": input_analysis.sha256_text(instruction_text),
                "raw_preserved": True,
            }
            (run_dir / "input.raw.json").write_text(
                json.dumps(
                    {
                        "raw_text": raw_text,
                        "role_text": role_text,
                        "user_instruction_text": instruction_text,
                        "source_integrity": integrity,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (run_dir / "input_analysis.output.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "round_id": "round-000001",
                        "analysis_mode": "fixture",
                        "source_integrity": integrity,
                        "semantic_units": [
                            {
                                "id": "su-001",
                                "type": "character_declaration",
                                "visibility": "public_world",
                                "raw_excerpt": "我叫雨蒙，一名普通的高一男生。",
                                "derived_summary": "主角角色声明：雨蒙，高一男生。",
                                "source_channel": "role_input",
                                "confidence": 0.8,
                                "persist": False,
                            }
                        ],
                        "world_updates": {
                            "hidden_facts": [],
                            "public_facts": [],
                            "important_characters": [
                                {
                                    "name": "雨蒙",
                                    "text": "高一男生，成绩稍好，性格平凡普通。",
                                    "visibility": "public_world",
                                    "status": "active",
                                },
                                {
                                    "name": "雨蒙",
                                    "text": "上学途中亲眼看到粉色花形云彩后失去意识、记忆中断。",
                                    "visibility": "character_private_and_gm",
                                    "status": "active",
                                }
                            ],
                            "retcon_requests": [],
                        },
                        "narrative_directives": {
                            "rewrite_previous_output": False,
                            "expand_synopsis_before_continue": True,
                            "continue_after_player_action": False,
                        },
                        "routing": {
                            "role_channel": role_text,
                            "role_action_channel": role_text,
                            "narrative_guidance_channel": "",
                            "user_instruction_channel": instruction_text,
                            "gm": True,
                            "player": True,
                            "characters": [],
                        },
                        "routing_requests": [],
                        "capability_requests": [],
                        "risks": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            mod.apply_current_run(card, ROOT)

            mapping_path = card / "characters" / "player.md"
            self.assertTrue(mapping_path.exists())
            player_mapping = mapping_path.read_text(encoding="utf-8")
            player_prompt = (run_dir / "prompts" / "player.prompt.md").read_text(encoding="utf-8")
            self.assertIn("name: 雨蒙", player_mapping)
            self.assertIn("path: characters/雨蒙", player_mapping)
            self.assertIn("我是 雨蒙。", player_prompt)
            self.assertIn("我是雨蒙。", player_prompt)

    def test_blank_unique_player_character_updates_mapping_even_when_source_channel_is_wrong(self):
        mod = _load_module("input_analysis_apply")
        input_analysis = mod.input_analysis
        agent_run = _load_module("agent_run")
        role_text = "我叫雨蒙，一名普通的高一男生。至少在今天早上之前，除了成绩稍好以外，我的确平凡普通。今天早上我看见粉色云彩。"
        instruction_text = "作品基调：日式轻小说风格。"
        raw_text = role_text + "\n\n[USER_INSTRUCTION]\n" + instruction_text
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            card.mkdir()
            (card / ".card_data.json").write_text(
                json.dumps(
                    {
                        "mode": "blank_bootstrap",
                        "source_type": "blank",
                        "name": "未命名角色",
                        "data": {"name": "未命名角色"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (card / "chat_log.json").write_text("[]", encoding="utf-8")
            run_dir = agent_run.create_run_dir(card, turn_index=0)
            integrity = {
                "raw_text_sha256": input_analysis.sha256_text(raw_text),
                "role_text_sha256": input_analysis.sha256_text(role_text),
                "user_instruction_text_sha256": input_analysis.sha256_text(instruction_text),
                "raw_preserved": True,
            }
            (run_dir / "input.raw.json").write_text(
                json.dumps(
                    {
                        "raw_text": raw_text,
                        "role_text": role_text,
                        "user_instruction_text": instruction_text,
                        "source_integrity": integrity,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (run_dir / "input_analysis.output.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "round_id": "round-000001",
                        "analysis_mode": "fixture",
                        "source_integrity": integrity,
                        "semantic_units": [
                            {
                                "id": "su-001",
                                "type": "character_declaration",
                                "visibility": "public_world",
                                "raw_excerpt": "我叫雨蒙，一名普通的高一男生。至少在今天早上之前，除了成绩稍好以外，的确平凡普通。",
                                "derived_summary": "主角角色声明：雨蒙，高一男生。",
                                "source_channel": "user_instruction",
                                "confidence": 0.5,
                                "persist": False,
                            }
                        ],
                        "world_updates": {
                            "hidden_facts": [],
                            "public_facts": [],
                            "important_characters": [
                                {
                                    "name": "雨蒙",
                                    "summary": "高一男生，玩家操控的主要角色。",
                                    "visibility": "public_world",
                                    "status": "active",
                                }
                            ],
                            "retcon_requests": [],
                        },
                        "narrative_directives": {
                            "rewrite_previous_output": False,
                            "expand_synopsis_before_continue": True,
                            "continue_after_player_action": False,
                        },
                        "routing": {
                            "role_channel": role_text,
                            "role_action_channel": "今天早上我看见粉色云彩。",
                            "narrative_guidance_channel": "",
                            "user_instruction_channel": instruction_text,
                            "gm": True,
                            "player": True,
                            "characters": ["雨蒙"],
                        },
                        "routing_requests": [],
                        "capability_requests": [],
                        "risks": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            mod.apply_current_run(card, ROOT)

            mapping_path = card / "characters" / "player.md"
            self.assertTrue(mapping_path.exists())
            player_mapping = mapping_path.read_text(encoding="utf-8")
            self.assertIn("name: 雨蒙", player_mapping)
            self.assertIn("path: characters/雨蒙", player_mapping)
            self.assertFalse((card / "characters" / "player").exists())

    def test_normalizes_non_style_units_that_use_instruction_as_false_evidence(self):
        mod = _load_module("input_analysis_apply")
        input_analysis = mod.input_analysis
        role_text = "I found a pink flower-shaped cloud and lost twenty minutes."
        instruction_text = "Style: energetic school comedy."
        raw_text = role_text + "\n\n[USER_INSTRUCTION]\n" + instruction_text
        raw_request = {
            "raw_text": raw_text,
            "role_text": role_text,
            "user_instruction_text": instruction_text,
            "source_integrity": {
                "raw_text_sha256": input_analysis.sha256_text(raw_text),
                "role_text_sha256": input_analysis.sha256_text(role_text),
                "user_instruction_text_sha256": input_analysis.sha256_text(instruction_text),
                "raw_preserved": True,
            },
        }
        analysis = {
            "schema_version": 1,
            "round_id": "round-000001",
            "analysis_mode": "fixture",
            "source_integrity": dict(raw_request["source_integrity"]),
            "semantic_units": [
                {
                    "id": "unit-wrong-source",
                    "source_channel": "user_instruction",
                    "type": "hidden_setting",
                    "text": "The missing twenty minutes are a mystery.",
                    "raw_excerpt": instruction_text,
                    "derived_summary": "The missing time should be tracked as a mystery.",
                    "confidence": 0.7,
                    "visibility": "gm_only",
                    "persist": False,
                },
                {
                    "id": "unit-style",
                    "source_channel": "user_instruction",
                    "type": "style_guidance",
                    "text": instruction_text,
                    "raw_excerpt": instruction_text,
                    "derived_summary": "Use energetic school comedy style.",
                    "confidence": 0.9,
                    "visibility": "gm_only",
                    "persist": False,
                },
            ],
            "world_updates": {
                "hidden_facts": [],
                "public_facts": [],
                "important_characters": [],
                "retcon_requests": [],
            },
            "narrative_directives": {
                "rewrite_previous_output": False,
                "expand_synopsis_before_continue": True,
                "continue_after_player_action": True,
            },
            "routing": {
                "role_channel": role_text,
                "user_instruction_channel": instruction_text,
                "gm": True,
                "player": True,
                "characters": [],
            },
            "routing_requests": [],
            "capability_requests": [],
            "risks": [],
        }

        normalized, changed = mod._normalize_legacy_semantic_units(
            copy.deepcopy(analysis),
            raw_request,
        )

        self.assertTrue(changed)
        self.assertEqual(normalized["semantic_units"][0]["source_channel"], "role_input")
        self.assertEqual(normalized["semantic_units"][0]["raw_excerpt"], role_text)
        self.assertEqual(normalized["semantic_units"][1]["source_channel"], "user_instruction")
        self.assertEqual(normalized["semantic_units"][1]["raw_excerpt"], instruction_text)
        input_analysis.validate_input_analysis(
            normalized,
            raw_text=raw_text,
            role_text=role_text,
            user_instruction_text=instruction_text,
        )

    def test_capability_request_source_channel_aliases_are_normalized(self):
        mod = _load_module("input_analysis_apply")
        input_analysis = mod.input_analysis
        role_text = "我叫雨蒙"
        raw_text = role_text
        raw_request = {
            "raw_text": raw_text,
            "role_text": role_text,
            "user_instruction_text": "",
        }
        analysis = {
            "schema_version": 1,
            "round_id": "round-000001",
            "analysis_mode": "fixture",
            "source_integrity": {
                "raw_text_sha256": input_analysis.sha256_text(raw_text),
                "role_text_sha256": input_analysis.sha256_text(role_text),
                "user_instruction_text_sha256": input_analysis.sha256_text(""),
                "raw_preserved": True,
            },
            "semantic_units": [
                {
                    "id": "su-001",
                    "source_channel": "role_input",
                    "type": "character_declaration",
                    "raw_excerpt": role_text,
                    "derived_summary": "主角声明自己叫雨蒙。",
                    "confidence": 0.9,
                    "visibility": "public_world",
                    "persist": True,
                }
            ],
            "world_updates": {
                "hidden_facts": [],
                "public_facts": [],
                "important_characters": [],
                "retcon_requests": [],
            },
            "narrative_directives": {
                "rewrite_previous_output": False,
                "expand_synopsis_before_continue": False,
                "continue_after_player_action": True,
            },
            "routing": {
                "role_channel": role_text,
                "user_instruction_channel": "",
                "gm": True,
                "player": True,
                "characters": [],
            },
            "routing_requests": [],
            "capability_requests": [
                {
                    "id": "cap-001",
                    "requested_by": "input_analyst",
                    "target": "memory",
                    "capability": "character.rename",
                    "summary": "将占位名改为雨蒙",
                    "reason": "玩家在 role channel 声明自己叫雨蒙。",
                    "source_channel": "role_text",
                    "risk": "low",
                    "authorization_gate": "none",
                    "payload": {
                        "from_name": "player",
                        "to_name": "雨蒙",
                        "actor_id": "player",
                    },
                    "evidence": {"raw_excerpt": role_text},
                },
                {
                    "id": "cap-002",
                    "requested_by": "input_analyst",
                    "target": "memory",
                    "capability": "character.rename",
                    "summary": "将占位名改为雨蒙",
                    "reason": "玩家在 role channel 声明自己叫雨蒙。",
                    "source_channel": "role",
                    "risk": "low",
                    "authorization_gate": "none",
                    "payload": {
                        "from_name": "未命名角色",
                        "to_name": "雨蒙",
                        "actor_id": "player",
                    },
                    "evidence": {"raw_excerpt": role_text},
                },
            ],
            "risks": [],
        }

        normalized, changed = mod._normalize_capability_request_source_channels(
            copy.deepcopy(analysis),
            raw_request,
        )

        self.assertTrue(changed)
        self.assertEqual(
            [request["source_channel"] for request in normalized["capability_requests"]],
            ["role_input", "role_input"],
        )
        input_analysis.validate_input_analysis(
            normalized,
            raw_text=raw_text,
            role_text=role_text,
            user_instruction_text="",
        )

    def test_legacy_replay_capability_aliases_normalize_to_replay_plan(self):
        mod = _load_module("input_analysis_apply")
        raw_request = {
            "raw_text": "Please handle the explicit capability request.",
            "role_text": "Please handle the explicit capability request.",
            "user_instruction_text": "",
        }
        analysis = {
            "capability_requests": [
                {
                    "id": "cap-retcon",
                    "requested_by": "input_analyst",
                    "target": "replay",
                    "capability": "retcon.replay",
                    "summary": "Plan replay.",
                    "reason": "Model used a legacy replay alias.",
                    "source_channel": "role_input",
                    "risk": "high",
                    "authorization_gate": "manual_confirmation",
                    "payload": {},
                    "evidence": {"raw_excerpt": "explicit capability request"},
                },
                {
                    "id": "cap-story",
                    "requested_by": "input_analyst",
                    "target": "replay",
                    "capability": "story.replay",
                    "summary": "Plan replay.",
                    "reason": "Model used a legacy replay alias.",
                    "source_channel": "role_input",
                    "risk": "high",
                    "authorization_gate": "automatic",
                    "payload": {},
                    "evidence": {"raw_excerpt": "explicit capability request"},
                },
                {
                    "id": "cap-short",
                    "requested_by": "input_analyst",
                    "target": "replay",
                    "capability": "replay",
                    "summary": "Plan replay.",
                    "reason": "Model used a legacy replay alias.",
                    "source_channel": "role_input",
                    "risk": "high",
                    "authorization_gate": "none",
                    "payload": {},
                    "evidence": {"raw_excerpt": "explicit capability request"},
                },
            ],
        }

        normalized, changed = mod._normalize_capability_request_source_channels(
            copy.deepcopy(analysis),
            raw_request,
        )

        self.assertTrue(changed)
        self.assertEqual(
            [request["capability"] for request in normalized["capability_requests"]],
            ["replay.plan", "replay.plan", "replay.plan"],
        )
        self.assertEqual(
            [request["authorization_gate"] for request in normalized["capability_requests"]],
            ["none", "none", "none"],
        )

    def test_live_input_analysis_schema_aliases_are_normalized(self):
        mod = _load_module("input_analysis_apply")
        input_analysis = mod.input_analysis
        role_text = "我叫雨蒙，天空出现诡异的粉色云彩。"
        raw_text = role_text
        raw_request = {
            "raw_text": raw_text,
            "role_text": role_text,
            "user_instruction_text": "",
        }
        analysis = {
            "schema_version": 1,
            "round_id": "round-000001",
            "analysis_mode": "fixture",
            "source_integrity": {
                "raw_text_sha256": input_analysis.sha256_text(raw_text),
                "role_text_sha256": input_analysis.sha256_text(role_text),
                "user_instruction_text_sha256": input_analysis.sha256_text(""),
                "raw_preserved": True,
            },
            "semantic_units": [
                {
                    "id": "su-001",
                    "type": "hidden_fact",
                    "raw_excerpt": "天空出现诡异的粉色云彩",
                    "derived_summary": "粉色云彩是需要GM跟踪的隐藏事实。",
                    "confidence": 0.8,
                    "visibility": "gm_only",
                    "persist": True,
                }
            ],
            "world_updates": {
                "hidden_facts": [],
                "public_facts": [],
                "important_characters": [],
                "retcon_requests": [],
            },
            "narrative_directives": {
                "rewrite_previous_output": False,
                "expand_synopsis_before_continue": False,
                "continue_after_player_action": True,
            },
            "routing": {
                "role_channel": role_text,
                "user_instruction_channel": "",
                "gm": True,
                "player": True,
                "characters": [],
            },
            "routing_requests": [],
            "capability_requests": [
                {
                    "id": "cap-001",
                    "requested_by": "player",
                    "target": "memory",
                    "capability": "character.rename",
                    "summary": "将主角占位名重命名为雨蒙",
                    "reason": "玩家在角色通道中自我介绍为雨蒙。",
                    "source_channel": "role",
                    "risk": "low",
                    "authorization_gate": "manual_confirmation",
                    "payload": {
                        "from_name": "未命名角色",
                        "to_name": "雨蒙",
                        "actor_id": "player",
                    },
                    "evidence": {"raw_excerpt": "我叫雨蒙"},
                },
                {
                    "id": "cap-002",
                    "requested_by": "input_analyst",
                    "target": "memory",
                    "capability": "character.rename",
                    "summary": "将主角占位名重命名为雨蒙",
                    "reason": "玩家在角色通道中自我介绍为雨蒙。",
                    "source_channel": "role_text",
                    "risk": "low",
                    "authorization_gate": "automatic",
                    "payload": {
                        "from_name": "player",
                        "to_name": "雨蒙",
                        "actor_id": "player",
                    },
                    "evidence": {"raw_excerpt": "我叫雨蒙"},
                },
            ],
            "risks": [],
        }

        normalized, semantic_changed = mod._normalize_legacy_semantic_units(
            copy.deepcopy(analysis),
            raw_request,
        )
        normalized, capability_changed = mod._normalize_capability_request_source_channels(
            normalized,
            raw_request,
        )

        self.assertTrue(semantic_changed)
        self.assertTrue(capability_changed)
        self.assertEqual(normalized["semantic_units"][0]["type"], "hidden_setting")
        self.assertEqual(
            [
                (
                    request["requested_by"],
                    request["source_channel"],
                    request["authorization_gate"],
                )
                for request in normalized["capability_requests"]
            ],
            [
                ("input_analyst", "role_input", "none"),
                ("input_analyst", "role_input", "none"),
            ],
        )
        input_analysis.validate_input_analysis(
            normalized,
            raw_text=raw_text,
            role_text=role_text,
            user_instruction_text="",
        )
