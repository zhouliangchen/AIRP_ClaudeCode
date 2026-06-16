import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_outputs():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_outputs", ROOT / "skills" / "agent_outputs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class AgentOutputsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000001"
        self.styles_dir = Path(self.tmp.name) / "root" / "skills" / "styles"
        self.run_dir.mkdir(parents=True)
        self.styles_dir.mkdir(parents=True)
        (self.card / ".agent_runs" / "current").write_text(str(self.run_dir.resolve()), encoding="utf-8")
        self.agent_outputs = _load_agent_outputs()
        self._write_base_round()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_base_round(self):
        _write_json(
            self.run_dir / "manifest.json",
            {
                "round_id": "round-000001",
                "stage": "prompts_ready",
                "expected_outputs": {
                    "gm": "gm.output.json",
                    "player": "player.output.json",
                    "characters": {"Ada": "characters/Ada.output.json"},
                    "story": "story.output.json",
                    "critic": "critic.report.json",
                },
            },
        )
        _write_json(
            self.run_dir / "input.json",
            {
                "raw_text": "I open the archive door.",
                "routed_input": {
                    "role_channel": "I open the archive door.",
                    "user_instruction_channel": "",
                },
            },
        )
        _write_json(
            self.run_dir / "gm.output.json",
            {
                "agent": "gm",
                "narration": "The archive answers with stale air.",
                "npc_events": [],
                "world_state_delta": [{"scope": "room", "fact": "the door is open"}],
                "handoff": {},
            },
        )
        _write_json(
            self.run_dir / "player.output.json",
            {
                "agent": "player",
                "agent_id": "player",
                "action": "I step through the door.",
                "dialogue": [],
                "perception": ["I smell paper dust."],
                "memory_delta": [{"text": "I opened the archive door.", "source": "perceived"}],
            },
        )
        _write_json(
            self.run_dir / "characters" / "Ada.output.json",
            {
                "agent": "character",
                "agent_id": "character:ada",
                "character_name": "Ada",
                "action": "I lift the lamp.",
                "dialogue": [{"target": "player", "text": "Stay close."}],
                "perception": ["I see the player cross the threshold."],
                "memory_delta": [{"text": "I saw the player enter the archive.", "source": "perceived"}],
            },
        )

    def _write_story_and_critic(self, decision="pass"):
        _write_json(
            self.run_dir / "story.output.json",
            {
                "content": "<content>Ada lifted the lamp. \"Stay close,\" she said.</content>",
                "character_dialogues": [
                    {"character": "Ada", "text": "Stay close.", "source_agent": "character:ada"}
                ],
                "metadata": {"round_id": "round-000001"},
            },
        )
        _write_json(
            self.run_dir / "critic.report.json",
            {
                "decision": decision,
                "hard_failures": ["logic gap"] if decision == "block" else [],
                "soft_issues": ["needs sharper sensory detail"] if decision == "revise" else [],
                "repair_instruction": "Revise sensory continuity." if decision == "revise" else "",
            },
        )

    def test_build_story_input_assembles_valid_agent_outputs(self):
        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(story_input["round_id"], "round-000001")
        self.assertEqual(story_input["player_inputs"]["raw_text"], "I open the archive door.")
        self.assertEqual(story_input["gm_output"]["world_state_delta"][0]["fact"], "the door is open")
        self.assertEqual(story_input["actor_outputs"]["player"]["action"], "I step through the door.")
        self.assertEqual(
            story_input["actor_outputs"]["characters"]["Ada"]["dialogue"][0]["text"],
            "Stay close.",
        )
        self.assertEqual(
            story_input["memory_deltas"]["characters"]["Ada"][0]["text"],
            "I saw the player enter the archive.",
        )
        self.assertTrue((self.run_dir / "story.input.json").exists())
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "story_ready")
        self.assertIn("story_ready", [item["stage"] for item in manifest["status"]])

    def test_build_story_input_includes_interaction_trace_summary(self):
        trace = {
            "round_id": "round-000001",
            "status": "decision_point",
            "participants": ["gm", "player", "character:Ada"],
            "chapter_target_words": 1200,
            "events": [
                {"actor": "character:Ada", "visibility": "world_visible", "type": "dialogue", "content": "Stay close."},
                {"actor": "character:Ada", "visibility": "private", "type": "thought", "content": "I fear this."},
            ],
            "decision_point": {"reason": "player must choose", "options": ["enter"]},
            "stop_reason": "player must choose",
        }
        _write_json(self.run_dir / "interaction.trace.json", trace)

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(story_input["interaction_trace"]["status"], "decision_point")
        self.assertEqual(story_input["interaction_trace"]["visible_events"][0]["content"], "Stay close.")
        self.assertEqual(story_input["interaction_trace"]["private_event_count"], 1)

    def test_build_story_input_degrades_malformed_interaction_trace(self):
        (self.run_dir / "interaction.trace.json").write_text("{bad json", encoding="utf-8")

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(story_input["interaction_trace"]["status"], "invalid")
        self.assertEqual(story_input["interaction_trace"]["visible_events"], [])

    def test_build_story_input_blocks_missing_required_character_output(self):
        (self.run_dir / "characters" / "Ada.output.json").unlink()

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "characters/Ada.output.json"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_prepare_delivery_blocks_critic_block_decision(self):
        self._write_story_and_critic(decision="block")

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "retry")
        self.assertEqual(result["reason"], "critic_block")
        self.assertFalse((self.styles_dir / "response.txt").exists())

    def test_prepare_delivery_blocks_missing_story_output(self):
        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "agent_outputs")
        self.assertIn("story.output.json", result["detail"])
        self.assertFalse((self.styles_dir / "response.txt").exists())
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertIn("blocked", [item["stage"] for item in manifest["status"]])

    def test_prepare_delivery_revise_increments_retry_without_rewriting_player_input(self):
        self._write_story_and_critic(decision="revise")
        before = (self.run_dir / "input.json").read_text(encoding="utf-8")

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "critic_revise")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["retry_count"], 1)
        self.assertEqual((self.run_dir / "input.json").read_text(encoding="utf-8"), before)
        self.assertFalse((self.styles_dir / "response.txt").exists())

    def test_prepare_delivery_pass_writes_story_content_to_response(self):
        self._write_story_and_critic(decision="pass")

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["story_output"]["content"], (self.styles_dir / "response.txt").read_text(encoding="utf-8"))
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "critic_passed")
        self.assertIn("critic_passed", [item["stage"] for item in manifest["status"]])

    def test_mark_delivered_updates_manifest_stage(self):
        self._write_story_and_critic(decision="pass")
        self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        result = self.agent_outputs.mark_delivered(self.card)

        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(result["ok"])
        self.assertEqual(manifest["stage"], "delivered")
        self.assertIn("delivered", [item["stage"] for item in manifest["status"]])


if __name__ == "__main__":
    unittest.main()
