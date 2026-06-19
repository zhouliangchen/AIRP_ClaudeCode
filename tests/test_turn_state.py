import importlib.util
import json
import os
import sys
import tempfile
import threading
import urllib.request

import unittest
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
            "rp-player-agent.md",
            "rp-character-agent.md",
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
        player = (skills_dir / "rp-player-agent.md").read_text(encoding="utf-8")
        character = (skills_dir / "rp-character-agent.md").read_text(encoding="utf-8")
        story = (skills_dir / "rp-story-agent.md").read_text(encoding="utf-8")
        critic = (skills_dir / "rp-critic-agent.md").read_text(encoding="utf-8")
        delivery = (skills_dir / "rp-delivery.md").read_text(encoding="utf-8")
        assets = (skills_dir / "rp-assets-ui.md").read_text(encoding="utf-8")
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

        self.assertIn("Claude Code 直驱", rp)
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

        self.assertIn("不知道玩家", player)
        self.assertIn("不知道 GM", player)
        self.assertIn("关键决策点", player)
        self.assertIn("actor.outputs.json", player)
        self.assertNotIn("player.output.json", player)

        self.assertIn("真正活在作品世界", character)
        self.assertIn("角色独立的人格", character)
        self.assertIn("感官", character)
        self.assertIn("memory_delta", character)

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

        self.assertIn("Claude Code 直驱", claude)
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

        self.assertEqual(progress["stage"], "delivering")
        self.assertEqual(progress["label"], "正在交付到前端")
        self.assertEqual(progress["percent"], 85)

    def test_blank_profile_derives_self_identity_from_authoritative_player_input(self):
        (self.card / "memory" / "characters" / "_self").mkdir(parents=True)
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
        profile = json.loads((self.card / "memory" / "characters" / "_self" / "profile.json").read_text(encoding="utf-8"))

        self.assertEqual(card_data["name"], "雨蒙")
        self.assertEqual(profile["fields"]["role"], "普通的高一男生")

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
        self.assertIn('value="analysis_only"', html)
        self.assertIn('value="limited"', html)
        self.assertIn('value="full"', html)
        self.assertIn("s.selfRepairMode || 'limited'", html)
        self.assertIn("selfRepairMode: document.getElementById('set-self-repair-mode').value", html)

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
        self.assertIn("ACTION", source)
        self.assertIn("SYNOPSIS", source)
        self.assertIn("OMNISCIENT_SETTING", source)
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

    def test_docs_describe_character_dialogues_contract(self):
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("<character_dialogues>", claude)
        self.assertIn("source=\"subagent\"", claude)
        self.assertIn("独立对话框", readme)
