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

    def test_compute_actor_context_version_changes_when_memory_changes(self):
        subjective = self.card / "characters" / "Ada"
        objective = self.card / "memory" / "characters" / "Ada"
        subjective.mkdir(parents=True)
        objective.mkdir(parents=True)
        (subjective / "profile.md").write_text("我是 Ada。", encoding="utf-8")
        (subjective / "long_term_memories.md").write_text("Ada trusts the player.", encoding="utf-8")
        (subjective / "key_memories.json").write_text(
            '{"memories":[{"tag":"lamp","summary":"Ada lent a lamp","detail":"secret detail"}]}',
            encoding="utf-8",
        )
        (subjective / "short_term_memories.md").write_text("Ada is near the door.", encoding="utf-8")
        (objective / "profile.md").write_text("Ada is an archivist.", encoding="utf-8")
        (objective / "background.md").write_text("Ada works in the old archive.", encoding="utf-8")
        legacy = self.card / "memory" / "characters" / "Ada"
        (legacy / "profile.json").write_text('{"legacy": true}', encoding="utf-8")
        (legacy / "recent.md").write_text("legacy recent should not affect version", encoding="utf-8")
        (legacy / "goals.json").write_text('{"goals":{"active":["legacy"]}}', encoding="utf-8")
        packet = {
            "agent_id": "character:Ada",
            "actor": {"name": "Ada"},
            "world": {"visible_events": []},
            "prompt": "You see the corridor.",
            "visibility_basis": {"mode": "direct", "summary": "Ada is present."},
        }

        first = self.agent_lifecycle.compute_actor_context_version(self.card, "character:Ada", packet)
        (subjective / "long_term_memories.md").write_text("Ada distrusts the player.", encoding="utf-8")
        second = self.agent_lifecycle.compute_actor_context_version(self.card, "character:Ada", packet)

        self.assertNotEqual(first["hash"], second["hash"])
        self.assertIn("characters/Ada/profile.md", first["source_paths"])
        self.assertIn("characters/Ada/long_term_memories.md", first["source_paths"])
        self.assertIn("characters/Ada/key_memories.json", first["source_paths"])
        self.assertIn("characters/Ada/short_term_memories.md", first["source_paths"])
        self.assertIn("memory/characters/Ada/profile.md", first["source_paths"])
        self.assertIn("memory/characters/Ada/background.md", first["source_paths"])
        self.assertNotIn("memory/characters/Ada/profile.json", first["source_paths"])
        self.assertNotIn("memory/characters/Ada/recent.md", first["source_paths"])
        self.assertNotIn("memory/characters/Ada/goals.json", first["source_paths"])

    def test_compute_actor_context_version_changes_when_packet_memory_changes(self):
        packet = {
            "actor_id": "character:Ada",
            "self_knowledge": {"name": "Ada"},
            "memory": {"long_term": ["Ada trusts the player."], "goals": []},
            "gm_prompt": "You see the corridor.",
            "visible_events": [],
            "gm_visibility_basis": {"mode": "direct", "summary": "Ada is present."},
        }

        first = self.agent_lifecycle.compute_actor_context_version(self.card, "character:Ada", packet)
        packet["memory"] = {"long_term": ["Ada distrusts the player."], "goals": []}
        second = self.agent_lifecycle.compute_actor_context_version(self.card, "character:Ada", packet)

        self.assertNotEqual(first["hash"], second["hash"])

    def test_compute_actor_context_version_normalizes_json_source_key_order(self):
        memory = self.card / "characters" / "Ada"
        memory.mkdir(parents=True)
        packet = {
            "actor_id": "character:Ada",
            "self_knowledge": {"name": "Ada"},
            "memory": {"long_term": [], "goals": []},
            "gm_prompt": "You see the corridor.",
            "visible_events": [],
        }
        goals_path = memory / "key_memories.json"
        goals_path.write_text(
            '{"memories":[{"tag":"key","summary":"Protect the key","detail":"exact shelf"}]}',
            encoding="utf-8",
        )

        first = self.agent_lifecycle.compute_actor_context_version(self.card, "character:Ada", packet)
        goals_path.write_text(
            '{\n  "memories": [\n    {\n      "summary": "Protect the key",\n      "detail": "exact shelf",\n      "tag": "key"\n    }\n  ]\n}',
            encoding="utf-8",
        )
        second = self.agent_lifecycle.compute_actor_context_version(self.card, "character:Ada", packet)

        self.assertEqual(first["hash"], second["hash"])

    def test_compute_actor_context_version_includes_nested_context_version_fields(self):
        packet = {
            "actor_id": "character:Ada",
            "self_knowledge": {"name": "Ada"},
            "memory": {"long_term": [], "goals": []},
            "gm_prompt": "You see the corridor.",
            "world": {
                "visible_events": [
                    {
                        "type": "scene",
                        "content": "A door opens.",
                        "metadata": {"context_version": "visible-event-v1"},
                    }
                ]
            },
            "context_version": {"algorithm": "sha256", "hash": "sha256:generated-top-level"},
        }

        first = self.agent_lifecycle.compute_actor_context_version(self.card, "character:Ada", packet)
        packet["world"]["visible_events"][0]["metadata"]["context_version"] = "visible-event-v2"
        second = self.agent_lifecycle.compute_actor_context_version(self.card, "character:Ada", packet)

        self.assertNotEqual(first["hash"], second["hash"])
