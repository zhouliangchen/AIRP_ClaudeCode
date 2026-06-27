import importlib.util
import json
import os
import re
import sys
import tempfile
import threading
import urllib.request

import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_handler():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("handler", ROOT / "skills" / "handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_response_parser():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("response_parser", ROOT / "skills" / "response_parser.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_round_state():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("round_state", ROOT / "skills" / "round_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_server():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    old_argv = sys.argv
    old_cwd = os.getcwd()
    try:
        sys.argv = ["server.py", "0"]
        spec = importlib.util.spec_from_file_location("server", ROOT / "skills" / "server.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)


class TurnStateTest(unittest.TestCase):

    def test_rp_skill_is_split_into_stage_skills(self):
        skills_dir = ROOT / ".claude" / "skills"
        expected = [
            "rp-orchestrator.md",
            "rp-input-router.md",
            "rp-context-projector.md",
            "rp-gm-agent.md",
            "rp-story-agent.md",
            "rp-critic-agent.md",
            "rp-delivery.md",
            "rp-assets-ui.md",
        ]

        for name in expected:
            self.assertTrue((skills_dir / name).exists(), name)

        rp = (skills_dir / "rp.md").read_text(encoding="utf-8")
        self.assertIn("rp-orchestrator", rp)

    def test_claude_md_delegates_stage_details_to_skills(self):
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

        self.assertIn("rp-orchestrator", claude)
        self.assertIn("rp-input-router", claude)
        self.assertIn("rp-critic-agent", claude)
        self.assertIn("Claude Code", claude)
        self.assertIn("response.txt", claude)

    def test_orchestrator_skill_references_agent_run_artifacts(self):
        orchestrator = (ROOT / ".claude" / "skills" / "rp-orchestrator.md").read_text(encoding="utf-8")
        story = (ROOT / ".claude" / "skills" / "rp-story-agent.md").read_text(encoding="utf-8")
        critic = (ROOT / ".claude" / "skills" / "rp-critic-agent.md").read_text(encoding="utf-8")
        delivery = (ROOT / ".claude" / "skills" / "rp-delivery.md").read_text(encoding="utf-8")

        self.assertIn(".agent_runs", orchestrator)
        self.assertIn("gm.context.json", orchestrator)
        self.assertIn("gm.output.json", orchestrator)
        self.assertIn("player.context.json", orchestrator)
        self.assertIn("characters/*.context.json", orchestrator)
        self.assertIn("actor.outputs.json", orchestrator)
        self.assertNotIn("player.output.json", orchestrator)
        self.assertNotIn("characters/*.output.json", orchestrator)
        self.assertIn("story.output.json", story)
        self.assertIn("story.input.json", story)
        self.assertIn("actor.outputs.json", story)
        self.assertNotIn("characters/*.output.json", story)
        self.assertIn("critic.report.json", critic)
        self.assertIn("skills/styles/response.txt", delivery)
        self.assertIn('{ROOT}/skills/round_deliver.py', delivery)
        self.assertIn("round_deliver.py", delivery)
        self.assertNotIn('python skills/round_deliver.py "<card_folder>" "."', delivery)
        self.assertIn("story.output.json", delivery)

    def test_rp_command_points_to_orchestrator(self):
        command = (ROOT / ".claude" / "commands" / "rp.md").read_text(encoding="utf-8")

        self.assertIn("rp-orchestrator", command)
        self.assertIn("Read `.claude/skills/rp.md`", command)
        self.assertIn("Read `.claude/skills/rp-orchestrator.md`", command)
        self.assertIn("Do not call `rp-orchestrator` as an external registered skill", command)
        self.assertIn("The first assistant action must be a PowerShell tool call", command)
        self.assertIn("Do not reply with prose before the first tool result", command)
        self.assertIn("Use the PowerShell tool on Windows", command)
        self.assertIn("```!", command)
        self.assertIn("rp_bootstrap.py", command)
        self.assertIn("rp_generate_cli.py", command)
        self.assertIn("turn_generated", command)
        self.assertIn("Do not generate this turn again", command)
        self.assertIn("Native subagent dispatch uses the Agent tool", command)
        self.assertIn("Do not describe a tooling mismatch", command)
        self.assertIn("gm.output.json", command)
        self.assertIn("actor.outputs.json", command)
        self.assertNotIn("player.output.json", command)
        self.assertTrue("启动模式" in command or "startup" in command.lower())
        self.assertNotIn("## 第一步", command)
        self.assertNotIn("## 第二步", command)

    def test_rp_frontmatter_has_no_bom(self):
        for path in (ROOT / ".claude" / "skills").glob("rp*.md"):
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"{path.name} has BOM")
            lines = raw.decode("utf-8").splitlines()
            self.assertGreaterEqual(len(lines), 4, f"{path.name} should include frontmatter and markdown body")
            self.assertEqual(lines[0], "---", f"{path.name} should start with frontmatter delimiter")
            self.assertIn("name:", lines[1], f"{path.name} second line should contain name field")

            frontmatter_end = None
            for i in range(2, min(5, len(lines))):
                if lines[i].strip() == "---":
                    frontmatter_end = i
                    break
            self.assertIsNotNone(frontmatter_end, f"{path.name} should close frontmatter with --- within first 5 lines")
            self.assertTrue(
                any(line.lstrip().startswith("#") for line in lines[frontmatter_end + 1 :]),
                f"{path.name} should include a markdown heading after frontmatter",
            )

    def test_rp_skills_define_immersive_multi_agent_contracts(self):
        skills_dir = ROOT / ".claude" / "skills"
        rp = (skills_dir / "rp.md").read_text(encoding="utf-8")
        orchestrator = (skills_dir / "rp-orchestrator.md").read_text(encoding="utf-8")
        router = (skills_dir / "rp-input-router.md").read_text(encoding="utf-8")
        projector = (skills_dir / "rp-context-projector.md").read_text(encoding="utf-8")
        gm = (skills_dir / "rp-gm-agent.md").read_text(encoding="utf-8")
        actor_prompt_source = (ROOT / "skills" / "agent_prompts.py").read_text(encoding="utf-8")
        story = (skills_dir / "rp-story-agent.md").read_text(encoding="utf-8")
        critic = (skills_dir / "rp-critic-agent.md").read_text(encoding="utf-8")
        delivery = (skills_dir / "rp-delivery.md").read_text(encoding="utf-8")
        assets = (skills_dir / "rp-assets-ui.md").read_text(encoding="utf-8")
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

        self.assertIn("Claude Code as the RP entry", rp)
        self.assertIn("configured LLM APIs", rp)
        self.assertIn("主 agent 只负责编排", rp)
        self.assertIn("按需导入", orchestrator)
        self.assertIn("不得直接撰写常规叙事正文", orchestrator)
        self.assertIn("交互循环", orchestrator)
        self.assertIn("关键决策点", orchestrator)
        self.assertIn("章节字数", orchestrator)
        self.assertIn("improvement_queue.jsonl", orchestrator)

        self.assertIn("role_channel", router)
        self.assertIn("user_instruction_channel", router)
        self.assertIn("第一人称剧情梗概", router)
        self.assertIn("第三人称上帝视角设定", router)
        self.assertIn("互不干扰", router)

        self.assertIn("GM agent 可以接收完整剧情", projector)
        self.assertIn("严格独立的第一人称视角", projector)
        self.assertIn("不得泄露", projector)
        self.assertIn("world-visible", projector)

        self.assertIn("旁白和非核心角色", gm)
        self.assertIn("实时运转", gm)
        self.assertIn("完整剧情", gm)
        self.assertIn("gm.output.json", gm)

        self.assertIn("def _actor_base_prompt", actor_prompt_source)
        self.assertIn("我不把不可感知的设定、幕后原因或外部指令当成自己知道的事", actor_prompt_source)
        self.assertIn("我直接用自然语言", actor_prompt_source)
        self.assertIn("我不写 JSON", actor_prompt_source)
        self.assertNotIn("player.output.json", actor_prompt_source)

        self.assertIn("_file_backed_actor_context", actor_prompt_source)
        self.assertIn("actor_memory_store.read_actor_memory", actor_prompt_source)
        self.assertIn("profile_text", actor_prompt_source)
        self.assertNotIn("_actor_basic_lines", actor_prompt_source)
        self.assertNotIn("_projected_memory", actor_prompt_source)
        self.assertIn("我只写自己的想法、动作、台词和感受", actor_prompt_source)
        self.assertIn("不写字段名", actor_prompt_source)

        self.assertIn("尽可能保留各 subagent", story)
        self.assertIn("<character_dialogues>", story)
        self.assertIn("整体性", story)
        self.assertIn("story.output.json", story)
        self.assertIn("story.input.json", story)

        self.assertIn("严谨的小说创作者", critic)
        self.assertIn("叙事连贯", critic)
        self.assertIn("逻辑严密", critic)
        self.assertIn("角色生动", critic)
        self.assertIn("系统迭代建议", critic)

        self.assertIn("story.output.json", delivery)
        self.assertIn("skills/styles/response.txt", delivery)
        self.assertIn("round_deliver.py", delivery)

        self.assertIn("image_generate.py", assets)
        self.assertIn("异步", assets)
        self.assertIn("不得阻塞正文交付", assets)
        self.assertIn("ui_manifest.json", assets)

        self.assertIn("Claude Code 主 agent 入口", claude)
        self.assertIn("LLM API", claude)
        self.assertIn("各阶段 skill 按需导入", claude)
        self.assertIn("叙事创作和角色扮演任务必须交给 subagent", claude)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.card = self.base / "card"
        self.card.mkdir()
        (self.card / "chat_log.json").write_text("[]", encoding="utf-8")
        self.styles = self.base / "styles"
        self.styles.mkdir()
        self.handler = _load_handler()
        self.old_styles = self.handler.STYLES
        self.handler.STYLES = self.styles

    def tearDown(self):
        self.handler.STYLES = self.old_styles
        self.tmp.cleanup()

    def _write_postprocess_output(self, core=None, ui_extensions=None, path=None, mvu=None):
        payload = {
            "schema_version": 1,
            "core": core or {
                "summary": "Postprocess summary",
                "current_goal": "Follow the postprocess goal",
                "options": ["Postprocess option"],
                "state_patch": {},
            },
            "ui_extensions": ui_extensions or {
                "status_panels": {"weather": "rain"},
                "custom_cards": {},
                "asset_bindings": {},
            },
            "ui_extension_status": {"status": "ok", "issues": []},
        }
        if mvu is not None:
            payload["mvu"] = mvu
        output_path = Path(path) if path is not None else self.card / "postprocess.output.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return payload

    def _content_window_var(self, name):
        content_js = (self.styles / "content.js").read_text(encoding="utf-8")
        match = re.search(rf"window\.{re.escape(name)} = (.*?);\n", content_js)
        self.assertIsNotNone(match, name)
        return json.loads(match.group(1))

    def test_pending_user_turn_is_rendered_before_ai_reply(self):
        self.handler.write_pending_user_turn(str(self.card), "我把地点改成雨夜码头。")

        self.handler.write_content_js(str(self.card))
        content_js = (self.styles / "content.js").read_text(encoding="utf-8")

        self.assertIn("turn-pending", content_js)
        self.assertIn("我把地点改成雨夜码头。", content_js)
        self.assertIn("等待 Claude Code 回复", content_js)

    def test_rebuild_content_activates_card_without_response_file(self):
        (self.card / "chat_log.json").write_text(
            json.dumps([
                {
                    "index": 0,
                    "ai": "<content><p>Existing story.</p></content>",
                    "summary": "Existing story",
                }
            ]),
            encoding="utf-8",
        )

        result = self.handler.rebuild_content(str(self.card))

        self.assertTrue(result["ok"])
        self.assertEqual((self.styles / ".card_path").read_text(encoding="utf-8"), str(self.card.resolve()))
        content_js = (self.styles / "content.js").read_text(encoding="utf-8")
        self.assertIn("Existing story.", content_js)
        self.assertFalse((self.styles / "response.txt").exists())

    def test_player_input_log_is_authoritative_jsonl(self):
        entry = self.handler.record_player_input(str(self.card), "原始输入", "【玩家】原始输入")

        log_path = self.card / ".player_inputs.jsonl"
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        saved = json.loads(lines[0])

        self.assertEqual(saved["id"], entry["id"])
        self.assertEqual(saved["raw_text"], "原始输入")
        self.assertEqual(saved["display_text"], "【玩家】原始输入")
        self.assertEqual(saved["source"], "player")

    def test_player_input_log_preserves_explicit_channels(self):
        entry = self.handler.record_player_input(
            str(self.card),
            "I open the gate.\n\n[USER_INSTRUCTION]\nMake the gate lead to orbit.",
            "I open the gate.",
            role_text="I open the gate.",
            user_instruction_text="Make the gate lead to orbit.",
            input_schema="dual_channel_v1",
        )

        saved = json.loads((self.card / ".player_inputs.jsonl").read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(saved["id"], entry["id"])
        self.assertEqual(saved["input_schema"], "dual_channel_v1")
        self.assertEqual(saved["role_text"], "I open the gate.")
        self.assertEqual(saved["user_instruction_text"], "Make the gate lead to orbit.")
        self.assertEqual(saved["raw_text"], "I open the gate.\n\n[USER_INSTRUCTION]\nMake the gate lead to orbit.")
        self.assertEqual(entry["role_text"], "I open the gate.")

    def test_pending_user_turn_preserves_channel_metadata(self):
        pending = self.handler.write_pending_user_turn(
            str(self.card),
            "I open the gate.",
            raw_text="I open the gate.\n\n[USER_INSTRUCTION]\nMake the gate lead to orbit.",
            input_id="input-1",
            role_text="I open the gate.",
            user_instruction_text="Make the gate lead to orbit.",
            input_schema="dual_channel_v1",
        )

        saved = self.handler.read_pending_user_turn(str(self.card))

        self.assertEqual(saved, pending)
        self.assertEqual(saved["input_schema"], "dual_channel_v1")
        self.assertEqual(saved["role_text"], "I open the gate.")
        self.assertEqual(saved["user_instruction_text"], "Make the gate lead to orbit.")
        self.assertEqual(saved["raw_text"], "I open the gate.\n\n[USER_INSTRUCTION]\nMake the gate lead to orbit.")

    def test_progress_state_round_trips(self):
        self.handler.write_progress("delivering", "正在交付到前端", percent=85)

        progress = self.handler.read_progress()

        self.assertEqual(progress["schema_version"], 2)
        self.assertEqual(progress["stage"], "delivery.delivering")
        self.assertEqual(progress["state"], "delivery.delivering")
        self.assertEqual(progress["label"], "正在交付到前端")
        self.assertEqual(progress["percent"], 85)

    def test_progress_state_v2_round_trips_through_handler(self):
        round_state = _load_round_state()
        round_state.write_progress_state(
            self.styles,
            "delivery.delivering",
            detail={"attempt": 1},
        )

        progress = self.handler.read_progress()

        self.assertEqual(progress["schema_version"], 2)
        self.assertEqual(progress["state"], "delivery.delivering")
        self.assertEqual(progress["stage"], "delivery.delivering")
        self.assertEqual(progress["detail"]["attempt"], 1)

    def test_progress_write_falls_back_to_old_shape_without_round_state(self):
        old_round_state = self.handler.round_state
        try:
            self.handler.round_state = None

            self.handler.write_progress("delivering", "正在交付到前端", percent=85)
            progress = self.handler.read_progress()

            self.assertNotIn("schema_version", progress)
            self.assertEqual(progress["stage"], "delivering")
            self.assertEqual(progress["label"], "正在交付到前端")
            self.assertEqual(progress["percent"], 85)
        finally:
            self.handler.round_state = old_round_state

    def test_progress_write_falls_back_to_old_shape_when_conversion_fails(self):
        old_round_state = self.handler.round_state

        class BrokenRoundState:
            @staticmethod
            def legacy_progress_record(stage, label, percent=None, detail=None):
                raise ValueError("conversion failed")

        try:
            self.handler.round_state = BrokenRoundState

            self.handler.write_progress("delivering", "正在交付到前端", percent=85)
            progress = self.handler.read_progress()

            self.assertNotIn("schema_version", progress)
            self.assertEqual(progress["stage"], "delivering")
            self.assertEqual(progress["label"], "正在交付到前端")
            self.assertEqual(progress["percent"], 85)
        finally:
            self.handler.round_state = old_round_state

    def test_progress_write_does_not_swallow_v2_write_failure(self):
        old_round_state = self.handler.round_state
        old_write_json_file = self.handler._write_json_file
        write_calls = []

        class WorkingRoundState:
            @staticmethod
            def legacy_progress_record(stage, label, percent=None, detail=None):
                return {
                    "schema_version": 2,
                    "stage": "delivery.delivering",
                    "state": "delivery.delivering",
                    "label": label,
                    "percent": percent,
                    "detail": detail or {},
                }

        def fail_first_write(path, data):
            write_calls.append(data)
            if len(write_calls) == 1:
                raise RuntimeError("progress write failed")
            old_write_json_file(path, data)

        try:
            self.handler.round_state = WorkingRoundState
            self.handler._write_json_file = fail_first_write

            with self.assertRaisesRegex(RuntimeError, "progress write failed"):
                self.handler.write_progress("delivering", "正在交付到前端", percent=85)

            self.assertEqual(len(write_calls), 1)
            self.assertEqual(write_calls[0]["schema_version"], 2)
        finally:
            self.handler.round_state = old_round_state
            self.handler._write_json_file = old_write_json_file

    def test_progress_write_uses_temp_file_before_replace(self):
        old_write_json_file = self.handler._write_json_file
        write_paths = []

        def recording_write(path, data):
            write_paths.append(Path(path))
            old_write_json_file(path, data)

        try:
            self.handler._write_json_file = recording_write

            self.handler.write_progress("delivering", "正在交付到前端", percent=85)

            self.assertTrue(write_paths)
            self.assertNotEqual(write_paths[0].name, "progress.json")
            self.assertEqual(write_paths[0].parent, self.styles)
            self.assertFalse(write_paths[0].exists())
            self.assertEqual(self.handler.read_progress()["state"], "delivery.delivering")
        finally:
            self.handler._write_json_file = old_write_json_file

    def test_blank_profile_derives_self_identity_from_authoritative_player_input(self):
        (self.card / "memory" / "characters" / "雨蒙").mkdir(parents=True)
        (self.card / ".card_data.json").write_text(
            json.dumps(
                {
                    "mode": "blank_bootstrap",
                    "source_type": "blank",
                    "name": "未命名角色",
                    "data": {"name": "未命名角色"},
                    "evolving_profile": {
                        "version": 1,
                        "last_turn": 0,
                        "confidence": "low",
                        "fields": {
                            "role": "",
                            "appearance": "",
                            "voice": "",
                            "motivation": "",
                            "relationship_to_user": "",
                            "world_assumptions": [],
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        self.handler.evolve_blank_profile(
            str(self.card),
            1,
            "【雨蒙】我叫雨蒙，一名普通的高一男生。今天早上我看见粉色云彩。",
            "<p>你在教室里醒来。</p>",
            "你在教室里醒来。",
            {"世界": {"地点": "高一教室"}},
        )

        card_data = json.loads((self.card / ".card_data.json").read_text(encoding="utf-8"))
        player_mapping = (self.card / "characters" / "player.md").read_text(encoding="utf-8")
        subjective_profile = (
            self.card / "characters" / "雨蒙" / "profile.md"
        ).read_text(encoding="utf-8")
        objective_profile = (
            self.card / "memory" / "characters" / "雨蒙" / "profile.md"
        ).read_text(encoding="utf-8")

        self.assertEqual(card_data["name"], "雨蒙")
        self.assertIn("name: 雨蒙", player_mapping)
        self.assertIn("path: characters/雨蒙", player_mapping)
        self.assertIn("我是雨蒙。", subjective_profile)
        self.assertIn("普通的高一男生", subjective_profile)
        self.assertNotIn("# 自定义角色卡", subjective_profile)
        self.assertNotIn("- 身份/定位:", subjective_profile)
        self.assertIn("雨蒙", objective_profile)
        self.assertTrue((self.card / "memory" / "characters" / "雨蒙" / "background.md").exists())
        self.assertFalse((self.card / "memory" / "characters" / "雨蒙" / "profile.json").exists())
        self.assertFalse((self.card / "characters" / "_self").exists())

    def test_frontend_polls_progress_and_refreshes_after_submit(self):
        html = (ROOT / "skills" / "styles" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="reply-progress"', html)
        self.assertIn("BRIDGE + '/api/progress'", html)
        self.assertIn("setInterval(loadProgress", html)
        self.assertIn("reloadData();", html)

    def test_append_turn_preserves_authoritative_pending_player_text(self):
        entry = self.handler.record_player_input(
            str(self.card),
            "RAW: I move north without trimming.",
            "DISPLAY: I move north without trimming.",
        )
        self.handler.write_pending_user_turn(
            str(self.card),
            "DISPLAY: I move north without trimming.",
            raw_text="RAW: I move north without trimming.",
            input_id=entry["id"],
        )

        self.handler.append_turn(
            str(self.card),
            polished_input="POLISHED: north.",
            content="<p>ok</p>",
            summary="ok",
        )

        log = json.loads((self.card / "chat_log.json").read_text(encoding="utf-8"))
        self.assertEqual(log[0]["user"], "DISPLAY: I move north without trimming.")
        self.assertEqual(log[0]["player_input_id"], entry["id"])
        self.assertEqual(log[0]["polished_input"], "POLISHED: north.")

    def test_append_turn_replaces_existing_player_input_turn_when_current_run_is_redelivered(self):
        entry = self.handler.record_player_input(
            str(self.card),
            "I ask Su Li what is happening.",
            "I ask Su Li what is happening.",
        )
        self.handler.write_chat_log(str(self.card), [
            {
                "index": 0,
                "user": "I ask Su Li what is happening.",
                "player_input_id": entry["id"],
                "ai": "<p>old leaked answer</p>",
                "tokens": {"total": 100},
            }
        ])
        run_dir = self.card / ".agent_runs" / "round-000001"
        run_dir.mkdir(parents=True)
        (self.card / ".agent_runs" / "current").write_text(str(run_dir.resolve()), encoding="utf-8")
        (run_dir / "input.json").write_text(
            json.dumps(
                {
                    "id": entry["id"],
                    "raw_text": "I ask Su Li what is happening.",
                    "display_text": "I ask Su Li what is happening.",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        index = self.handler.append_turn(
            str(self.card),
            content="<p>new safe answer</p>",
            summary="new summary",
            tokens={"total": 7},
        )

        log = self.handler.read_chat_log(str(self.card))
        self.assertEqual(index, 0)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["index"], 0)
        self.assertEqual(log[0]["user"], "I ask Su Li what is happening.")
        self.assertEqual(log[0]["player_input_id"], entry["id"])
        self.assertIn("new safe answer", log[0]["ai"])
        self.assertNotIn("old leaked answer", log[0]["ai"])
        state_js = (self.card / "state.js").read_text(encoding="utf-8")
        self.assertIn("generatedCount: 1", state_js)

    def test_update_only_player_input_edit_records_impact_without_truncating_chat(self):
        first = self.handler.record_player_input(str(self.card), "first input", "first input")
        second = self.handler.record_player_input(str(self.card), "second input", "second input")
        self.handler.write_chat_log(str(self.card), [
            {"index": 0, "user": "first input", "player_input_id": first["id"], "ai": "<p>one</p>"},
            {"index": 1, "user": "second input", "player_input_id": second["id"], "ai": "<p>two</p>"},
        ])

        result = self.handler.edit_player_input(str(self.card), first["id"], "first input revised", "update_only")

        self.assertEqual(result["mode"], "update_only")
        log = self.handler.read_chat_log(str(self.card))
        self.assertEqual(len(log), 2)
        self.assertEqual(log[0]["user"], "first input revised")
        self.assertFalse(self.handler.read_pending_user_turn(str(self.card)))
        inputs = self.handler.read_player_inputs(str(self.card))
        self.assertEqual(inputs[0]["raw_text"], "first input revised")
        edits = self.handler.read_player_input_edits(str(self.card), processed=False)
        self.assertEqual(edits[0]["mode"], "update_only")
        self.assertEqual(edits[0]["input_id"], first["id"])

    def test_player_input_edit_clears_stale_dual_channel_metadata(self):
        entry = self.handler.record_player_input(
            str(self.card),
            "Old role.\n\n[USER_INSTRUCTION]\nOld hidden instruction.",
            "Old role.",
            role_text="Old role.",
            user_instruction_text="Old hidden instruction.",
            input_schema="dual_channel_v1",
        )

        self.handler.edit_player_input(str(self.card), entry["id"], "New legacy replacement.", "branch_submit")

        saved = self.handler.read_player_inputs(str(self.card))[0]
        pending = self.handler.read_pending_user_turn(str(self.card))
        self.assertEqual(saved["raw_text"], "New legacy replacement.")
        self.assertEqual(saved["display_text"], "New legacy replacement.")
        self.assertNotIn("input_schema", saved)
        self.assertNotIn("role_text", saved)
        self.assertNotIn("user_instruction_text", saved)
        self.assertEqual(pending["raw_text"], "New legacy replacement.")
        self.assertNotIn("input_schema", pending)
        self.assertNotIn("role_text", pending)
        self.assertNotIn("user_instruction_text", pending)

    def test_content_js_exposes_only_visible_player_input_text(self):
        self.handler.record_player_input(
            str(self.card),
            "Visible role.\n\n[USER_INSTRUCTION]\nHidden magic truth.",
            "Visible role.",
            role_text="Visible role.",
            user_instruction_text="Hidden magic truth.",
            input_schema="dual_channel_v1",
        )
        self.handler.write_chat_log(str(self.card), [
            {"index": 0, "user": "Visible role.", "ai": "<p>ok</p>", "summary": "ok"}
        ])

        self.handler.write_content_js(str(self.card))

        content_js = (self.styles / "content.js").read_text(encoding="utf-8")
        self.assertIn("Visible role.", content_js)
        self.assertNotIn("Hidden magic truth.", content_js)
        self.assertNotIn("user_instruction_text", content_js)
        self.assertNotIn("[USER_INSTRUCTION]", content_js)

    def test_frontend_player_inputs_excludes_unreferenced_stale_inputs(self):
        stale = self.handler.record_player_input(str(self.card), "stale", "stale")
        active = self.handler.record_player_input(str(self.card), "active", "active")
        self.handler.write_chat_log(str(self.card), [
            {"index": 0, "user": "active", "player_input_id": active["id"], "ai": "<p>ok</p>"}
        ])

        visible = self.handler.frontend_player_inputs(str(self.card))

        self.assertEqual([item["id"] for item in visible], [active["id"]])
        self.assertNotIn(stale["id"], [item["id"] for item in visible])

    def test_server_player_inputs_api_uses_frontend_filtered_inputs(self):
        server_py = (ROOT / "skills" / "server.py").read_text(encoding="utf-8")

        self.assertIn("handler.frontend_player_inputs(card)", server_py)
        self.assertNotIn('handler.read_player_inputs(card)})', server_py)

    def test_derived_content_edits_only_record_when_actionable(self):
        log = [{"index": 0, "user": "player", "ai": "<p>old</p>", "summary": "old"}]

        applied = self.handler.apply_derived_content_edits(
            log,
            [{"op": "replace", "path": "/prior_ai_turn/scene", "value": "not actionable"}],
        )

        self.assertEqual(applied, [])
        self.assertNotIn("derived_repairs", log[0])

    def test_derived_content_edits_can_update_prior_summary(self):
        log = [{"index": 0, "user": "player", "ai": "<p>old</p><summary>old</summary>", "summary": "old"}]

        applied = self.handler.apply_derived_content_edits(
            log,
            [{"turn_index": 0, "summary": "课堂段落改定为梦境预示", "reason": "玩家梦醒回拨"}],
        )

        self.assertEqual(applied[0]["op"], "replace_summary")
        self.assertEqual(log[0]["summary"], "课堂段落改定为梦境预示")
        self.assertIn("课堂段落改定为梦境预示", log[0]["ai"])
        self.assertIn("derived_repairs", log[0])

    def test_derived_content_edits_clean_full_ai_replacement_contract_tags(self):
        log = [{"index": 0, "user": "player", "ai": "<p>old</p>", "summary": "old"}]

        applied = self.handler.apply_derived_content_edits(
            log,
            [{
                "turn_index": 0,
                "ai": "<content><p>Dream preview replacement.</p></content>\n<character_dialogues>[]</character_dialogues>\n<summary>retconned as dream</summary>",
                "reason": "player reframed previous scene",
            }],
        )

        self.assertEqual(applied[0]["op"], "replace_ai")
        self.assertEqual(log[0]["ai"], "<p>Dream preview replacement.</p>")
        self.assertEqual(log[0]["summary"], "retconned as dream")
        self.assertNotIn("<content>", log[0]["ai"])
        self.assertNotIn("<summary>", log[0]["ai"])
        self.assertIn("derived_repairs", log[0])

    def test_append_turn_retargers_current_index_derived_edit_to_prior_ai_when_reframing_previous_scene(self):
        self.handler.write_chat_log(str(self.card), [
            {
                "index": 0,
                "user": "first input",
                "ai": "<p>Old classroom happened as reality.</p><summary>old</summary>",
                "summary": "old",
            }
        ])

        self.handler.append_turn(
            str(self.card),
            content="<p>You wake on the road and hold the pendant.</p>",
            summary="woke",
            derived_content_edits=[
                {
                    "turn_index": 1,
                    "first_paragraph": "The classroom continuity is now only a dream preview.",
                    "summary": "Previous classroom scene is now a dream preview.",
                    "reason": "previous AI turn reframed as dream by player input",
                }
            ],
        )

        log = self.handler.read_chat_log(str(self.card))
        self.assertIn("dream preview", log[0]["ai"])
        self.assertEqual(log[0]["summary"], "Previous classroom scene is now a dream preview.")
        self.assertNotIn("Old classroom happened", log[0]["ai"])
        self.assertIn("You wake on the road", log[1]["ai"])
        self.assertEqual(log[1]["derived_content_edits_applied"][0]["turn_index"], 0)
        self.assertEqual(log[1]["derived_content_edits_applied"][0]["original_turn_index"], 1)

    def test_append_turn_retargers_future_index_derived_edit_to_prior_ai_when_reframing_previous_scene(self):
        self.handler.write_chat_log(str(self.card), [
            {
                "index": 0,
                "user": "first input",
                "ai": "<p>Old classroom happened as reality.</p><summary>old</summary>",
                "summary": "old",
            }
        ])

        self.handler.append_turn(
            str(self.card),
            content="<p>You wake on the road and hold the pendant.</p>",
            summary="woke",
            derived_content_edits=[
                {
                    "turn_index": 2,
                    "first_paragraph": "The previous classroom scene is now a dream preview.",
                    "summary": "Previous classroom scene is now a dream preview.",
                    "reason": "previous AI turn reframed as dream by player input",
                }
            ],
        )

        log = self.handler.read_chat_log(str(self.card))
        self.assertIn("dream preview", log[0]["ai"])
        self.assertEqual(log[0]["summary"], "Previous classroom scene is now a dream preview.")
        self.assertNotIn("Old classroom happened", log[0]["ai"])
        self.assertEqual(log[1]["derived_content_edits_applied"][0]["turn_index"], 0)
        self.assertEqual(log[1]["derived_content_edits_applied"][0]["original_turn_index"], 2)

    def test_branch_submit_player_input_edit_truncates_and_pends_revised_turn(self):
        first = self.handler.record_player_input(str(self.card), "first input", "first input")
        second = self.handler.record_player_input(str(self.card), "second input", "second input")
        self.handler.write_chat_log(str(self.card), [
            {"index": 0, "user": "first input", "player_input_id": first["id"], "ai": "<p>one</p>"},
            {"index": 1, "user": "second input", "player_input_id": second["id"], "ai": "<p>two</p>"},
        ])

        result = self.handler.edit_player_input(str(self.card), first["id"], "first branch", "branch_submit")

        self.assertEqual(result["mode"], "branch_submit")
        self.assertEqual(result["branch_from_index"], 0)
        self.assertEqual(self.handler.read_chat_log(str(self.card)), [])
        pending = self.handler.read_pending_user_turn(str(self.card))
        self.assertEqual(pending["id"], first["id"])
        self.assertEqual(pending["display_text"], "first branch")
        self.assertEqual((self.styles / "input.txt").read_text(encoding="utf-8"), "first branch")
        self.assertTrue((self.styles / ".pending").exists())

    def test_frontend_exposes_player_input_edit_controls(self):
        html = (ROOT / "skills" / "styles" / "index.html").read_text(encoding="utf-8")

        self.assertIn("openPlayerInputEditor", html)
        self.assertIn("BRIDGE + '/api/player_inputs/edit'", html)
        self.assertIn("update_only", html)
        self.assertIn("branch_submit", html)

    def test_frontend_exposes_dual_channel_inputs(self):
        html = (ROOT / "skills" / "styles" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="role-input"', html)
        self.assertIn('id="instruction-input"', html)
        self.assertIn("roleText", html)
        self.assertIn("instructionText", html)
        self.assertIn("syncLegacyInputBridge", html)

    def test_mobile_keeps_instruction_input_in_settings_drawer(self):
        html = (ROOT / "skills" / "styles" / "index.html").read_text(encoding="utf-8")

        drawer_index = html.index('id="mobile-settings-drawer"')
        instruction_index = html.index('id="instruction-input"')
        input_panel_index = html.index('id="input-panel"')
        self.assertLess(drawer_index, instruction_index)
        self.assertLess(instruction_index, input_panel_index)
        self.assertIn("mobile-settings-open", html)

    def test_frontend_exposes_self_repair_mode_setting(self):
        html = (ROOT / "skills" / "styles" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="set-self-repair-mode"', html)
        self.assertIn('id="set-source-code-self-repair"', html)
        self.assertIn('value="analysis_only"', html)
        self.assertIn('value="limited"', html)
        self.assertIn('value="full"', html)
        self.assertIn("s.selfRepairMode || 'limited'", html)
        self.assertIn("s.allowSourceCodeSelfRepair === true", html)
        self.assertIn("selfRepairMode: document.getElementById('set-self-repair-mode').value", html)
        self.assertIn("allowSourceCodeSelfRepair: document.getElementById('set-source-code-self-repair').checked", html)

    def test_frontend_removes_obsolete_runtime_settings_controls(self):
        html = (ROOT / "skills" / "styles" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn('id="set-person"', html)
        self.assertNotIn('id="set-antiimp"', html)
        self.assertNotIn('id="set-bgnpc"', html)
        self.assertNotIn('id="player-settings-card"', html)
        self.assertNotIn('id="player-name-input"', html)
        self.assertNotIn("antiImpersonation", html)
        self.assertNotIn("bgNpc", html)
        self.assertNotIn("charName: document.getElementById", html)

    def test_frontend_exposes_model_debug_mode_setting(self):
        html = (ROOT / "skills" / "styles" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="set-model-debug-mode"', html)
        self.assertIn("s.modelDebugMode === true", html)
        self.assertIn("modelDebugMode: document.getElementById('set-model-debug-mode').checked", html)

    def test_frontend_exposes_llm_settings_modal(self):
        html = (ROOT / "skills" / "styles" / "index.html").read_text(encoding="utf-8")

        for field_id in [
            "open-llm-settings",
            "llm-settings-modal",
            "llm-cc-enabled",
            "llm-cc-service-url",
            "llm-openai-enabled",
            "llm-openai-base-url",
            "llm-openai-api-key",
            "llm-openai-model",
            "llm-image-base-url",
            "llm-image-api-key",
            "llm-image-model",
        ]:
            self.assertIn(f'id="{field_id}"', html)

        for function_name in [
            "openLlmSettings",
            "closeLlmSettings",
            "loadLlmSettings",
            "collectLlmSettings",
            "saveLlmSettings",
            "testLlmSettings",
        ]:
            self.assertIn(f"function {function_name}", html)

        self.assertIn("BRIDGE + '/api/llm_settings'", html)
        self.assertIn("BRIDGE + '/api/llm_settings/test'", html)
        self.assertIn("image_generation", html)
        self.assertIn("api_key_set", html)
        self.assertIn("if (openaiKey)", html)
        self.assertIn("if (imageKey)", html)
        self.assertIn("applyLlmSettings(data)", html)
        self.assertIn("applyLlmSettings(settings)", html)
        self.assertIn("configuration_errors", html)
        self.assertIn("Array.isArray(data.results)", html)
        self.assertNotIn("save: true", html)
        self.assertNotIn('placeholder="http://127.0.0.1:15721"', html)
        self.assertNotIn('placeholder="https://api.openai.com/v1"', html)
        self.assertNotIn('placeholder="gpt-image-2"', html)

    def test_frontend_renders_schema_v2_progress_detail_panel(self):
        html = (ROOT / "skills" / "styles" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="reply-progress-detail"', html)
        self.assertIn("progress.schema_version === 2", html)
        self.assertIn("formatProgressDetail", html)
        self.assertIn("progress.state", html)
        self.assertIn("progress.detail", html)

    def test_frontend_keeps_legacy_terminal_progress_aliases_visible(self):
        html = (ROOT / "skills" / "styles" / "index.html").read_text(encoding="utf-8")

        self.assertIn("legacyTerminalStages", html)
        self.assertIn("'done'", html)
        self.assertIn("'blocked'", html)
        self.assertIn("legacyCompletionStages", html)
        self.assertIn("legacyCompletionStages.indexOf(stage) >= 0", html)

    def test_frontend_dual_channel_submit_and_bridge_contract(self):
        html = (ROOT / "skills" / "styles" / "index.html").read_text(encoding="utf-8")

        self.assertIn("const roleText = roleInput ? roleInput.value : ''", html)
        self.assertIn("const instructionText = instructionInput ? instructionInput.value : ''", html)
        self.assertIn("if (!roleText.trim() && !instructionText.trim()) return", html)
        self.assertIn("text: roleText", html)
        self.assertIn("roleText: roleText", html)
        self.assertIn("instructionText: instructionText", html)
        self.assertIn("if (roleInput) roleInput.value = ''", html)
        self.assertIn("if (instructionInput) instructionInput.value = ''", html)
        self.assertIn("if (legacy) legacy.value = value", html)
        self.assertIn("if (sendTextarea) sendTextarea.value = value", html)
        self.assertIn("var $ui = $('#role-input')", html)
        self.assertIn("document.getElementById('role-input').addEventListener('input', syncLegacyInputBridge)", html)

    def test_round_prepare_documents_player_input_interpretation_policy(self):
        source = (ROOT / "skills" / "round_prepare.py").read_text(encoding="utf-8")

        self.assertIn("PLAYER_INPUT_INTERPRETATION", source)
        self.assertIn("Do not infer semantic intent from fixed keywords", source)
        self.assertIn("input_analysis.output.json", source)
        self.assertIn("actor-facing packets must use explicit dual-channel or analysis-applied routing", source)
        self.assertIn("PLAYER_INPUT_EDITS_PENDING", source)

    def test_server_does_not_trim_player_submitted_text(self):
        source = (ROOT / "skills" / "server.py").read_text(encoding="utf-8")

        self.assertNotIn('data.get("text", "").strip()', source)
        self.assertNotIn('data.get("message", "").strip()', source)

    def test_server_submit_accepts_explicit_dual_channel_fields(self):
        server = _load_server()
        server.ROOT = self.styles
        server.INPUT_FILE = self.styles / "input.txt"
        server.PENDING_FILE = self.styles / ".pending"
        server.CARD_PATH_FILE = self.styles / ".card_path"
        server.SETTINGS_FILE = self.styles / "settings.json"
        server.handler.STYLES = self.styles
        server.CARD_PATH_FILE.write_text(str(self.card), encoding="utf-8")

        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def post_submit(payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{httpd.server_port}/api/submit",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            role_text = " I open the gate. "
            instruction_text = " Make the gate lead to orbit. "
            result = post_submit({"roleText": role_text, "instructionText": instruction_text})

            self.assertTrue(result["ok"])
            self.assertEqual(result["text"], role_text)
            expected_raw = role_text + "\n\n[USER_INSTRUCTION]\n" + instruction_text
            self.assertEqual(server.INPUT_FILE.read_text(encoding="utf-8"), expected_raw)
            inputs = self.handler.read_player_inputs(str(self.card))
            pending = self.handler.read_pending_user_turn(str(self.card))
            self.assertEqual(inputs[-1]["input_schema"], "dual_channel_v1")
            self.assertEqual(inputs[-1]["raw_text"], expected_raw)
            self.assertEqual(inputs[-1]["display_text"], role_text)
            self.assertEqual(inputs[-1]["role_text"], role_text)
            self.assertEqual(inputs[-1]["user_instruction_text"], instruction_text)
            self.assertEqual(pending["display_text"], role_text)
            self.assertEqual(pending["raw_text"], expected_raw)
            self.assertEqual(pending["role_text"], role_text)
            self.assertEqual(pending["user_instruction_text"], instruction_text)

            instruction_only = "Only update the hidden world state."
            instruction_result = post_submit({"instructionText": instruction_only, "charName": "Hero"})

            self.assertTrue(instruction_result["ok"])
            self.assertEqual(instruction_result["text"], "")
            expected_instruction_raw = "\n\n[USER_INSTRUCTION]\n" + instruction_only
            self.assertEqual(server.INPUT_FILE.read_text(encoding="utf-8"), expected_instruction_raw)
            inputs = self.handler.read_player_inputs(str(self.card))
            pending = self.handler.read_pending_user_turn(str(self.card))
            self.assertEqual(inputs[-1]["display_text"], "")
            self.assertEqual(inputs[-1]["role_text"], "")
            self.assertEqual(inputs[-1]["user_instruction_text"], instruction_only)
            self.assertEqual(pending["display_text"], "")
            self.assertEqual(pending["role_text"], "")
            self.assertEqual(pending["user_instruction_text"], instruction_only)

            self.handler.append_turn(str(self.card), polished_input="", content="<p>ok</p>", summary="ok")
            log = self.handler.read_chat_log(str(self.card))
            self.assertNotIn("user", log[-1])
            self.assertNotIn(instruction_only, json.dumps(log[-1], ensure_ascii=False))

            whitespace_role_instruction = "Update only the private weather rule."
            whitespace_role_result = post_submit({
                "roleText": "   ",
                "instructionText": whitespace_role_instruction,
                "charName": "Hero",
            })

            self.assertTrue(whitespace_role_result["ok"])
            self.assertEqual(whitespace_role_result["text"], "")
            expected_whitespace_raw = "   \n\n[USER_INSTRUCTION]\n" + whitespace_role_instruction
            self.assertEqual(server.INPUT_FILE.read_text(encoding="utf-8"), expected_whitespace_raw)
            inputs = self.handler.read_player_inputs(str(self.card))
            pending = self.handler.read_pending_user_turn(str(self.card))
            self.assertEqual(inputs[-1]["raw_text"], expected_whitespace_raw)
            self.assertEqual(inputs[-1]["display_text"], "")
            self.assertEqual(inputs[-1]["role_text"], "   ")
            self.assertEqual(inputs[-1]["user_instruction_text"], whitespace_role_instruction)
            self.assertEqual(pending["display_text"], "")
            self.assertEqual(pending["role_text"], "   ")
            self.assertEqual(pending["user_instruction_text"], whitespace_role_instruction)

            legacy_text = " Legacy exact text  "
            legacy = post_submit({"text": legacy_text, "charName": "Hero"})

            self.assertTrue(legacy["ok"])
            self.assertEqual(server.INPUT_FILE.read_text(encoding="utf-8"), f"【Hero】{legacy_text}")
            inputs = self.handler.read_player_inputs(str(self.card))
            self.assertEqual(inputs[-1]["raw_text"], legacy_text)
            self.assertEqual(inputs[-1]["display_text"], f"【Hero】{legacy_text}")
            self.assertNotIn("input_schema", inputs[-1])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_server_settings_api_normalizes_runtime_settings_and_preserves_debug_mode(self):
        server = _load_server()
        server.ROOT = self.styles
        server.SETTINGS_FILE = self.styles / "settings.json"
        payload = json.dumps(
            {
                "style": "轻松活泼",
                "wordCount": "1800",
                "nsfw": "舒缓",
                "selfRepairMode": "full",
                "allowSourceCodeSelfRepair": True,
                "modelDebugMode": True,
                "person": "第三人称",
                "antiImpersonation": False,
                "bgNpc": True,
                "charName": "旧主角",
                "unknown": "drop me",
            },
            ensure_ascii=False,
        )
        server.SETTINGS_FILE.write_bytes(payload.encode("utf-8-sig"))

        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/settings", timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))

            post_payload = {
                "style": "轻松活泼",
                "wordCount": "bad",
                "nsfw": "关闭",
                "selfRepairMode": "danger",
                "allowSourceCodeSelfRepair": "yes",
                "modelDebugMode": False,
                "person": "第三人称",
                "charName": "新主角",
                "unknown": "drop me",
            }
            request = urllib.request.Request(
                f"http://127.0.0.1:{httpd.server_port}/api/settings",
                data=json.dumps(post_payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                post_result = json.loads(response.read().decode("utf-8"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        self.assertEqual(result["style"], "轻松活泼")
        self.assertEqual(result["wordCount"], 1800)
        self.assertEqual(result["nsfw"], "舒缓")
        self.assertEqual(result["selfRepairMode"], "full")
        self.assertTrue(result["allowSourceCodeSelfRepair"])
        self.assertTrue(result["modelDebugMode"])
        self.assertNotIn("person", result)
        self.assertNotIn("antiImpersonation", result)
        self.assertNotIn("bgNpc", result)
        self.assertNotIn("charName", result)
        self.assertNotIn("unknown", result)

        self.assertTrue(post_result["ok"])
        self.assertEqual(post_result["settings"]["style"], "轻松活泼")
        self.assertEqual(post_result["settings"]["wordCount"], 600)
        self.assertEqual(post_result["settings"]["nsfw"], "关闭")
        self.assertEqual(post_result["settings"]["selfRepairMode"], "limited")
        self.assertFalse(post_result["settings"]["allowSourceCodeSelfRepair"])
        self.assertFalse(post_result["settings"]["modelDebugMode"])
        self.assertNotIn("person", post_result["settings"])
        self.assertNotIn("charName", post_result["settings"])
        self.assertNotIn("unknown", post_result["settings"])

        saved = json.loads(server.SETTINGS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved, post_result["settings"])

    def test_server_llm_settings_api_saves_redacts_and_preserves_keys(self):
        server = _load_server()
        server.ROOT = self.styles
        server.LLM_FRONTEND_SETTINGS_FILE = self.styles / "llm_settings.frontend.json"
        server.LLM_LOCAL_SETTINGS_FILE = self.styles / "llm_settings.local.json"
        server.CLAUDE_SETTINGS_FILE = self.base / ".claude" / "settings.json"

        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def request_json(path, payload=None, method="GET"):
            data = None
            headers = {}
            if payload is not None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request(
                f"http://127.0.0.1:{httpd.server_port}{path}",
                data=data,
                headers=headers,
                method=method,
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            first = request_json(
                "/api/llm_settings",
                {
                    "cc_switch": {
                        "enabled": True,
                        "service_url": "http://cc-switch.local:15721",
                        "api_key": "drop-cc-key",
                        "model": "drop-cc-model",
                    },
                    "openai_compatible": {
                        "enabled": True,
                        "base_url": "https://text.example/v1",
                        "api_key": "openai-secret",
                        "model": "text-model",
                    },
                    "image_generation": {
                        "base_url": "https://image.example/v1",
                        "api_key": "image-secret",
                        "model": "image-model",
                    },
                },
                method="POST",
            )
            get_result = request_json("/api/llm_settings")
            roundtrip = request_json("/api/llm_settings", get_result, method="POST")
            second = request_json(
                "/api/llm_settings",
                {
                    "openai_compatible": {
                        "enabled": True,
                        "base_url": "https://text2.example/v1",
                        "model": "text-model-2",
                    },
                    "image_generation": {
                        "base_url": "https://image2.example/v1",
                        "model": "image-model-2",
                    },
                },
                method="POST",
            )
            third = request_json(
                "/api/llm_settings",
                {
                    "cc_switch": {
                        "enabled": False,
                        "api_key": "drop-later-cc-key",
                        "model": "drop-later-cc-model",
                    },
                },
                method="POST",
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        self.assertTrue(first["ok"])
        self.assertEqual(first["settings"]["openai_compatible"]["api_key"], "")
        self.assertTrue(first["settings"]["openai_compatible"]["api_key_set"])
        self.assertEqual(first["settings"]["image_generation"]["api_key"], "")
        self.assertTrue(first["settings"]["image_generation"]["api_key_set"])
        self.assertNotIn("api_key", first["settings"]["cc_switch"])
        self.assertNotIn("model", first["settings"]["cc_switch"])
        self.assertEqual(get_result, first["settings"])

        saved = json.loads(server.LLM_FRONTEND_SETTINGS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved["openai_compatible"]["api_key"], "openai-secret")
        self.assertEqual(saved["image_generation"]["api_key"], "image-secret")
        self.assertNotIn("api_key", saved["cc_switch"])
        self.assertNotIn("model", saved["cc_switch"])
        self.assertTrue(roundtrip["settings"]["openai_compatible"]["api_key_set"])
        self.assertTrue(roundtrip["settings"]["image_generation"]["api_key_set"])
        saved_after_roundtrip = json.loads(server.LLM_FRONTEND_SETTINGS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved_after_roundtrip["openai_compatible"]["api_key"], "openai-secret")
        self.assertEqual(saved_after_roundtrip["image_generation"]["api_key"], "image-secret")

        self.assertTrue(second["settings"]["openai_compatible"]["api_key_set"])
        self.assertTrue(second["settings"]["image_generation"]["api_key_set"])
        saved_after_second = json.loads(server.LLM_FRONTEND_SETTINGS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved_after_second["openai_compatible"]["api_key"], "openai-secret")
        self.assertEqual(saved_after_second["image_generation"]["api_key"], "image-secret")
        self.assertEqual(saved_after_second["openai_compatible"]["base_url"], "https://text2.example/v1")
        self.assertEqual(saved_after_second["image_generation"]["base_url"], "https://image2.example/v1")
        self.assertFalse(third["settings"]["cc_switch"]["enabled"])
        self.assertEqual(third["settings"]["cc_switch"]["service_url"], "http://cc-switch.local:15721")
        self.assertNotIn("api_key", third["settings"]["cc_switch"])
        self.assertNotIn("model", third["settings"]["cc_switch"])
        saved_after_third = json.loads(server.LLM_FRONTEND_SETTINGS_FILE.read_text(encoding="utf-8"))
        self.assertFalse(saved_after_third["cc_switch"]["enabled"])
        self.assertEqual(saved_after_third["cc_switch"]["service_url"], "http://cc-switch.local:15721")
        self.assertNotIn("api_key", saved_after_third["cc_switch"])
        self.assertNotIn("model", saved_after_third["cc_switch"])

    def test_server_llm_settings_get_uses_frontend_env_local_priority(self):
        server = _load_server()
        server.ROOT = self.styles
        server.LLM_FRONTEND_SETTINGS_FILE = self.styles / "llm_settings.frontend.json"
        server.LLM_LOCAL_SETTINGS_FILE = self.styles / "llm_settings.local.json"
        server.CLAUDE_SETTINGS_FILE = self.base / ".claude" / "settings.json"
        server.LLM_LOCAL_SETTINGS_FILE.write_text(
            json.dumps(
                {
                    "cc_switch": {"enabled": True, "service_url": "http://local-switch"},
                    "openai_compatible": {
                        "enabled": False,
                        "base_url": "https://local.example/v1",
                        "api_key": "local-secret",
                        "model": "local-model",
                    },
                    "image_generation": {
                        "base_url": "https://local-image.example/v1",
                        "api_key": "local-image-secret",
                        "model": "local-image-model",
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        server.LLM_FRONTEND_SETTINGS_FILE.write_text(
            json.dumps(
                {
                    "cc_switch": {"enabled": False, "service_url": "http://frontend-switch"},
                    "openai_compatible": {
                        "enabled": True,
                        "base_url": "",
                        "api_key": "",
                        "model": "frontend-model",
                    },
                    "image_generation": {
                        "base_url": "https://frontend-image.example/v1",
                        "api_key": "",
                        "model": "",
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        env = {
            "AIRP_CC_SWITCH_ENABLED": "true",
            "AIRP_CC_SWITCH_SERVICE_URL": "http://env-switch",
            "AIRP_OPENAI_COMPATIBLE_ENABLED": "false",
            "AIRP_OPENAI_COMPATIBLE_BASE_URL": "https://env.example/v1",
            "AIRP_OPENAI_COMPATIBLE_API_KEY": "env-secret",
            "AIRP_OPENAI_COMPATIBLE_MODEL": "env-model",
            "AIRP_IMAGE_GENERATION_BASE_URL": "https://env-image.example/v1",
            "AIRP_IMAGE_GENERATION_API_KEY": "env-image-secret",
            "AIRP_IMAGE_GENERATION_MODEL": "env-image-model",
        }
        try:
            with mock.patch.dict(os.environ, env, clear=False):
                with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/llm_settings", timeout=5) as response:
                    result = json.loads(response.read().decode("utf-8"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        self.assertFalse(result["cc_switch"]["enabled"])
        self.assertEqual(result["cc_switch"]["service_url"], "http://frontend-switch")
        self.assertTrue(result["openai_compatible"]["enabled"])
        self.assertEqual(result["openai_compatible"]["base_url"], "https://env.example/v1")
        self.assertEqual(result["openai_compatible"]["model"], "frontend-model")
        self.assertTrue(result["openai_compatible"]["api_key_set"])
        self.assertEqual(result["image_generation"]["base_url"], "https://frontend-image.example/v1")
        self.assertEqual(result["image_generation"]["model"], "env-image-model")
        self.assertTrue(result["image_generation"]["api_key_set"])

    def test_server_llm_settings_get_reports_missing_configuration_to_frontend(self):
        server = _load_server()
        server.ROOT = self.styles
        server.LLM_FRONTEND_SETTINGS_FILE = self.styles / "llm_settings.frontend.json"
        server.LLM_LOCAL_SETTINGS_FILE = self.styles / "llm_settings.local.json"
        server.CLAUDE_SETTINGS_FILE = self.base / ".claude" / "settings.json"

        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        try:
            with mock.patch.dict(os.environ, {
                "AIRP_CC_SWITCH_ENABLED": "",
                "AIRP_CC_SWITCH_SERVICE_URL": "",
                "AIRP_OPENAI_COMPATIBLE_ENABLED": "",
                "AIRP_OPENAI_COMPATIBLE_BASE_URL": "",
                "AIRP_OPENAI_COMPATIBLE_API_KEY": "",
                "AIRP_OPENAI_COMPATIBLE_MODEL": "",
                "AIRP_IMAGE_GENERATION_BASE_URL": "",
                "AIRP_IMAGE_GENERATION_API_KEY": "",
                "AIRP_IMAGE_GENERATION_MODEL": "",
            }, clear=False):
                with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/llm_settings", timeout=5) as response:
                    result = json.loads(response.read().decode("utf-8"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        self.assertFalse(result["cc_switch"]["enabled"])
        self.assertEqual(result["cc_switch"]["service_url"], "")
        self.assertIn("configuration_errors", result)
        self.assertIn("未启用可用的文本 LLM provider", result["configuration_errors"])
        self.assertIn("图片生成 API 缺少 api_key", result["configuration_errors"])

    def test_server_llm_settings_test_uses_enabled_text_providers_only(self):
        server = _load_server()
        server.ROOT = self.styles
        server.LLM_FRONTEND_SETTINGS_FILE = self.styles / "llm_settings.frontend.json"
        server.LLM_LOCAL_SETTINGS_FILE = self.styles / "llm_settings.local.json"
        server.CLAUDE_SETTINGS_FILE = self.base / ".claude" / "settings.json"
        server.CLAUDE_SETTINGS_FILE.parent.mkdir(parents=True)
        server.CLAUDE_SETTINGS_FILE.write_text(
            json.dumps(
                {
                    "env": {
                        "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-test",
                        "ANTHROPIC_API_KEY": "claude-secret",
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        server.LLM_FRONTEND_SETTINGS_FILE.write_text(
            json.dumps(
                {
                    "cc_switch": {
                        "enabled": True,
                        "service_url": "http://cc-switch.test",
                    },
                    "openai_compatible": {
                        "enabled": True,
                        "base_url": "https://text.example/v1",
                        "api_key": "openai-secret",
                        "model": "text-model",
                    },
                    "image_generation": {
                        "base_url": "https://image.example/v1",
                        "api_key": "image-secret",
                        "model": "image-model",
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        calls = []

        def fake_test_connection(provider, config):
            calls.append((provider, dict(config)))
            return {"ok": True, "provider": provider, "status": 200, "model": config.get("model", "")}

        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        try:
            with mock.patch.object(server.llm_provider, "test_connection", side_effect=fake_test_connection):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/api/llm_settings/test",
                    data=json.dumps({}, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    result = json.loads(response.read().decode("utf-8"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        self.assertTrue(result["ok"])
        self.assertEqual([provider for provider, _ in calls], ["cc_switch", "openai_compatible"])
        self.assertEqual(calls[0][1]["model"], "claude-test")
        self.assertEqual(calls[0][1]["headers"]["x-api-key"], "claude-secret")
        self.assertEqual(calls[1][1]["api_key"], "openai-secret")
        self.assertEqual([item["provider"] for item in result["results"]], ["cc_switch", "openai_compatible"])

    def test_server_llm_settings_test_uses_unsaved_payload_without_writing_file(self):
        server = _load_server()
        server.ROOT = self.styles
        server.LLM_FRONTEND_SETTINGS_FILE = self.styles / "llm_settings.frontend.json"
        server.LLM_LOCAL_SETTINGS_FILE = self.styles / "llm_settings.local.json"
        server.CLAUDE_SETTINGS_FILE = self.base / ".claude" / "settings.json"

        saved_settings = {
            "cc_switch": {
                "enabled": False,
                "service_url": "http://cc-switch.test",
            },
            "openai_compatible": {
                "enabled": False,
                "base_url": "https://old.example/v1",
                "api_key": "old-openai-secret",
                "model": "old-model",
            },
            "image_generation": {
                "base_url": "https://image.example/v1",
                "api_key": "image-secret",
                "model": "image-model",
            },
        }
        server.LLM_FRONTEND_SETTINGS_FILE.write_text(json.dumps(saved_settings, ensure_ascii=False), encoding="utf-8")

        calls = []

        def fake_test_connection(provider, config):
            calls.append((provider, dict(config)))
            return {"ok": True, "provider": provider, "status": 200, "model": config.get("model", "")}

        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        try:
            with mock.patch.object(server.llm_provider, "test_connection", side_effect=fake_test_connection):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/api/llm_settings/test",
                    data=json.dumps(
                        {
                            "openai_compatible": {
                                "enabled": True,
                                "base_url": "https://new.example/v1",
                                "api_key": "new-openai-secret",
                                "model": "new-model",
                            }
                        },
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    result = json.loads(response.read().decode("utf-8"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        self.assertTrue(result["ok"])
        self.assertEqual([provider for provider, _ in calls], ["openai_compatible"])
        self.assertEqual(calls[0][1]["base_url"], "https://new.example/v1")
        self.assertEqual(calls[0][1]["model"], "new-model")
        self.assertEqual(calls[0][1]["api_key"], "new-openai-secret")
        self.assertEqual(json.loads(server.LLM_FRONTEND_SETTINGS_FILE.read_text(encoding="utf-8")), saved_settings)

    def test_server_llm_settings_test_save_true_writes_payload(self):
        server = _load_server()
        server.ROOT = self.styles
        server.LLM_FRONTEND_SETTINGS_FILE = self.styles / "llm_settings.frontend.json"
        server.LLM_LOCAL_SETTINGS_FILE = self.styles / "llm_settings.local.json"
        server.CLAUDE_SETTINGS_FILE = self.base / ".claude" / "settings.json"

        server.LLM_FRONTEND_SETTINGS_FILE.write_text(
            json.dumps(
                {
                    "openai_compatible": {
                        "enabled": False,
                        "base_url": "https://old.example/v1",
                        "api_key": "old-secret",
                        "model": "old-model",
                    },
                    "cc_switch": {
                        "enabled": False,
                        "service_url": "http://old-cc-switch.test",
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        calls = []

        def fake_test_connection(provider, config):
            calls.append((provider, dict(config)))
            return {"ok": True, "provider": provider, "status": 200}

        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        try:
            with mock.patch.object(server.llm_provider, "test_connection", side_effect=fake_test_connection):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/api/llm_settings/test",
                    data=json.dumps(
                        {
                            "save": True,
                            "openai_compatible": {
                                "enabled": True,
                                "base_url": "https://new.example/v1",
                                "api_key": "new-secret",
                                "model": "new-model",
                            },
                        },
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    result = json.loads(response.read().decode("utf-8"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        self.assertTrue(result["ok"])
        self.assertEqual([provider for provider, _ in calls], ["openai_compatible"])
        self.assertEqual(calls[0][1]["base_url"], "https://new.example/v1")
        saved = json.loads(server.LLM_FRONTEND_SETTINGS_FILE.read_text(encoding="utf-8"))
        self.assertTrue(saved["openai_compatible"]["enabled"])
        self.assertEqual(saved["openai_compatible"]["base_url"], "https://new.example/v1")
        self.assertEqual(saved["openai_compatible"]["api_key"], "new-secret")
        self.assertEqual(saved["openai_compatible"]["model"], "new-model")

    def test_server_llm_settings_post_invalid_json_returns_json_400(self):
        server = _load_server()
        server.ROOT = self.styles
        server.LLM_FRONTEND_SETTINGS_FILE = self.styles / "llm_settings.frontend.json"
        server.LLM_LOCAL_SETTINGS_FILE = self.styles / "llm_settings.local.json"
        server.CLAUDE_SETTINGS_FILE = self.base / ".claude" / "settings.json"

        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        try:
            for path in ["/api/llm_settings", "/api/llm_settings/test"]:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}{path}",
                    data=b"{invalid json",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(cm.exception.code, 400)
                self.assertEqual(cm.exception.headers.get_content_type(), "application/json")
                payload = json.loads(cm.exception.read().decode("utf-8"))
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"], "invalid json")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_server_llm_settings_test_exception_returns_json_500(self):
        server = _load_server()
        server.ROOT = self.styles
        server.LLM_FRONTEND_SETTINGS_FILE = self.styles / "llm_settings.frontend.json"
        server.LLM_LOCAL_SETTINGS_FILE = self.styles / "llm_settings.local.json"
        server.CLAUDE_SETTINGS_FILE = self.base / ".claude" / "settings.json"
        server.LLM_FRONTEND_SETTINGS_FILE.write_text(
            json.dumps(
                {
                    "openai_compatible": {
                        "enabled": True,
                        "base_url": "https://text.example/v1",
                        "api_key": "secret",
                        "model": "model",
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        try:
            with (
                mock.patch.object(server.llm_provider, "test_connection", side_effect=RuntimeError("provider boom")),
                mock.patch("traceback.print_exc"),
            ):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/api/llm_settings/test",
                    data=json.dumps({}, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(cm.exception.code, 500)
                self.assertEqual(cm.exception.headers.get_content_type(), "application/json")
                payload = json.loads(cm.exception.read().decode("utf-8"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "provider boom")

    def test_server_style_profiles_api_lists_json_presets(self):
        server = _load_server()
        server.ROOT = self.styles
        server.PRESETS_DIR = self.styles / "presets"
        server.PRESETS_DIR.mkdir()
        (server.PRESETS_DIR / "轻松活泼.json").write_text(
            json.dumps(
                {
                    "name": "轻松活泼",
                    "title": "轻快节奏",
                    "description": "明亮快节奏",
                    "content": "用明亮、轻快的句子推进场景。",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        old_profiles = self.styles / "profiles"
        old_profiles.mkdir()
        (old_profiles / "旧文风.md").write_text("# 旧文风\n不应列出", encoding="utf-8")

        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/style-profiles", timeout=5) as response:
                profiles = json.loads(response.read().decode("utf-8"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        self.assertEqual(
            profiles,
            [
                {
                    "name": "轻松活泼",
                    "title": "轻快节奏",
                    "description": "明亮快节奏",
                }
            ],
        )

    def test_response_parser_extracts_character_dialogues(self):
        parser = _load_response_parser()
        response = """
<content><p>Main narration.</p></content>
<character_dialogues>
[
  {"name":"Ada","source":"subagent","line":"I will take point.","aside":"steady"},
  {"name":"Narrator","source":"main","line":"Ignore me."}
]
</character_dialogues>
<summary>summary</summary>
"""

        parts = parser.parse_response(response)

        self.assertEqual(parts["character_dialogues"], [
            {"name": "Ada", "source": "subagent", "line": "I will take point.", "aside": "steady"},
            {"name": "Narrator", "source": "main", "line": "Ignore me."},
        ])

    def test_response_parser_falls_back_to_body_without_runtime_tags(self):
        parser = _load_response_parser()
        response = """<p>Main narration.</p>
<character_dialogues>[{"name":"Ada","source":"subagent","line":"Ready."}]</character_dialogues>
<tokens>
round_total: 7
</tokens>
"""

        parts = parser.parse_response(response)

        self.assertEqual(parts["content"], "<p>Main narration.</p>")
        self.assertEqual(parts["character_dialogues"], [
            {"name": "Ada", "source": "subagent", "line": "Ready."}
        ])
        self.assertEqual(parts["tokens"]["round_total"], 7)

    def test_response_parser_ignores_content_tags_inside_derived_edits(self):
        parser = _load_response_parser()
        replacement = "<content><p>Prior dream replacement.</p></content>\n<summary>prior summary</summary>"
        response = (
            "<derived_content_edits>"
            + json.dumps([{"turn_index": 0, "ai": replacement}], ensure_ascii=False)
            + "</derived_content_edits>\n"
            + "<p>Current turn continues after the player wakes up.</p>\n"
            + "<summary>current summary</summary>"
        )

        parts = parser.parse_response(response)

        self.assertEqual(parts["content"], "<p>Current turn continues after the player wakes up.</p>")
        self.assertEqual(parts["summary"], "current summary")
        self.assertEqual(parts["derived_content_edits"][0]["turn_index"], 0)
        self.assertIn("Prior dream replacement", parts["derived_content_edits"][0]["ai"])

    def test_handler_does_not_store_runtime_tags_in_chat_ai_without_content_tag(self):
        response = """<p>Main narration.</p>
<character_dialogues>[{"name":"Ada","source":"subagent","line":"Ready."}]</character_dialogues>
<tokens>
round_total: 7
</tokens>
"""
        parts = _load_response_parser().parse_response(response)

        self.handler.append_turn(
            str(self.card),
            content=parts["content"],
            character_dialogues=parts["character_dialogues"],
            tokens=parts["tokens"],
            full_text=response,
        )

        log = json.loads((self.card / "chat_log.json").read_text(encoding="utf-8"))
        self.assertEqual(log[0]["ai"], "<p>Main narration.</p>")
        self.assertNotIn("<character_dialogues>", log[0]["ai"])
        self.assertNotIn("<tokens>", log[0]["ai"])
        self.assertEqual(log[0]["character_dialogues"], [
            {"name": "Ada", "source": "subagent", "line": "Ready."}
        ])
        self.assertEqual(log[0]["tokens"]["round_total"], 7)

    def test_append_turn_stores_subagent_character_dialogues(self):
        dialogues = [
            {"name": "Ada", "source": "subagent", "line": "I will take point.", "aside": "steady"},
            {"name": "Narrator", "source": "main", "line": "Ignore me."},
        ]

        self.handler.append_turn(
            str(self.card),
            content="<p>Main narration.</p>",
            summary="summary",
            character_dialogues=dialogues,
        )

        log = json.loads((self.card / "chat_log.json").read_text(encoding="utf-8"))
        self.assertEqual(log[0]["character_dialogues"], [
            {"name": "Ada", "source": "subagent", "line": "I will take point.", "aside": "steady"}
        ])

    def test_append_turn_accepts_character_agent_dialogue_marker(self):
        dialogues = [
            {
                "name": "\u82cf\u9ece",
                "agent": "character",
                "agent_id": "character:\u82cf\u9ece",
                "line": "\u4f60\u679c\u7136\u4f1a\u5728\u8fd9\u4e2a\u65f6\u5019\u95ee\u3002",
                "aside": "\u51b7\u9759",
            }
        ]

        self.handler.append_turn(
            str(self.card),
            content="<p>Main narration.</p>",
            summary="summary",
            character_dialogues=dialogues,
        )

        log = json.loads((self.card / "chat_log.json").read_text(encoding="utf-8"))
        self.assertEqual(log[0]["character_dialogues"], [
            {
                "name": "\u82cf\u9ece",
                "source": "subagent",
                "line": "\u4f60\u679c\u7136\u4f1a\u5728\u8fd9\u4e2a\u65f6\u5019\u95ee\u3002",
                "aside": "\u51b7\u9759",
            }
        ])

    def test_content_js_renders_character_dialogues_as_independent_boxes(self):
        self.handler.write_chat_log(str(self.card), [{
            "index": 0,
            "ai": "<p>Main narration.</p>",
            "summary": "summary",
            "character_dialogues": [
                {"name": "Ada", "source": "subagent", "line": "I will take point.", "aside": "steady"}
            ],
        }])

        self.handler.write_content_js(str(self.card))
        content_js = (self.styles / "content.js").read_text(encoding="utf-8")

        self.assertIn("character-dialogues", content_js)
        self.assertIn("character-dialogue-card", content_js)
        self.assertIn("Ada", content_js)
        self.assertIn("I will take point.", content_js)
        self.assertIn("steady", content_js)

    def test_content_js_inserts_character_dialogues_inside_ai_flow(self):
        self.handler.write_chat_log(str(self.card), [{
            "index": 0,
            "ai": "<p>Before.</p><p>After.</p>",
            "summary": "summary",
            "character_dialogues": [
                {"name": "Ada", "source": "subagent", "line": "I will take point."}
            ],
        }])

        self.handler.write_content_js(str(self.card))
        content_js = (self.styles / "content.js").read_text(encoding="utf-8")

        before_idx = content_js.index("Before.")
        dialogue_idx = content_js.index("character-dialogues", before_idx)
        after_idx = content_js.index("After.", before_idx)
        self.assertLess(before_idx, dialogue_idx)
        self.assertLess(dialogue_idx, after_idx)

    def test_write_content_js_prefers_postprocess_core_and_exposes_ui_extensions(self):
        self.handler.write_chat_log(str(self.card), [{
            "index": 0,
            "ai": "<p>Main narration.</p><summary>Legacy summary</summary><options>\nLegacy option\n</options>",
            "summary": "Legacy summary",
        }])
        self._write_postprocess_output(
            core={
                "summary": "Postprocess preferred summary",
                "current_goal": "Postprocess goal",
                "options": [
                    {"label": "Postprocess option A", "source": "postprocess"},
                    "Postprocess option B",
                ],
                "state_patch": {},
            },
            ui_extensions={
                "status_panels": {"weather": "rain"},
                "custom_cards": {"focus": {"title": "Door"}},
                "asset_bindings": {},
            },
        )

        self.handler.write_content_js(str(self.card))

        self.assertEqual(self._content_window_var("SUMMARY_TEXT"), "Postprocess preferred summary")
        self.assertEqual(self._content_window_var("TURN_OPTIONS"), ["Postprocess option A", "Postprocess option B"])
        self.assertEqual(
            self._content_window_var("POSTPROCESS_UI"),
            {
                "status_panels": {"weather": "rain"},
                "custom_cards": {"focus": {"title": "Door"}},
                "asset_bindings": {},
            },
        )

    def test_append_turn_uses_current_run_postprocess_before_stale_card_root(self):
        run_dir = self.card / ".agent_runs" / "round-000001"
        (self.card / ".agent_runs").mkdir()
        (self.card / ".agent_runs" / "current").write_text(str(run_dir.resolve()), encoding="utf-8")
        self._write_postprocess_output(
            core={
                "summary": "Stale root summary",
                "current_goal": "Stale root goal",
                "options": ["Stale root option"],
                "state_patch": {"quest": "Stale root quest"},
            },
            ui_extensions={
                "status_panels": {"weather": "stale"},
                "custom_cards": {},
                "asset_bindings": {},
            },
        )
        self._write_postprocess_output(
            path=run_dir / "postprocess.output.json",
            core={
                "summary": "Current run summary",
                "current_goal": "Current run goal",
                "options": [{"label": "Current run option", "source": "postprocess"}],
                "state_patch": {"quest": "Current run quest"},
            },
            ui_extensions={
                "status_panels": {"weather": "current"},
                "custom_cards": {"focus": {"title": "Current run"}},
                "asset_bindings": {},
            },
        )

        self.handler.append_turn(str(self.card), content="<p>Main narration.</p>", summary="Legacy summary")

        self.assertEqual(self._content_window_var("SUMMARY_TEXT"), "Current run summary")
        self.assertEqual(self._content_window_var("TURN_OPTIONS"), ["Current run option"])
        self.assertEqual(
            self._content_window_var("POSTPROCESS_UI"),
            {
                "status_panels": {"weather": "current"},
                "custom_cards": {"focus": {"title": "Current run"}},
                "asset_bindings": {},
            },
        )
        state_js = (self.card / "state.js").read_text(encoding="utf-8")
        self.assertIn('quest: "Current run quest"', state_js)
        self.assertNotIn("Stale root", state_js)

    def test_write_content_js_uses_current_run_artifacts_postprocess(self):
        run_dir = self.card / ".agent_runs" / "round-000001"
        (self.card / ".agent_runs").mkdir()
        (self.card / ".agent_runs" / "current").write_text(str(run_dir.resolve()), encoding="utf-8")
        self.handler.write_chat_log(str(self.card), [{
            "index": 0,
            "ai": "<p>Main narration.</p><summary>Legacy summary</summary><options>\nLegacy option\n</options>",
            "summary": "Legacy summary",
        }])
        self._write_postprocess_output(
            path=run_dir / "artifacts" / "postprocess.output.json",
            core={
                "summary": "Artifact summary",
                "current_goal": "Artifact goal",
                "options": [{"label": "Artifact option", "source": "postprocess"}],
                "state_patch": {},
            },
            ui_extensions={
                "status_panels": {"weather": "artifact"},
                "custom_cards": {"focus": {"title": "Artifact"}},
                "asset_bindings": {},
            },
        )

        self.handler.write_content_js(str(self.card))

        self.assertEqual(self._content_window_var("SUMMARY_TEXT"), "Artifact summary")
        self.assertEqual(self._content_window_var("TURN_OPTIONS"), ["Artifact option"])
        self.assertEqual(
            self._content_window_var("POSTPROCESS_UI"),
            {
                "status_panels": {"weather": "artifact"},
                "custom_cards": {"focus": {"title": "Artifact"}},
                "asset_bindings": {},
            },
        )

    def test_append_turn_applies_postprocess_state_patch_quest(self):
        self._write_postprocess_output(
            core={
                "summary": "Postprocess summary",
                "current_goal": "Fallback current goal",
                "options": ["Continue"],
                "state_patch": {"quest": "Quest from state patch"},
            }
        )

        self.handler.append_turn(str(self.card), content="<p>Main narration.</p>", summary="Legacy summary")

        state_js = (self.card / "state.js").read_text(encoding="utf-8")
        self.assertIn('quest: "Quest from state patch"', state_js)

    def test_fallback_beautify_panel_strips_card_html_from_variable_values(self):
        html = self.handler._build_beautify_panel(
            {
                "世界": {
                    "时间": '<span style="color:Orange">黄昏</span><br>雨夜',
                },
                "铃天阳子": {
                    "当前状况": '<span style="color:Orange">强装镇定</span>',
                    "着装": {
                        "上衣": '<span style="color:#fff">白色校服</span>',
                    },
                    "身体状况": {
                        "疲劳": "轻微",
                    },
                },
            },
            {},
            {},
        )

        self.assertIn("黄昏<br>雨夜", html)
        self.assertIn("强装镇定", html)
        self.assertIn("白色校服", html)
        self.assertNotIn("&lt;span", html)
        self.assertNotIn("style=&quot;color", html)

    def test_append_turn_uses_postprocess_current_goal_when_quest_patch_missing(self):
        self._write_postprocess_output(
            core={
                "summary": "Postprocess summary",
                "current_goal": "Current goal fallback quest",
                "options": ["Continue"],
                "state_patch": {"stage": "Archive"},
            }
        )

        self.handler.append_turn(str(self.card), content="<p>Main narration.</p>", summary="Legacy summary")

        state_js = (self.card / "state.js").read_text(encoding="utf-8")
        self.assertIn('quest: "Current goal fallback quest"', state_js)
        self.assertIn('stage: "Archive"', state_js)

    def test_append_turn_applies_mvu_commands_from_postprocess_output(self):
        self._write_postprocess_output(
            mvu={
                "commands": [
                    '<UpdateVariable><JSONPatch>[{"op":"replace","path":"/mood","value":"alert"}]</JSONPatch></UpdateVariable>'
                ],
                "status": "ok",
                "issues": [],
            }
        )

        self.handler.append_turn(str(self.card), content="<p>Main narration.</p>", summary="Legacy summary")

        log = self.handler.read_chat_log(str(self.card))
        self.assertEqual(log[0]["variables"]["stat_data"]["mood"], "alert")
        self.assertEqual(log[0]["variables"]["delta"]["mood"]["new"], "alert")

    def test_append_turn_ignores_story_mvu_commands_when_postprocess_controls_mvu(self):
        self._write_postprocess_output(
            mvu={
                "commands": [],
                "status": "ok",
                "issues": [],
            }
        )
        story_text = (
            "<p>Main narration.</p>"
            '<UpdateVariable><JSONPatch>[{"op":"replace","path":"/mood","value":"story"}]</JSONPatch></UpdateVariable>'
        )

        self.handler.append_turn(str(self.card), content=story_text, summary="Legacy summary")

        log = self.handler.read_chat_log(str(self.card))
        self.assertNotIn("variables", log[0])

    def test_docs_describe_character_dialogues_contract(self):
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("<character_dialogues>", claude)
        self.assertIn("source=\"subagent\"", claude)
        self.assertIn("独立对话框", readme)
