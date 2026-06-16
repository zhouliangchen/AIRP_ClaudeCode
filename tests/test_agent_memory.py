import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_memory():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_memory", ROOT / "skills" / "agent_memory.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class AgentMemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000001"
        self.run_dir.mkdir(parents=True)
        self.agent_memory = _load_agent_memory()
        self._write_story_input()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_story_input(self):
        _write_json(
            self.run_dir / "story.input.json",
            {
                "round_id": "round-000001",
                "player_inputs": {"raw_text": "I open the archive door."},
                "memory_deltas": {
                    "player": [
                        {"text": "I opened the archive door and smelled old paper.", "source": "perceived"}
                    ],
                    "characters": {
                        "Ada": [
                            {"text": "I saw the player enter the archive.", "source": "perceived"}
                        ]
                    },
                    "world": [
                        {"scope": "room", "fact": "the archive door is open"}
                    ],
                },
            },
        )

    def test_ingest_memory_deltas_writes_player_character_and_world_memory(self):
        result = self.agent_memory.ingest_memory_deltas(
            self.card,
            self.run_dir,
            date_str="2026-06-16 12:00",
        )

        self.assertEqual(result["ingested"], ["player", "character:Ada", "world"])
        player_recent = (self.card / "memory" / "player" / "recent.md").read_text(encoding="utf-8")
        ada_recent = (self.card / "memory" / "characters" / "Ada" / "recent.md").read_text(encoding="utf-8")
        world_recent = (self.card / "memory" / "world_delta.md").read_text(encoding="utf-8")
        self.assertIn("I opened the archive door", player_recent)
        self.assertIn("I saw the player enter", ada_recent)
        self.assertIn("the archive door is open", world_recent)

    def test_ingest_memory_deltas_is_idempotent_by_round_and_agent(self):
        self.agent_memory.ingest_memory_deltas(self.card, self.run_dir, date_str="2026-06-16 12:00")
        self.agent_memory.ingest_memory_deltas(self.card, self.run_dir, date_str="2026-06-16 12:01")

        player_recent = (self.card / "memory" / "player" / "recent.md").read_text(encoding="utf-8")
        ada_recent = (self.card / "memory" / "characters" / "Ada" / "recent.md").read_text(encoding="utf-8")
        self.assertEqual(player_recent.count("I opened the archive door"), 1)
        self.assertEqual(ada_recent.count("I saw the player enter"), 1)

    def test_actor_memory_rejects_gm_only_source(self):
        story_input = json.loads((self.run_dir / "story.input.json").read_text(encoding="utf-8"))
        story_input["memory_deltas"]["characters"]["Ada"] = [
            {"text": "The hidden vault was never real.", "source": "gm_only"}
        ]
        _write_json(self.run_dir / "story.input.json", story_input)

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "gm_only"):
            self.agent_memory.ingest_memory_deltas(self.card, self.run_dir)

    def test_ingest_memory_deltas_preserves_player_input_log(self):
        input_log = self.card / ".player_inputs.jsonl"
        original = '{"id":"p1","raw_text":"  keep exact spaces  ","display_text":"  keep exact spaces  "}\n'
        input_log.write_text(original, encoding="utf-8")

        self.agent_memory.ingest_memory_deltas(self.card, self.run_dir, date_str="2026-06-16 12:00")

        self.assertEqual(input_log.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
