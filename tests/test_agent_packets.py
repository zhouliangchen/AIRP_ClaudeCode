import importlib.util
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_run():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_run", ROOT / "skills" / "agent_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_agent_packets():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_packets", ROOT / "skills" / "agent_packets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_round_prepare():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("round_prepare", ROOT / "skills" / "round_prepare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.card.mkdir()
        self.agent_run = _load_agent_run()

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_run_dir_uses_next_round_number(self):
        first = self.agent_run.create_run_dir(self.card, turn_index=0)
        second = self.agent_run.create_run_dir(self.card, turn_index=7)

        self.assertEqual(first.name, "round-000001")
        self.assertEqual(second.name, "round-000008")
        self.assertTrue((self.card / ".agent_runs" / "round-000001").exists())
        self.assertTrue((self.card / ".agent_runs" / "current").read_text(encoding="utf-8").endswith("round-000008"))

    def test_create_run_dir_auto_increments(self):
        first = self.agent_run.create_run_dir(self.card)
        second = self.agent_run.create_run_dir(self.card)

        self.assertEqual(first.name, "round-000001")
        self.assertEqual(second.name, "round-000002")

    def test_current_run_dir_with_relative_card_path(self):
        relative_parent = Path(self.tmp.name) / "parent"
        relative_parent.mkdir()
        relative_card = relative_parent / "card"
        relative_card.mkdir()
        relative_name = "card"

        old_cwd = Path.cwd()
        try:
            os.chdir(relative_parent)
            run_dir = self.agent_run.create_run_dir(relative_name)
            current = self.agent_run.current_run_dir(relative_name)

            self.assertIsNotNone(current)
            self.assertEqual(current, run_dir.resolve())
            self.assertTrue(current.name.startswith("round-000001"))
        finally:
            os.chdir(old_cwd)

    def test_write_json_and_read_current_report(self):
        run_dir = self.agent_run.create_run_dir(self.card, turn_index=2)
        self.agent_run.write_json(run_dir / "critic.report.json", {"passed": False, "hard_failures": ["bad"]})

        report = self.agent_run.read_current_critic_report(self.card)

        self.assertEqual(report["passed"], False)
        self.assertEqual(report["hard_failures"], ["bad"])


class AgentPacketTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.card.mkdir()
        self.agent_packets = _load_agent_packets()
        self.agent_run = _load_agent_run()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_round_prepare_fixture(self):
        temp_root = Path(self.tmp.name) / "root"
        styles_dir = temp_root / "skills" / "styles"
        styles_dir.mkdir(parents=True)
        (styles_dir / "input.txt").write_text("I step into the archive.", encoding="utf-8")
        (styles_dir / "settings.json").write_text("{}", encoding="utf-8")
        (self.card / ".card_data.json").write_text("{}", encoding="utf-8")
        return temp_root, styles_dir

    def test_route_player_input_splits_omniscient_setting_block(self):
        text = "\u6211\u63a8\u5f00\u95e8\u8d70\u8fdb\u53bb\u3002\n\uff08\u4e0a\u5e1d\u89c6\u89d2\u8bbe\u5b9a\uff1a\u95e8\u540e\u5176\u5b9e\u662f\u68a6\u5883\u6d78\u54cd\u3002\uff09"
        routed = self.agent_packets.route_player_input(text)

        self.assertEqual(routed["role_channel"], "\u6211\u63a8\u5f00\u95e8\u8d70\u8fdb\u53bb\u3002")
        self.assertEqual(
            routed["user_instruction_channel"],
            "\uff08\u4e0a\u5e1d\u89c6\u89d2\u8bbe\u5b9a\uff1a\u95e8\u540e\u5176\u5b9e\u662f\u68a6\u5883\u6d78\u54cd\u3002\uff09",
        )
        self.assertEqual(
            routed["components"],
            [
                {"channel": "role", "text": "\u6211\u63a8\u5f00\u95e8\u8d70\u8fdb\u53bb\u3002"},
                {
                    "channel": "user_instruction",
                    "text": "\uff08\u4e0a\u5e1d\u89c6\u89d2\u8bbe\u5b9a\uff1a\u95e8\u540e\u5176\u5b9e\u662f\u68a6\u5883\u6d78\u54cd\u3002\uff09",
                },
            ],
        )

    def test_route_player_input_supports_english_instruction_cues(self):
        routed = self.agent_packets.route_player_input(
            "I open the gate.\nSystem: make the castle a moon base.\n"
        )
        self.assertEqual(routed["role_channel"], "I open the gate.")
        self.assertEqual(routed["user_instruction_channel"], "System: make the castle a moon base.")

    def test_route_player_input_keeps_parenthesized_action_as_role(self):
        routed = self.agent_packets.route_player_input("(I glance at the door and step inside.)")
        self.assertEqual(routed["role_channel"], "(I glance at the door and step inside.)")
        self.assertEqual(routed["user_instruction_channel"], "")

    def test_route_player_input_keeps_ordinary_rewrite_sentence_as_role(self):
        routed = self.agent_packets.route_player_input("I rewrite the rune on the wall.")
        self.assertEqual(routed["role_channel"], "I rewrite the rune on the wall.")
        self.assertEqual(routed["user_instruction_channel"], "")

    def test_build_player_packet_uses_role_channel_without_user_instructions(self):
        routed = self.agent_packets.route_player_input("\u6211\u62ab\u51fa\u77ed\u5251\u3002\n\uff08\u7cfb\u7edf\u6307\u4ee4\uff1a\u5c06\u57ce\u5821\u8bbe\u5b9a\u4e3a\u88ab\u9057\u5fd8\u7684\u6708\u9762\u57fa\u5730\u3002\uff09")
        packet = self.agent_packets.build_player_packet(self.card, routed, [])

        self.assertIn("\u62ab\u51fa\u77ed\u5251", packet["role_channel"])
        self.assertNotIn("\u6708\u9762\u57fa\u5730", json.dumps(packet, ensure_ascii=False))
        self.assertNotIn("user_instruction_channel", packet)
        self.assertEqual(packet["agent"], "player")

    def test_prepare_agent_run_builds_expected_context_files(self):
        user_text = "\u6211\u524d\u5f80\u6708\u9762\u57fa\u5730\uff0c\u5bfb\u627e\u65b0\u7684\u7ebf\u7d22\u3002"
        chat_log = [{"index": 3, "summary": "\u5f00\u542f\u7b2c\u4e00\u8f6e"}]
        card_data = {"title": "\u6d4b\u8bd5\u5361"}
        character_contexts = {
            "characters": [
                {
                    "name": "Ada",
                    "profile_summary": "Ada is cautious.",
                }
            ],
            "minor_policy": "main_agent",
        }

        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text=user_text,
            chat_log=chat_log,
            card_data=card_data,
            character_contexts=character_contexts,
            turn_index=0,
        )

        run_dir = Path(result["run_dir"])
        self.assertTrue((run_dir / "input.json").exists())
        self.assertTrue((run_dir / "gm.context.json").exists())
        self.assertTrue((run_dir / "player.context.json").exists())

        safe_name = self.agent_run.safe_name("Ada")
        char_path = run_dir / "characters" / f"{safe_name}.context.json"
        self.assertTrue(char_path.exists())

        critic = json.loads((run_dir / "critic.report.json").read_text(encoding="utf-8"))
        self.assertEqual(critic, self.agent_packets.DEFAULT_CRITIC_REPORT)

    def test_prepare_agent_run_ignores_metadata_dict_as_character_context(self):
        user_text = "I test metadata-only character context."
        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text=user_text,
            chat_log=[],
            card_data={"title": "\u6d4b\u8bd5\u5361"},
            character_contexts={"meta": {"version": 1}},
            turn_index=0,
        )
        run_dir = Path(result["run_dir"])
        self.assertFalse((run_dir / "characters" / "meta.context.json").exists())

    def test_build_character_packet_excludes_user_instruction_text(self):
        routed = self.agent_packets.route_player_input("I step toward the gate.\nOmniscient: the door is a dream echo.")
        packet = self.agent_packets.build_character_packet(
            self.card,
            {"name": "Ada", "profile_summary": "Ada is cautious."},
            routed,
            [],
        )
        self.assertEqual(packet["role_channel"], "I step toward the gate.")
        self.assertNotIn("user_instruction_channel", packet)
        self.assertNotIn("dream echo", json.dumps(packet, ensure_ascii=False))

    def test_round_prepare_writes_agent_run_packets_and_reports_path(self):
        temp_root, styles_dir = self._make_round_prepare_fixture()
        round_prepare = _load_round_prepare()
        called = {}
        expected_run_dir = str(self.card / ".agent_runs" / "round-000001")

        def stub_prepare_agent_run(**kwargs):
            called.update(kwargs)
            return {
                "run_dir": expected_run_dir,
                "routed_input": {
                    "role_channel": "I step into the archive.",
                    "user_instruction_channel": "",
                },
            }

        round_prepare.agent_packets.prepare_agent_run = stub_prepare_agent_run
        round_prepare.write_progress = lambda *args, **kwargs: None
        round_prepare.apply_injections = lambda card_folder: []
        round_prepare.match_worldbook.match_worldbook = lambda card_folder: []
        round_prepare.mvu_check.generate_checklist = lambda card_folder: None

        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["round_prepare.py", str(self.card), str(temp_root)]
            with contextlib.redirect_stdout(stdout):
                round_prepare.main()
        finally:
            sys.argv = old_argv

        self.assertEqual(called["turn_index"], 0)
        self.assertIsInstance(called["character_contexts"], dict)

        round_context_path = styles_dir / "round_context.txt"
        self.assertTrue(round_context_path.exists())
        self.assertIn("=== AGENT_RUN ===", round_context_path.read_text(encoding="utf-8"))

        character_contexts_path = styles_dir / "character_contexts.json"
        self.assertTrue(character_contexts_path.exists())

        payload = json.loads(stdout.getvalue().strip())
        self.assertEqual(payload["agent_run"], expected_run_dir)

    def test_round_prepare_continues_when_agent_run_packet_generation_fails(self):
        temp_root, styles_dir = self._make_round_prepare_fixture()
        round_prepare = _load_round_prepare()

        def boom(**kwargs):
            raise OSError("boom")

        round_prepare.agent_packets.prepare_agent_run = boom
        round_prepare.write_progress = lambda *args, **kwargs: None
        round_prepare.apply_injections = lambda card_folder: []
        round_prepare.match_worldbook.match_worldbook = lambda card_folder: []
        round_prepare.mvu_check.generate_checklist = lambda card_folder: None

        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["round_prepare.py", str(self.card), str(temp_root)]
            with contextlib.redirect_stdout(stdout):
                round_prepare.main()
        finally:
            sys.argv = old_argv

        round_context_path = styles_dir / "round_context.txt"
        character_contexts_path = styles_dir / "character_contexts.json"
        self.assertTrue(round_context_path.exists())
        self.assertTrue(character_contexts_path.exists())

        payload = json.loads(stdout.getvalue().strip())
        self.assertIsNone(payload["agent_run"])
