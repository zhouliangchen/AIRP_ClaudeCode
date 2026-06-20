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


class AgentLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000001"
        self.run_dir.mkdir(parents=True)
        write_json(
            self.run_dir / "manifest.json",
            {"round_id": "round-000001", "stage": "delivered", "status": []},
        )
        self.subgm_threads = load_module("subgm_threads")
        self.agent_lifecycle = load_module("agent_lifecycle")

    def tearDown(self):
        self.tmp.cleanup()

    def _thread(self, thread_id, status, allowed=None):
        side = self.run_dir / "side_threads" / thread_id
        write_json(
            side / "state.json",
            {
                "thread_id": thread_id,
                "status": status,
                "title": thread_id,
                "boundary": {"location": "library"},
                "objective": "Observe the side scene.",
                "allowed_characters": allowed or ["character:Ada"],
                "forbidden_characters": [],
            },
        )
        (side / "messages.jsonl").write_text("", encoding="utf-8")
        return side

    def test_cleanup_pauses_active_side_threads_and_releases_reservations(self):
        self._thread("side_active", "running", ["character:Ada"])
        self._thread("side_done", "completed", ["character:SuLi"])

        result = self.agent_lifecycle.cleanup_round_agents(
            self.card,
            self.run_dir,
            reason="delivered",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["paused_side_threads"], ["side_active"])
        self.assertEqual(result["already_terminal"], ["side_done"])
        self.assertEqual(self.subgm_threads.active_character_reservations(self.run_dir), {})
        active_state = json.loads(
            (self.run_dir / "side_threads" / "side_active" / "state.json").read_text(
                encoding="utf-8"
            )
        )
        done_state = json.loads(
            (self.run_dir / "side_threads" / "side_done" / "state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(active_state["status"], "paused")
        self.assertIn("resume when the main GM schedules", active_state["next_resume_point"])
        self.assertEqual(done_state["status"], "completed")
        messages = (
            self.run_dir / "side_threads" / "side_active" / "messages.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(messages[-1])["action"], "lifecycle_cleanup")

    def test_cleanup_records_manifest_result(self):
        self._thread("side_blocked", "blocked", ["character:Ada"])

        self.agent_lifecycle.cleanup_round_agents(self.card, self.run_dir, reason="delivered")

        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        cleanup = manifest["agent_lifecycle_cleanup"]
        self.assertEqual(cleanup["status"], "complete")
        self.assertEqual(cleanup["reason"], "delivered")
        self.assertEqual(cleanup["paused_side_threads"], ["side_blocked"])
        self.assertEqual(manifest["stage"], "delivered")

    def test_cleanup_rejects_malformed_side_thread_messages_without_pausing(self):
        side = self._thread("side_bad_log", "running", ["character:Ada"])
        original_messages = '{"ok": true}\nnot-json\n'
        (side / "messages.jsonl").write_text(original_messages, encoding="utf-8")

        result = self.agent_lifecycle.cleanup_round_agents(
            self.card,
            self.run_dir,
            reason="delivered",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["paused_side_threads"], [])
        self.assertEqual(result["failed"][0]["thread_id"], "side_bad_log")
        self.assertIn("messages.jsonl", result["failed"][0]["error"])
        self.assertIn("invalid JSONL", result["failed"][0]["error"])
        state = json.loads((side / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "running")
        self.assertEqual((side / "messages.jsonl").read_text(encoding="utf-8"), original_messages)

    def test_cleanup_preserves_invalid_manifest_and_reports_degraded(self):
        self._thread("side_active", "running", ["character:Ada"])
        original_manifest = '{"round_id": "round-000001", bad-json'
        (self.run_dir / "manifest.json").write_text(original_manifest, encoding="utf-8")

        result = self.agent_lifecycle.cleanup_round_agents(
            self.card,
            self.run_dir,
            reason="delivered",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(any(item.get("scope") == "manifest" for item in result["failed"]))
        self.assertEqual((self.run_dir / "manifest.json").read_text(encoding="utf-8"), original_manifest)
