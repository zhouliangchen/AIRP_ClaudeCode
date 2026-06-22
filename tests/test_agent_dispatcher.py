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
        if not hasattr(self.dispatcher, "agent_snapshots"):
            self.dispatcher.agent_snapshots = _load("agent_snapshots")
        self._restore_after_test(self.dispatcher.input_analysis_apply, "apply_current_run")
        self._restore_after_test(self.dispatcher.rp_generate_cli, "_dispatch_agent_payload")
        self._restore_after_test(self.dispatcher.rp_generate_cli, "_run_interactive_agent_loop")
        self._restore_after_test(self.dispatcher.rp_generate_cli, "_run_delivery")
        self._restore_after_test(self.dispatcher.agent_outputs, "build_story_input")
        self._restore_after_test(self.dispatcher, "_dispatch_agent_payload")
        if hasattr(self.dispatcher, "agent_snapshots"):
            self._restore_after_test(self.dispatcher.agent_snapshots, "restore_snapshot")

    def tearDown(self):
        self.tmp.cleanup()

    def _restore_after_test(self, owner, name):
        original = getattr(owner, name)
        self.addCleanup(setattr, owner, name, original)

    def _install_dispatcher_dependencies(self):
        if not hasattr(self.dispatcher, "agent_outputs"):
            self.dispatcher.agent_outputs = _load("agent_outputs")
        if not hasattr(self.dispatcher, "rp_generate_cli"):
            self.dispatcher.rp_generate_cli = _load("rp_generate_cli")

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

    def test_artifact_path_rejects_absolute_relative_path(self):
        absolute = self.run_dir / "escape.json"

        with self.assertRaises(self.dispatcher.AgentDispatcherError):
            self.dispatcher.artifact_path(self.run_dir, str(absolute))

    def test_artifact_path_rejects_parent_escape(self):
        with self.assertRaises(self.dispatcher.AgentDispatcherError):
            self.dispatcher.artifact_path(self.run_dir, "../escape.json")

    def test_dispatch_next_preserves_existing_blocked_manifest_reason(self):
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "assets_task", "payload": {"target": "scene"}},
        )["intent"]

        first_result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)
        manifest_after_first = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        first_history = list(manifest_after_first.get("stage_history", []))
        second_result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)
        manifest_after_second = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertFalse(first_result["ok"])
        self.assertEqual(first_result["reason"], "unsupported_intent_type")
        self.assertFalse(second_result["ok"])
        self.assertEqual(second_result["status"], "blocked")
        self.assertEqual(second_result["intent_id"], "")
        self.assertEqual(second_result["reason"], "unsupported_intent_type")
        self.assertEqual(manifest_after_second["dispatcher"]["reason"], "unsupported_intent_type")
        self.assertEqual(manifest_after_second.get("stage_history", []), first_history)
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])

    def test_analyze_input_completes_and_creates_run_gm_turn(self):
        _write_json(self.run_dir / "input_analysis.output.json", {"analysis_mode": "fixture"})
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "main_agent",
                "type": "analyze_input",
                "payload": {"input_analysis_request_path": "input_analysis.request.md"},
            },
        )["intent"]
        apply_calls = []

        def fake_apply(card_folder, root_dir):
            apply_calls.append((Path(card_folder), Path(root_dir)))
            return {"ok": True, "analysis": {"analysis_mode": "fixture"}}

        self.dispatcher.input_analysis_apply.apply_current_run = fake_apply

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["artifacts"], ["artifacts/input_analysis.output.json"])
        self.assertTrue((self.run_dir / "artifacts" / "input_analysis.output.json").exists())
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_gm_turn"])
        self.assertEqual(result["created_intents"], [pending[0]["id"]])
        self.assertEqual(pending[0]["requested_by"], "input_analyst")
        self.assertEqual(pending[0]["payload"], {"reason": "input_analysis_applied"})
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(len(apply_calls), 1)
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        applied = [item for item in messages if item.get("type") == "analysis_applied"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(result["created_messages"], [applied[0]["id"]])
        self.assertEqual(applied[0]["to"], ["gm", "main_agent"])
        self.assertEqual(applied[0]["payload"]["applied"]["analysis"]["analysis_mode"], "fixture")

    def test_analyze_input_blocks_with_failure_when_apply_raises_after_accept(self):
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "main_agent",
                "type": "analyze_input",
                "payload": {"input_analysis_request_path": "input_analysis.request.md"},
            },
        )["intent"]

        def fail_apply(_card_folder, _root_dir):
            raise RuntimeError("fixture apply exploded")

        self.dispatcher.input_analysis_apply.apply_current_run = fail_apply

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "analyze_input_failed")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertIn("fixture apply exploded", blocked[0]["result"]["outputs"]["error"])
        self.assertEqual(self.intents.list_intents(self.run_dir, "completed"), [])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "analyze_input_failed")
        self.assertIn("fixture apply exploded", manifest["dispatcher"]["detail"]["error"])
        self.assertEqual(self.dispatcher.agent_messages.read_messages(self.run_dir), [])

    def test_analyze_input_reuses_apply_path_analysis_applied_message(self):
        _write_json(self.run_dir / "input_analysis.output.json", {"analysis_mode": "fixture"})
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "main_agent",
                "type": "analyze_input",
                "payload": {"input_analysis_request_path": "input_analysis.request.md"},
            },
        )["intent"]

        def fake_apply(card_folder, root_dir):
            message_result = self.dispatcher.agent_messages.append_message(
                self.run_dir,
                {
                    "from": "input_analyst",
                    "to": ["gm"],
                    "type": "analysis_applied",
                    "visibility": "gm_only",
                    "payload": {
                        "input_path": "input.json",
                        "analysis_path": "input_analysis.output.json",
                        "routed_characters": [],
                    },
                },
            )
            self.assertTrue(message_result["ok"])
            return {"ok": True, "analysis": {"analysis_mode": "fixture"}}

        self.dispatcher.input_analysis_apply.apply_current_run = fake_apply

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        applied = [item for item in messages if item.get("type") == "analysis_applied"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["to"], ["gm"])
        self.assertEqual(result["created_messages"], [applied[0]["id"]])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_gm_turn"])

    def test_analyze_input_ignores_stale_unrelated_analysis_applied_message(self):
        _write_json(self.run_dir / "input_analysis.output.json", {"analysis_mode": "fixture"})
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "main_agent",
                "type": "analyze_input",
                "payload": {"input_analysis_request_path": "input_analysis.request.md"},
            },
        )["intent"]
        stale = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "input_analyst",
                "to": ["gm"],
                "type": "analysis_applied",
                "visibility": "gm_only",
                "payload": {"unrelated": True},
            },
        )
        self.assertTrue(stale["ok"])

        def fake_apply(_card_folder, _root_dir):
            return {"ok": True, "analysis": {"analysis_mode": "fixture"}}

        self.dispatcher.input_analysis_apply.apply_current_run = fake_apply

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        applied = [item for item in messages if item.get("type") == "analysis_applied"]
        self.assertEqual(len(applied), 2)
        self.assertEqual(applied[0]["payload"], {"unrelated": True})
        self.assertEqual(applied[1]["to"], ["gm", "main_agent"])
        self.assertEqual(applied[1]["payload"]["applied"]["analysis"]["analysis_mode"], "fixture")
        self.assertEqual(result["created_messages"], [applied[1]["id"]])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_gm_turn"])

    def test_run_gm_turn_writes_artifacts_and_creates_compose_story(self):
        self._install_dispatcher_dependencies()
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "input_analyst", "type": "run_gm_turn", "payload": {}},
        )["intent"]
        loop_calls = []

        def fake_loop(run_dir, manifest, root, run_claude, repair_context=None):
            loop_calls.append((Path(run_dir), manifest, Path(root), run_claude, repair_context))
            _write_json(run_dir / "gm.output.json", {"agent": "gm_loop", "outputs": []})
            _write_json(run_dir / "actor.outputs.json", {"actor_outputs": {}})
            _write_json(run_dir / "interaction.trace.json", {"schema_version": 2, "status": "decision_point", "events": []})
            return {"gm_steps": 1, "called_actors": []}

        self.dispatcher.rp_generate_cli._run_interactive_agent_loop = fake_loop

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertTrue((self.run_dir / "artifacts" / "gm.output.json").exists())
        self.assertTrue((self.run_dir / "artifacts" / "actor.outputs.json").exists())
        self.assertEqual(
            result["artifacts"],
            ["artifacts/gm.output.json", "artifacts/actor.outputs.json"],
        )
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["compose_story"])
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(pending[0]["payload"], {"loop_result": {"gm_steps": 1, "called_actors": []}})
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])
        self.assertEqual(len(loop_calls), 1)
        self.assertEqual(loop_calls[0][0], self.run_dir)
        self.assertEqual(loop_calls[0][2], ROOT)
        self.assertIsNone(loop_calls[0][4])

    def test_run_gm_turn_rejects_stale_authoritative_artifacts_without_current_root_outputs(self):
        self._install_dispatcher_dependencies()
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "input_analyst", "type": "run_gm_turn", "payload": {}},
        )["intent"]
        self.dispatcher.write_artifact(self.run_dir, "gm.output.json", {"agent": "stale_gm", "outputs": []})
        self.dispatcher.write_artifact(self.run_dir, "actor.outputs.json", {"stale_actor": []})

        def fake_loop(_run_dir, _manifest, _root, _run_claude, repair_context=None):
            return {"gm_steps": 1, "called_actors": []}

        self.dispatcher.rp_generate_cli._run_interactive_agent_loop = fake_loop

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "run_gm_turn_failed")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertIn("gm.output.json", blocked[0]["result"]["outputs"]["error"])
        self.assertEqual(self.intents.list_intents(self.run_dir, "completed"), [])

    def test_run_gm_turn_rejects_non_object_current_root_artifact(self):
        self._install_dispatcher_dependencies()
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "input_analyst", "type": "run_gm_turn", "payload": {}},
        )["intent"]

        def fake_loop(run_dir, _manifest, _root, _run_claude, repair_context=None):
            _write_json(run_dir / "gm.output.json", [])
            _write_json(run_dir / "actor.outputs.json", {"actor_outputs": {}})
            return {"gm_steps": 1, "called_actors": []}

        self.dispatcher.rp_generate_cli._run_interactive_agent_loop = fake_loop

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "run_gm_turn_failed")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertIn("gm.output.json", blocked[0]["result"]["outputs"]["error"])
        self.assertIn("JSON object", blocked[0]["result"]["outputs"]["error"])

    def test_run_gm_turn_blocks_when_loop_raises_after_accept(self):
        self._install_dispatcher_dependencies()
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "input_analyst", "type": "run_gm_turn", "payload": {}},
        )["intent"]

        def fake_loop(_run_dir, _manifest, _root, _run_claude, repair_context=None):
            raise RuntimeError("fixture gm loop exploded")

        self.dispatcher.rp_generate_cli._run_interactive_agent_loop = fake_loop

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "run_gm_turn_failed")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertIn("fixture gm loop exploded", blocked[0]["result"]["outputs"]["error"])
        self.assertEqual(self.intents.list_intents(self.run_dir, "accepted"), [])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "run_gm_turn_failed")

    def test_compose_story_writes_story_output_artifact_and_creates_review_intent(self):
        self._install_dispatcher_dependencies()
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "compose_story", "payload": {"reason": "loop_complete"}},
        )["intent"]
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["prompts"] = {"story": "prompts/story.custom.md"}
        _write_json(self.run_dir / "manifest.json", manifest)
        (self.run_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "prompts" / "story.custom.md").write_text("story prompt", encoding="utf-8")
        build_calls = []
        dispatch_calls = []

        def fake_build_story_input(run_dir):
            payload = {"round_id": "round-000001", "fixture": "story input"}
            build_calls.append(Path(run_dir))
            self.dispatcher.write_artifact(run_dir, "story.input.json", payload)
            return payload

        def fake_dispatch(agent_key, prompt, root_dir, run_claude, extra_context=None):
            dispatch_calls.append((agent_key, prompt, Path(root_dir), extra_context))
            self.assertEqual(agent_key, "story")
            return {"content": "<content>Story text.</content>", "metadata": {"round_id": "round-000001"}}

        original_build_story_input = self.dispatcher.agent_outputs.build_story_input
        original_dispatch = self.dispatcher.rp_generate_cli._dispatch_agent_payload
        try:
            self.dispatcher.agent_outputs.build_story_input = fake_build_story_input
            self.dispatcher.rp_generate_cli._dispatch_agent_payload = fake_dispatch

            result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        finally:
            self.dispatcher.agent_outputs.build_story_input = original_build_story_input
            self.dispatcher.rp_generate_cli._dispatch_agent_payload = original_dispatch

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["artifacts"], ["artifacts/story.input.json", "artifacts/story.output.json"])
        story_output = json.loads((self.run_dir / "artifacts" / "story.output.json").read_text(encoding="utf-8"))
        self.assertEqual(
            story_output["content"],
            "<content>Story text.</content>\n<character_dialogues>[]</character_dialogues>",
        )
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["review_critic"])
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(len(build_calls), 1)
        self.assertEqual(dispatch_calls[0][1], "story prompt")
        self.assertEqual(dispatch_calls[0][3], {"story_input": {"round_id": "round-000001", "fixture": "story input"}})

    def test_compose_story_writes_normalized_story_output_artifact(self):
        self._install_dispatcher_dependencies()
        self.intents.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "compose_story", "payload": {"reason": "loop_complete"}},
        )
        (self.run_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "prompts" / "story.prompt.md").write_text("story prompt", encoding="utf-8")

        def fake_build_story_input(run_dir):
            payload = {"round_id": "round-000001", "loop_outputs": {"actors": {}}}
            self.dispatcher.write_artifact(run_dir, "story.input.json", payload)
            return payload

        def fake_dispatch(_agent_key, _run_dir, _root_dir, _run_claude, extra_context=None):
            return {
                "content": (
                    "<content>Story text.</content>"
                    "<TOKENS>in: NNNN\nout: NNNN\ntotal: NNNN</TOKENS>"
                    "<summary>Summary.</summary><options>Wait</options>"
                ),
                "character_dialogues": "not a list",
                "tokens": {"in": "NNNN"},
                "token_usage": {"total": "NNNN"},
            }

        original_build_story_input = self.dispatcher.agent_outputs.build_story_input
        original_dispatch = self.dispatcher._dispatch_agent_payload
        try:
            self.dispatcher.agent_outputs.build_story_input = fake_build_story_input
            self.dispatcher._dispatch_agent_payload = fake_dispatch

            result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        finally:
            self.dispatcher.agent_outputs.build_story_input = original_build_story_input
            self.dispatcher._dispatch_agent_payload = original_dispatch

        self.assertTrue(result["ok"])
        story_output = json.loads((self.run_dir / "artifacts" / "story.output.json").read_text(encoding="utf-8"))
        self.assertNotIn("tokens", story_output)
        self.assertNotIn("token_usage", story_output)
        self.assertEqual(story_output["character_dialogues"], [])
        self.assertNotIn("<tokens>", story_output["content"].lower())
        self.assertNotIn("NNNN", story_output["content"])
        self.assertIn("<character_dialogues>[]</character_dialogues>", story_output["content"])

    def test_review_critic_pass_creates_deliver_round_intent(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "story.input.json", {"round_id": "round-000001"})
        _write_json(
            self.run_dir / "artifacts" / "story.output.json",
            {"content": "<content>Story text.</content>", "metadata": {"round_id": "round-000001"}},
        )
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "review_critic", "payload": {"reason": "story_ready"}},
        )["intent"]
        (self.run_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "prompts" / "critic.prompt.md").write_text("critic prompt", encoding="utf-8")
        dispatch_calls = []

        def fake_dispatch(agent_key, prompt, root_dir, run_claude, extra_context=None):
            dispatch_calls.append((agent_key, prompt, Path(root_dir), extra_context))
            self.assertEqual(agent_key, "critic")
            return {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": ""}

        self.dispatcher.rp_generate_cli._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["artifacts"], ["artifacts/critic.report.json"])
        critic_report = json.loads((self.run_dir / "artifacts" / "critic.report.json").read_text(encoding="utf-8"))
        self.assertEqual(critic_report["decision"], "pass")
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["deliver_round"])
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(dispatch_calls[0][1], "critic prompt")
        self.assertEqual(
            dispatch_calls[0][3],
            {
                "story_input": {"round_id": "round-000001"},
                "story_output": {"content": "<content>Story text.</content>", "metadata": {"round_id": "round-000001"}},
            },
        )

    def test_review_critic_normalizes_stale_token_failure_to_delivery(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "story.input.json", {"round_id": "round-000001"})
        _write_json(
            self.run_dir / "artifacts" / "story.output.json",
            {
                "content": "<content>Clean story without token placeholders.</content><summary>Clean.</summary><options>Wait</options>",
                "character_dialogues": [],
                "metadata": {},
            },
        )
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "review_critic", "payload": {"reason": "story_ready"}},
        )["intent"]
        (self.run_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "prompts" / "critic.prompt.md").write_text("critic prompt", encoding="utf-8")

        def fake_dispatch(_agent_key, _run_dir, _root_dir, _run_claude, extra_context=None):
            return {
                "decision": "revise",
                "hard_failures": ["story.output.json contains placeholder <tokens> values ('NNNN')"],
                "soft_issues": ["Prose could use sharper sensory detail."],
                "repair_instruction": "Remove fake token placeholders.",
                "system_iteration_suggestion": "",
            }

        original_dispatch = self.dispatcher._dispatch_agent_payload
        try:
            self.dispatcher._dispatch_agent_payload = fake_dispatch

            result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")
        finally:
            self.dispatcher._dispatch_agent_payload = original_dispatch

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent_id"], created["id"])
        critic_report = json.loads((self.run_dir / "artifacts" / "critic.report.json").read_text(encoding="utf-8"))
        self.assertEqual(critic_report["decision"], "pass")
        self.assertEqual(critic_report["hard_failures"], [])
        self.assertEqual(critic_report["soft_issues"], ["Prose could use sharper sensory detail."])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["deliver_round"])

    def test_review_critic_revise_records_repair_request_metadata_and_message(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "story.input.json", {"round_id": "round-000001"})
        _write_json(
            self.run_dir / "artifacts" / "story.output.json",
            {"content": "<content>Story text.</content>", "metadata": {"round_id": "round-000001"}},
        )
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "review_critic", "payload": {"reason": "story_ready"}},
        )["intent"]
        (self.run_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "prompts" / "critic.prompt.md").write_text("critic prompt", encoding="utf-8")

        def fake_dispatch(_agent_key, _prompt, _root_dir, _run_claude, extra_context=None):
            self.assertEqual(extra_context["story_input"], {"round_id": "round-000001"})
            return {
                "decision": "revise",
                "hard_failures": [],
                "soft_issues": ["weak stop point"],
                "repair_instruction": "Rewrite the stop point around the player decision.",
                "repair_routing": {"stage": "delivery_gate"},
            }

        self.dispatcher.rp_generate_cli._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["repair_request"])
        repair_payload = pending[0]["payload"]
        self.assertEqual(repair_payload["critic_report_path"], "artifacts/critic.report.json")
        self.assertEqual(repair_payload["decision"], "revise")
        self.assertEqual(repair_payload["repair_instruction"], "Rewrite the stop point around the player decision.")
        self.assertEqual(
            repair_payload["repair_routing"],
            {
                "stage": "delivery_gate",
                "target_agents": ["story"],
                "rollback": "story_only",
                "can_auto_repair": True,
                "risk": "low",
            },
        )
        self.assertTrue(repair_payload["repair_fingerprint"])
        history_path = self.run_dir / "repair_history.jsonl"
        self.assertTrue(history_path.exists())
        history = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["repair_instruction"], "Rewrite the stop point around the player decision.")
        self.assertEqual(history[0]["repair_routing"], repair_payload["repair_routing"])
        messages = self.dispatcher.agent_messages.read_messages(self.run_dir)
        repair_messages = [item for item in messages if item.get("type") == "repair_request"]
        self.assertEqual(len(repair_messages), 1)
        self.assertEqual(repair_messages[0]["payload"]["repair_fingerprint"], repair_payload["repair_fingerprint"])

    def test_repair_request_story_only_creates_compose_story(self):
        _write_json(
            self.run_dir / "artifacts" / "critic.report.json",
            {
                "decision": "revise",
                "repair_instruction": "Rewrite the ending around a concrete player choice.",
                "repair_routing": {
                    "stage": "story_composition",
                    "rollback": "story_only",
                    "can_auto_repair": True,
                    "risk": "low",
                },
            },
        )
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "critic",
                "type": "repair_request",
                "payload": {"critic_report_path": "artifacts/critic.report.json"},
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["compose_story"])
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(pending[0]["payload"]["critic_report_path"], "artifacts/critic.report.json")
        self.assertEqual(
            pending[0]["payload"]["repair_routing"],
            {
                "stage": "story_composition",
                "target_agents": ["story"],
                "rollback": "story_only",
                "can_auto_repair": True,
                "risk": "low",
            },
        )
        self.assertEqual(
            pending[0]["payload"]["repair_instruction"],
            "Rewrite the ending around a concrete player choice.",
        )
        self.assertEqual([item["id"] for item in self.intents.list_intents(self.run_dir, "completed")], [created["id"]])

    def test_repair_request_round_progression_creates_rollback_request(self):
        _write_json(
            self.run_dir / "artifacts" / "critic.report.json",
            {
                "decision": "revise",
                "repair_instruction": "Re-run the GM loop from the before-round snapshot.",
                "repair_routing": {
                    "stage": "gm_loop",
                    "rollback": "round_progression",
                    "can_auto_repair": True,
                    "risk": "medium",
                },
                "snapshot_id": "round-000001-20260621T123456123456Z-abcdef123456",
            },
        )
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "critic",
                "type": "repair_request",
                "payload": {"critic_report_path": "artifacts/critic.report.json"},
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["rollback_request"])
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(
            pending[0]["payload"],
            {
                "mode": "round_progression",
                "reason": "critic_repair",
                "critic_report_path": "artifacts/critic.report.json",
                "snapshot_id": "round-000001-20260621T123456123456Z-abcdef123456",
            },
        )

    def test_rollback_request_blocks_when_snapshot_missing(self):
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "repair",
                "type": "rollback_request",
                "payload": {
                    "snapshot_id": "round-000001-20260621T123456123456Z-abcdef123456",
                    "mode": "round_progression",
                },
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "rollback_failed")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        self.assertEqual(blocked[0]["result"]["outputs"]["restore"]["reason"], "snapshot_missing")

    def test_rollback_request_story_only_success_creates_compose_story(self):
        restore_calls = []

        def fake_restore(card_folder, snapshot_id, *, mode):
            restore_calls.append((Path(card_folder), snapshot_id, mode))
            return {"ok": True, "snapshot_id": snapshot_id, "mode": mode, "restored": ["memory"], "removed": []}

        self.dispatcher.agent_snapshots.restore_snapshot = fake_restore
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "repair",
                "type": "rollback_request",
                "payload": {"snapshot_id": "snapshot_fixture", "mode": "story_only"},
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["compose_story"])
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(pending[0]["payload"]["rollback"]["mode"], "story_only")
        self.assertEqual(restore_calls, [(self.card, "snapshot_fixture", "story_only")])

    def test_rollback_request_round_progression_success_creates_run_gm_turn(self):
        restore_calls = []

        def fake_restore(card_folder, snapshot_id, *, mode):
            restore_calls.append((Path(card_folder), snapshot_id, mode))
            return {"ok": True, "snapshot_id": snapshot_id, "mode": mode, "restored": [".agent_runs/current"], "removed": []}

        self.dispatcher.agent_snapshots.restore_snapshot = fake_restore
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "repair",
                "type": "rollback_request",
                "payload": {"snapshot_id": "snapshot_fixture", "mode": "round_progression"},
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_gm_turn"])
        self.assertEqual(pending[0]["policy"], {"source_intent_id": created["id"]})
        self.assertEqual(pending[0]["payload"]["rollback"]["mode"], "round_progression")
        self.assertEqual(restore_calls, [(self.card, "snapshot_fixture", "round_progression")])

    def test_deliver_round_marks_delivered_when_delivery_command_passes(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "critic.report.json", {"decision": "pass"})
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "critic", "type": "deliver_round", "payload": {"reason": "critic_passed"}},
        )["intent"]
        delivery_calls = []

        def fake_run_delivery(card_folder, root_dir, run_command):
            delivery_calls.append((Path(card_folder), Path(root_dir), run_command))
            return {"ok": True, "result": {"ok": True, "mode": "agent_run"}}

        self.dispatcher.rp_generate_cli._run_delivery = fake_run_delivery

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_command=lambda *args, **kwargs: None)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(result["intent_id"], created["id"])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "delivered")
        self.assertEqual(manifest["dispatcher"]["status"], "delivered")
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])
        self.assertEqual(len(delivery_calls), 1)

    def test_deliver_round_blocks_when_delivery_command_fails(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "critic.report.json", {"decision": "pass"})
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "critic", "type": "deliver_round", "payload": {"reason": "critic_passed"}},
        )["intent"]

        def fake_run_delivery(_card_folder, _root_dir, _run_command):
            return {"ok": False, "returncode": 1, "result": {"ok": False, "reason": "fixture_delivery_error"}}

        self.dispatcher.rp_generate_cli._run_delivery = fake_run_delivery

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_command=lambda *args, **kwargs: None)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "delivery_failed")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "delivery_failed")

    def test_deliver_round_blocks_when_delivery_requests_retry_with_outer_ok(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "critic.report.json", {"decision": "pass"})
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "critic", "type": "deliver_round", "payload": {"reason": "critic_passed"}},
        )["intent"]

        def fake_run_delivery(_card_folder, _root_dir, _run_command):
            return {"ok": True, "result": {"action": "retry", "ok": True}}

        self.dispatcher.rp_generate_cli._run_delivery = fake_run_delivery

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_command=lambda *args, **kwargs: None)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(result["reason"], "delivery_failed")
        self.assertEqual(self.intents.list_intents(self.run_dir, "completed"), [])
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual([item["id"] for item in blocked], [created["id"]])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertEqual(manifest["dispatcher"]["reason"], "delivery_failed")


if __name__ == "__main__":
    unittest.main()
