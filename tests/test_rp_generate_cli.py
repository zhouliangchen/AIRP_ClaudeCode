import importlib.util
import json
import os
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


def _visibility_basis(actor_id):
    return {
        "mode": "direct",
        "summary": f"{actor_id} is directly addressed by this test GM prompt.",
        "target_actor": actor_id,
        "visible_to": [actor_id],
    }


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


def _gm_output(
    *,
    scene_beats=None,
    events=None,
    actor_calls=None,
    world_state_delta=None,
    decision_point=None,
    stop_reason="player_decision",
):
    if actor_calls is None:
        actor_calls = [
            {
                "call_id": "call-player-1",
                "actor_id": "player",
                "prompt": "Respond to the current player action.",
                "reason": "The player is the only required actor for this turn.",
                "metadata": {},
                "visibility_basis": _visibility_basis("player"),
            }
        ]
    return {
        "agent": "gm",
        "scene_beats": scene_beats if scene_beats is not None else [{"content": "The alley light flickers."}],
        "events": events if events is not None else [],
        "actor_calls": actor_calls,
        "parallel_groups": [],
        "world_state_delta": world_state_delta if world_state_delta is not None else [],
        "decision_point": decision_point if decision_point is not None else {"prompt": "What do you do next?"},
        "stop_reason": stop_reason,
    }


def _player_output(content="I wait.", *, stop_reason="continue"):
    return {
        "agent": "player",
        "agent_id": "player",
        "events": [{"type": "action", "target": "", "content": content, "metadata": {}}],
        "stop_reason": stop_reason,
    }


def _character_output(agent_id="character:Ada", content="Stay close."):
    character_name = agent_id.split(":", 1)[1] if ":" in agent_id else agent_id
    return {
        "agent": "character",
        "agent_id": agent_id,
        "character_name": character_name,
        "events": [{"type": "dialogue", "target": "player", "content": content, "metadata": {}}],
        "stop_reason": "continue",
    }


def _subgm_output(thread_id="side_a", *, status="completed"):
    return {
        "agent": "subGM",
        "thread_id": thread_id,
        "status": status,
        "scene_beats": [{"content": "Ada checks the archive seal."}],
        "events": [],
        "actor_calls": [],
        "messages_to_gm": [{"content": "Archive seal checked."}],
        "world_state_delta": [],
        "character_usage": ["character:Ada"],
        "promotion_requests": [],
        "boundary_requests": [],
        "notes_for_story": ["Keep off-screen until GM merges it."],
        "next_resume_point": "",
    }


def _story_output(content="<content>ok</content>", *, metadata=None):
    return {"content": content, "character_dialogues": [], "metadata": metadata or {}}


def _critic_pass():
    return {
        "decision": "pass",
        "hard_failures": [],
        "soft_issues": [],
        "repair_instruction": "",
        "system_iteration_suggestion": "",
    }


def _critic_revise_with_routing(stage, rollback, *, risk="medium"):
    return {
        "decision": "revise",
        "hard_failures": ["repair routed by critic"],
        "soft_issues": [],
        "repair_instruction": "Redo the failed stage with stricter continuity.",
        "system_iteration_suggestion": "",
        "repair_routing": {
            "stage": stage,
            "target_agents": ["gm"] if rollback == "round_progression" else ["story"],
            "rollback": rollback,
            "can_auto_repair": True,
            "risk": risk,
        },
    }


