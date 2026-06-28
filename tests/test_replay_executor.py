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


class ReplayExecutorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.root = Path(self.tmp.name)
        self.card.mkdir()
        self.run_dir = self.card / ".agent_runs" / "round-000001"
        self.run_dir.mkdir(parents=True)
        (self.card / ".agent_runs" / "current").write_text("round-000001", encoding="utf-8")
        (self.card / "chat_log.json").write_text("[]", encoding="utf-8")
        (self.card / ".card_data.json").write_text('{"name":"Test Card"}', encoding="utf-8")

        self.agent_snapshots = _load("agent_snapshots")
        self.replay_capabilities = _load("replay_capabilities")
        self.replay_executor = _load("replay_executor")
        backup = self.agent_snapshots.create_snapshot(
            self.card,
            "round-000001",
            reason="before_round_prepare",
        )
        self.backup_id = backup["backup_id"]
        self.plan = {
            "schema_version": 1,
            "scope": "multi_round",
            "plan_id": "replay-001",
            "backup_id": self.backup_id,
            "affected_inputs": [
                {
                    "round_id": "round-000001",
                    "input_id": "input-1",
                    "role_text": "first role",
                    "user_instruction_text": "",
                },
                {
                    "round_id": "round-000002",
                    "input_id": "input-2",
                    "role_text": "second role",
                    "user_instruction_text": "second guide",
                },
            ],
        }
        self.replay_capabilities.materialize_replay_plan(self.run_dir, self.plan)

    def tearDown(self):
        self.tmp.cleanup()

    def _read_status(self):
        path = self.card / ".replay" / "sessions" / "replay-001" / "status.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_active(self):
        path = self.card / ".replay" / "active.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _session_dir(self):
        return self.card / ".replay" / "sessions" / "replay-001"

    def _write_plan(self, plan):
        path = self._session_dir() / "plan.json"
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_execute_session_restores_backup_and_runs_rounds_in_order(self):
        calls = []
        outlines = []

        def fake_prepare(card, root, outline):
            outlines.append(outline)
            calls.append(
                (
                    "prepare",
                    outline["input_id"],
                    outline["next_input"].get("input_id", ""),
                )
            )
            return {"ok": True, "agent_run": str(Path(card) / ".agent_runs" / outline["round_id"])}

        def fake_generate(card, root):
            pending = json.loads((Path(card) / ".pending_user_turn.json").read_text(encoding="utf-8"))
            calls.append(("generate", pending["id"], ""))
            chat_path = Path(card) / "chat_log.json"
            chat = json.loads(chat_path.read_text(encoding="utf-8"))
            chat.append({"user": pending["id"], "ai": "new " + pending["id"]})
            chat_path.write_text(json.dumps(chat, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"ok": True, "post_round_memory": {"status": "complete"}}

        result = self.replay_executor.execute_replay_session(
            self.card,
            self.root,
            "replay-001",
            prepare_round=fake_prepare,
            generate_round=fake_generate,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            calls,
            [
                ("prepare", "input-1", "input-2"),
                ("generate", "input-1", ""),
                ("prepare", "input-2", ""),
                ("generate", "input-2", ""),
            ],
        )
        self.assertEqual(self._read_status()["status"], "complete")
        self.assertTrue((self._session_dir() / "plan.json").is_file())
        chat = json.loads((self.card / "chat_log.json").read_text(encoding="utf-8"))
        self.assertEqual(len(chat), 2)
        self.assertEqual([item["user"] for item in chat], ["input-1", "input-2"])
        self.assertEqual(outlines[0]["round_id"], "round-000001-replay-001")
        self.assertEqual(outlines[0]["original_round_id"], "round-000001")
        self.assertEqual(outlines[1]["round_id"], "round-000002-replay-001")
        self.assertEqual(outlines[1]["original_round_id"], "round-000002")
        self.assertEqual(outlines[0]["current_input"]["round_id"], "round-000001")
        pending = json.loads((self.card / ".pending_user_turn.json").read_text(encoding="utf-8"))
        self.assertEqual(pending["raw_text"], "second role\n\n[USER_INSTRUCTION]\nsecond guide")
        self.assertIn("Do not rewrite player input text.", outlines[0]["must_preserve"])
        self.assertIn(
            "Do not expose next_input to player or character actors.",
            outlines[0]["must_preserve"],
        )

    def test_execute_session_blocks_and_resumes_failed_round(self):
        generate_calls = 0
        first_calls = []

        def fake_prepare(card, root, outline):
            first_calls.append(("prepare", outline["input_id"]))
            return {"ok": True, "agent_run": str(Path(card) / ".agent_runs" / outline["round_id"])}

        def flaky_generate(card, root):
            nonlocal generate_calls
            generate_calls += 1
            pending = json.loads((Path(card) / ".pending_user_turn.json").read_text(encoding="utf-8"))
            first_calls.append(("generate", pending["id"]))
            if generate_calls == 2:
                return {"ok": False, "reason": "fixture_block"}
            chat_path = Path(card) / "chat_log.json"
            chat = json.loads(chat_path.read_text(encoding="utf-8"))
            chat.append({"user": pending["id"], "ai": "new " + pending["id"]})
            chat_path.write_text(json.dumps(chat, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"ok": True, "post_round_memory": {"status": "complete"}}

        result = self.replay_executor.execute_replay_session(
            self.card,
            self.root,
            "replay-001",
            prepare_round=fake_prepare,
            generate_round=flaky_generate,
        )

        status = self._read_status()
        self.assertFalse(result["ok"])
        self.assertEqual(status["status"], "blocked")
        self.assertEqual(self._read_active(), {"session_id": "replay-001", "status": "blocked"})
        self.assertEqual(status["active_round_index"], 1)
        self.assertEqual(status["failed_round"]["input_id"], "input-2")
        self.assertEqual(status["last_error"], "fixture_block")
        self.assertEqual(
            first_calls,
            [
                ("prepare", "input-1"),
                ("generate", "input-1"),
                ("prepare", "input-2"),
                ("generate", "input-2"),
            ],
        )
        self.assertTrue((self._session_dir() / "plan.json").is_file())
        self.assertTrue((self._session_dir() / "rounds" / "1" / "result.json").is_file())
        self.assertEqual([item["input_id"] for item in status["completed_rounds"]], ["input-1"])

        second_calls = []

        def resume_prepare(card, root, outline):
            second_calls.append(("prepare", outline["input_id"]))
            return {"ok": True, "agent_run": str(Path(card) / ".agent_runs" / outline["round_id"])}

        def successful_generate(card, root):
            pending = json.loads((Path(card) / ".pending_user_turn.json").read_text(encoding="utf-8"))
            second_calls.append(("generate", pending["id"]))
            chat_path = Path(card) / "chat_log.json"
            chat = json.loads(chat_path.read_text(encoding="utf-8"))
            chat.append({"user": pending["id"], "ai": "new " + pending["id"]})
            chat_path.write_text(json.dumps(chat, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"ok": True, "post_round_memory": {"status": "complete"}}

        resumed = self.replay_executor.execute_replay_session(
            self.card,
            self.root,
            "replay-001",
            prepare_round=resume_prepare,
            generate_round=successful_generate,
        )

        final_status = self._read_status()
        self.assertTrue(resumed["ok"])
        self.assertEqual(second_calls, [("prepare", "input-2"), ("generate", "input-2")])
        self.assertEqual(final_status["status"], "complete")
        self.assertEqual(self._read_active(), {"session_id": "", "status": "complete"})
        self.assertEqual([item["input_id"] for item in final_status["completed_rounds"]], ["input-1", "input-2"])
        chat = json.loads((self.card / "chat_log.json").read_text(encoding="utf-8"))
        self.assertEqual([item["user"] for item in chat], ["input-1", "input-2"])

    def test_execute_session_rejects_unsafe_session_id_without_writing_outside_sessions(self):
        outside = self.root / "outside-session"
        unsafe_ids = [".", "..", "../escape", "..\\escape", str(outside.resolve())]

        def fail_prepare(card, root, outline):
            raise AssertionError("prepare should not be called for unsafe session id")

        def fail_generate(card, root):
            raise AssertionError("generate should not be called for unsafe session id")

        for session_id in unsafe_ids:
            with self.subTest(session_id=session_id):
                result = self.replay_executor.execute_replay_session(
                    self.card,
                    self.root,
                    session_id,
                    prepare_round=fail_prepare,
                    generate_round=fail_generate,
                )

                self.assertEqual(result, {"ok": False, "reason": "unsafe_session_id"})

        self.assertFalse((self.card / ".replay" / "escape" / "status.json").exists())
        self.assertFalse((self.card / ".replay" / "sessions" / "status.json").exists())
        self.assertFalse((outside / "status.json").exists())

    def test_execute_session_blocks_when_restore_fails_and_updates_active_pointer(self):
        plan = dict(self.plan)
        plan["backup_id"] = "round-000001-20260623T000000000000Z-abc123def456"
        self._write_plan(plan)

        def fail_prepare(card, root, outline):
            raise AssertionError("prepare should not be called when restore fails")

        def fail_generate(card, root):
            raise AssertionError("generate should not be called when restore fails")

        result = self.replay_executor.execute_replay_session(
            self.card,
            self.root,
            "replay-001",
            prepare_round=fail_prepare,
            generate_round=fail_generate,
        )

        status = self._read_status()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "snapshot_missing")
        self.assertEqual(status["status"], "blocked")
        self.assertEqual(status["active_round_index"], 0)
        self.assertEqual(status["last_error"], "snapshot_missing")
        self.assertEqual(self._read_active(), {"session_id": "replay-001", "status": "blocked"})

    def test_execute_session_blocks_when_prepare_returns_empty_result(self):
        calls = []

        def empty_prepare(card, root, outline):
            calls.append(("prepare", outline["input_id"]))
            return {}

        def fail_generate(card, root):
            calls.append(("generate", "unexpected"))
            raise AssertionError("generate should not be called after prepare failure")

        result = self.replay_executor.execute_replay_session(
            self.card,
            self.root,
            "replay-001",
            prepare_round=empty_prepare,
            generate_round=fail_generate,
        )

        status = self._read_status()
        self.assertFalse(result["ok"])
        self.assertEqual(calls, [("prepare", "input-1")])
        self.assertEqual(status["status"], "blocked")
        self.assertEqual(status["last_error"], "prepare_round_failed")
        self.assertEqual(status["failed_round"]["input_id"], "input-1")

    def test_execute_session_blocks_when_generate_returns_empty_result(self):
        calls = []

        def ok_prepare(card, root, outline):
            calls.append(("prepare", outline["input_id"]))
            return {"ok": True}

        def empty_generate(card, root):
            pending = json.loads((Path(card) / ".pending_user_turn.json").read_text(encoding="utf-8"))
            calls.append(("generate", pending["id"]))
            return {}

        result = self.replay_executor.execute_replay_session(
            self.card,
            self.root,
            "replay-001",
            prepare_round=ok_prepare,
            generate_round=empty_generate,
        )

        status = self._read_status()
        self.assertFalse(result["ok"])
        self.assertEqual(calls, [("prepare", "input-1"), ("generate", "input-1")])
        self.assertEqual(status["status"], "blocked")
        self.assertEqual(status["last_error"], "generate_round_failed")
        self.assertEqual(status["failed_round"]["input_id"], "input-1")

    def test_prepare_round_default_delegates_to_round_prepare_api(self):
        calls = []
        original_prepare = self.replay_executor.round_prepare.prepare_round

        def fake_prepare(card, root):
            calls.append((Path(card), Path(root)))
            return {"ok": True, "agent_run": "round-000001"}

        try:
            self.replay_executor.round_prepare.prepare_round = fake_prepare
            result = self.replay_executor.prepare_round_default(
                self.card,
                self.root,
                {"input_id": "input-1"},
            )
        finally:
            self.replay_executor.round_prepare.prepare_round = original_prepare

        self.assertEqual(result, {"ok": True, "agent_run": "round-000001"})
        self.assertEqual(calls, [(self.card, self.root)])

    def test_generate_round_default_delegates_and_converts_exceptions(self):
        calls = []
        original_run_round = self.replay_executor.rp_generate_cli.run_round

        def fake_run_round(card, root):
            calls.append((Path(card), Path(root)))
            return {"ok": True, "status": "delivered"}

        try:
            self.replay_executor.rp_generate_cli.run_round = fake_run_round
            result = self.replay_executor.generate_round_default(self.card, self.root)
        finally:
            self.replay_executor.rp_generate_cli.run_round = original_run_round

        self.assertEqual(result, {"ok": True, "status": "delivered"})
        self.assertEqual(calls, [(self.card, self.root)])

        def failing_run_round(card, root):
            raise RuntimeError("fixture generate failure")

        try:
            self.replay_executor.rp_generate_cli.run_round = failing_run_round
            failed = self.replay_executor.generate_round_default(self.card, self.root)
        finally:
            self.replay_executor.rp_generate_cli.run_round = original_run_round

        self.assertEqual(failed["ok"], False)
        self.assertEqual(failed["reason"], "fixture generate failure")


if __name__ == "__main__":
    unittest.main()
