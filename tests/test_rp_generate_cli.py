import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _load_rp_generate_cli():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("rp_generate_cli", ROOT / "skills" / "rp_generate_cli.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _agent_stream(text):
    return "\n".join(
        [
            json.dumps(
                {
                    "type": "system",
                    "subtype": "task_started",
                    "task_type": "local_agent",
                    "subagent_type": "general-purpose",
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "tool_use_result": {
                        "status": "completed",
                        "content": [{"type": "text", "text": text}],
                    },
                }
            ),
            json.dumps({"type": "result", "subtype": "success", "result": text}),
        ]
    )


class RpGenerateCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.card = self.root / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000002"
        self.styles_dir = self.root / "skills" / "styles"
        self.run_dir.mkdir(parents=True)
        self.styles_dir.mkdir(parents=True)
        (self.card / ".agent_runs" / "current").write_text(str(self.run_dir.resolve()), encoding="utf-8")
        (self.run_dir / "prompts").mkdir()
        for name in ["gm", "player", "story", "critic"]:
            (self.run_dir / "prompts" / f"{name}.prompt.md").write_text(f"# {name}\n", encoding="utf-8")
        _write_json(
            self.run_dir / "manifest.json",
            {
                "round_id": "round-000002",
                "stage": "awaiting_agent_outputs",
                "prompts": {
                    "gm": "prompts/gm.prompt.md",
                    "player": "prompts/player.prompt.md",
                    "characters": {},
                    "story": "prompts/story.prompt.md",
                    "critic": "prompts/critic.prompt.md",
                },
                "expected_outputs": {
                    "gm": "gm.output.json",
                    "player": "player.output.json",
                    "characters": {},
                    "story": "story.output.json",
                    "critic": "critic.report.json",
                },
            },
        )
        _write_json(
            self.run_dir / "input.json",
            {
                "raw_text": "I follow the noise.",
                "routed_input": {
                    "role_channel": "I follow the noise.",
                    "user_instruction_channel": "",
                },
            },
        )
        self.module = _load_rp_generate_cli()

    def tearDown(self):
        self.tmp.cleanup()

    def test_extract_agent_text_requires_local_agent_task(self):
        payload = {"agent": "gm", "narration": "ok", "npc_events": [], "world_state_delta": [], "handoff": {}}
        text = self.module.extract_agent_text(_agent_stream(json.dumps(payload)))
        self.assertEqual(json.loads(text)["agent"], "gm")

        no_agent_stream = json.dumps({"type": "result", "subtype": "success", "result": json.dumps(payload)})
        with self.assertRaisesRegex(self.module.AgentExecutionError, "local_agent"):
            self.module.extract_agent_text(no_agent_stream)

    def test_extract_json_object_uses_first_balanced_object(self):
        payload = self.module._extract_json_object(
            '{"agent":"critic","passed":true,"hard_failures":[]}'
            '\n{"note":"extra text that should not poison the first object"}'
        )

        self.assertEqual(payload["agent"], "critic")
        self.assertTrue(payload["passed"])

    def test_outer_prompt_does_not_wrap_nested_prompt_in_markdown_fence(self):
        nested_prompt = '## Required Output Contract\n```json\n{"agent":"gm"}\n```\n## Context Packet\n```json\n{"role_channel":"I ask."}\n```'

        outer = self.module._outer_prompt("gm", nested_prompt)

        self.assertNotIn("```markdown", outer)
        self.assertNotIn("TOOL_NOT_AVAILABLE", outer)
        self.assertIn("<subagent_prompt>", outer)
        self.assertIn("</subagent_prompt>", outer)
        self.assertIn("embedded-context mode", outer)
        self.assertIn("Do not block because run_dir", outer)
        self.assertIn('{"role_channel":"I ask."}', outer)

    def test_validate_accepts_common_artifact_wrapper_keys(self):
        gm_payload = {
            "gm_output": {
                "agent": "gm",
                "narration": "ok",
                "npc_events": [],
                "world_state_delta": [],
                "handoff": {},
            }
        }
        story_payload = {
            "story_output": {
                "content": "<content>ok</content>",
                "character_dialogues": [],
                "metadata": {},
            }
        }

        self.assertEqual(self.module._validate("gm", gm_payload)["agent"], "gm")
        self.assertEqual(self.module._validate("story", story_payload)["content"], "<content>ok</content>")

    def test_validate_normalizes_gm_world_state_delta_for_memory(self):
        payload = {
            "agent": "gm",
            "narration": "ok",
            "npc_events": [],
            "world_state_delta": [{"path": "/scene/position", "value": "Uiharu is by the window."}],
            "handoff": {},
        }

        normalized = self.module._validate("gm", payload)

        self.assertEqual(normalized["world_state_delta"], [{"scope": "/scene/position", "fact": "Uiharu is by the window."}])

    def test_validate_normalizes_legacy_actor_output_shape(self):
        payload = {
            "actor_output": {
                "embodied_intent": "I help Uiharu to the window.",
                "immediate_action": "I steady Uiharu and guide her toward the window.",
                "inner_sensation": "I stay alert to the classroom.",
                "spoken_line": "Let's keep this quiet.",
                "state_suggestions": ["I decide to avoid drawing attention."],
            }
        }

        normalized = self.module._validate("player", payload)

        self.assertEqual(normalized["agent"], "player")
        self.assertEqual(normalized["agent_id"], "player")
        self.assertIn("guide her toward the window", normalized["action"])
        self.assertEqual(normalized["dialogue"], [{"text": "Let's keep this quiet."}])
        self.assertIn("classroom", normalized["perception"][0])

    def test_stdout_json_is_ascii_safe_for_windows_console(self):
        text = self.module._stdout_json({"ok": True, "text": "中文\ufffd"})

        self.assertTrue(all(ord(ch) < 128 for ch in text))
        self.assertEqual(json.loads(text)["text"], "中文\ufffd")

    def test_run_round_writes_subagent_artifacts_and_invokes_delivery(self):
        responses = {
            "gm": {
                "agent": "gm",
                "narration": "The alley light flickers.",
                "npc_events": [],
                "world_state_delta": [],
                "handoff": {},
            },
            "player": {
                "agent": "player",
                "agent_id": "player",
                "action": "I follow the noise.",
                "dialogue": [],
                "perception": ["I see a flickering alley light."],
                "memory_delta": [],
            },
            "story": {
                "content": "<content>I followed the noise toward the flickering alley light.</content>",
                "character_dialogues": [],
                "metadata": {},
            },
            "critic": {
                "decision": "pass",
                "hard_failures": [],
                "soft_issues": [],
                "repair_instruction": "",
                "system_iteration_suggestion": "",
            },
        }
        calls = []
        delivery_calls = []

        def fake_run_claude(agent_key, prompt, cwd):
            calls.append((agent_key, prompt, Path(cwd)))
            return _agent_stream(json.dumps(responses[agent_key], ensure_ascii=False))

        def fake_run_command(command, **kwargs):
            delivery_calls.append([str(part) for part in command])
            return SimpleNamespace(returncode=0, stdout='{"action":"done"}\n', stderr="")

        result = self.module.run_round(
            self.card,
            self.root,
            run_claude=fake_run_claude,
            run_command=fake_run_command,
        )

        self.assertTrue(result["ok"])
        self.assertEqual([call[0] for call in calls], ["gm", "player", "story", "critic"])
        self.assertTrue((self.run_dir / "gm.output.json").exists())
        self.assertTrue((self.run_dir / "player.output.json").exists())
        self.assertTrue((self.run_dir / "story.input.json").exists())
        self.assertTrue((self.run_dir / "story.output.json").exists())
        self.assertTrue((self.run_dir / "critic.report.json").exists())
        self.assertTrue(any("round_deliver.py" in " ".join(command) for command in delivery_calls))

    def test_run_round_accepts_direct_agent_plain_json_output(self):
        responses = {
            "gm": {"agent": "gm", "narration": "ok", "npc_events": [], "world_state_delta": [], "handoff": {}},
            "player": {"agent": "player", "agent_id": "player", "action": "I wait.", "dialogue": [], "perception": [], "memory_delta": []},
            "story": {"content": "<content>ok</content>", "character_dialogues": [], "metadata": {}},
            "critic": {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": "", "system_iteration_suggestion": ""},
        }

        def fake_run_claude(agent_key, prompt, cwd):
            return json.dumps(responses[agent_key], ensure_ascii=False)

        def fake_delivery(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout='{"action":"done"}\n', stderr="")

        result = self.module.run_round(
            self.card,
            self.root,
            run_claude=fake_run_claude,
            run_command=fake_delivery,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(json.loads((self.run_dir / "player.output.json").read_text(encoding="utf-8"))["action"], "I wait.")

    def test_run_round_treats_delivery_retry_as_not_ok(self):
        responses = {
            "gm": {
                "agent": "gm",
                "narration": "The alley light flickers.",
                "npc_events": [],
                "world_state_delta": [],
                "handoff": {},
            },
            "player": {
                "agent": "player",
                "agent_id": "player",
                "action": "I follow the noise.",
                "dialogue": [],
                "perception": [],
                "memory_delta": [],
            },
            "story": {
                "content": "<content>Too short.</content>",
                "character_dialogues": [],
                "metadata": {},
            },
            "critic": {
                "decision": "pass",
                "hard_failures": [],
                "soft_issues": [],
                "repair_instruction": "",
                "system_iteration_suggestion": "",
            },
        }

        def fake_run_claude(agent_key, prompt, cwd):
            return _agent_stream(json.dumps(responses[agent_key], ensure_ascii=False))

        def fake_retry_delivery(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout='{"action":"retry","reason":"word_count"}\n', stderr="")

        result = self.module.run_round(
            self.card,
            self.root,
            run_claude=fake_run_claude,
            run_command=fake_retry_delivery,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["delivery"]["result"]["action"], "retry")

    def test_run_round_retries_once_when_outer_model_skips_task(self):
        valid = {
            "gm": {
                "agent": "gm",
                "narration": "The alley light flickers.",
                "npc_events": [],
                "world_state_delta": [],
                "handoff": {},
            },
            "player": {
                "agent": "player",
                "agent_id": "player",
                "action": "I follow the noise.",
                "dialogue": [],
                "perception": [],
                "memory_delta": [],
            },
            "story": {
                "content": "<content>I followed the noise.</content>",
                "character_dialogues": [],
                "metadata": {},
            },
            "critic": {
                "decision": "pass",
                "hard_failures": [],
                "soft_issues": [],
                "repair_instruction": "",
                "system_iteration_suggestion": "",
            },
        }
        calls = []

        def fake_run_claude(agent_key, prompt, cwd):
            calls.append(agent_key)
            if agent_key == "gm" and calls.count("gm") == 1:
                return json.dumps({"type": "result", "subtype": "success", "result": "{}"})
            return _agent_stream(json.dumps(valid[agent_key], ensure_ascii=False))

        def fake_delivery(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout='{"action":"done"}\n', stderr="")

        result = self.module.run_round(
            self.card,
            self.root,
            run_claude=fake_run_claude,
            run_command=fake_delivery,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(calls.count("gm"), 2)

    def test_run_round_retries_once_when_agent_returns_invalid_schema(self):
        calls = []

        def fake_run_claude(agent_key, prompt, cwd):
            calls.append(agent_key)
            if agent_key == "gm" and calls.count("gm") == 1:
                payload = {"gm_output": {"scene_state": {}, "notes": "old schema"}}
            elif agent_key == "gm":
                payload = {"agent": "gm", "narration": "ok", "npc_events": [], "world_state_delta": [], "handoff": {}}
            elif agent_key == "player":
                payload = {"agent": "player", "agent_id": "player", "action": "I wait.", "dialogue": [], "perception": [], "memory_delta": []}
            elif agent_key == "story":
                payload = {"content": "<content>ok</content>", "character_dialogues": [], "metadata": {}}
            else:
                payload = {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": "", "system_iteration_suggestion": ""}
            return _agent_stream(json.dumps(payload, ensure_ascii=False))

        def fake_delivery(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout='{"action":"done"}\n', stderr="")

        result = self.module.run_round(
            self.card,
            self.root,
            run_claude=fake_run_claude,
            run_command=fake_delivery,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(calls.count("gm"), 2)

    def test_run_round_rewrites_story_once_when_delivery_requests_retry(self):
        calls = []
        story_payloads = [
            {
                "content": "<content>Too short.</content>",
                "character_dialogues": [],
                "metadata": {"attempt": 1},
            },
            {
                "content": "<content>Expanded enough for delivery.</content>",
                "character_dialogues": [],
                "metadata": {"attempt": 2},
            },
        ]

        def fake_run_claude(agent_key, prompt, cwd):
            calls.append(agent_key)
            if agent_key == "gm":
                payload = {"agent": "gm", "narration": "ok", "npc_events": [], "world_state_delta": [], "handoff": {}}
            elif agent_key == "player":
                payload = {"agent": "player", "agent_id": "player", "action": "I wait.", "dialogue": [], "perception": [], "memory_delta": []}
            elif agent_key == "story":
                payload = story_payloads.pop(0)
            else:
                payload = {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": "", "system_iteration_suggestion": ""}
            return _agent_stream(json.dumps(payload, ensure_ascii=False))

        delivery_attempts = []

        def fake_delivery(command, **kwargs):
            delivery_attempts.append(command)
            if len(delivery_attempts) == 1:
                return SimpleNamespace(returncode=0, stdout='{"action":"retry","word_count":{"current":10,"threshold":100}}\n', stderr="")
            return SimpleNamespace(returncode=0, stdout='{"action":"done"}\n', stderr="")

        result = self.module.run_round(
            self.card,
            self.root,
            run_claude=fake_run_claude,
            run_command=fake_delivery,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(calls.count("story"), 2)
        self.assertEqual(calls.count("critic"), 2)
        final_story = json.loads((self.run_dir / "story.output.json").read_text(encoding="utf-8"))
        self.assertEqual(final_story["metadata"]["attempt"], 2)

    def test_run_round_rewrites_story_once_when_critic_requests_revision(self):
        calls = []
        story_prompts = []
        story_payloads = [
            {
                "content": "<content>Draft without the requested repair.</content>",
                "character_dialogues": [],
                "metadata": {"attempt": 1},
            },
            {
                "content": "<content>Repaired according to critic instruction.</content>",
                "character_dialogues": [],
                "metadata": {"attempt": 2},
            },
        ]

        def fake_run_claude(agent_key, prompt, cwd):
            calls.append(agent_key)
            if agent_key == "gm":
                payload = {"agent": "gm", "narration": "ok", "npc_events": [], "world_state_delta": [], "handoff": {}}
            elif agent_key == "player":
                payload = {"agent": "player", "agent_id": "player", "action": "I wait.", "dialogue": [], "perception": [], "memory_delta": []}
            elif agent_key == "story":
                story_prompts.append(prompt)
                payload = story_payloads.pop(0)
            else:
                payload = {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": "", "system_iteration_suggestion": ""}
            return _agent_stream(json.dumps(payload, ensure_ascii=False))

        delivery_attempts = []

        def fake_delivery(command, **kwargs):
            delivery_attempts.append(command)
            if len(delivery_attempts) == 1:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "action": "retry",
                            "reason": "critic_revise",
                            "detail": {
                                "repair_instruction": "Restore the required narrative handoff.",
                                "hard_failures": ["handoff missing"],
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout='{"action":"done"}\n', stderr="")

        result = self.module.run_round(
            self.card,
            self.root,
            run_claude=fake_run_claude,
            run_command=fake_delivery,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(calls.count("story"), 2)
        self.assertEqual(calls.count("critic"), 2)
        self.assertIn("Restore the required narrative handoff", story_prompts[-1])
        final_story = json.loads((self.run_dir / "story.output.json").read_text(encoding="utf-8"))
        self.assertEqual(final_story["metadata"]["attempt"], 2)

    def test_run_round_handles_sequential_delivery_repair_requests(self):
        calls = []
        story_payloads = [
            {
                "content": "<content>Too short.</content>",
                "character_dialogues": [],
                "metadata": {"attempt": 1},
            },
            {
                "content": "<content>Long enough but missing state repair.</content>",
                "character_dialogues": [],
                "metadata": {"attempt": 2},
            },
            {
                "content": "<content>Long enough with state repair.</content>",
                "character_dialogues": [],
                "metadata": {"attempt": 3},
            },
        ]

        def fake_run_claude(agent_key, prompt, cwd):
            calls.append(agent_key)
            if agent_key == "gm":
                payload = {"agent": "gm", "narration": "ok", "npc_events": [], "world_state_delta": [], "handoff": {}}
            elif agent_key == "player":
                payload = {"agent": "player", "agent_id": "player", "action": "I wait.", "dialogue": [], "perception": [], "memory_delta": []}
            elif agent_key == "story":
                payload = story_payloads.pop(0)
            else:
                payload = {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": "", "system_iteration_suggestion": ""}
            return _agent_stream(json.dumps(payload, ensure_ascii=False))

        delivery_attempts = []

        def fake_delivery(command, **kwargs):
            delivery_attempts.append(command)
            if len(delivery_attempts) == 1:
                return SimpleNamespace(returncode=0, stdout='{"action":"retry","word_count":{"current":10,"threshold":100},"hint":"expand"}\n', stderr="")
            if len(delivery_attempts) == 2:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "action": "retry",
                            "reason": "critic_revise",
                            "detail": {"repair_instruction": "Add state patches."},
                        }
                    )
                    + "\n",
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout='{"action":"done"}\n', stderr="")

        result = self.module.run_round(
            self.card,
            self.root,
            run_claude=fake_run_claude,
            run_command=fake_delivery,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(calls.count("story"), 3)
        self.assertEqual(calls.count("critic"), 3)
        self.assertEqual(len(delivery_attempts), 3)
        final_story = json.loads((self.run_dir / "story.output.json").read_text(encoding="utf-8"))
        self.assertEqual(final_story["metadata"]["attempt"], 3)

    def test_run_round_preflights_story_word_count_before_critic_when_settings_exist(self):
        _write_json(self.styles_dir / "settings.json", {"wordCount": 100})
        calls = []
        short_story = {
            "content": "<content>太短。</content><summary>s</summary><options>o</options>",
            "character_dialogues": [],
            "metadata": {"attempt": 1},
        }
        long_story = {
            "content": "<content>" + ("长" * 90) + "</content><summary>s</summary><options>o</options>",
            "character_dialogues": [],
            "metadata": {"attempt": 2},
        }
        stories = [short_story, long_story]

        def fake_run_claude(agent_key, prompt, cwd):
            calls.append(agent_key)
            if agent_key == "gm":
                payload = {"agent": "gm", "narration": "ok", "npc_events": [], "world_state_delta": [], "handoff": {}}
            elif agent_key == "player":
                payload = {"agent": "player", "agent_id": "player", "action": "I wait.", "dialogue": [], "perception": [], "memory_delta": []}
            elif agent_key == "story":
                payload = stories.pop(0)
            else:
                payload = {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": "", "system_iteration_suggestion": ""}
            return _agent_stream(json.dumps(payload, ensure_ascii=False))

        def fake_delivery(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout='{"action":"done"}\n', stderr="")

        result = self.module.run_round(
            self.card,
            self.root,
            run_claude=fake_run_claude,
            run_command=fake_delivery,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(calls.count("story"), 2)
        self.assertEqual(calls.count("critic"), 1)
        final_story = json.loads((self.run_dir / "story.output.json").read_text(encoding="utf-8"))
        self.assertEqual(final_story["metadata"]["attempt"], 2)

    def test_normalize_story_output_orders_dialogues_and_removes_tokens(self):
        story = {
            "content": (
                "<content>正文</content><summary>小结</summary><options>选项</options>"
                "<TOKENS>\nin: NNNN\nout: NNNN\ntotal: NNNN\n</TOKENS><character_dialogues></character_dialogues>"
            ),
            "character_dialogues": [],
            "metadata": {},
            "tokens": {"in": "NNNN"},
            "token_usage": {"total": "NNNN"},
        }

        normalized = self.module._normalize_story_output(story)
        content = normalized["content"]

        self.assertNotIn("<tokens>", content)
        self.assertNotIn("<TOKENS>", content)
        self.assertNotIn("tokens", normalized)
        self.assertNotIn("token_usage", normalized)
        self.assertIn("<character_dialogues>[]</character_dialogues>", content)
        self.assertLess(content.index("<character_dialogues>"), content.index("<summary>"))

    def test_normalize_story_output_removes_visible_polished_input_notes(self):
        story = {
            "content": (
                "<polished_input>玩家以第一人称提供开局设定：这是内部分析。</polished_input>"
                "<content>你在教室里醒来。</content><summary>醒来</summary><options>观察</options>"
            ),
            "character_dialogues": [],
            "metadata": {},
        }

        normalized = self.module._normalize_story_output(story)
        content = normalized["content"]

        self.assertNotIn("<polished_input>", content)
        self.assertNotIn("玩家以第一人称提供开局设定", content)
        self.assertIn("<content>你在教室里醒来。</content>", content)

    def test_normalize_story_output_rewrites_invalid_update_analysis(self):
        story = {
            "content": (
                "<content>你走向苏黎。</content>"
                "<UpdateVariable><Analysis>时间推进到走廊，状态发生变化。</Analysis>"
                "<JSONPatch>[]</JSONPatch></UpdateVariable>"
                "<summary>接触苏黎</summary><options>继续</options>"
            ),
            "character_dialogues": [],
            "metadata": {},
        }

        normalized = self.module._normalize_story_output(story)
        content = normalized["content"]

        self.assertNotIn("时间推进到走廊", content)
        self.assertIn("Time advances through the current player action.", content)
        self.assertIn("<JSONPatch>[]</JSONPatch>", content)

    def test_normalize_story_output_fills_dialogues_from_character_outputs(self):
        story = {
            "content": "<content>你靠近苏黎。</content><summary>接触</summary><options>继续</options>",
            "character_dialogues": [],
            "metadata": {},
        }
        story_input = {
            "actor_outputs": {
                "characters": {
                    "_self": {"dialogue": ["ignore self"]},
                    "\u82cf\u9ece": {
                        "dialogue": ["\u4f60\u679c\u7136\u4f1a\u5728\u8fd9\u4e2a\u65f6\u5019\u95ee\u3002"],
                        "perception": ["\u5148\u786e\u8ba4\u5468\u56f4\u662f\u5426\u5b89\u5168\u3002"],
                    },
                }
            }
        }

        normalized = self.module._normalize_story_output(story, story_input)

        self.assertEqual(normalized["character_dialogues"], [
            {
                "name": "\u82cf\u9ece",
                "source": "subagent",
                "line": "\u4f60\u679c\u7136\u4f1a\u5728\u8fd9\u4e2a\u65f6\u5019\u95ee\u3002",
                "aside": "\u5148\u786e\u8ba4\u5468\u56f4\u662f\u5426\u5b89\u5168\u3002",
            }
        ])
        self.assertIn(
            '"source": "subagent"',
            normalized["content"],
        )

    def test_story_preflight_rejects_third_person_when_second_person_required(self):
        story = {
            "content": "<content>雨蒙醒过来的时候，首先闻到教室里的粉笔味。他抬头看向窗外。</content>",
        }
        requirements = {
            "required_person": "第二人称",
            "player_character_names": ["雨蒙"],
        }

        issues = self.module._story_preflight_issues(story, requirements)

        self.assertTrue(any("second_person" in issue for issue in issues), issues)

    def test_critic_skill_does_not_require_story_agent_tokens(self):
        skill = (ROOT / ".claude" / "skills" / "rp-critic-agent.md").read_text(encoding="utf-8")

        self.assertIn("Do not hard-fail", skill)
        self.assertIn("missing <tokens>", skill)
        self.assertIn("round_deliver.py appends", skill)

    def test_run_delivery_forces_utf8_python_stdio(self):
        captured = {}

        def fake_run_command(command, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(returncode=0, stdout='{"action":"done","summary":"正常中文"}\n', stderr="")

        delivery = self.module._run_delivery(self.card, self.root, fake_run_command)

        self.assertTrue(delivery["ok"])
        self.assertEqual(delivery["result"]["summary"], "正常中文")
        self.assertEqual(captured["encoding"], "utf-8")
        self.assertEqual(captured["env"]["PYTHONIOENCODING"], "utf-8")

    def test_run_round_resets_stale_critic_retry_budget_before_regeneration(self):
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["stage"] = "blocked"
        manifest["critic_retry_count"] = 2
        _write_json(self.run_dir / "manifest.json", manifest)
        responses = {
            "gm": {"agent": "gm", "narration": "ok", "npc_events": [], "world_state_delta": [], "handoff": {}},
            "player": {"agent": "player", "agent_id": "player", "action": "I wait.", "dialogue": [], "perception": [], "memory_delta": []},
            "story": {"content": "<content>ok</content>", "character_dialogues": [], "metadata": {}},
            "critic": {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": "", "system_iteration_suggestion": ""},
        }

        def fake_run_claude(agent_key, prompt, cwd):
            return _agent_stream(json.dumps(responses[agent_key], ensure_ascii=False))

        def fake_delivery(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout='{"action":"done"}\n', stderr="")

        result = self.module.run_round(
            self.card,
            self.root,
            run_claude=fake_run_claude,
            run_command=fake_delivery,
        )

        self.assertTrue(result["ok"])
        final_manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(final_manifest.get("critic_retry_count"), 0)

    def test_delivery_retry_context_marks_previous_story_as_rejected(self):
        context = self.module._delivery_retry_context(
            {"action": "retry", "reason": "critic_revise", "detail": {"repair_instruction": "Rewrite from source."}},
            {"content": "<content>bad prior draft</content>"},
            {"decision": "revise"},
            2,
        )

        self.assertNotIn("previous_story_output", context)
        self.assertIn("previous_rejected_story_output", context)
        self.assertTrue(context["do_not_preserve_rejected_content"])
        self.assertIn("story_input", context["authoritative_sources"])


if __name__ == "__main__":
    unittest.main()
