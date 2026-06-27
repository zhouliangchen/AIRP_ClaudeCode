import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CapabilityExecutorsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000001"
        self.run_dir.mkdir(parents=True)
        self.executors = _load("capability_executors")
        self.store = _load("actor_memory_store")

    def tearDown(self):
        self.tmp.cleanup()

    def test_execute_character_rename_updates_storage_player_mapping_and_registry(self):
        paths = self.store.ensure_actor_files(self.card, "player")
        paths.short_term.write_text("记忆的回声：你听见有人叫你的真名。\n", encoding="utf-8")
        (self.card / ".card_data.json").write_text(
            json.dumps(
                {
                    "name": "Blank",
                    "mode": "blank_bootstrap",
                    "character_orchestration": {"major": ["player"], "minor_policy": "main_agent"},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        intent = {
            "id": "intent_000001",
            "type": "character_rename",
            "payload": {
                "from_name": "player",
                "to_name": "雨蒙",
                "actor_id": "player",
                "reason": "主角名字已经明确。",
            },
        }

        result = self.executors.execute_intent(
            self.card,
            self.run_dir,
            intent,
            phase="after_input_analysis",
        )

        self.assertEqual(result["status"], "completed")
        outputs = result["outputs"]
        self.assertEqual(outputs["from_name"], "player")
        self.assertEqual(outputs["to_name"], "雨蒙")
        self.assertTrue(outputs["player_mapping_updated"])
        self.assertFalse((self.card / "characters" / "player").exists())
        self.assertTrue((self.card / "characters" / "雨蒙").is_dir())
        self.assertEqual(
            (self.card / "characters" / "player.md").read_text(encoding="utf-8"),
            "name: 雨蒙\npath: characters/雨蒙\n",
        )
        card_data = json.loads((self.card / ".card_data.json").read_text(encoding="utf-8"))
        self.assertEqual(card_data["character_orchestration"]["major"], ["雨蒙"])
        artifact = self.run_dir / "artifacts" / "runtime_pump" / "character_renames" / "intent_000001.json"
        self.assertTrue(artifact.exists())


if __name__ == "__main__":
    unittest.main()
