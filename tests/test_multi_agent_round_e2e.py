import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "multi_agent_round" / "scenario.json"


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


class MultiAgentRoundE2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.styles_dir = Path(self.tmp.name) / "root" / "skills" / "styles"
        self.card.mkdir()
        self.styles_dir.mkdir(parents=True)
        self.agent_packets = _load_module("agent_packets")
        self.agent_outputs = _load_module("agent_outputs")
        self.agent_memory = _load_module("agent_memory")

    def tearDown(self):
        self.tmp.cleanup()

    def test_complete_file_protocol_round_without_live_model(self):
        self.assertTrue(FIXTURE.exists())
        scenario = json.loads(FIXTURE.read_text(encoding="utf-8"))

        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text=scenario["user_text"],
            chat_log=[],
            card_data={"title": "E2E", "character_orchestration": {"major": scenario["characters"]}},
            character_contexts={"characters": [{"name": name} for name in scenario["characters"]]},
            turn_index=0,
        )
        run_dir = Path(result["run_dir"])

        _write_json(
            run_dir / "gm.output.json",
            {
                "agent": "gm",
                "narration": "The archive door opens into a disguised moon-base airlock.",
                "npc_events": [],
                "world_state_delta": [{"scope": "hidden_truth", "fact": "the archive is a disguised moon base"}],
                "handoff": {"decision_point": "whether to enter the airlock"},
            },
        )
        _write_json(
            run_dir / "player.output.json",
            {
                "agent": "player",
                "agent_id": "player",
                "action": "I keep one hand on the doorframe and look inside.",
                "dialogue": [],
                "perception": ["I hear the door seal breathe like machinery."],
                "memory_delta": [{"text": "I heard machinery behind the archive door.", "source": "perceived"}],
            },
        )
        for name in scenario["characters"]:
            _write_json(
                run_dir / "characters" / f"{name}.output.json",
                {
                    "agent": "character",
                    "agent_id": f"character:{name.lower()}",
                    "character_name": name,
                    "action": f"I react to the opening door as {name}.",
                    "dialogue": [{"target": "player", "text": scenario["dialogue"][name]}],
                    "perception": ["I see the player hesitate at the threshold."],
                    "memory_delta": [{"text": "I saw the player hesitate at the archive door.", "source": "perceived"}],
                },
            )
        _write_json(
            run_dir / "story.output.json",
            {
                "content": scenario["story_content"],
                "character_dialogues": [
                    {"character": name, "text": scenario["dialogue"][name], "source_agent": f"character:{name.lower()}"}
                    for name in scenario["characters"]
                ],
                "metadata": {"round_id": "round-000001"},
            },
        )
        _write_json(
            run_dir / "critic.report.json",
            {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": ""},
        )

        delivery = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)
        memory = self.agent_memory.ingest_memory_deltas(self.card, run_dir, date_str="2026-06-16 12:00")
        delivered = self.agent_outputs.mark_delivered(self.card)
        story_input = json.loads((run_dir / "story.input.json").read_text(encoding="utf-8"))
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertTrue(delivery["ok"])
        self.assertTrue(memory["ok"])
        self.assertTrue(delivered["ok"])
        self.assertEqual((self.styles_dir / "response.txt").read_text(encoding="utf-8"), scenario["story_content"])
        self.assertEqual(story_input["player_inputs"]["raw_text"], scenario["user_text"])
        self.assertIn("moon base", story_input["player_inputs"]["routed_input"]["user_instruction_channel"])
        self.assertNotIn("moon base", story_input["actor_outputs"]["player"]["action"])
        self.assertEqual(len(delivery["story_output"]["character_dialogues"]), 2)
        self.assertIn("the archive is a disguised moon base", (self.card / "memory" / "world_delta.md").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "delivered")


if __name__ == "__main__":
    unittest.main()
