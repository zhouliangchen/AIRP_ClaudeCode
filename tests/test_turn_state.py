import importlib.util
import json
import sys
import tempfile
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

    def test_rp_command_points_to_orchestrator(self):
        command = (ROOT / ".claude" / "commands" / "rp.md").read_text(encoding="utf-8")

        self.assertIn("rp-orchestrator", command)
        self.assertIn("主 agent 不直接承担常规叙事创作", command)
        self.assertNotIn("## 第一步", command)
        self.assertNotIn("## 第二步", command)

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

    def test_player_input_log_is_authoritative_jsonl(self):
        entry = self.handler.record_player_input(str(self.card), "原始输入", "【玩家】原始输入")

        log_path = self.card / ".player_inputs.jsonl"
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        saved = json.loads(lines[0])

        self.assertEqual(saved["id"], entry["id"])
        self.assertEqual(saved["raw_text"], "原始输入")
        self.assertEqual(saved["display_text"], "【玩家】原始输入")
        self.assertEqual(saved["source"], "player")

    def test_progress_state_round_trips(self):
        self.handler.write_progress("delivering", "正在交付到前端", percent=85)

        progress = self.handler.read_progress()

        self.assertEqual(progress["stage"], "delivering")
        self.assertEqual(progress["label"], "正在交付到前端")
        self.assertEqual(progress["percent"], 85)

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

    def test_docs_describe_character_dialogues_contract(self):
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("<character_dialogues>", claude)
        self.assertIn("source=\"subagent\"", claude)
        self.assertIn("独立对话框", readme)
