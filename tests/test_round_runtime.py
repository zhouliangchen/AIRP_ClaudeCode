import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _fake_run_claude(agent_key, prompt, cwd):
    payloads = {
        "input_analyst": {
            "schema_version": 1,
            "round_id": "round-000001",
            "analysis_mode": "fixture",
            "source_integrity": {},
            "semantic_units": [],
            "routed_input": {"role_channel": "我推开门。"},
            "world_updates": {},
            "narrative_directives": {},
            "routing_requests": [],
            "capability_requests": [],
            "risks": [],
        },
        "gm": {
            "agent": "gm",
            "scene_beats": [{"content": "门后传来微弱灯光。", "metadata": {}}],
            "events": [{"type": "world_event", "target": "", "content": "门被推开。", "metadata": {}}],
            "actor_calls": [],
            "parallel_groups": [],
            "world_state_delta": [],
            "character_promotions": [],
            "subgm_commands": [],
            "decision_point": None,
            "stop_reason": "complete",
        },
        "story": {
            "content": "<content>你推开门，灯光从门缝里流出来。</content>",
            "character_dialogues": [],
            "metadata": {},
        },
        "critic": {
            "decision": "pass",
            "hard_failures": [],
            "soft_issues": [],
            "repair_instruction": "",
            "system_iteration_suggestion": "",
            "quality_checks": {},
        },
        "postprocess": {
            "schema_version": 1,
            "core": {
                "summary": "你推开门。",
                "current_goal": "观察门后",
                "options": [{"label": "进入房间"}],
            },
            "mvu": {"commands": []},
            "ui_extensions": {},
            "ui_extension_status": {"status": "ok", "issues": []},
        },
    }
    return json.dumps(payloads[agent_key], ensure_ascii=False)


def _fake_run_command(*args, **kwargs):
    return SimpleNamespace(
        returncode=0,
        stdout=json.dumps({"action": "done", "generatedCount": 1}, ensure_ascii=False),
        stderr="",
    )


class RoundRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.card = self.root / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000001"
        self.run_dir.mkdir(parents=True)
        (self.card / ".agent_runs" / "current").write_text(str(self.run_dir.resolve()), encoding="utf-8")
        (self.run_dir / "prompts").mkdir()
        for name in ["input_analyst", "gm", "story", "critic"]:
            (self.run_dir / "prompts" / f"{name}.prompt.md").write_text(f"# {name}\n", encoding="utf-8")
        _write_json(
            self.run_dir / "manifest.json",
            {
                "round_id": "round-000001",
                "stage": "prompts_ready",
                "prompts": {
                    "input_analyst": "prompts/input_analyst.prompt.md",
                    "gm": "prompts/gm.prompt.md",
                    "story": "prompts/story.prompt.md",
                    "critic": "prompts/critic.prompt.md",
                },
                "expected_outputs": {
                    "input_analysis": "input_analysis.output.json",
                    "gm": "gm.output.json",
                    "story": "story.output.json",
                    "critic": "critic.report.json",
                },
                "runtime_settings": {"style": "default", "wordCount": 800, "nsfw": False},
                "style_profile": {},
            },
        )
        _write_json(
            self.run_dir / "input.json",
            {
                "raw_text": "我推开门。",
                "routed_input": {"role_channel": "我推开门。"},
                "runtime_settings": {"style": "default", "wordCount": 800, "nsfw": False},
            },
        )
        self.round_runtime = _load_module("round_runtime")

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_round_writes_thin_runtime_artifacts_and_delivers(self):
        original_apply = self.round_runtime.input_analysis_apply.apply_current_run
        self.round_runtime.input_analysis_apply.apply_current_run = lambda *_args, **_kwargs: {
            "ok": True,
            "capability_requests": [],
            "manifest": {
                "runtime_settings": {"style": "default", "wordCount": 800, "nsfw": False},
                "style_profile": {},
            },
        }
        try:
            result = self.round_runtime.run_round(
                self.card,
                self.root,
                run_claude=_fake_run_claude,
                run_command=_fake_run_command,
            )
        finally:
            self.round_runtime.input_analysis_apply.apply_current_run = original_apply

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "generated")
        self.assertEqual(result["runtime"]["mode"], "thin")
        self.assertEqual(
            result["runtime"]["stages"],
            ["input_analysis", "gm_collaboration", "story", "critic", "postprocess", "delivery"],
        )
        artifacts = self.run_dir / "artifacts"
        self.assertTrue((artifacts / "input_analysis.output.json").exists())
        self.assertTrue((artifacts / "gm.output.json").exists())
        self.assertTrue((artifacts / "story.input.json").exists())
        self.assertTrue((artifacts / "story.output.json").exists())
        self.assertTrue((artifacts / "critic.report.json").exists())
        self.assertTrue((artifacts / "postprocess.output.json").exists())
        self.assertTrue((artifacts / "delivery.result.json").exists())
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "delivered")

    def test_run_round_audits_input_analysis_capability_requests(self):
        request = {
            "id": "unknown-capability",
            "requested_by": "input_analyst",
            "target": "main-agent",
            "capability": "external.weather_lookup",
            "summary": "Weather lookup requested.",
            "reason": "The player asked for weather.",
            "source_channel": "user_instruction",
            "risk": "low",
            "authorization_gate": "none",
            "payload": {},
            "evidence": {"raw_excerpt": "check the weather"},
        }
        original_apply = self.round_runtime.input_analysis_apply.apply_current_run
        self.round_runtime.input_analysis_apply.apply_current_run = lambda *_args, **_kwargs: {
            "ok": True,
            "capability_requests": [request],
            "manifest": {
                "runtime_settings": {"style": "default", "wordCount": 800, "nsfw": False},
                "style_profile": {},
            },
        }
        try:
            self.round_runtime.run_round(
                self.card,
                self.root,
                run_claude=_fake_run_claude,
                run_command=_fake_run_command,
            )
        finally:
            self.round_runtime.input_analysis_apply.apply_current_run = original_apply

        audits = list((self.run_dir / "artifacts" / "capability_requests").glob("unknown-capability-*.json"))
        self.assertEqual(len(audits), 1)
        audit = json.loads(audits[0].read_text(encoding="utf-8"))
        self.assertEqual(audit["status"], "unsupported_capability")
        self.assertEqual(audit["capability"], "external.weather_lookup")
        messages = self.round_runtime.agent_messages.read_messages(self.run_dir)
        self.assertIn("unsupported_capability", {item.get("type") for item in messages})

    def test_run_round_executes_input_analysis_capability_intents(self):
        request = {
            "id": "scene-image",
            "requested_by": "input_analyst",
            "target": "assets-ui",
            "capability": "assets.generate_image",
            "summary": "Create a scene image.",
            "reason": "The player explicitly requested a visual update.",
            "source_channel": "user_instruction",
            "risk": "low",
            "authorization_gate": "none",
            "payload": {
                "kind": "scene",
                "target": "doorway",
                "prompt": "a dim doorway with warm light",
            },
            "evidence": {"raw_excerpt": "生成一张门后的图"},
        }
        original_apply = self.round_runtime.input_analysis_apply.apply_current_run
        self.round_runtime.input_analysis_apply.apply_current_run = lambda *_args, **_kwargs: {
            "ok": True,
            "capability_requests": [request],
            "manifest": {
                "runtime_settings": {"style": "default", "wordCount": 800, "nsfw": False},
                "style_profile": {},
            },
        }
        try:
            result = self.round_runtime.run_round(
                self.card,
                self.root,
                run_claude=_fake_run_claude,
                run_command=_fake_run_command,
            )
        finally:
            self.round_runtime.input_analysis_apply.apply_current_run = original_apply

        pump_path = self.run_dir / "artifacts" / "runtime_pump" / "after_input_analysis.json"
        self.assertTrue(pump_path.exists())
        pump = json.loads(pump_path.read_text(encoding="utf-8"))
        self.assertEqual(pump["phase"], "after_input_analysis")
        self.assertEqual(pump["processed"], [])
        self.assertEqual(pump["skipped"][0]["type"], "assets_task")
        self.assertEqual(pump["skipped"][0]["reason"], "phase_deferred")
        after_critic = result["runtime_pump"]["after_critic"]
        self.assertEqual(after_critic["processed"][0]["type"], "assets_task")
        self.assertIn(after_critic["processed"][0]["outputs"]["status"], {"queued", "deferred"})
        self.assertEqual(
            result["runtime_pump"]["after_critic"]["processed"][0]["type"],
            "assets_task",
        )
        agent_intents = _load_module("agent_intents")
        pending = agent_intents.list_intents(self.run_dir, "pending")
        self.assertEqual(pending, [])
        completed = agent_intents.list_intents(self.run_dir, "completed")
        self.assertEqual(completed[0]["type"], "assets_task")

    def test_input_analysis_apply_failure_retries_with_rejection_feedback(self):
        calls = {"apply": 0, "prompts": []}

        def run_claude(agent_key, prompt, cwd):
            self.assertEqual(agent_key, "input_analyst")
            calls["prompts"].append(prompt)
            payload = {"attempt": len(calls["prompts"])}
            return json.dumps(payload, ensure_ascii=False)

        def apply_current_run(*_args, **_kwargs):
            calls["apply"] += 1
            if calls["apply"] == 1:
                raise self.round_runtime.input_analysis_apply.input_analysis.InputAnalysisError(
                    "world_updates.important_characters[0].visibility is invalid: player_pov"
                )
            return {
                "ok": True,
                "capability_requests": [],
                "manifest": {
                    "runtime_settings": {"style": "default", "wordCount": 800, "nsfw": False},
                    "style_profile": {},
                },
            }

        original_apply = self.round_runtime.input_analysis_apply.apply_current_run
        self.round_runtime.input_analysis_apply.apply_current_run = apply_current_run
        try:
            result = self.round_runtime._ensure_input_analysis(
                self.card,
                self.root,
                self.run_dir,
                json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8")),
                run_claude,
            )
        finally:
            self.round_runtime.input_analysis_apply.apply_current_run = original_apply

        self.assertTrue(result["ok"])
        self.assertEqual(calls["apply"], 2)
        self.assertEqual(len(calls["prompts"]), 2)
        self.assertNotIn("Previous Attempt Rejection", calls["prompts"][0])
        self.assertIn("Previous Attempt Rejection", calls["prompts"][1])
        self.assertIn("player_pov", calls["prompts"][1])
        written = json.loads((self.run_dir / "input_analysis.output.json").read_text(encoding="utf-8"))
        self.assertEqual(written["attempt"], 2)

    def test_run_round_auto_repairs_story_only_critic_revision(self):
        calls = {"story": 0, "critic": 0, "repair_context_seen": False}

        def run_claude(agent_key, prompt, cwd):
            if agent_key == "story":
                calls["story"] += 1
                if "previous_rejected_story_output" in prompt and "repair_instruction" in prompt:
                    calls["repair_context_seen"] = True
                return json.dumps(
                    {
                        "content": (
                            "<content>修正版剧情。</content>"
                            if calls["story"] > 1
                            else "<content>初稿剧情。</content>"
                        ),
                        "character_dialogues": [],
                        "metadata": {},
                    },
                    ensure_ascii=False,
                )
            if agent_key == "critic":
                calls["critic"] += 1
                if calls["critic"] == 1:
                    return json.dumps(
                        {
                            "decision": "revise",
                            "hard_failures": ["missing derived_content_edits"],
                            "soft_issues": [],
                            "repair_instruction": "Rewrite story with the required edit artifact.",
                            "system_iteration_suggestion": "",
                            "quality_checks": {},
                            "repair_routing": {
                                "stage": "story_composition",
                                "target_agents": ["story"],
                                "rollback": "story_only",
                                "can_auto_repair": True,
                                "risk": "low",
                            },
                        },
                        ensure_ascii=False,
                    )
                return json.dumps(
                    {
                        "decision": "pass",
                        "hard_failures": [],
                        "soft_issues": [],
                        "repair_instruction": "",
                        "system_iteration_suggestion": "",
                        "quality_checks": {},
                    },
                    ensure_ascii=False,
                )
            return _fake_run_claude(agent_key, prompt, cwd)

        original_apply = self.round_runtime.input_analysis_apply.apply_current_run
        self.round_runtime.input_analysis_apply.apply_current_run = lambda *_args, **_kwargs: {
            "ok": True,
            "capability_requests": [],
            "manifest": {
                "runtime_settings": {"style": "default", "wordCount": 800, "nsfw": False},
                "style_profile": {},
            },
        }
        try:
            result = self.round_runtime.run_round(
                self.card,
                self.root,
                run_claude=run_claude,
                run_command=_fake_run_command,
            )
        finally:
            self.round_runtime.input_analysis_apply.apply_current_run = original_apply

        self.assertTrue(result["ok"])
        self.assertEqual(calls["story"], 2)
        self.assertEqual(calls["critic"], 2)
        self.assertTrue(calls["repair_context_seen"])
        story = json.loads((self.run_dir / "artifacts" / "story.output.json").read_text(encoding="utf-8"))
        self.assertIn("修正版剧情", story["content"])
        history = (self.run_dir / "repair_history.jsonl").read_text(encoding="utf-8")
        self.assertIn("story_composition", history)

    def test_run_round_auto_repairs_critic_revision_with_issue_repair_routes(self):
        calls = {"story": 0, "critic": 0}

        def run_claude(agent_key, prompt, cwd):
            if agent_key == "story":
                calls["story"] += 1
                return json.dumps(
                    {
                        "content": (
                            "<content>扩写后的正文，补足场景互动与动作细节。</content>"
                            if calls["story"] > 1
                            else "<content>偏短的正文。</content>"
                        ),
                        "character_dialogues": [],
                        "metadata": {},
                    },
                    ensure_ascii=False,
                )
            if agent_key == "critic":
                calls["critic"] += 1
                if calls["critic"] == 1:
                    return json.dumps(
                        {
                            "decision": "revise",
                            "hard_failures": [],
                            "soft_issues": [
                                {
                                    "dimension": "length",
                                    "description": "正文低于最低字数。",
                                    "repair_route": "story_composition",
                                }
                            ],
                            "repair_instruction": "扩写正文到最低字数以上。",
                            "system_iteration_suggestion": "",
                            "quality_checks": {
                                "length": {
                                    "status": "revise",
                                    "target": 1200,
                                    "minimum": 960,
                                    "current": 812,
                                    "exempted": False,
                                    "notes": "偏短。",
                                }
                            },
                        },
                        ensure_ascii=False,
                    )
                return json.dumps(
                    {
                        "decision": "pass",
                        "hard_failures": [],
                        "soft_issues": [],
                        "repair_instruction": "",
                        "system_iteration_suggestion": "",
                        "quality_checks": {},
                    },
                    ensure_ascii=False,
                )
            return _fake_run_claude(agent_key, prompt, cwd)

        original_apply = self.round_runtime.input_analysis_apply.apply_current_run
        self.round_runtime.input_analysis_apply.apply_current_run = lambda *_args, **_kwargs: {
            "ok": True,
            "capability_requests": [],
            "manifest": {
                "runtime_settings": {"style": "default", "wordCount": 800, "nsfw": False},
                "style_profile": {},
            },
        }
        try:
            result = self.round_runtime.run_round(
                self.card,
                self.root,
                run_claude=run_claude,
                run_command=_fake_run_command,
            )
        finally:
            self.round_runtime.input_analysis_apply.apply_current_run = original_apply

        self.assertTrue(result["ok"])
        self.assertEqual(calls["story"], 2)
        self.assertEqual(calls["critic"], 2)
        critic = json.loads((self.run_dir / "critic.report.json").read_text(encoding="utf-8"))
        self.assertEqual(critic["decision"], "pass")
        history = (self.run_dir / "repair_history.jsonl").read_text(encoding="utf-8")
        self.assertIn("story_composition", history)

    def test_run_post_round_memory_jobs_executes_recall_protocol_before_ingest(self):
        actor_dir = self.card / "characters" / "Ada"
        actor_dir.mkdir(parents=True, exist_ok=True)
        (actor_dir / "profile.md").write_text("我是Ada。", encoding="utf-8")
        (actor_dir / "long_term_memories.md").write_text("", encoding="utf-8")
        (actor_dir / "short_term_memories.md").write_text("记忆的回声：雨夜很冷。\n\n我：我裹紧披风。\n\n", encoding="utf-8")
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
        _write_json(
            self.run_dir / "story.input.json",
            {
                "round_id": self.run_dir.name,
                "loop_outputs": {
                    "actors": {
                        "character:Ada": [
                            {
                                "agent": "character",
                                "agent_id": "character:Ada",
                                "character_name": "Ada",
                                "events": [
                                    {"type": "reply", "target": "gm", "content": "我裹紧披风。"}
                                ],
                            }
                        ]
                    }
                },
            },
        )
        prompts = []

        def run_claude(agent_key, prompt, cwd):
            prompts.append((agent_key, prompt))
            if agent_key == "post_round_memory:character_Ada" and len([
                item for item in prompts if item[0] == "post_round_memory:character_Ada"
            ]) == 1:
                return "我想回忆：雨夜披风"
            if agent_key.startswith("post_round_objective_memory:"):
                return json.dumps(
                    {
                        "agent_id": "gm",
                        "updates": [
                            {
                                "character_name": "Ada",
                                "recent": "Ada在雨夜裹紧披风。",
                                "objective_profile": "Ada会记住雨夜披风这件事。",
                                "actor_profile": "我是Ada。我记得雨夜披风。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
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
                },
                ensure_ascii=False,
            )

        result = self.round_runtime._run_post_round_memory_jobs(
            self.card,
            self.root,
            self.run_dir,
            run_claude,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(prompts), 3)
        self.assertEqual(prompts[0][0], "post_round_memory:character_Ada")
        self.assertIn("披风边缘有银线", prompts[1][1])
        self.assertEqual(prompts[2][0], "post_round_objective_memory:character_Ada")
        self.assertIn("玩家借给我披风", (actor_dir / "long_term_memories.md").read_text(encoding="utf-8"))
        self.assertEqual((actor_dir / "short_term_memories.md").read_text(encoding="utf-8"), "")
        self.assertEqual(
            (self.card / "memory" / "characters" / "Ada" / "recent.md").read_text(encoding="utf-8"),
            "Ada在雨夜裹紧披风。\n",
        )


if __name__ == "__main__":
    unittest.main()