def _basic_responses(*, gm=None, player=None, story=None, critic=None):
    return {
        "gm": gm if gm is not None else _gm_output(),
        "player": player if player is not None else _player_output(),
        "story": story if story is not None else _story_output(),
        "critic": critic if critic is not None else _critic_pass(),
    }


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
                    "actors": "actor.outputs.json",
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
        payload = _gm_output(actor_calls=[], stop_reason="complete")
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
        gm_payload = {"gm_output": _gm_output(actor_calls=[], stop_reason="complete")}
        story_payload = {
            "story_output": {
                "content": "<content>ok</content>",
                "character_dialogues": [],
                "metadata": {},
            }
        }

        self.assertEqual(self.module._validate("gm", gm_payload)["agent"], "gm")
        self.assertEqual(self.module._validate("story", story_payload)["content"], "<content>ok</content>")

    def test_progression_rollback_preserves_post_round_memory_jobs(self):
        job_path = self.run_dir / "post_round_memory_jobs" / "character_Ada.job.json"
        _write_json(job_path, {"agent_id": "character:Ada"})

        self.module._reset_round_progression_outputs(self.run_dir)

        self.assertTrue(job_path.exists())

    def test_validate_accepts_subgm_wrapper_and_checks_thread_id(self):
        payload = {"subgm_output": _subgm_output("side_a")}

        normalized = self.module._validate("subGM:side_a", payload)

        self.assertEqual(normalized["agent"], "subGM")
        self.assertEqual(normalized["thread_id"], "side_a")

    def test_validate_rejects_subgm_thread_id_mismatch(self):
        payload = {"subgm_output": _subgm_output("side_b")}

        with self.assertRaisesRegex(self.module.AgentExecutionError, "thread_id"):
            self.module._validate("subGM:side_a", payload)

    def test_read_loop_prompt_generates_subgm_prompt(self):
        prompt = self.module._read_loop_prompt(
            self.run_dir,
            json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8")),
            "subGM:side_a",
            {"thread_id": "side_a", "objective": "Check the archive seal."},
        )

        self.assertIn("side_a", prompt)
        self.assertIn(".claude/skills/rp-subgm-agent.md", prompt)
        self.assertIn("no player participation", prompt)

    def test_validate_normalizes_gm_world_state_delta_for_memory(self):
        payload = _gm_output(
            actor_calls=[],
            world_state_delta=[{"path": "/scene/position", "value": "Uiharu is by the window."}],
            stop_reason="complete",
        )

        normalized = self.module._validate("gm", payload)

        self.assertEqual(normalized["world_state_delta"], [{"scope": "/scene/position", "fact": "Uiharu is by the window."}])

    def test_validate_rejects_legacy_actor_output_shape(self):
        payload = {
            "actor_output": {
                "agent": "player",
                "agent_id": "player",
                "action": "I wait.",
                "dialogue": [],
                "perception": [],
                "memory_delta": [],
            }
        }

        with self.assertRaisesRegex(self.module.AgentExecutionError, "legacy actor output key"):
            self.module._validate("player", payload)

    def test_stdout_json_is_ascii_safe_for_windows_console(self):
        text = self.module._stdout_json({"ok": True, "text": "中文\ufffd"})

        self.assertTrue(all(ord(ch) < 128 for ch in text))
        self.assertEqual(json.loads(text)["text"], "中文\ufffd")

    def test_dispatch_agent_payload_retries_claude_process_failure(self):
        payload = _critic_pass()
        attempts = []

        def fake_run_claude(agent_key, prompt, cwd):
            attempts.append((agent_key, prompt, Path(cwd)))
            if len(attempts) == 1:
                raise self.module.AgentExecutionError("claude exited with 1: ")
            return _agent_stream(json.dumps(payload, ensure_ascii=False))

        result = self.module._dispatch_agent_payload(
            "critic",
            "# critic\n",
            self.root,
            fake_run_claude,
        )

        self.assertEqual(result["decision"], "pass")
        self.assertEqual(len(attempts), 2)

    def test_run_claude_agent_reports_stdout_tail_when_stderr_empty(self):
        original_run = self.module.subprocess.run

        def fake_run(*args, **kwargs):
            return SimpleNamespace(
                returncode=1,
                stdout="diagnostic stdout from claude",
                stderr="",
            )

        try:
            self.module.subprocess.run = fake_run
            with self.assertRaisesRegex(self.module.AgentExecutionError, "diagnostic stdout from claude"):
                self.module.run_claude_agent("critic", "# critic\n", self.root)
        finally:
            self.module.subprocess.run = original_run

    def test_run_claude_agent_uses_claude_settings_env_over_process_env(self):
        settings_path = self.root / ".claude" / "settings.json"
        _write_json(
            settings_path,
            {
                "env": {
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-4-6[1M]",
                    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "gpt-5-5",
                    "CLAUDE_CODE_SUBAGENT_MODEL": "inherit",
                }
            },
        )
        captured = {}
        original_run = self.module.subprocess.run
        original_settings_path = self.module._claude_settings_path
        original_process_value = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL")

        def fake_run(*args, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        try:
            os.environ["CLAUDE_CODE_SUBAGENT_MODEL"] = "deepseek-v4-flash"
            self.module._claude_settings_path = lambda: settings_path
            self.module.subprocess.run = fake_run

            self.module.run_claude_agent("critic", "# critic\n", self.root)
        finally:
            self.module.subprocess.run = original_run
            self.module._claude_settings_path = original_settings_path
            if original_process_value is None:
                os.environ.pop("CLAUDE_CODE_SUBAGENT_MODEL", None)
            else:
                os.environ["CLAUDE_CODE_SUBAGENT_MODEL"] = original_process_value

        self.assertEqual(captured["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "inherit")
        self.assertEqual(captured["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL_NAME"], "gpt-5-5")

    def test_run_round_writes_subagent_artifacts_and_invokes_delivery(self):
        responses = _basic_responses(
            player=_player_output("I follow the noise."),
            story=_story_output("<content>I followed the noise toward the flickering alley light.</content>"),
        )
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
        self.assertTrue((self.run_dir / "actor.outputs.json").exists())
        self.assertTrue((self.run_dir / "interaction.trace.json").exists())
        self.assertTrue((self.run_dir / "story.input.json").exists())
        self.assertTrue((self.run_dir / "story.output.json").exists())
        self.assertTrue((self.run_dir / "critic.report.json").exists())
        self.assertFalse((self.run_dir / "player.output.json").exists())
        self.assertNotIn("player", result["artifacts"])
        self.assertNotIn("characters", result["artifacts"])
        self.assertEqual(result["artifacts"]["called_actors"], ["player"])
        gm_loop = json.loads((self.run_dir / "gm.output.json").read_text(encoding="utf-8"))
        actor_outputs = json.loads((self.run_dir / "actor.outputs.json").read_text(encoding="utf-8"))
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        self.assertEqual(gm_loop["agent"], "gm_loop")
        self.assertEqual(actor_outputs["player"][0]["events"][0]["content"], "I follow the noise.")
        self.assertEqual(trace["schema_version"], 2)
        self.assertTrue(any("round_deliver.py" in " ".join(command) for command in delivery_calls))

    def test_run_round_dispatches_input_analyst_and_applies_before_gm(self):
        (self.run_dir / "prompts" / "input_analyst.prompt.md").write_text("# input analyst\n", encoding="utf-8")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["prompts"]["input_analyst"] = "prompts/input_analyst.prompt.md"
        manifest["expected_outputs"]["input_analysis"] = "input_analysis.output.json"
        _write_json(self.run_dir / "manifest.json", manifest)
        raw_text = "I follow the noise."
        _write_json(self.card / ".card_data.json", {"character_orchestration": {"major": []}})
        input_analysis = self.module.input_analysis_apply.input_analysis
        _write_json(
            self.run_dir / "input.raw.json",
            {
                "round_id": "round-000002",
                "raw_text": raw_text,
                "explicit_payload": {},
                "role_text": raw_text,
                "user_instruction_text": "",
                "source_integrity": {
                    "raw_text_sha256": input_analysis.sha256_text(raw_text),
                    "role_text_sha256": input_analysis.sha256_text(raw_text),
                    "user_instruction_text_sha256": input_analysis.sha256_text(""),
                    "raw_preserved": True,
                },
            },
        )

        order = []
        dispatch_payloads = {
            "input_analyst": {
                "schema_version": 1,
                "round_id": "round-000002",
                "analysis_mode": "fixture",
                "source_integrity": {
                    "raw_text_sha256": input_analysis.sha256_text(raw_text),
                    "role_text_sha256": input_analysis.sha256_text(raw_text),
                    "user_instruction_text_sha256": input_analysis.sha256_text(""),
                    "raw_preserved": True,
                },
                "semantic_units": [
                    {
                        "id": "unit-action-1",
                        "source_channel": "role_input",
                        "type": "action",
                        "raw_excerpt": raw_text,
                        "derived_summary": "The player follows the noise.",
                        "confidence": 0.9,
                        "visibility": "player_pov",
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
                    "rewrite_previous_output": False,
                    "expand_synopsis_before_continue": False,
                    "continue_after_player_action": True,
                    "must_stop_for_player_decision": False,
                },
                "routing": {
                    "role_channel": raw_text,
                    "user_instruction_channel": "",
                    "gm": False,
                    "player": True,
                    "characters": [],
                },
                "risks": [],
            },
            **_basic_responses(),
        }

        def fake_run_claude(agent_key, prompt, cwd):
            order.append(agent_key)
            payload = dispatch_payloads[agent_key]
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
        self.assertEqual(order[:3], ["input_analyst", "gm", "player"])
        applied_input = json.loads((self.run_dir / "input.json").read_text(encoding="utf-8"))
        applied_manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(applied_input["input_analysis"]["analysis_mode"], "fixture")
        self.assertIn(
            "analysis_applied",
            [entry.get("stage") for entry in applied_manifest.get("status", [])],
        )

    def test_run_round_retries_invalid_input_analyst_output_before_gm(self):
        (self.run_dir / "prompts" / "input_analyst.prompt.md").write_text("# input analyst\n", encoding="utf-8")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["prompts"]["input_analyst"] = "prompts/input_analyst.prompt.md"
        manifest["expected_outputs"]["input_analysis"] = "input_analysis.output.json"
        _write_json(self.run_dir / "manifest.json", manifest)
        raw_text = "I follow the noise.\nSetting: keep it quiet."
        role_text = "I follow the noise."
        instruction_text = "Setting: keep it quiet."
        _write_json(self.card / ".card_data.json", {"character_orchestration": {"major": []}})
        input_analysis = self.module.input_analysis_apply.input_analysis
        _write_json(
            self.run_dir / "input.raw.json",
            {
                "round_id": "round-000002",
                "raw_text": raw_text,
                "explicit_payload": {},
                "role_text": role_text,
                "user_instruction_text": instruction_text,
                "source_integrity": {
                    "raw_text_sha256": input_analysis.sha256_text(raw_text),
                    "role_text_sha256": input_analysis.sha256_text(role_text),
                    "user_instruction_text_sha256": input_analysis.sha256_text(instruction_text),
                    "raw_preserved": True,
                },
            },
        )

        valid_analysis = {
            "schema_version": 1,
            "round_id": "round-000002",
            "analysis_mode": "fixture",
            "source_integrity": {
                "raw_text_sha256": input_analysis.sha256_text(raw_text),
                "role_text_sha256": input_analysis.sha256_text(role_text),
                "user_instruction_text_sha256": input_analysis.sha256_text(instruction_text),
                "raw_preserved": True,
            },
            "semantic_units": [
                {
                    "id": "unit-action-1",
                    "source_channel": "role_input",
                    "type": "action",
                    "raw_excerpt": role_text,
                    "derived_summary": "The player follows the noise.",
                    "confidence": 0.9,
                    "visibility": "player_pov",
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
                "rewrite_previous_output": False,
                "expand_synopsis_before_continue": False,
                "continue_after_player_action": True,
                "must_stop_for_player_decision": False,
            },
            "routing": {
                "role_channel": role_text,
                "user_instruction_channel": instruction_text,
                "gm": True,
                "player": True,
                "characters": [],
            },
            "risks": [],
        }
        invalid_analysis = json.loads(json.dumps(valid_analysis))
        invalid_analysis["source_integrity"]["raw_text_sha256"] = "bad-hash"
        responses = _basic_responses()
        order = []
        analyst_attempts = {"count": 0}

        def fake_run_claude(agent_key, prompt, cwd):
            order.append(agent_key)
            if agent_key == "input_analyst":
                analyst_attempts["count"] += 1
                payload = invalid_analysis if analyst_attempts["count"] == 1 else valid_analysis
                return _agent_stream(json.dumps(payload, ensure_ascii=False))
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
        self.assertEqual(order[:3], ["input_analyst", "input_analyst", "gm"])
        applied_input = json.loads((self.run_dir / "input.json").read_text(encoding="utf-8"))
        self.assertEqual(applied_input["input_analysis"]["source_integrity"]["raw_text_sha256"], input_analysis.sha256_text(raw_text))

    def test_run_round_applies_existing_input_analysis_without_dispatching_analyst(self):
        (self.run_dir / "prompts" / "input_analyst.prompt.md").write_text("# input analyst\n", encoding="utf-8")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["prompts"]["input_analyst"] = "prompts/input_analyst.prompt.md"
        manifest["expected_outputs"]["input_analysis"] = "input_analysis.output.json"
        _write_json(self.run_dir / "manifest.json", manifest)
        _write_json(self.run_dir / "input_analysis.output.json", {"analysis": "already available"})

        order = []
        dispatch_payloads = _basic_responses()
        original_apply = getattr(self.module, "input_analysis_apply", None)

        def fake_run_claude(agent_key, prompt, cwd):
            order.append(agent_key)
            payload = dispatch_payloads[agent_key]
            return _agent_stream(json.dumps(payload, ensure_ascii=False))

        def fake_apply(card, root):
            order.append("apply")
            return {"ok": True}

        def fake_delivery(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout='{"action":"done"}\n', stderr="")

        try:
            self.module.input_analysis_apply = SimpleNamespace(apply_current_run=fake_apply)
            result = self.module.run_round(
                self.card,
                self.root,
                run_claude=fake_run_claude,
                run_command=fake_delivery,
            )
        finally:
            if original_apply is None:
                delattr(self.module, "input_analysis_apply")
            else:
                self.module.input_analysis_apply = original_apply

        self.assertTrue(result["ok"])
        self.assertNotIn("input_analyst", order)
        self.assertEqual(order[:2], ["apply", "gm"])

    def test_run_round_reuses_existing_input_analysis_after_blocked_without_reapply(self):
        (self.run_dir / "prompts" / "input_analyst.prompt.md").write_text("# input analyst\n", encoding="utf-8")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["stage"] = "blocked"
        manifest["critic_retry_count"] = 2
        manifest["prompts"]["input_analyst"] = "prompts/input_analyst.prompt.md"
        manifest["expected_outputs"]["input_analysis"] = "input_analysis.output.json"
        _write_json(self.run_dir / "manifest.json", manifest)
        _write_json(self.run_dir / "input_analysis.output.json", {"analysis": "already applied"})

        order = []
        dispatch_payloads = _basic_responses()
        original_apply = getattr(self.module, "input_analysis_apply", None)

        def fake_run_claude(agent_key, prompt, cwd):
            order.append(agent_key)
            payload = dispatch_payloads[agent_key]
            return _agent_stream(json.dumps(payload, ensure_ascii=False))

        def fail_apply(card, root):
            raise AssertionError("apply_current_run should not be called after blocked stage")

        def fake_delivery(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout='{"action":"done"}\n', stderr="")

        try:
            self.module.input_analysis_apply = SimpleNamespace(apply_current_run=fail_apply)
            result = self.module.run_round(
                self.card,
                self.root,
                run_claude=fake_run_claude,
                run_command=fake_delivery,
            )
        finally:
            if original_apply is None:
                delattr(self.module, "input_analysis_apply")
            else:
                self.module.input_analysis_apply = original_apply

        self.assertTrue(result["ok"])
        self.assertEqual(order[0], "gm")
        self.assertNotIn("input_analyst", order)
        final_manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(final_manifest.get("critic_retry_count"), 0)

    def test_run_round_requires_input_analyst_prompt_when_expected_output_declared(self):
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["expected_outputs"]["input_analysis"] = "input_analysis.output.json"
        manifest["prompts"].pop("input_analyst", None)
        _write_json(self.run_dir / "manifest.json", manifest)

        def fail_run_claude(agent_key, prompt, cwd):
            raise AssertionError(f"{agent_key} should not run before input analysis validation")

        with self.assertRaisesRegex(self.module.AgentExecutionError, "manifest.prompts.input_analyst"):
            self.module.run_round(
                self.card,
                self.root,
                run_claude=fail_run_claude,
                run_command=lambda command, **kwargs: SimpleNamespace(returncode=0, stdout='{"action":"done"}\n', stderr=""),
            )

    def test_run_round_rejects_existing_input_analysis_non_object(self):
        (self.run_dir / "prompts" / "input_analyst.prompt.md").write_text("# input analyst\n", encoding="utf-8")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["prompts"]["input_analyst"] = "prompts/input_analyst.prompt.md"
        manifest["expected_outputs"]["input_analysis"] = "input_analysis.output.json"
        _write_json(self.run_dir / "manifest.json", manifest)
        (self.run_dir / "input_analysis.output.json").write_text('["not", "object"]', encoding="utf-8")

        def fail_run_claude(agent_key, prompt, cwd):
            raise AssertionError(f"{agent_key} should not run with invalid existing input analysis")

        with self.assertRaisesRegex(self.module.AgentExecutionError, "input analysis output must be a JSON object"):
            self.module.run_round(
                self.card,
                self.root,
                run_claude=fail_run_claude,
                run_command=lambda command, **kwargs: SimpleNamespace(returncode=0, stdout='{"action":"done"}\n', stderr=""),
            )

    def test_run_round_without_input_analysis_expected_output_is_noop(self):
        order = []
        dispatch_payloads = _basic_responses()
        original_apply = getattr(self.module, "input_analysis_apply", None)

        def fake_run_claude(agent_key, prompt, cwd):
            order.append(agent_key)
            payload = dispatch_payloads[agent_key]
            return _agent_stream(json.dumps(payload, ensure_ascii=False))

        def fail_apply(card, root):
            raise AssertionError("apply_current_run should not be called when input_analysis is not expected")

        def fake_delivery(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout='{"action":"done"}\n', stderr="")

        try:
            self.module.input_analysis_apply = SimpleNamespace(apply_current_run=fail_apply)
            result = self.module.run_round(
                self.card,
                self.root,
                run_claude=fake_run_claude,
                run_command=fake_delivery,
            )
        finally:
            if original_apply is None:
                delattr(self.module, "input_analysis_apply")
            else:
                self.module.input_analysis_apply = original_apply

        self.assertTrue(result["ok"])
        self.assertEqual(order, ["gm", "player", "story", "critic"])

    def test_run_round_accepts_direct_agent_plain_json_output(self):
        responses = _basic_responses()

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
        actor_outputs = json.loads((self.run_dir / "actor.outputs.json").read_text(encoding="utf-8"))
        self.assertEqual(actor_outputs["player"][0]["events"][0]["content"], "I wait.")
        self.assertFalse((self.run_dir / "player.output.json").exists())

    def test_run_round_treats_delivery_retry_as_not_ok(self):
        responses = _basic_responses(
            player=_player_output("I follow the noise."),
            story=_story_output("<content>Too short.</content>"),
        )

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

    def test_analysis_only_mode_does_not_auto_repair_delivery_retry(self):
        _write_json(self.styles_dir / "settings.json", {"selfRepairMode": "analysis_only", "wordCount": 1})
        responses = _basic_responses(
            player=_player_output("I follow the noise."),
            story=_story_output("<content>足够。</content>"),
        )
        calls = []

        def fake_run_claude(agent_key, prompt, cwd):
            calls.append(agent_key)
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
        self.assertEqual(calls.count("story"), 1)
        self.assertEqual(calls.count("critic"), 1)
        self.assertEqual(result["delivery"]["result"]["action"], "retry")

    def test_run_round_retries_once_when_outer_model_skips_task(self):
        valid = _basic_responses(
            player=_player_output("I follow the noise."),
            story=_story_output("<content>I followed the noise.</content>"),
        )
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

    def test_full_mode_rolls_back_and_reruns_gm_loop_for_progression_repair(self):
        _write_json(self.styles_dir / "settings.json", {"selfRepairMode": "full", "wordCount": 1})
        calls = []
        gm_payloads = [
            _gm_output(scene_beats=[{"content": "The bad branch continues."}]),
            _gm_output(scene_beats=[{"content": "The repaired branch restarts cleanly."}]),
        ]
        player_payloads = [
            _player_output("I follow the bad branch."),
            _player_output("I follow the repaired branch."),
        ]
        story_payloads = [
            _story_output("<content>坏分支。</content>", metadata={"attempt": 1}),
            _story_output("<content>修复分支。</content>", metadata={"attempt": 2}),
        ]

        def fake_run_claude(agent_key, prompt, cwd):
            calls.append(agent_key)
            if agent_key == "gm":
                payload = gm_payloads.pop(0)
                if len(calls) > 1:
                    self.assertIn("Redo the failed stage", prompt)
            elif agent_key == "player":
                payload = player_payloads.pop(0)
            elif agent_key == "story":
                payload = story_payloads.pop(0)
            else:
                payload = _critic_pass()
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
                            "detail": _critic_revise_with_routing("gm_loop", "round_progression"),
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
        self.assertEqual(calls.count("gm"), 2)
        self.assertEqual(calls.count("player"), 2)
        self.assertEqual(calls.count("story"), 2)
        self.assertEqual(calls.count("critic"), 2)
        final_story_input = json.loads((self.run_dir / "story.input.json").read_text(encoding="utf-8"))
        self.assertEqual(
            final_story_input["loop_outputs"]["gm"]["outputs"][0]["scene_beats"][0]["content"],
            "The repaired branch restarts cleanly.",
        )

    def test_limited_mode_does_not_auto_repair_progression_routing(self):
        _write_json(self.styles_dir / "settings.json", {"selfRepairMode": "limited", "wordCount": 1})
        calls = []
        responses = _basic_responses(
            player=_player_output("I follow the noise."),
            story=_story_output("<content>坏分支。</content>"),
        )

        def fake_run_claude(agent_key, prompt, cwd):
            calls.append(agent_key)
            return _agent_stream(json.dumps(responses[agent_key], ensure_ascii=False))

        def fake_delivery(command, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "action": "retry",
                        "reason": "critic_revise",
                        "detail": _critic_revise_with_routing("gm_loop", "round_progression"),
                    },
                    ensure_ascii=False,
                )
                + "\n",
                stderr="",
            )

        result = self.module.run_round(
            self.card,
            self.root,
            run_claude=fake_run_claude,
            run_command=fake_delivery,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(calls.count("gm"), 1)
        self.assertEqual(calls.count("story"), 1)

    def test_run_round_retries_once_when_agent_returns_invalid_schema(self):
        calls = []

        def fake_run_claude(agent_key, prompt, cwd):
            calls.append(agent_key)
            if agent_key == "gm" and calls.count("gm") == 1:
                payload = {"gm_output": {"scene_state": {}, "notes": "old schema"}}
            elif agent_key == "gm":
                payload = _gm_output()
            elif agent_key == "player":
                payload = _player_output()
            elif agent_key == "story":
                payload = _story_output()
            else:
                payload = _critic_pass()
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

    def test_run_round_retries_actor_output_with_wrong_agent_id(self):
        calls = []

        def fake_run_claude(agent_key, prompt, cwd):
            calls.append(agent_key)
            if agent_key == "gm":
                payload = _gm_output()
            elif agent_key == "player" and calls.count("player") == 1:
                payload = dict(_player_output("I wait."))
                payload["agent_id"] = "character:Ada"
            elif agent_key == "player":
                payload = _player_output("I correct myself.")
            elif agent_key == "story":
                payload = _story_output()
            else:
                payload = _critic_pass()
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
        self.assertEqual(calls.count("player"), 2)
        actor_outputs = json.loads((self.run_dir / "actor.outputs.json").read_text(encoding="utf-8"))
        self.assertEqual(actor_outputs["player"][0]["events"][0]["content"], "I correct myself.")

    def test_run_round_retries_character_output_with_wrong_agent_id(self):
        (self.run_dir / "prompts" / "characters").mkdir()
        (self.run_dir / "prompts" / "characters" / "Ada.prompt.md").write_text("# Ada\n", encoding="utf-8")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["prompts"]["characters"] = {"Ada": "prompts/characters/Ada.prompt.md"}
        _write_json(self.run_dir / "manifest.json", manifest)
        input_payload = json.loads((self.run_dir / "input.json").read_text(encoding="utf-8"))
        input_payload["character_contexts"] = {"characters": [{"name": "Ada"}]}
        _write_json(self.run_dir / "input.json", input_payload)

        calls = []
        gm_payload = _gm_output(
            actor_calls=[
                {
                    "call_id": "call-character-Ada-1",
                    "actor_id": "character:Ada",
                    "prompt": "Ada sees the player enter.",
                    "reason": "Ada is present.",
                    "metadata": {},
                    "visibility_basis": _visibility_basis("character:Ada"),
                }
            ],
        )

        def fake_run_claude(agent_key, prompt, cwd):
            calls.append(agent_key)
            if agent_key == "gm":
                payload = gm_payload
            elif agent_key == "character:Ada" and calls.count("character:Ada") == 1:
                payload = _character_output("character:Bob", "This should be retried.")
            elif agent_key == "character:Ada":
                payload = _character_output("character:Ada", "Stay close.")
            elif agent_key == "player":
                payload = _player_output("I stay close.")
            elif agent_key == "story":
                payload = _story_output()
            else:
                payload = _critic_pass()
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
        self.assertEqual(calls.count("character:Ada"), 2)
        actor_outputs = json.loads((self.run_dir / "actor.outputs.json").read_text(encoding="utf-8"))
        self.assertEqual(actor_outputs["character:Ada"][0]["events"][0]["content"], "Stay close.")

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
                payload = _gm_output()
            elif agent_key == "player":
                payload = _player_output()
            elif agent_key == "story":
                payload = story_payloads.pop(0)
            else:
                payload = _critic_pass()
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
                payload = _gm_output()
            elif agent_key == "player":
                payload = _player_output()
            elif agent_key == "story":
                story_prompts.append(prompt)
                payload = story_payloads.pop(0)
            else:
                payload = _critic_pass()
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
        _write_json(self.styles_dir / "settings.json", {"selfRepairMode": "full", "wordCount": 1})
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
                payload = _gm_output()
            elif agent_key == "player":
                payload = _player_output()
            elif agent_key == "story":
                payload = story_payloads.pop(0)
            else:
                payload = _critic_pass()
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
                payload = _gm_output()
            elif agent_key == "player":
                payload = _player_output()
            elif agent_key == "story":
                payload = stories.pop(0)
            else:
                payload = _critic_pass()
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

    def test_run_round_resumes_from_existing_story_input_without_rerunning_loop(self):
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["stage"] = "story_ready"
        _write_json(self.run_dir / "manifest.json", manifest)
        _write_json(self.run_dir / "gm.output.json", {"agent": "gm_loop", "outputs": [_gm_output()]})
        _write_json(self.run_dir / "actor.outputs.json", {"player": [_player_output("I keep listening.")]})
        _write_json(
            self.run_dir / "story.input.json",
            {
                "round_id": "round-000002",
                "player_inputs": {
                    "raw_text": "I follow the noise.",
                    "routed_input": {"role_channel": "I follow the noise.", "user_instruction_channel": ""},
                },
                "loop_outputs": {
                    "gm": [_gm_output()],
                    "actors": {"player": [_player_output("I keep listening.")]},
                },
            },
        )
        calls = []

        def fake_run_claude(agent_key, prompt, cwd):
            calls.append(agent_key)
            if agent_key in {"gm", "player"} or agent_key.startswith("character:"):
                raise AssertionError(f"{agent_key} should not be rerun after story.input.json exists")
            payload = _story_output("<content>I kept listening near the flickering alley light.</content>")
            if agent_key == "critic":
                payload = _critic_pass()
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
        self.assertEqual(calls, ["story", "critic"])
        self.assertEqual(result["artifacts"]["called_actors"], ["player"])

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

    def test_run_round_ignores_stale_critic_token_placeholder_failure_when_story_is_clean(self):
        calls = []
        story = {
            "content": "<content>The repaired scene continues without placeholder tokens.</content><summary>Clean.</summary><options>Wait</options>",
            "character_dialogues": [],
            "metadata": {"attempt": 1},
            "tokens": {"in": "NNNN"},
        }
        stale_critic = {
            "decision": "revise",
            "hard_failures": ["story.output.json contains placeholder <tokens> values ('NNNN')"],
            "soft_issues": ["Prose could use sharper sensory detail."],
            "repair_instruction": "Remove fake token placeholders.",
            "system_iteration_suggestion": "",
        }

        def fake_run_claude(agent_key, prompt, cwd):
            calls.append(agent_key)
            if agent_key == "gm":
                payload = _gm_output()
            elif agent_key == "player":
                payload = _player_output()
            elif agent_key == "story":
                payload = dict(story)
            else:
                payload = dict(stale_critic)
            return _agent_stream(json.dumps(payload, ensure_ascii=False))

        delivery_attempts = []

        def fake_delivery(command, **kwargs):
            delivery_attempts.append(command)
            critic = json.loads((self.run_dir / "critic.report.json").read_text(encoding="utf-8"))
            if critic.get("decision") == "pass" and critic.get("hard_failures") == []:
                return SimpleNamespace(returncode=0, stdout='{"action":"done"}\n', stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"action": "retry", "reason": "critic_revise", "detail": critic}) + "\n",
                stderr="",
            )

        result = self.module.run_round(
            self.card,
            self.root,
            run_claude=fake_run_claude,
            run_command=fake_delivery,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(calls.count("story"), 1)
        self.assertEqual(calls.count("critic"), 1)
        self.assertEqual(len(delivery_attempts), 1)
        normalized_story = json.loads((self.run_dir / "story.output.json").read_text(encoding="utf-8"))
        normalized_critic = json.loads((self.run_dir / "critic.report.json").read_text(encoding="utf-8"))
        self.assertNotIn("<tokens>", normalized_story["content"].lower())
        self.assertNotIn("NNNN", normalized_story["content"])
        self.assertNotIn("tokens", normalized_story)
        self.assertEqual(normalized_critic["decision"], "pass")
        self.assertEqual(normalized_critic["hard_failures"], [])
        self.assertEqual(normalized_critic["soft_issues"], ["Prose could use sharper sensory detail."])

    def test_normalize_critic_report_keeps_token_failure_when_current_story_has_placeholder(self):
        hard_failures = ["story.output.json contains placeholder <tokens> values ('NNNN')"]
        critic = {
            "decision": "revise",
            "hard_failures": list(hard_failures),
            "soft_issues": ["Style can improve."],
            "repair_instruction": "Remove fake token placeholders.",
            "system_iteration_suggestion": "",
        }
        story = {
            "content": "<content>Draft.</content><tokens>in: NNNN</tokens><summary>Draft.</summary><options>Wait</options>",
            "character_dialogues": [],
            "metadata": {},
        }

        normalized = self.module._normalize_critic_report_for_story(critic, story)

        self.assertEqual(normalized["decision"], "revise")
        self.assertEqual(normalized["hard_failures"], hard_failures)
        self.assertEqual(normalized["soft_issues"], ["Style can improve."])

    def test_normalize_critic_report_keeps_non_token_failure_when_story_is_clean(self):
        token_failure = "story.output.json contains placeholder <tokens> values ('NNNN')"
        continuity_failure = "story skips the current player decision point"
        critic = {
            "decision": "revise",
            "hard_failures": [token_failure, continuity_failure],
            "soft_issues": [],
            "repair_instruction": "Fix the hard failures.",
            "system_iteration_suggestion": "",
        }
        story = {
            "content": "<content>Clean story without token placeholders.</content><summary>Clean.</summary><options>Wait</options>",
            "character_dialogues": [],
            "metadata": {},
        }

        normalized = self.module._normalize_critic_report_for_story(critic, story)

        self.assertEqual(normalized["decision"], "revise")
        self.assertEqual(normalized["hard_failures"], [continuity_failure])

    def test_normalize_critic_report_keeps_compound_token_and_non_token_failure_when_story_is_clean(self):
        compound_failure = (
            "story.output.json contains placeholder <tokens> values ('NNNN') "
            "and skips the current player decision point"
        )
        critic = {
            "decision": "revise",
            "hard_failures": [compound_failure],
            "soft_issues": [],
            "repair_instruction": "Fix the hard failure.",
            "system_iteration_suggestion": "",
        }
        story = {
            "content": "<content>Clean story without token placeholders.</content><summary>Clean.</summary><options>Wait</options>",
            "character_dialogues": [],
            "metadata": {},
        }

        normalized = self.module._normalize_critic_report_for_story(critic, story)

        self.assertEqual(normalized["decision"], "revise")
        self.assertEqual(normalized["hard_failures"], [compound_failure])

    def test_normalize_critic_report_keeps_token_failure_when_current_story_has_all_zero_tokens(self):
        hard_failures = ["story.output.json contains all-zero <tokens> values"]
        critic = {
            "decision": "revise",
            "hard_failures": list(hard_failures),
            "soft_issues": [],
            "repair_instruction": "Replace all-zero token values.",
            "system_iteration_suggestion": "",
        }
        story = {
            "content": "<content>Draft.</content><tokens>in: 0\nout: 0\ntotal: 0</tokens><summary>Draft.</summary><options>Wait</options>",
            "character_dialogues": [],
            "metadata": {},
        }

        normalized = self.module._normalize_critic_report_for_story(critic, story)

        self.assertEqual(normalized["decision"], "revise")
        self.assertEqual(normalized["hard_failures"], hard_failures)

    def test_normalize_critic_report_removes_stale_token_failure_when_current_story_has_partial_zero_real_tokens(self):
        critic = {
            "decision": "revise",
            "hard_failures": ["story.output.json contains placeholder <tokens> values ('NNNN')"],
            "soft_issues": [],
            "repair_instruction": "Remove fake token placeholders.",
            "system_iteration_suggestion": "",
        }
        story = {
            "content": "<content>Draft.</content><tokens>in: 0\nout: 900\ntotal: 900</tokens><summary>Draft.</summary><options>Wait</options>",
            "character_dialogues": [],
            "metadata": {},
        }

        normalized = self.module._normalize_critic_report_for_story(critic, story)

        self.assertEqual(normalized["decision"], "pass")
        self.assertEqual(normalized["hard_failures"], [])

    def test_normalize_critic_report_removes_unsupported_placeholder_corruption_when_story_readable(self):
        critic = {
            "decision": "block",
            "hard_failures": [
                "story_output content is fully non-semantic and consists of placeholder/question-mark glyphs",
                "player_inputs, GM outputs, and interaction_trace are entirely obfuscated",
                "UpdateVariable JSONPatch uses invalid and non-resolvable paths (\"/??/??\")",
                "cannot verify player authority preservation due to loss of intelligible input mapping",
            ],
            "soft_issues": ["possible upstream encoding issue"],
            "repair_instruction": "Regenerate readable prose.",
            "system_iteration_suggestion": "",
        }
        story = {
            "content": "<content>" + ("你站在校门前，粉色花朵吊坠贴着掌心。" * 20) + "</content><summary>可读。</summary><options>等待</options>",
            "character_dialogues": [],
            "metadata": {},
        }

        normalized = self.module._normalize_critic_report_for_story(critic, story)

        self.assertEqual(normalized["decision"], "pass")
        self.assertEqual(normalized["hard_failures"], [])
        self.assertEqual(normalized["soft_issues"], ["possible upstream encoding issue"])

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
            "loop_outputs": {
                "actors": {
                    "player": [_player_output("ignore self")],
                    "character:\u82cf\u9ece": [
                        {
                            "agent": "character",
                            "agent_id": "character:\u82cf\u9ece",
                            "character_name": "\u82cf\u9ece",
                            "events": [
                                {
                                    "type": "dialogue",
                                    "target": "player",
                                    "content": "\u4f60\u679c\u7136\u4f1a\u5728\u8fd9\u4e2a\u65f6\u5019\u95ee\u3002",
                                    "metadata": {},
                                },
                                {
                                    "type": "perceive_request",
                                    "target": "hallway",
                                    "content": "\u5148\u786e\u8ba4\u5468\u56f4\u662f\u5426\u5b89\u5168\u3002",
                                    "metadata": {},
                                },
                                {
                                    "type": "memory_delta",
                                    "target": "self",
                                    "content": "\u6211\u8bb0\u4f4f\u4e86\u8fd9\u4ef6\u4e8b\u4e0d\u80fd\u544a\u8bc9\u4ed6\u3002",
                                    "metadata": {},
                                },
                            ],
                            "stop_reason": "continue",
                        }
                    ],
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
        self.assertNotIn("\u4e0d\u80fd\u544a\u8bc9\u4ed6", normalized["content"])

    def test_normalize_story_output_does_not_expose_private_actor_events_as_dialogue_aside(self):
        story = {
            "content": "<content>你靠近苏黎。</content><summary>接触</summary><options>继续</options>",
            "character_dialogues": [],
            "metadata": {},
        }
        story_input = {
            "loop_outputs": {
                "actors": {
                    "character:SuLi": [
                        {
                            "agent": "character",
                            "agent_id": "character:SuLi",
                            "character_name": "SuLi",
                            "events": [
                                {
                                    "type": "dialogue",
                                    "target": "player",
                                    "content": "Where did you get that?",
                                    "metadata": {},
                                },
                                {
                                    "type": "memory_delta",
                                    "target": "self",
                                    "content": "I must not tell him about the old ritual.",
                                    "metadata": {},
                                },
                            ],
                            "stop_reason": "continue",
                        }
                    ],
                }
            }
        }

        normalized = self.module._normalize_story_output(story, story_input)

        self.assertEqual(normalized["character_dialogues"], [
            {
                "name": "SuLi",
                "source": "subagent",
                "line": "Where did you get that?",
            }
        ])
        self.assertNotIn("old ritual", normalized["content"])

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

    def test_story_preflight_repair_context_uses_current_loop_sources(self):
        context = self.module._story_preflight_repair_context(
            {"content": "<content>bad draft</content>"},
            ["content_chinese_chars 3 is below required minimum 10"],
            {"minimum_chinese_chars": 10},
            1,
            None,
        )

        self.assertIn("loop_outputs", context["authoritative_sources"])
        self.assertIn("actor.outputs.json", context["authoritative_sources"])
        self.assertNotIn("player_output", context["authoritative_sources"])
        self.assertNotIn("character_outputs", context["authoritative_sources"])

    def test_story_preflight_repair_context_explains_delivery_word_count(self):
        context = self.module._story_preflight_repair_context(
            {"content": "<content>短稿。</content>"},
            ["content_chinese_chars 1858 is below required minimum 3200"],
            {"word_count_target": 4000, "minimum_chinese_chars": 3200},
            2,
            None,
        )

        self.assertIn("round_deliver.py count_chinese", context["word_count_contract"]["method"])
        self.assertEqual(context["word_count_contract"]["minimum_chinese_chars"], 3200)
        self.assertEqual(context["word_count_contract"]["recommended_chinese_chars"], 3600)
        self.assertEqual(context["word_count_contract"]["current_chinese_chars"], 2)
        self.assertEqual(context["word_count_contract"]["missing_chinese_chars"], 3198)
        self.assertIn("excluding tags", context["instruction"])
        self.assertIn("3600", context["instruction"])
        self.assertIn("Do not summarize or shorten", context["instruction"])
        self.assertIn("sensory detail", context["instruction"])

    def test_critic_skill_does_not_require_story_agent_tokens(self):
        skill = (ROOT / ".claude" / "skills" / "rp-critic-agent.md").read_text(encoding="utf-8")

        self.assertIn("Do not hard-fail", skill)
        self.assertIn("missing `<tokens>`", skill)
        self.assertIn("current `story_output.content` literally contains", skill)
        self.assertIn("Do not report token failures from historical rejected drafts", skill)

    def test_critic_skill_does_not_treat_redacted_markers_as_mojibake(self):
        skill = (ROOT / ".claude" / "skills" / "rp-critic-agent.md").read_text(encoding="utf-8")

        self.assertIn("`[redacted]`", skill)
        self.assertIn("not mojibake", skill)
        self.assertIn("story_output.content", skill)

    def test_story_skill_forbids_story_agent_tokens(self):
        skill = (ROOT / ".claude" / "skills" / "rp-story-agent.md").read_text(encoding="utf-8")

        self.assertIn("Do not emit `<tokens>`", skill)
        self.assertIn("delivery/handler appends the real token block", skill)

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
        responses = _basic_responses()

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
        self.assertIn("loop_outputs", context["authoritative_sources"])
        self.assertIn("actor.outputs.json", context["authoritative_sources"])
        self.assertNotIn("player_output", context["authoritative_sources"])
        self.assertNotIn("character_outputs", context["authoritative_sources"])


if __name__ == "__main__":
    unittest.main()
