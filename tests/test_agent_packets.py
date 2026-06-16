import importlib.util
import os
import json
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
