import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_round_state():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(
        "round_state",
        ROOT / "skills" / "round_state.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RoundStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.styles = self.base / "styles"
        self.styles.mkdir()
        self.run_dir = self.base / ".agent_runs" / "round-000001"
        self.run_dir.mkdir(parents=True)
        self.round_state = _load_round_state()

    def tearDown(self):
        self.tmp.cleanup()

    def _read_progress(self):
        return json.loads((self.styles / "progress.json").read_text(encoding="utf-8"))

    def _write_manifest(self, data):
        (self.run_dir / "manifest.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_manifest(self):
        return json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))

    def test_actor_dispatch_writes_schema_v2_progress_record(self):
        self.round_state.write_progress_state(
            self.styles,
            "gm_loop.actor_dispatch",
            run_id="round-000001",
            detail={"agent": "character:Ada", "actor_call_id": "call-1"},
        )

        progress = self._read_progress()

        self.assertEqual(progress["schema_version"], 2)
        self.assertEqual(progress["state"], "gm_loop.actor_dispatch")
        self.assertEqual(progress["phase"], "gm_loop")
        self.assertEqual(progress["stage"], "gm_loop.actor_dispatch")
        self.assertEqual(progress["label"], "角色行动中")
        self.assertEqual(progress["percent"], 48)
        self.assertFalse(progress["terminal"])
        self.assertEqual(progress["detail"]["agent"], "character:Ada")

    def test_unknown_state_is_rejected(self):
        with self.assertRaisesRegex(self.round_state.RoundStateError, "unknown progress state"):
            self.round_state.write_progress_state(self.styles, "missing.state")

    def test_removed_story_quality_gate_state_is_rejected(self):
        removed_state = "story." + "pre" + "flight_repair"

        with self.assertRaisesRegex(self.round_state.RoundStateError, "unknown progress state"):
            self.round_state.write_progress_state(self.styles, removed_state)

    def test_complete_requires_delivered_manifest_when_run_dir_is_passed(self):
        self._write_manifest({"stage": "story_ready"})

        with self.assertRaisesRegex(self.round_state.RoundStateError, "complete requires delivered"):
            self.round_state.write_progress_state(self.styles, "complete", run_dir=self.run_dir)

        self._write_manifest({"stage": "delivered"})
        self.round_state.write_progress_state(self.styles, "complete", run_dir=self.run_dir)

        progress = self._read_progress()
        self.assertEqual(progress["state"], "complete")
        self.assertTrue(progress["terminal"])

    def test_repeated_state_writes_refresh_detail(self):
        self.round_state.write_progress_state(
            self.styles,
            "delivery.retrying",
            detail={"attempt": 1},
        )
        self.round_state.write_progress_state(
            self.styles,
            "delivery.retrying",
            detail={"attempt": 2, "reason": "critic"},
        )

        progress = self._read_progress()

        self.assertEqual(progress["state"], "delivery.retrying")
        self.assertEqual(progress["detail"], {"attempt": 2, "reason": "critic"})

    def test_manifest_sync_records_progress_state_and_status_entry(self):
        self._write_manifest({"stage": "awaiting_input_analysis", "status": []})

        self.round_state.write_progress_state(
            self.styles,
            "input_analysis.applied",
            run_dir=self.run_dir,
            manifest_message="Input analysis applied.",
        )

        manifest = self._read_manifest()

        self.assertEqual(manifest["stage"], "awaiting_input_analysis")
        self.assertEqual(manifest["progress_state"], "input_analysis.applied")
        self.assertEqual(manifest["status"][-1]["stage"], "input_analysis.applied")
        self.assertEqual(manifest["status"][-1]["message"], "Input analysis applied.")


if __name__ == "__main__":
    unittest.main()
