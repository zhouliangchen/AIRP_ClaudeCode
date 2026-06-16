import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_workflow():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_workflow", ROOT / "skills" / "agent_workflow.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class AgentWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "round-000001"
        self.run_dir.mkdir()
        self.agent_workflow = _load_agent_workflow()
        _write_json(
            self.run_dir / "manifest.json",
            {
                "round_id": "round-000001",
                "stage": "awaiting_agent_outputs",
                "expected_outputs": {
                    "gm": "gm.output.json",
                    "player": "player.output.json",
                    "characters": {"Ada": "characters/Ada.output.json"},
                    "story": "story.output.json",
                    "critic": "critic.report.json",
                },
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_reports_missing_required_agent_outputs(self):
        advice = self.agent_workflow.advise_next_actions(self.run_dir)
        self.assertEqual(advice["stage"], "awaiting_agent_outputs")
        self.assertEqual(advice["next_action"], "dispatch_agent_outputs")
        self.assertEqual(
            sorted(item["path"] for item in advice["missing_required"]),
            ["characters/Ada.output.json", "gm.output.json", "player.output.json"],
        )

    def test_reports_build_story_input_when_actor_outputs_exist(self):
        for rel in ["gm.output.json", "player.output.json", "characters/Ada.output.json"]:
            _write_json(self.run_dir / rel, {"agent": "fixture"})
        advice = self.agent_workflow.advise_next_actions(self.run_dir)
        self.assertEqual(advice["next_action"], "build_story_input")
        self.assertEqual(advice["missing_required"], [])

    def test_reports_story_and_critic_work_after_story_ready(self):
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["stage"] = "story_ready"
        _write_json(self.run_dir / "manifest.json", manifest)
        advice = self.agent_workflow.advise_next_actions(self.run_dir)
        self.assertEqual(advice["next_action"], "dispatch_story_and_critic")
        self.assertEqual(
            sorted(item["path"] for item in advice["missing_required"]),
            ["critic.report.json", "story.output.json"],
        )

    def test_reports_repair_when_blocked(self):
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["stage"] = "blocked"
        manifest["retry_count"] = 1
        _write_json(self.run_dir / "manifest.json", manifest)
        _write_json(
            self.run_dir / "critic.report.json",
            {"decision": "revise", "hard_failures": [], "soft_issues": ["weak handoff"], "repair_instruction": "Sharpen the stop point."},
        )
        advice = self.agent_workflow.advise_next_actions(self.run_dir)
        self.assertEqual(advice["next_action"], "repair_from_critic")
        self.assertEqual(advice["critic_decision"], "revise")
        self.assertEqual(advice["retry_count"], 1)


if __name__ == "__main__":
    unittest.main()
