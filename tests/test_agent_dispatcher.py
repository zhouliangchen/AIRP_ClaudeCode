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


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class AgentDispatcherFoundationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000001"
        self.run_dir.mkdir(parents=True)
        (self.card / ".agent_runs" / "current").write_text(str(self.run_dir.resolve()), encoding="utf-8")
        _write_json(self.run_dir / "manifest.json", {"round_id": "round-000001", "stage": "prepared"})
        _write_json(self.run_dir / "input.json", {"raw_text": "I listen.", "routed_input": {"role_channel": "I listen."}})
        self.dispatcher = _load("agent_dispatcher")
        self.intents = _load("agent_intents")

    def tearDown(self):
        self.tmp.cleanup()

    def test_dispatch_next_blocks_unsupported_intent(self):
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "assets_task", "payload": {"target": "scene"}},
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "unsupported_intent_type")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])

    def test_dispatch_next_uses_oldest_pending_intent(self):
        first = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "assets_task", "payload": {"target": "first"}},
        )["intent"]
        second = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "assets_task", "payload": {"target": "second"}},
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertEqual(result["intent_id"], first["id"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["id"] for item in pending], [second["id"]])

    def test_dispatch_next_blocks_stalled_runtime_when_no_pending_intents(self):
        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "stalled")
        self.assertEqual(result["reason"], "dispatcher_stalled")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "dispatcher_stalled")

    def test_dispatch_next_reports_delivered_without_blocking_when_manifest_delivered(self):
        _write_json(self.run_dir / "manifest.json", {"round_id": "round-000001", "stage": "delivered"})

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(result["reason"], "")
        self.assertEqual(self.intents.list_intents(self.run_dir, "blocked"), [])


if __name__ == "__main__":
    unittest.main()
