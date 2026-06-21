import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class AgentSnapshotsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.card.mkdir()
        self.snapshots = load_module("agent_snapshots")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_card_state(self):
        write_json(self.card / "chat_log.json", [{"role": "assistant", "content": "old"}])
        write_json(self.card / ".card_data.json", {"name": "Fixture"})
        (self.card / "memory").mkdir()
        (self.card / "memory" / "project.md").write_text("old memory", encoding="utf-8")

    def test_create_snapshot_copies_card_state(self):
        self._write_card_state()

        result = self.snapshots.create_snapshot(
            self.card,
            "round-000001",
            reason="before_input",
        )

        self.assertTrue(result["ok"])
        snapshot_dir = Path(result["snapshot_dir"])
        self.assertTrue((snapshot_dir / "chat_log.json").exists())
        self.assertTrue((snapshot_dir / ".card_data.json").exists())
        self.assertTrue((snapshot_dir / "memory" / "project.md").exists())
        metadata = json.loads((snapshot_dir / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["snapshot_id"], result["snapshot_id"])
        self.assertEqual(metadata["round_id"], "round-000001")
        self.assertEqual(metadata["reason"], "before_input")
        self.assertIn("chat_log.json", metadata["copied"])
        self.assertIn(".card_data.json", metadata["copied"])
        self.assertIn("memory", metadata["copied"])

    def test_restore_snapshot_restores_files(self):
        self._write_card_state()
        created = self.snapshots.create_snapshot(
            self.card,
            "round-000001",
            reason="before_input",
        )
        (self.card / "memory" / "project.md").write_text("new memory", encoding="utf-8")

        restored = self.snapshots.restore_snapshot(
            self.card,
            created["snapshot_id"],
            mode="round_progression",
        )

        self.assertTrue(restored["ok"])
        self.assertEqual(restored["mode"], "round_progression")
        self.assertEqual((self.card / "memory" / "project.md").read_text(encoding="utf-8"), "old memory")

    def test_create_snapshot_ids_do_not_collide_for_same_round(self):
        self._write_card_state()

        first = self.snapshots.create_snapshot(self.card, "round-000001", reason="before_input")
        second = self.snapshots.create_snapshot(self.card, "round-000001", reason="before_input")

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertTrue(Path(first["snapshot_dir"]).exists())
        self.assertTrue(Path(second["snapshot_dir"]).exists())

    def test_restore_snapshot_reports_missing_snapshot(self):
        result = self.snapshots.restore_snapshot(
            self.card,
            "snapshot_missing",
            mode="round_progression",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "snapshot_missing")

    def test_restore_snapshot_replaces_changed_target_shapes(self):
        self._write_card_state()
        created = self.snapshots.create_snapshot(
            self.card,
            "round-000001",
            reason="before_input",
        )
        (self.card / "memory" / "project.md").unlink()
        (self.card / "memory" / "project.md").mkdir()
        (self.card / "memory" / "project.md" / "nested.txt").write_text(
            "new shape",
            encoding="utf-8",
        )

        restored = self.snapshots.restore_snapshot(
            self.card,
            created["snapshot_id"],
            mode="round_progression",
        )

        self.assertTrue(restored["ok"])
        self.assertTrue((self.card / "memory" / "project.md").is_file())
        self.assertEqual((self.card / "memory" / "project.md").read_text(encoding="utf-8"), "old memory")


if __name__ == "__main__":
    unittest.main()
