import importlib.util
import json
import os
import subprocess
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


class RpGenerateCliImportBoundaryTest(unittest.TestCase):
    def test_import_does_not_require_agent_dispatcher(self):
        script = f"""
import importlib.abc
import pathlib
import sys

class BlockDispatcher(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "agent_dispatcher":
            raise ImportError("agent_dispatcher is not part of the thin runtime boundary")
        return None

sys.path.insert(0, {str(ROOT / "skills")!r})
sys.meta_path.insert(0, BlockDispatcher())
import rp_generate_cli
print(rp_generate_cli.run_round.__name__)
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.strip(), "run_round")


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
    stop_reason="complete",
):
    if actor_calls is None:
        actor_calls = []
    return {
        "agent": "gm",
        "scene_beats": scene_beats if scene_beats is not None else [{"content": "The alley light flickers."}],
        "events": events if events is not None else [],
        "actor_calls": actor_calls,
        "parallel_groups": [],
        "world_state_delta": world_state_delta if world_state_delta is not None else [],
        "decision_point": decision_point,
        "stop_reason": stop_reason,
    }


def _gm_player_call(call_id="call-player-1"):
    return {
        "call_id": call_id,
        "actor_id": "player",
        "prompt": "Respond to the current player action.",
        "reason": "The player is the only required actor for this turn.",
        "metadata": {},
        "visibility_basis": _visibility_basis("player"),
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


def _projection_output(actor_id, source_call_id, final_actor_message):
    return {
        "decision": "pass",
        "target_actor_id": actor_id,
        "source_call_id": source_call_id,
        "final_actor_message": final_actor_message,
        "feedback": "",
    }


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


def _postprocess_output(
    *,
    summary="Postprocess summary.",
    current_goal="Follow the noise.",
    options=None,
    state_patch=None,
):
    return {
        "schema_version": 1,
        "core": {
            "summary": summary,
            "current_goal": current_goal,
            "options": options if options is not None else ["Continue following the noise."],
            "state_patch": state_patch if state_patch is not None else {"quest": current_goal},
        },
        "ui_extensions": {
            "status_panels": {},
            "custom_cards": {},
            "asset_bindings": {},
        },
        "ui_extension_status": {"status": "ok", "issues": []},
    }


def _basic_responses(*, gm=None, player=None, story=None, critic=None, postprocess=None):
    return {
        "gm": gm if gm is not None else _gm_output(),
        "player": player if player is not None else _player_output(),
        "story": story if story is not None else _story_output(),
        "critic": critic if critic is not None else _critic_pass(),
        "postprocess": postprocess if postprocess is not None else _postprocess_output(),
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
        for name in ["gm", "player", "story", "critic", "postprocess"]:
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
        self.agent_intents = importlib.import_module("agent_intents")

    def _write_player_context_packet(self):
        _write_json(
            self.run_dir / "player.context.json",
            {
                "actor_id": "player",
                "agent": "player",
                "visibility": "first_person_player",
                "immersive_context": "You are following the noise.",
                "memory": {"key_memories": ["You heard the alley noise."]},
                "visible_events": [],
            },
        )

    def _write_character_context_packet(self, safe_name="Ada"):
        _write_json(
            self.run_dir / "characters" / f"{safe_name}.context.json",
            {
                "actor_id": f"character:{safe_name}",
                "agent": "character",
                "visibility": "first_person_character",
                "immersive_context": f"{safe_name} is watching the player enter.",
                "memory": {"key_memories": [f"{safe_name} is present in the scene."]},
                "visible_events": [],
            },
        )

    def _queue_run_gm_turn(self):
        return self.agent_intents.create_intent(
            self.run_dir,
            {
                "requested_by": "test",
                "type": "run_gm_turn",
                "payload": {"reason": "legacy_run_round_fixture"},
            },
        )["intent"]

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

    def test_extract_json_object_rejects_malformed_top_level_object_instead_of_nested_fallback(self):
        malformed = (
            '{"agent":"gm","scene_beats":[{"content":"He said hello",'
            '"metadata":{"npc":"Ada"}}'
        )

        with self.assertRaisesRegex(self.module.AgentExecutionError, "invalid JSON"):
            self.module._extract_json_object(malformed)

    def test_extract_json_object_repairs_unescaped_quotes_inside_string_values(self):
        malformed = '{"text":"大家都"一如往常"地交流","ok":true}'

        payload = self.module._extract_json_object(malformed)

        self.assertEqual(payload["text"], '大家都"一如往常"地交流')
        self.assertTrue(payload["ok"])

    def test_outer_prompt_does_not_wrap_nested_prompt_in_markdown_fence(self):
        nested_prompt = '## Required Output Contract\n```json\n{"agent":"gm"}\n```\n## Context Packet\n```json\n{"role_channel":"I ask."}\n```'

        outer = self.module._outer_prompt("gm", nested_prompt)

        self.assertNotIn("```markdown", outer)
        self.assertNotIn("TOOL_NOT_AVAILABLE", outer)
        self.assertIn("<subagent_prompt>", outer)
        self.assertIn("</subagent_prompt>", outer)
        self.assertEqual(outer.count("<subagent_prompt>"), 1)
        self.assertEqual(outer.count("</subagent_prompt>"), 1)
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
        reset_files = [
            "gm.output.json",
            "actor.outputs.json",
            "interaction.trace.json",
            "story.input.json",
            "story.output.json",
            "critic.report.json",
        ]
        for name in reset_files:
            _write_json(self.run_dir / name, {"source": "root", "name": name})
            _write_json(self.run_dir / "artifacts" / name, {"source": "artifacts", "name": name})

        self.module._reset_round_progression_outputs(self.run_dir)

        self.assertTrue(job_path.exists())
        for name in reset_files:
            self.assertFalse((self.run_dir / name).exists(), name)
            self.assertFalse((self.run_dir / "artifacts" / name).exists(), name)

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

    def test_read_loop_prompt_generates_projection_prompt(self):
        prompt = self.module._read_loop_prompt(
            self.run_dir,
            json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8")),
            "projection",
            {
                "target_actor_id": "character:Ada",
                "source_call_id": "call-ada-1",
                "actor_context": "I am Ada and I only know what I can perceive.",
                "gm_message": "You hear two careful knocks at the archive door.",
            },
        )

        self.assertIn("Projection Agent Prompt", prompt)
        self.assertIn("character:Ada", prompt)
        self.assertIn("call-ada-1", prompt)
        self.assertIn("You hear two careful knocks at the archive door.", prompt)

    def test_read_loop_prompt_uses_current_character_packet_over_static_prompt(self):
        character_prompt = self.run_dir / "prompts" / "characters" / "Ada.prompt.md"
        character_prompt.parent.mkdir(parents=True)
        character_prompt.write_text("STATIC PROMPT WITHOUT PROJECTED MESSAGE", encoding="utf-8")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["prompts"]["characters"] = {"Ada": "prompts/characters/Ada.prompt.md"}

        prompt = self.module._read_loop_prompt(
            self.run_dir,
            manifest,
            "character:Ada",
            {
                "actor_id": "character:Ada",
                "character_name": "Ada",
                "gm_prompt": "You hear two careful knocks at the archive door.",
                "immersive_context": "我是 Ada。",
            },
        )

        self.assertIn("You hear two careful knocks at the archive door.", prompt)
        self.assertNotIn("STATIC PROMPT WITHOUT PROJECTED MESSAGE", prompt)

    def test_read_loop_prompt_uses_current_player_packet_over_static_prompt(self):
        player_prompt = self.run_dir / "prompts" / "player.prompt.md"
        player_prompt.parent.mkdir(parents=True, exist_ok=True)
        player_prompt.write_text("STATIC PLAYER PROMPT WITHOUT PROJECTED MESSAGE", encoding="utf-8")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))

        prompt = self.module._read_loop_prompt(
            self.run_dir,
            manifest,
            "player",
            {
                "actor_id": "player",
                "gm_prompt": "你听见班长在桌前问：你的作业呢？",
                "card_folder": str(self.card),
            },
        )

        self.assertIn("你听见班长在桌前问：你的作业呢？", prompt)
        self.assertNotIn("STATIC PLAYER PROMPT WITHOUT PROJECTED MESSAGE", prompt)

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

        with self.assertRaisesRegex(self.module.AgentExecutionError, "action is not an allowed actor output field"):
            self.module._validate("player", payload)

    def test_dispatch_actor_accepts_natural_language_reply_without_json(self):
        reply = "我把手从门把上收回来，低声说：先别进去。"
        packet = {"actor_id": "player", "character_name": "雨蒙"}

        result = self.module._dispatch_agent_payload(
            "player",
            "# player\n",
            self.root,
            lambda _agent_key, _prompt, _cwd: _agent_stream(reply),
            extra_context={"loop_packet": packet},
        )

        self.assertEqual(result["agent"], "player")
        self.assertEqual(result["agent_id"], "player")
        self.assertEqual(result["natural_reply"], reply)
        self.assertEqual(result["events"], [{"type": "reply", "target": "gm", "content": reply, "metadata": {}}])
        self.assertNotIn("stop_reason", result)

    def test_dispatch_actor_reruns_after_key_memory_recall_protocol(self):
        actor_dir = self.card / "characters" / "雨蒙"
        actor_dir.mkdir(parents=True, exist_ok=True)
        (self.card / "characters" / "player.md").write_text(
            "name: 雨蒙\npath: characters/雨蒙\n",
            encoding="utf-8",
        )
        (actor_dir / "key_memories.json").write_text(
            json.dumps(
                {
                    "memories": [
                        {
                            "tag": "封存索引",
                            "summary": "我知道它和Ada的灯有关",
                            "detail": "索引藏在Ada灯座下方。",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        prompts = []

        def fake_run_claude(agent_key, prompt, cwd):
            prompts.append(prompt)
            if len(prompts) == 1:
                return _agent_stream("我想回忆：封存索引")
            return _agent_stream("我想起灯座下方的索引，低声提醒自己。")

        result = self.module._dispatch_agent_payload(
            "player",
            "# player\n",
            self.root,
            fake_run_claude,
            extra_context={"loop_packet": {"actor_id": "player", "card_folder": str(self.card)}},
        )

        self.assertEqual(len(prompts), 2)
        self.assertIn("索引藏在Ada灯座下方。", prompts[1])
        self.assertEqual(result["natural_reply"], "我想起灯座下方的索引，低声提醒自己。")

    def test_dispatch_post_round_memory_reruns_after_key_memory_recall_protocol(self):
        actor_dir = self.card / "characters" / "Ada"
        actor_dir.mkdir(parents=True, exist_ok=True)
        (actor_dir / "key_memories.json").write_text(
            json.dumps(
                {
                    "memories": [
                        {
                            "tag": "雨夜披风",
                            "summary": "玩家曾把披风借给我",
                            "detail": "那天雨很冷，我记得披风边缘有银线。",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        prompts = []

        def fake_run_claude(agent_key, prompt, cwd):
            prompts.append(prompt)
            if len(prompts) == 1:
                return _agent_stream("我想回忆：雨夜披风")
            payload = {
                "agent_id": "character:Ada",
                "character_name": "Ada",
                "long_term_memories": "我记得雨夜里玩家借给我披风。",
                "key_memories": [
                    {
                        "tag": "雨夜披风",
                        "summary": "玩家曾把披风借给我",
                        "detail": "那天雨很冷，我记得披风边缘有银线。",
                    }
                ],
            }
            return _agent_stream(json.dumps(payload, ensure_ascii=False))

        result = self.module._dispatch_agent_payload(
            "post_round_memory",
            "# memory\n",
            self.root,
            fake_run_claude,
            extra_context={
                "card_folder": str(self.card),
                "post_round_memory_job": {"agent_id": "character:Ada", "character_name": "Ada"},
                "post_round_output_path": "post_round_memory_jobs/character_Ada.summary.json",
            },
        )

        self.assertEqual(len(prompts), 2)
        self.assertIn("披风边缘有银线", prompts[1])
        self.assertEqual(result["agent_id"], "character:Ada")
        self.assertIn("雨夜", result["long_term_memories"])

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

    def test_dispatch_agent_payload_adds_json_repair_feedback_after_malformed_output(self):
        payload = _gm_output(actor_calls=[], stop_reason="complete")
        attempts = []
        malformed = (
            '```json\n'
            '{"agent":"gm","scene_beats":[{"content":"He said "hello" without escaping.",'
            '"metadata":{"npc":"Ada"}}]}\n'
            '```'
        )

        def fake_run_claude(agent_key, prompt, cwd):
            attempts.append(prompt)
            if len(attempts) == 1:
                return _agent_stream(malformed)
            return _agent_stream(json.dumps(payload, ensure_ascii=False))

        result = self.module._dispatch_agent_payload(
            "gm",
            "# gm\n",
            self.root,
            fake_run_claude,
        )

        self.assertEqual(result["agent"], "gm")
        self.assertEqual(len(attempts), 2)
        self.assertIn("Previous Attempt Rejection", attempts[1])
        self.assertIn("valid JSON", attempts[1])
        self.assertIn("Escape", attempts[1])

    def test_dispatch_agent_payload_recovers_story_content_from_malformed_json_string(self):
        attempts = []
        malformed = (
            '```json\n'
            '{"content":"<content><p>Ada said "hello" by the window.</p></content>",'
            '"character_dialogues":[{"agent_id":"character:Ada","content":"hi"}],'
            '"metadata":{"round_id":"round-1"}}\n'
            '```'
        )

        def fake_run_claude(agent_key, prompt, cwd):
            attempts.append(prompt)
            return _agent_stream(malformed)

        result = self.module._dispatch_agent_payload(
            "story",
            "# story\n",
            self.root,
            fake_run_claude,
        )

        self.assertEqual(len(attempts), 1)
        self.assertIn('"hello"', result["content"])
        self.assertEqual(result["character_dialogues"][0]["agent_id"], "character:Ada")
        self.assertEqual(result["metadata"]["round_id"], "round-1")
        self.assertNotIn("recovery_error", result["metadata"])

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

    def test_run_round_uses_thin_round_runtime_by_default(self):
        calls = []

        def fake_run_round(card, root, *, run_claude=None, run_command=None):
            calls.append((Path(card), Path(root), run_claude, run_command))
            return {"ok": True, "action": "generated", "runtime": {"mode": "thin"}}

        original_run_round = self.module.round_runtime.run_round
        try:
            self.module.round_runtime.run_round = fake_run_round

            result = self.module.run_round(
                self.card,
                self.root,
                run_claude=lambda *args: "",
                run_command=lambda *args, **kwargs: None,
            )
        finally:
            self.module.round_runtime.run_round = original_run_round

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "generated")
        self.assertEqual(result["runtime"]["mode"], "thin")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], self.card)
        self.assertEqual(calls[0][1], self.root)

    def test_removed_story_quality_gate_helpers_are_absent_from_cli_surface(self):
        self.assertFalse(hasattr(self.module, "_delivery_requirements"))
        self.assertFalse(hasattr(self.module, "_story_" + "pre" + "flight_issues"))
        self.assertFalse(hasattr(self.module, "_story_" + "pre" + "flight_repair_context"))

    def test_ensure_input_analysis_retries_malformed_json_with_rejection_feedback(self):
        (self.run_dir / "prompts" / "input_analyst.prompt.md").write_text("# input analyst\n", encoding="utf-8")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["prompts"]["input_analyst"] = "prompts/input_analyst.prompt.md"
        manifest["expected_outputs"]["input_analysis"] = "input_analysis.output.json"
        _write_json(self.run_dir / "manifest.json", manifest)
        attempts = []
        malformed = (
            '```json\n'
            '{"schema_version":1 "routing":{"user_instruction_channel":"设定重要角色：苏黎"}}\n'
            '```'
        )
        valid_analysis = {
            "schema_version": 1,
            "round_id": "round-000002",
            "analysis_mode": "fixture",
            "source_integrity": {"raw_preserved": True},
            "semantic_units": [],
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
            "routing": {"gm": True, "player": True, "characters": []},
            "routing_requests": [],
            "capability_requests": [],
            "risks": [],
        }
        original_apply = getattr(self.module, "input_analysis_apply", None)

        def fake_run_claude(agent_key, prompt, cwd):
            self.assertEqual(agent_key, "input_analyst")
            attempts.append(prompt)
            if len(attempts) == 1:
                return _agent_stream(malformed)
            return _agent_stream(json.dumps(valid_analysis, ensure_ascii=False))

        try:
            self.module.input_analysis_apply = SimpleNamespace(apply_current_run=lambda _card, _root: {"applied": True})
            result = self.module._ensure_input_analysis(self.run_dir, manifest, self.card, self.root, fake_run_claude)
        finally:
            if original_apply is None:
                delattr(self.module, "input_analysis_apply")
            else:
                self.module.input_analysis_apply = original_apply

        self.assertEqual(result, {"applied": True})
        self.assertEqual(len(attempts), 2)
        self.assertIn("Previous Attempt Rejection", attempts[1])
        self.assertIn("valid JSON", attempts[1])
        written = json.loads((self.run_dir / "input_analysis.output.json").read_text(encoding="utf-8"))
        self.assertEqual(written["schema_version"], 1)

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

    def test_normalize_story_output_normalizes_derived_content_edit_aliases(self):
        story = {
            "content": (
                "<derived_content_edits>\n"
                "[{\"turn\": 1, \"field\": \"ai\", \"action\": \"replace\", "
                "\"replacement\": \"（梦中）\n教室像隔着水声。\"}]\n"
                "</derived_content_edits>\n"
                "<p>Current scene.</p>"
            ),
            "character_dialogues": [],
            "metadata": {},
        }

        normalized = self.module._normalize_story_output(story)

        self.assertEqual(normalized["derived_content_edits"][0]["turn_index"], 0)
        self.assertIn("教室像隔着水声", normalized["derived_content_edits"][0]["ai"])
        self.assertIn("<derived_content_edits>", normalized["content"])
        self.assertIn('"turn_index": 0', normalized["content"])
        self.assertNotIn('"turn": 1', normalized["content"])

    def test_normalize_critic_report_revises_retcon_story_with_only_first_paragraph_edit(self):
        critic = {
            "decision": "pass",
            "hard_failures": [],
            "soft_issues": [],
            "repair_instruction": "",
            "system_iteration_suggestion": "",
        }
        story = {
            "content": "<p>Current.</p>",
            "character_dialogues": [],
            "derived_content_edits": [
                {"turn_index": 0, "first_paragraph": "（梦中）教室像隔着水声。"}
            ],
        }
        story_input = {
            "player_inputs": {
                "input_analysis": {
                    "narrative_directives": {"rewrite_previous_output": True},
                    "world_updates": {"retcon_requests": [{"id": "r1", "text": "dream"}]},
                }
            }
        }

        normalized = self.module._normalize_critic_report_for_story(critic, story, story_input)

        self.assertEqual(normalized["decision"], "revise")
        self.assertIn("derived_content_edits", "\n".join(normalized["hard_failures"]))
        self.assertIn("complete replacement", normalized["repair_instruction"])

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
            "content": "<content>Draft.</content><tokens>in: NNNN</tokens>",
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
            "content": "<content>Clean story without token placeholders.</content>",
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
            "content": "<content>Clean story without token placeholders.</content>",
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
            "content": "<content>Draft.</content><tokens>in: 0\nout: 0\ntotal: 0</tokens>",
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
            "content": "<content>Draft.</content><tokens>in: 0\nout: 900\ntotal: 900</tokens>",
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
            "content": "<content>" + ("你站在校门前，粉色花朵吊坠贴着掌心。" * 20) + "</content>",
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
                "<content>你在教室里醒来。</content>"
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
            "content": "<content>你靠近苏黎。</content>",
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
                                    "type": "reply",
                                    "target": "player",
                                    "content": "\u4f60\u679c\u7136\u4f1a\u5728\u8fd9\u4e2a\u65f6\u5019\u95ee\u3002",
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
            }
        ])
        self.assertIn(
            '"source": "subagent"',
            normalized["content"],
        )
        self.assertNotIn("\u4e0d\u80fd\u544a\u8bc9\u4ed6", normalized["content"])

    def test_normalize_story_output_does_not_expose_private_actor_events_as_dialogue_aside(self):
        story = {
            "content": "<content>你靠近苏黎。</content>",
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
                                    "type": "reply",
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

    def test_player_character_names_come_from_input_analysis_not_raw_keyword_matching(self):
        story_input = {
            "player_inputs": {
                "raw_text": "我叫雨蒙。",
                "input_analysis": {
                    "world_updates": {
                        "important_characters": [
                            {"name": "雨蒙", "status": "active"},
                        ],
                    },
                },
            },
        }

        self.assertEqual(self.module._player_character_names_from_story_input(story_input), ["雨蒙"])

        story_input["player_inputs"].pop("input_analysis")
        self.assertEqual(self.module._player_character_names_from_story_input(story_input), [])

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

    def test_critic_skill_routes_length_as_story_quality_not_delivery_gate(self):
        skill = (ROOT / ".claude" / "skills" / "rp-critic-agent.md").read_text(encoding="utf-8")

        self.assertIn("Length", skill)
        self.assertIn("quality_checks", skill)
        self.assertIn('`stage: "story_composition"` when GM/actor facts are usable and only final prose, structure, tags, polish, or length need regeneration.', skill)
        self.assertIn('`stage: "delivery_gate"` when the failure is a mechanical delivery contract such as required response tags, JSON/schema shape, artifact readiness, response mirroring, or handler/parser execution.', skill)
        self.assertNotIn("mechanical delivery contract such as word count", skill)

    def test_story_skill_forbids_story_agent_tokens(self):
        skill = (ROOT / ".claude" / "skills" / "rp-story-agent.md").read_text(encoding="utf-8")

        self.assertIn("Do not emit `<tokens>`", skill)
        self.assertIn("delivery/handler appends the real token block", skill)

    def test_story_skill_assigns_mvu_commands_to_postprocess(self):
        skill = (ROOT / ".claude" / "skills" / "rp-story-agent.md").read_text(encoding="utf-8")

        self.assertIn("Do not emit `<UpdateVariable>`", skill)
        self.assertIn("postprocess owns MVU variable update commands", skill)
        self.assertNotIn("Use `<UpdateVariable><JSONPatch>", skill)

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

if __name__ == "__main__":
    unittest.main()
