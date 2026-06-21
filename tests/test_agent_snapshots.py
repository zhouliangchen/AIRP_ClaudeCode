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

    def test_restore_snapshot_rejects_parent_id_without_mutating_card(self):
        self._write_card_state()
        (self.card / "memory" / "project.md").write_text("safe memory", encoding="utf-8")
        agent_runs = self.card / ".agent_runs"
        (agent_runs / "snapshots").mkdir(parents=True)
        write_json(
            agent_runs / "snapshot.json",
            {
                "snapshot_id": "..",
                "round_id": "round-000001",
                "reason": "malicious",
                "copied": ["memory"],
            },
        )
        (agent_runs / "memory").mkdir()
        (agent_runs / "memory" / "project.md").write_text("escaped memory", encoding="utf-8")

        result = self.snapshots.restore_snapshot(
            self.card,
            "..",
            mode="round_progression",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "snapshot_missing")
        self.assertEqual((self.card / "memory" / "project.md").read_text(encoding="utf-8"), "safe memory")

    def test_restore_snapshot_rejects_path_shaped_ids(self):
        ids = [
            "round-000001-20260621T123456123456Z-abcdef123456/child",
            "round-000001-20260621T123456123456Z-abcdef123456\\child",
            "C:\\temp\\round-000001-20260621T123456123456Z-abcdef123456",
            "/tmp/round-000001-20260621T123456123456Z-abcdef123456",
            ".",
            "..",
        ]

        for snapshot_id in ids:
            with self.subTest(snapshot_id=snapshot_id):
                result = self.snapshots.restore_snapshot(
                    self.card,
                    snapshot_id,
                    mode="round_progression",
                )

                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "snapshot_missing")

    def test_restore_snapshot_ignores_traversal_entries_in_metadata(self):
        self._write_card_state()
        (self.card / "memory" / "project.md").write_text("current memory", encoding="utf-8")
        snapshot_id = "round-000001-20260621T123456123456Z-abcdef123456"
        snapshot_dir = self.card / ".agent_runs" / "snapshots" / snapshot_id
        write_json(
            snapshot_dir / "snapshot.json",
            {
                "snapshot_id": snapshot_id,
                "round_id": "round-000001",
                "reason": "test",
                "copied": ["../memory", ".agent_runs/snapshots/evil", "memory"],
            },
        )
        (snapshot_dir / "memory").mkdir()
        (snapshot_dir / "memory" / "project.md").write_text("restored memory", encoding="utf-8")
        (snapshot_dir.parent / "memory").mkdir()
        (snapshot_dir.parent / "memory" / "project.md").write_text("traversal memory", encoding="utf-8")
        (snapshot_dir / ".agent_runs" / "snapshots" / "evil").mkdir(parents=True)
        (snapshot_dir / ".agent_runs" / "snapshots" / "evil" / "project.md").write_text(
            "nested snapshot memory",
            encoding="utf-8",
        )

        result = self.snapshots.restore_snapshot(
            self.card,
            snapshot_id,
            mode="round_progression",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["restored"], ["memory"])
        self.assertEqual((self.card / "memory" / "project.md").read_text(encoding="utf-8"), "restored memory")

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

    def test_restore_snapshot_removes_failed_round_artifacts(self):
        self._write_card_state()
        agent_runs = self.card / ".agent_runs"
        previous_round = agent_runs / "round-000001"
        previous_round.mkdir(parents=True)
        (agent_runs / "current").write_text(str(previous_round.resolve()), encoding="utf-8")
        created = self.snapshots.create_snapshot(
            self.card,
            "round-000002",
            reason="before_round_prepare",
        )
        failed_round = agent_runs / "round-000002"
        write_json(failed_round / "story.input.json", {"stale": True})
        write_json(failed_round / "artifacts" / "story.input.json", {"stale": True})
        write_json(failed_round / "side_threads" / "side_gate" / "state.json", {"status": "running"})

        restored = self.snapshots.restore_snapshot(
            self.card,
            created["snapshot_id"],
            mode="round_progression",
        )

        self.assertTrue(restored["ok"])
        self.assertIn(".agent_runs/round-000002", restored["removed"])
        self.assertFalse(failed_round.exists())
        self.assertTrue(previous_round.exists())
        self.assertEqual((agent_runs / "current").read_text(encoding="utf-8"), str(previous_round.resolve()))

    def test_restore_snapshot_does_not_remove_nonstandard_round_id(self):
        self._write_card_state()
        created = self.snapshots.create_snapshot(
            self.card,
            "round-smoke",
            reason="control_plane_smoke",
        )
        smoke_dir = self.card / ".agent_runs" / "round-smoke"
        write_json(smoke_dir / "story.input.json", {"keep": True})

        restored = self.snapshots.restore_snapshot(
            self.card,
            created["snapshot_id"],
            mode="round_progression",
        )

        self.assertTrue(restored["ok"])
        self.assertEqual(restored["removed"], [])
        self.assertTrue(smoke_dir.exists())

    def test_restore_snapshot_preserves_round_dir_that_was_current_at_snapshot_time(self):
        self._write_card_state()
        agent_runs = self.card / ".agent_runs"
        current_round = agent_runs / "round-000001"
        write_json(current_round / "manifest.json", {"stage": "delivered"})
        (agent_runs / "current").write_text(str(current_round.resolve()), encoding="utf-8")
        created = self.snapshots.create_snapshot(
            self.card,
            "round-000001",
            reason="debug_current_round",
        )

        restored = self.snapshots.restore_snapshot(
            self.card,
            created["snapshot_id"],
            mode="debug",
        )

        self.assertTrue(restored["ok"])
        self.assertEqual(restored["removed"], [])
        self.assertTrue((current_round / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
