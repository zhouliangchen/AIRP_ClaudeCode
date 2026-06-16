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

    def test_reports_missing_manifest_as_create_agent_run(self):
        (self.run_dir / "manifest.json").unlink()
        advice = self.agent_workflow.advise_next_actions(self.run_dir)
        self.assertIs(advice.get("ok"), False)
        self.assertEqual(advice["stage"], "missing_manifest")
        self.assertEqual(advice["next_action"], "create_agent_run")
        self.assertEqual(advice["missing_required"], [{"path": "manifest.json"}])

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
        manifest["critic_retry_count"] = 1
        _write_json(self.run_dir / "manifest.json", manifest)
        _write_json(
            self.run_dir / "critic.report.json",
            {"decision": "revise", "hard_failures": [], "soft_issues": ["weak handoff"], "repair_instruction": "Sharpen the stop point."},
        )
        advice = self.agent_workflow.advise_next_actions(self.run_dir)
        self.assertEqual(advice["next_action"], "repair_from_critic")
        self.assertEqual(advice["critic_decision"], "revise")
        self.assertEqual(advice["retry_count"], 1)
        self.assertEqual(advice["critic_retry_count"], 1)

    def test_reports_terminal_action_when_blocked_critic_retry_is_capped(self):
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["stage"] = "blocked"
        manifest["retry_count"] = 0
        manifest["critic_retry_count"] = 2
        _write_json(self.run_dir / "manifest.json", manifest)
        _write_json(
            self.run_dir / "critic.report.json",
            {"decision": "block", "hard_failures": ["unsafe continuation"], "soft_issues": [], "repair_instruction": "Stop for manual review."},
        )

        advice = self.agent_workflow.advise_next_actions(self.run_dir)

        self.assertEqual(advice["next_action"], "blocked_terminal")
        self.assertEqual(advice["critic_decision"], "block")
        self.assertEqual(advice["critic_retry_count"], 2)
        self.assertEqual(advice["retry_count"], 0)

    def test_reports_no_action_after_delivery(self):
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["stage"] = "delivered"
        _write_json(self.run_dir / "manifest.json", manifest)
        advice = self.agent_workflow.advise_next_actions(self.run_dir)
        self.assertEqual(advice["stage"], "delivered")
        self.assertEqual(advice["next_action"], "none")
        self.assertEqual(advice["missing_required"], [])

    def test_reports_delivery_gate_when_all_artifacts_exist(self):
        for rel in [
            "gm.output.json",
            "player.output.json",
            "characters/Ada.output.json",
            "story.input.json",
            "story.output.json",
        ]:
            _write_json(self.run_dir / rel, {"agent": "fixture"})
        _write_json(self.run_dir / "critic.report.json", {"decision": "pass"})
        advice = self.agent_workflow.advise_next_actions(self.run_dir)
        self.assertEqual(advice["next_action"], "run_delivery_gate")
        self.assertEqual(advice["missing_required"], [])

    def test_treats_seeded_default_critic_report_as_incomplete(self):
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["stage"] = "story_ready"
        _write_json(self.run_dir / "manifest.json", manifest)
        _write_json(self.run_dir / "story.input.json", {"agent": "fixture"})
        _write_json(self.run_dir / "story.output.json", {"agent": "fixture"})
        _write_json(
            self.run_dir / "critic.report.json",
            {
                "passed": True,
                "hard_failures": [],
                "soft_issues": [],
                "repair_instruction": "",
                "system_iteration_suggestion": "",
                "source": "default-pre-critic",
            },
        )
        advice = self.agent_workflow.advise_next_actions(self.run_dir)
        self.assertEqual(advice["next_action"], "dispatch_story_and_critic")
        self.assertEqual(
            [item["path"] for item in advice["missing_required"]],
            ["critic.report.json"],
        )


if __name__ == "__main__":
    unittest.main()
