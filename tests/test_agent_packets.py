import contextlib
import importlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_run():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_run", ROOT / "skills" / "agent_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_agent_packets():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_packets", ROOT / "skills" / "agent_packets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_agent_memory():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_memory", ROOT / "skills" / "agent_memory.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_round_prepare():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("round_prepare", ROOT / "skills" / "round_prepare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_round_deliver():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("round_deliver", ROOT / "skills" / "round_deliver.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class CriticGateSourceTest(unittest.TestCase):
    def test_round_deliver_reads_current_critic_report(self):
        source = (ROOT / "skills" / "round_deliver.py").read_text(encoding="utf-8")

        self.assertIn("import agent_run", source)
        self.assertIn("read_current_critic_report", source)
        self.assertIn("critic_hard_failures", source)

    def test_round_deliver_uses_agent_output_gate(self):
        source = (ROOT / "skills" / "round_deliver.py").read_text(encoding="utf-8")

        self.assertIn("import agent_outputs", source)
        self.assertIn("prepare_delivery", source)

    def test_round_deliver_ingests_agent_memory_deltas(self):
        source = (ROOT / "skills" / "round_deliver.py").read_text(encoding="utf-8")

        self.assertIn("import agent_memory", source)
        self.assertIn("ingest_memory_deltas", source)

    def test_round_deliver_ingests_agent_memory_summaries(self):
        source = (ROOT / "skills" / "round_deliver.py").read_text(encoding="utf-8")

        self.assertIn("import agent_memory", source)
        self.assertIn("ingest_memory_summaries", source)

    def test_round_deliver_runs_agent_lifecycle_cleanup(self):
        source = (ROOT / "skills" / "round_deliver.py").read_text(encoding="utf-8")

        self.assertIn("import agent_lifecycle", source)
        self.assertIn("cleanup_round_agents", source)

    def test_round_deliver_decodes_child_process_output_as_utf8(self):
        source = (ROOT / "skills" / "round_deliver.py").read_text(encoding="utf-8")

        self.assertGreaterEqual(source.count('encoding="utf-8"'), 2)
        self.assertGreaterEqual(source.count('errors="replace"'), 2)


class PlayerProcessingGuardTest(unittest.TestCase):
    def setUp(self):
        self.round_deliver = _load_round_deliver()

    def test_mixed_input_evidence_may_live_in_update_variable(self):
        context = """
=== PLAYER_INPUT_HEURISTIC_FALLBACK (debug only; input_analysis.output.json is authoritative when present) ===
  1. OMNISCIENT_SETTING: hidden rule
  2. SYNOPSIS: dream breaks
  3. ACTION: throw pendant
  conflict_cues: 梦境破碎, 醒来
"""
        response = """
<content><p>你在上学路上醒来，并尝试丢掉吊坠。</p></content>
<UpdateVariable>
<Analysis>
- mixed input handled: OMNISCIENT_SETTING, SYNOPSIS, ACTION
- repair/reframing: prior classroom branch treated as dream preview
</Analysis>
<JSONPatch>
[
  {"op":"replace","path":"/异常/隐藏机制","value":"吊坠为未公开暗线"},
  {"op":"replace","path":"/异常/前文修正","value":"上一轮教室段落降级为梦境预示"}
]
</JSONPatch>
</UpdateVariable>
<summary>你醒来后确认吊坠无法丢弃。</summary>
"""

        warnings = self.round_deliver.validate_player_processing(response, context)

        self.assertEqual(warnings, [])

    def test_required_repair_requires_derived_content_edits(self):
        context = """
=== PLAYER_INPUT_HEURISTIC_FALLBACK (debug only; input_analysis.output.json is authoritative when present) ===
  1. SYNOPSIS: dream breaks
  2. ACTION: throw pendant
  conflict_cues: 梦境破碎, 醒来
  prior_ai_to_reconcile: turn=0 summary=classroom already happened.
  required_repair: identify which prior AI-derived facts become dream/preview/false branch/obsolete.
"""
        response = """
<content><p>你在上学路上醒来，并尝试丢掉吊坠。</p></content>
<UpdateVariable>
<Analysis>
- mixed input handled: SYNOPSIS, ACTION
- repair/reframing: prior classroom branch treated as dream preview
</Analysis>
<JSONPatch>
[
  {"op":"replace","path":"/异常/前文修正","value":"上一轮教室段落降级为梦境预示"}
]
</JSONPatch>
</UpdateVariable>
<summary>你醒来后确认吊坠无法丢弃。</summary>
"""

        warnings = self.round_deliver.validate_player_processing(response, context)

        self.assertEqual(warnings, [])

    def test_mixed_input_guard_accepts_semantic_evidence_without_english_labels(self):
        context = """
=== PLAYER_INPUT_HEURISTIC_FALLBACK (debug only; input_analysis.output.json is authoritative when present) ===
  1. OMNISCIENT_SETTING: hidden rule
  2. SYNOPSIS: dream breaks
  3. ACTION: throw pendant
  conflict_cues: 梦境破碎, 醒来
  prior_ai_to_reconcile: turn=0 summary=classroom already happened.
  required_repair: identify which prior AI-derived facts become dream/preview/false branch/obsolete.
"""
        response = """
<content><p>你在上学路上从梦境残留中醒来，并尝试丢弃吊坠。</p></content>
<UpdateVariable>
<Analysis>
- player attempted discard; pendant returned cleanly, then classroom observation began without revealing hidden future setting
</Analysis>
<JSONPatch>
[
  {"op":"replace","path":"/异常/梦境残留/状态","value":"梦境细节快速消散，仅保留女性身份感、粉色花朵吊坠与未来预兆的碎片印象"},
  {"op":"replace","path":"/暗线/长期引导/吊坠秘密","value":"吊坠真实用途与代价暂不向角色显性揭露，仅作为后续线索保留"}
]
</JSONPatch>
</UpdateVariable>
<derived_content_edits>
[
  {"turn_index":0,"summary":"上一轮课堂段落改定为梦境预示。","reason":"玩家梦醒回拨"}
]
</derived_content_edits>
<summary>你醒来后确认吊坠无法丢弃。</summary>
"""

        warnings = self.round_deliver.validate_player_processing(response, context)

        self.assertEqual(warnings, [])

    def test_required_repair_rejects_unactionable_derived_content_edits(self):
        context = """
=== PLAYER_INPUT_HEURISTIC_FALLBACK (debug only; input_analysis.output.json is authoritative when present) ===
  1. SYNOPSIS: dream breaks
  2. ACTION: throw pendant
  conflict_cues: 梦境破碎, 醒来
  prior_ai_to_reconcile: turn=0 summary=classroom already happened.
  required_repair: identify which prior AI-derived facts become dream/preview/false branch/obsolete.
"""
        response = """
<content><p>你在上学路上醒来，并尝试丢掉吊坠。</p></content>
<UpdateVariable>
<Analysis>- attempted discard; dream residue stored</Analysis>
<JSONPatch>[{"op":"replace","path":"/异常/梦境残留","value":"已记录"}]</JSONPatch>
</UpdateVariable>
<derived_content_edits>
[
  {"op":"replace","path":"/prior_ai_turn/scene","value":"not actionable for handler"}
]
</derived_content_edits>
<summary>你醒来后确认吊坠无法丢弃。</summary>
"""

        warnings = self.round_deliver.validate_player_processing(response, context)

        self.assertEqual(warnings, [])


class CriticGateRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.card.mkdir()
        self.root = Path(self.tmp.name) / "root"
        self.styles_dir = self.root / "skills" / "styles"
        self.styles_dir.mkdir(parents=True)
        (self.styles_dir / "response.txt").write_text("<content>短</content>", encoding="utf-8")
        (self.styles_dir / "settings.json").write_text(json.dumps({"wordCount": 2000}), encoding="utf-8")
        self.round_deliver = _load_round_deliver()

    def tearDown(self):
        self.tmp.cleanup()

    def _run_round_deliver(self, critic_report, handler_message):
        progress_calls = []
        self.round_deliver.write_progress = lambda *args, **kwargs: progress_calls.append((args, kwargs))
        original_read_current_critic_report = self.round_deliver.agent_run.read_current_critic_report
        original_subprocess_run = self.round_deliver.subprocess.run
        self.round_deliver.agent_run.read_current_critic_report = lambda card_folder: critic_report
        self.round_deliver.subprocess.run = lambda *args, **kwargs: self.fail(handler_message)

        token_stats = importlib.import_module("token_stats")
        original_locate_transcript = token_stats.locate_transcript
        original_load_checkpoint = token_stats.load_checkpoint
        original_compute_delta = token_stats.compute_delta
        original_read_usage_since = token_stats.read_usage_since
        try:
            token_stats.locate_transcript = lambda: None
            token_stats.load_checkpoint = lambda card_folder: {}
            token_stats.read_usage_since = lambda transcript_path, byte_offset=0: []
            token_stats.compute_delta = lambda entries: {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read": 0,
                "cache_creation": 0,
                "request_count": 0,
                "cache_hit_pct": 0.0,
            }

            old_argv = sys.argv
            stdout = io.StringIO()
            try:
                sys.argv = ["round_deliver.py", str(self.card), str(self.root)]
                with self.assertRaises(SystemExit) as ctx:
                    with contextlib.redirect_stdout(stdout):
                        self.round_deliver.main()
            finally:
                sys.argv = old_argv
        finally:
            self.round_deliver.agent_run.read_current_critic_report = original_read_current_critic_report
            self.round_deliver.subprocess.run = original_subprocess_run
            token_stats.locate_transcript = original_locate_transcript
            token_stats.load_checkpoint = original_load_checkpoint
            token_stats.compute_delta = original_compute_delta
            token_stats.read_usage_since = original_read_usage_since

        return ctx.exception.code, json.loads(stdout.getvalue().strip()), progress_calls

    def test_round_deliver_retries_on_critic_hard_failures(self):
        exit_code, payload, progress_calls = self._run_round_deliver(
            critic_report={"passed": False, "hard_failures": ["missing continuity fix"]},
            handler_message="handler should not run when critic gate retries",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["action"], "retry")
        self.assertEqual(payload["reason"], "critic_hard_failures")
        self.assertEqual(payload["critic_report"]["hard_failures"], ["missing continuity fix"])
        self.assertTrue(any(args[:2] == ("retry", "质检未通过，等待修复") for args, _ in progress_calls))

    def test_round_deliver_ignores_malformed_critic_hard_failures(self):
        exit_code, payload, progress_calls = self._run_round_deliver(
            critic_report={"passed": False, "hard_failures": "oops"},
            handler_message="handler should not run when word-count retry triggers",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["action"], "retry")
        self.assertNotEqual(payload.get("reason"), "critic_hard_failures")
        self.assertNotIn("critic_report", payload)
        self.assertIn("word_count", payload)
        self.assertTrue(any(args[:2] == ("retry", "回复未达字数要求，等待重写") for args, _ in progress_calls))

    def test_round_deliver_ignores_missing_critic_report(self):
        original_read_current_critic_report = self.round_deliver.agent_run.read_current_critic_report
        exit_code, payload, progress_calls = self._run_round_deliver(
            critic_report={},
            handler_message="handler should not run when no-report falls through to word-count retry",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["action"], "retry")
        self.assertNotEqual(payload.get("reason"), "critic_hard_failures")
        self.assertNotIn("critic_report", payload)
        self.assertIn("word_count", payload)
        self.assertIs(self.round_deliver.agent_run.read_current_critic_report, original_read_current_critic_report)
        self.assertTrue(any(args[:2] == ("retry", "回复未达字数要求，等待重写") for args, _ in progress_calls))

    def test_round_deliver_returns_retry_when_agent_output_gate_blocks(self):
        progress_calls = []
        self.round_deliver.write_progress = lambda *args, **kwargs: progress_calls.append((args, kwargs))
        original_subprocess_run = self.round_deliver.subprocess.run
        self.round_deliver.subprocess.run = lambda *args, **kwargs: self.fail("handler should not run when agent output gate blocks")
        original_prepare_delivery = self.round_deliver.agent_outputs.prepare_delivery

        def gate(card_folder, styles_dir):
            return {
                "ok": False,
                "action": "retry",
                "reason": "agent_outputs",
                "message": "Required agent outputs are missing or invalid.",
                "detail": "gm.output.json",
            }

        self.round_deliver.agent_outputs.prepare_delivery = gate

        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["round_deliver.py", str(self.card), str(self.root)]
            with self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stdout(stdout):
                    self.round_deliver.main()
        finally:
            sys.argv = old_argv
            self.round_deliver.subprocess.run = original_subprocess_run
            self.round_deliver.agent_outputs.prepare_delivery = original_prepare_delivery

        payload = json.loads(stdout.getvalue().strip())
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(payload["action"], "retry")
        self.assertEqual(payload["reason"], "agent_outputs")
        self.assertEqual(payload["detail"], "gm.output.json")
        self.assertTrue(any(args[:2] == ("retry", "多代理产物未就绪，等待修复") for args, _ in progress_calls))

    def test_round_deliver_propagates_terminal_agent_output_block(self):
        progress_calls = []
        self.round_deliver.write_progress = lambda *args, **kwargs: progress_calls.append((args, kwargs))
        original_subprocess_run = self.round_deliver.subprocess.run
        self.round_deliver.subprocess.run = lambda *args, **kwargs: self.fail("handler should not run when agent output gate is terminal")
        original_prepare_delivery = self.round_deliver.agent_outputs.prepare_delivery

        def gate(card_folder, styles_dir):
            return {
                "ok": False,
                "action": "blocked",
                "reason": "critic_retry_limit",
                "message": "Critic retry limit reached.",
                "detail": {"decision": "block"},
            }

        self.round_deliver.agent_outputs.prepare_delivery = gate

        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["round_deliver.py", str(self.card), str(self.root)]
            with self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stdout(stdout):
                    self.round_deliver.main()
        finally:
            sys.argv = old_argv
            self.round_deliver.subprocess.run = original_subprocess_run
            self.round_deliver.agent_outputs.prepare_delivery = original_prepare_delivery

        payload = json.loads(stdout.getvalue().strip())
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(payload["action"], "blocked")
        self.assertEqual(payload["reason"], "critic_retry_limit")
        self.assertTrue(any(args and args[0] == "blocked" for args, _ in progress_calls))
        self.assertFalse(any(args and args[0] == "retry" for args, _ in progress_calls))

    def test_round_deliver_noops_when_agent_run_already_delivered(self):
        progress_calls = []
        self.round_deliver.write_progress = lambda *args, **kwargs: progress_calls.append((args, kwargs))
        original_subprocess_run = self.round_deliver.subprocess.run
        self.round_deliver.subprocess.run = lambda *args, **kwargs: self.fail("handler should not run for already delivered run")
        original_prepare_delivery = self.round_deliver.agent_outputs.prepare_delivery

        def gate(card_folder, styles_dir):
            return {
                "ok": True,
                "mode": "already_delivered",
                "run_dir": str(self.card / ".agent_runs" / "round-000001"),
                "stage": "delivered",
            }

        self.round_deliver.agent_outputs.prepare_delivery = gate

        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["round_deliver.py", str(self.card), str(self.root)]
            with self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stdout(stdout):
                    self.round_deliver.main()
        finally:
            sys.argv = old_argv
            self.round_deliver.subprocess.run = original_subprocess_run
            self.round_deliver.agent_outputs.prepare_delivery = original_prepare_delivery

        payload = json.loads(stdout.getvalue().strip())
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(payload["action"], "already_done")
        self.assertEqual(payload["agent_delivery"]["mode"], "already_delivered")

    def test_round_deliver_reports_post_round_memory_status(self):
        progress_calls = []
        self.round_deliver.write_progress = lambda *args, **kwargs: progress_calls.append((args, kwargs))
        (self.styles_dir / "settings.json").write_text(json.dumps({"wordCount": 1}), encoding="utf-8")
        (self.styles_dir / "response.txt").write_text("<content>足够长</content>", encoding="utf-8")
        run_dir = self.card / ".agent_runs" / "round-000001"
        run_dir.mkdir(parents=True)
        (self.card / ".agent_runs" / "current").write_text(str(run_dir.resolve()), encoding="utf-8")
        _write_json(
            run_dir / "manifest.json",
            {
                "round_id": "round-000001",
                "stage": "critic_passed",
                "expected_outputs": {},
            },
        )
        _write_json(
            run_dir / "story.input.json",
            {
                "round_id": "round-000001",
                "loop_outputs": {"actors": {}, "gm": {"outputs": []}},
                "side_threads": {"threads": []},
                "memory_deltas": {"actors": {}, "world": []},
                "interaction_trace": {"visible_events": []},
            },
        )

        original_prepare_delivery = self.round_deliver.agent_outputs.prepare_delivery
        original_subprocess_run = self.round_deliver.subprocess.run
        token_stats = importlib.import_module("token_stats")
        original_locate_transcript = token_stats.locate_transcript
        original_load_checkpoint = token_stats.load_checkpoint
        original_compute_delta = token_stats.compute_delta
        original_read_usage_since = token_stats.read_usage_since
        original_save_checkpoint = token_stats.save_checkpoint

        self.round_deliver.agent_outputs.prepare_delivery = lambda card_folder, styles_dir: {
            "ok": True,
            "mode": "agent_run",
            "run_dir": str(run_dir),
        }
        self.round_deliver.subprocess.run = lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="ok",
            stderr="",
        )
        try:
            token_stats.locate_transcript = lambda: None
            token_stats.load_checkpoint = lambda card_folder: {}
            token_stats.read_usage_since = lambda transcript_path, byte_offset=0: []
            token_stats.compute_delta = lambda entries: {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read": 0,
                "cache_creation": 0,
                "request_count": 0,
                "cache_hit_pct": 0.0,
            }
            token_stats.save_checkpoint = lambda *args, **kwargs: None

            old_argv = sys.argv
            stdout = io.StringIO()
            try:
                sys.argv = ["round_deliver.py", str(self.card), str(self.root)]
                with contextlib.redirect_stdout(stdout):
                    self.round_deliver.main()
            finally:
                sys.argv = old_argv
        finally:
            self.round_deliver.agent_outputs.prepare_delivery = original_prepare_delivery
            self.round_deliver.subprocess.run = original_subprocess_run
            token_stats.locate_transcript = original_locate_transcript
            token_stats.load_checkpoint = original_load_checkpoint
            token_stats.compute_delta = original_compute_delta
            token_stats.read_usage_since = original_read_usage_since
            token_stats.save_checkpoint = original_save_checkpoint

        payload = json.loads(stdout.getvalue().strip())
        self.assertEqual(payload["action"], "done")
        self.assertEqual(payload["post_round_memory"]["status"], "not_required")
        self.assertEqual(payload["agent_lifecycle_cleanup"]["status"], "complete")
        self.assertTrue(any(args and args[0] == "complete" for args, _ in progress_calls))

    def test_round_deliver_keeps_done_action_when_lifecycle_cleanup_fails(self):
        self.round_deliver.write_progress = lambda *args, **kwargs: None
        (self.styles_dir / "settings.json").write_text(json.dumps({"wordCount": 1}), encoding="utf-8")
        (self.styles_dir / "response.txt").write_text("<content>瓒冲闀?/content>", encoding="utf-8")
        run_dir = self.card / ".agent_runs" / "round-000001"
        run_dir.mkdir(parents=True)
        (self.card / ".agent_runs" / "current").write_text(str(run_dir.resolve()), encoding="utf-8")
        _write_json(
            run_dir / "manifest.json",
            {
                "round_id": "round-000001",
                "stage": "critic_passed",
                "expected_outputs": {},
            },
        )
        _write_json(
            run_dir / "story.input.json",
            {
                "round_id": "round-000001",
                "loop_outputs": {"actors": {}, "gm": {"outputs": []}},
                "side_threads": {"threads": []},
                "memory_deltas": {"actors": {}, "world": []},
                "interaction_trace": {"visible_events": []},
            },
        )

        original_prepare_delivery = self.round_deliver.agent_outputs.prepare_delivery
        original_subprocess_run = self.round_deliver.subprocess.run
        original_cleanup = self.round_deliver.agent_lifecycle.cleanup_round_agents
        token_stats = importlib.import_module("token_stats")
        original_locate_transcript = token_stats.locate_transcript
        original_load_checkpoint = token_stats.load_checkpoint
        original_compute_delta = token_stats.compute_delta
        original_read_usage_since = token_stats.read_usage_since
        original_save_checkpoint = token_stats.save_checkpoint

        self.round_deliver.agent_outputs.prepare_delivery = lambda card_folder, styles_dir: {
            "ok": True,
            "mode": "agent_run",
            "run_dir": str(run_dir),
        }
        self.round_deliver.subprocess.run = lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="ok",
            stderr="",
        )

        def fail_cleanup(*args, **kwargs):
            raise RuntimeError("cleanup boom")

        self.round_deliver.agent_lifecycle.cleanup_round_agents = fail_cleanup
        try:
            token_stats.locate_transcript = lambda: None
            token_stats.load_checkpoint = lambda card_folder: {}
            token_stats.read_usage_since = lambda transcript_path, byte_offset=0: []
            token_stats.compute_delta = lambda entries: {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read": 0,
                "cache_creation": 0,
                "request_count": 0,
                "cache_hit_pct": 0.0,
            }
            token_stats.save_checkpoint = lambda *args, **kwargs: None

            old_argv = sys.argv
            stdout = io.StringIO()
            try:
                sys.argv = ["round_deliver.py", str(self.card), str(self.root)]
                with contextlib.redirect_stdout(stdout):
                    self.round_deliver.main()
            finally:
                sys.argv = old_argv
        finally:
            self.round_deliver.agent_outputs.prepare_delivery = original_prepare_delivery
            self.round_deliver.subprocess.run = original_subprocess_run
            self.round_deliver.agent_lifecycle.cleanup_round_agents = original_cleanup
            token_stats.locate_transcript = original_locate_transcript
            token_stats.load_checkpoint = original_load_checkpoint
            token_stats.compute_delta = original_compute_delta
            token_stats.read_usage_since = original_read_usage_since
            token_stats.save_checkpoint = original_save_checkpoint

        payload = json.loads(stdout.getvalue().strip())
        self.assertEqual(payload["action"], "done")
        self.assertEqual(payload["agent_lifecycle_cleanup"]["status"], "error")
        self.assertIn("cleanup boom", payload["agent_lifecycle_cleanup"]["error"])


class AgentRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.card.mkdir()
        self.agent_run = _load_agent_run()

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_run_dir_uses_next_round_number(self):
        first = self.agent_run.create_run_dir(self.card, turn_index=0)
        second = self.agent_run.create_run_dir(self.card, turn_index=7)

        self.assertEqual(first.name, "round-000001")
        self.assertEqual(second.name, "round-000008")
        self.assertTrue((self.card / ".agent_runs" / "round-000001").exists())
        self.assertTrue((self.card / ".agent_runs" / "current").read_text(encoding="utf-8").endswith("round-000008"))

    def test_create_run_dir_auto_increments(self):
        first = self.agent_run.create_run_dir(self.card)
        second = self.agent_run.create_run_dir(self.card)

        self.assertEqual(first.name, "round-000001")
        self.assertEqual(second.name, "round-000002")

    def test_current_run_dir_with_relative_card_path(self):
        relative_parent = Path(self.tmp.name) / "parent"
        relative_parent.mkdir()
        relative_card = relative_parent / "card"
        relative_card.mkdir()
        relative_name = "card"

        old_cwd = Path.cwd()
        try:
            os.chdir(relative_parent)
            run_dir = self.agent_run.create_run_dir(relative_name)
            current = self.agent_run.current_run_dir(relative_name)

            self.assertIsNotNone(current)
            self.assertEqual(current, run_dir.resolve())
            self.assertTrue(current.name.startswith("round-000001"))
        finally:
            os.chdir(old_cwd)

    def test_write_json_and_read_current_report(self):
        run_dir = self.agent_run.create_run_dir(self.card, turn_index=2)
        self.agent_run.write_json(run_dir / "critic.report.json", {"passed": False, "hard_failures": ["bad"]})

        report = self.agent_run.read_current_critic_report(self.card)

        self.assertEqual(report["passed"], False)
        self.assertEqual(report["hard_failures"], ["bad"])


class SemanticInputPolicyTest(unittest.TestCase):
    def test_production_code_has_no_player_input_keyword_heuristics(self):
        forbidden = {
            "skills/round_prepare.py": [
                "PLAYER_INPUT_HEURISTIC_FALLBACK",
                "setting_cues",
                "synopsis_cues",
                "important_character_cues",
                "conflict_patterns",
                "def _input_matches",
                "name in user_text",
            ],
            "skills/agent_packets.py": [
                "INSTRUCTION_PREFIXES",
                "omniscient:",
                "important character:",
            ],
            "skills/hidden_settings.py": [
                "HIDDEN_SETTING_CUES",
                "is_hidden_setting_instruction",
                "def persist_hidden_setting(",
            ],
            "skills/round_deliver.py": [
                "semantic_terms",
                "conflict_cues:",
                "DERIVED_CONTENT_EDIT",
                "IMPORTANT_CHARACTER_DECLARATION",
                "OMNISCIENT_SETTING",
            ],
        }
        for rel, needles in forbidden.items():
            source = (ROOT / rel).read_text(encoding="utf-8")
            for needle in needles:
                self.assertNotIn(needle, source, f"{rel} still contains {needle!r}")


class AgentPacketTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.card.mkdir()
        self.agent_packets = _load_agent_packets()
        self.agent_run = _load_agent_run()
        self.agent_memory = _load_agent_memory()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_round_prepare_fixture(self):
        temp_root = Path(self.tmp.name) / "root"
        styles_dir = temp_root / "skills" / "styles"
        styles_dir.mkdir(parents=True)
        (styles_dir / "input.txt").write_text("I step into the archive.", encoding="utf-8")
        (styles_dir / "settings.json").write_text("{}", encoding="utf-8")
        (self.card / ".card_data.json").write_text("{}", encoding="utf-8")
        return temp_root, styles_dir

    def test_route_player_input_does_not_split_omniscient_setting_keywords(self):
        text = "\u6211\u63a8\u5f00\u95e8\u8d70\u8fdb\u53bb\u3002\n\uff08\u4e0a\u5e1d\u89c6\u89d2\u8bbe\u5b9a\uff1a\u95e8\u540e\u5176\u5b9e\u662f\u68a6\u5883\u6d78\u54cd\u3002\uff09"
        routed = self.agent_packets.route_player_input(text)

        self.assertEqual(routed["role_channel"], text)
        self.assertEqual(routed["user_instruction_channel"], "")
        self.assertEqual(routed["components"], [{"channel": "role", "text": text}])

    def test_route_player_input_does_not_split_english_instruction_keywords(self):
        routed = self.agent_packets.route_player_input(
            "I open the gate.\nSystem: make the castle a moon base.\n"
        )
        self.assertEqual(routed["role_channel"], "I open the gate.\nSystem: make the castle a moon base.")
        self.assertEqual(routed["user_instruction_channel"], "")

    def test_route_player_input_does_not_split_inline_chinese_setting_keyword(self):
        routed = self.agent_packets.route_player_input(
            "\u6211\u8d70\u8fdb\u623f\u95f4\u3002\u8bbe\u5b9a\uff1a\u95e8\u540e\u662f\u68a6\u5883\u3002"
        )

        self.assertEqual(
            routed["role_channel"],
            "\u6211\u8d70\u8fdb\u623f\u95f4\u3002\u8bbe\u5b9a\uff1a\u95e8\u540e\u662f\u68a6\u5883\u3002",
        )
        self.assertEqual(routed["user_instruction_channel"], "")

    def test_route_input_payload_uses_only_explicit_dual_channel_for_instruction_text(self):
        routed = self.agent_packets.route_input_payload(
            "fallback should not be interpreted",
            {
                "input_schema": "dual_channel_v1",
                "role_text": "\u6211\u8d70\u8fdb\u623f\u95f4\u3002",
                "user_instruction_text": "\u8bbe\u5b9a\uff1a\u95e8\u540e\u662f\u68a6\u5883\u3002",
            },
        )

        self.assertEqual(routed["role_channel"], "\u6211\u8d70\u8fdb\u623f\u95f4\u3002")
        self.assertEqual(routed["user_instruction_channel"], "\u8bbe\u5b9a\uff1a\u95e8\u540e\u662f\u68a6\u5883\u3002")
        self.assertEqual(routed["input_schema"], "dual_channel_v1")

    def test_route_player_input_keeps_parenthesized_action_as_role(self):
        routed = self.agent_packets.route_player_input("(I glance at the door and step inside.)")
        self.assertEqual(routed["role_channel"], "(I glance at the door and step inside.)")
        self.assertEqual(routed["user_instruction_channel"], "")

    def test_route_player_input_keeps_ordinary_rewrite_sentence_as_role(self):
        routed = self.agent_packets.route_player_input("I rewrite the rune on the wall.")
        self.assertEqual(routed["role_channel"], "I rewrite the rune on the wall.")
        self.assertEqual(routed["user_instruction_channel"], "")

    def test_build_player_packet_uses_explicit_role_channel_without_user_instructions(self):
        routed = self.agent_packets.route_input_payload(
            "",
            {
                "input_schema": "dual_channel_v1",
                "role_text": "\u6211\u62ab\u51fa\u77ed\u5251\u3002",
                "user_instruction_text": "\u5c06\u57ce\u5821\u8bbe\u5b9a\u4e3a\u88ab\u9057\u5fd8\u7684\u6708\u9762\u57fa\u5730\u3002",
            },
        )
        packet = self.agent_packets.build_player_packet(self.card, routed, [])

        self.assertEqual(packet["visibility"], "first_person_player")
        self.assertIn("\u62ab\u51fa\u77ed\u5251", packet["role_channel_anchor"])
        self.assertNotIn("\u6708\u9762\u57fa\u5730", json.dumps(packet, ensure_ascii=False))
        self.assertNotIn("user_instruction_channel", packet)
        self.assertEqual(packet["agent"], "player")

    def test_build_player_packet_holds_unanalysed_single_channel_input(self):
        routed = self.agent_packets.route_input_payload(
            "\u6211\u8d70\u8fdb\u623f\u95f4\u3002\u8bbe\u5b9a\uff1a\u95e8\u540e\u662f\u68a6\u5883\u3002",
            None,
        )
        packet = self.agent_packets.build_player_packet(self.card, routed, [])

        self.assertEqual(packet["role_channel_anchor"], "")
        self.assertNotIn("\u95e8\u540e\u662f\u68a6\u5883", json.dumps(packet, ensure_ascii=False))

    def test_prepare_agent_run_projects_actor_context_without_hidden_channels(self):
        hidden_text = "The archive hides a moon base under the floor."
        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="I open the archive door.\nOmniscient: The vault is a dream echo.",
            chat_log=[
                {
                    "speaker": "gm",
                    "summary": "GM-only recent chat says the moon base is active.",
                    "visibility": "gm_only",
                }
            ],
            card_data={"title": "Projection Test"},
            character_contexts={
                "characters": [
                    {
                        "name": "Ada",
                        "role": "cautious archivist",
                        "memory": {
                            "long_term": ["I keep the archive keys."],
                            "goals": ["Protect the player from danger."],
                        },
                    }
                ],
            },
            turn_index=0,
            hidden_setting_records=[{"visibility": "gm_only", "text": hidden_text}],
        )

        run_dir = Path(result["run_dir"])
        player_packet = json.loads((run_dir / "player.context.json").read_text(encoding="utf-8"))
        safe_name = self.agent_run.safe_name("Ada")
        character_packet = json.loads((run_dir / "characters" / f"{safe_name}.context.json").read_text(encoding="utf-8"))
        gm_packet = json.loads((run_dir / "gm.context.json").read_text(encoding="utf-8"))
        player_prompt = (run_dir / "prompts" / "player.prompt.md").read_text(encoding="utf-8")
        char_prompt = (run_dir / "prompts" / "characters" / f"{safe_name}.prompt.md").read_text(encoding="utf-8")

        self.assertEqual(player_packet["visibility"], "first_person_player")
        self.assertEqual(character_packet["visibility"], "first_person_character")
        self.assertEqual(player_packet["role_channel_anchor"], "")
        self.assertEqual(character_packet["role_channel_anchor"], "")
        self.assertEqual(character_packet["self_knowledge"]["name"], "Ada")
        self.assertIn("I keep the archive keys.", json.dumps(character_packet, ensure_ascii=False))
        self.assertIn("Protect the player", json.dumps(character_packet, ensure_ascii=False))
        self.assertIn("The vault is a dream echo.", json.dumps(gm_packet, ensure_ascii=False))
        self.assertIn(hidden_text, json.dumps(gm_packet, ensure_ascii=False))

        for actor_payload in (player_packet, character_packet):
            serialized = json.dumps(actor_payload, ensure_ascii=False)
            self.assertEqual(actor_payload["context_version"]["algorithm"], "sha256")
            self.assertTrue(actor_payload["context_version"]["hash"].startswith("sha256:"))
            self.assertEqual(sorted(actor_payload["memory"]), ["goals", "key_memories", "long_term", "short_term"])
            self.assertNotIn("user_instruction_text", serialized)
            self.assertNotIn("user_instruction_channel", serialized)
            self.assertNotIn("dream echo", serialized)
            self.assertNotIn("moon base", serialized)
            self.assertNotIn("recent_chat", serialized)
            self.assertNotIn("GM-only recent chat", serialized)

        self.assertNotIn("dream echo", player_prompt)
        self.assertNotIn("moon base", player_prompt)
        self.assertNotIn("dream echo", char_prompt)
        self.assertNotIn("moon base", char_prompt)

    def test_prepare_agent_run_reads_structured_actor_memory_files(self):
        player_dir = self.card / "memory" / "player"
        ada_dir = self.card / "memory" / "characters" / "Ada"
        for directory in (player_dir, ada_dir):
            directory.mkdir(parents=True, exist_ok=True)

        (player_dir / "long_term.md").write_text("# Player Long-Term\n\nI remember the archive code.", encoding="utf-8")
        (player_dir / "key_memories.md").write_text("# Player Key\n\nI found the sealed index.", encoding="utf-8")
        (player_dir / "short_term.md").write_text("# Player Short\n\nI am holding the door open.", encoding="utf-8")
        (player_dir / "recent.md").write_text("# Player Agent Memory\n\n- recent player delta", encoding="utf-8")
        (player_dir / "summary.md").write_text("stale player summary should not be loaded", encoding="utf-8")
        _write_json(
            player_dir / "goals.json",
            {"goals": {"active": ["Read the sealed index."], "paused": [], "resolved": []}},
        )

        (ada_dir / "long_term.md").write_text("# Ada Long-Term\n\nI remember lending my lamp.", encoding="utf-8")
        (ada_dir / "key_memories.md").write_text("# Ada Key\n\nI saw the player at the threshold.", encoding="utf-8")
        (ada_dir / "short_term.md").write_text("# Ada Short\n\nI am beside the player.", encoding="utf-8")
        (ada_dir / "recent.md").write_text("# Character Recent Memory\n\n- recent Ada delta", encoding="utf-8")
        (ada_dir / "summary.md").write_text("stale Ada summary should not be loaded", encoding="utf-8")
        _write_json(
            ada_dir / "goals.json",
            {"goals": {"active": ["Keep the player close."], "paused": [], "resolved": []}},
        )

        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="I open the archive door.",
            chat_log=[],
            card_data={"title": "Structured Memory Packet Test"},
            character_contexts={"characters": [{"name": "Ada"}]},
            turn_index=0,
        )

        run_dir = Path(result["run_dir"])
        player_packet = json.loads((run_dir / "player.context.json").read_text(encoding="utf-8"))
        character_packet = json.loads((run_dir / "characters" / "Ada.context.json").read_text(encoding="utf-8"))
        player_memory = json.dumps(player_packet["memory"], ensure_ascii=False)
        character_memory = json.dumps(character_packet["memory"], ensure_ascii=False)

        self.assertEqual(sorted(player_packet["memory"]), ["goals", "key_memories", "long_term", "short_term"])
        self.assertEqual(sorted(character_packet["memory"]), ["goals", "key_memories", "long_term", "short_term"])
        self.assertIn("I remember the archive code.", player_memory)
        self.assertIn("I found the sealed index.", player_memory)
        self.assertIn("I am holding the door open.", player_memory)
        self.assertIn("recent player delta", player_memory)
        self.assertIn("Read the sealed index.", player_memory)
        self.assertIn("I remember lending my lamp.", character_memory)
        self.assertIn("I saw the player at the threshold.", character_memory)
        self.assertIn("recent Ada delta", character_memory)
        self.assertIn("Keep the player close.", character_memory)
        self.assertNotIn("stale player summary", player_memory)
        self.assertNotIn("stale Ada summary", character_memory)

    def test_prepare_agent_run_does_not_project_old_actor_memory_aliases(self):
        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="I open the archive door.",
            chat_log=[],
            card_data={"title": "Old Memory Alias Test"},
            character_contexts={
                "characters": [
                    {
                        "name": "Ada",
                        "memory": {
                            "long_term_memory": ["old long-term alias"],
                            "recent": ["old recent alias"],
                            "recent_memory": ["old recent-memory alias"],
                            "short_term_memory": ["old short-term alias"],
                            "current_goals": ["old current-goals alias"],
                            "memories": ["old memories alias"],
                            "key_memory": ["old key-memory alias"],
                        },
                    },
                    {
                        "name": "Bert",
                        "memory": ["old bare memory list"],
                    },
                ],
            },
            turn_index=0,
        )

        run_dir = Path(result["run_dir"])
        ada_packet = json.loads((run_dir / "characters" / "Ada.context.json").read_text(encoding="utf-8"))
        bert_packet = json.loads((run_dir / "characters" / "Bert.context.json").read_text(encoding="utf-8"))
        serialized = json.dumps([ada_packet["memory"], bert_packet["memory"]], ensure_ascii=False)

        self.assertEqual(sorted(ada_packet["memory"]), ["goals", "key_memories", "long_term", "short_term"])
        self.assertEqual(sorted(bert_packet["memory"]), ["goals", "key_memories", "long_term", "short_term"])
        self.assertNotIn("old long-term alias", serialized)
        self.assertNotIn("old recent alias", serialized)
        self.assertNotIn("old recent-memory alias", serialized)
        self.assertNotIn("old short-term alias", serialized)
        self.assertNotIn("old current-goals alias", serialized)
        self.assertNotIn("old memories alias", serialized)
        self.assertNotIn("old key-memory alias", serialized)
        self.assertNotIn("old bare memory list", serialized)

    def test_prepare_agent_run_drops_stale_recent_after_summary_ingestion(self):
        player_dir = self.card / "memory" / "player"
        player_dir.mkdir(parents=True, exist_ok=True)
        (player_dir / "recent.md").write_text(
            "# Player Agent Memory\n\n- stale recent player delta\n",
            encoding="utf-8",
        )
        summary_run = self.card / ".agent_runs" / "round-000006"
        _write_json(
            summary_run / "manifest.json",
            {"expected_outputs": {"memory_summaries": {"player": "memory_summaries/player.summary.json"}}},
        )
        _write_json(
            summary_run / "memory_summaries" / "player.summary.json",
            {
                "agent_id": "player",
                "source": "self",
                "visibility": "actor",
                "long_term": {
                    "self_understanding": ["I remember organizing the archive threshold."],
                    "stable_beliefs": [],
                    "relationship_models": [],
                },
                "key_memories": [],
                "short_term": [
                    {
                        "content": "I am choosing what to inspect after the organization.",
                        "expires_after": "scene_end",
                    }
                ],
                "goals": {"active": ["Inspect the archive shelves."], "paused": [], "resolved": []},
            },
        )
        self.agent_memory.ingest_memory_summaries(self.card, summary_run)

        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="I look toward the shelves.",
            chat_log=[],
            card_data={"title": "Recent Cleanup Packet Test"},
            character_contexts={"characters": []},
            turn_index=7,
        )

        memory = json.dumps(result["player_packet"]["memory"], ensure_ascii=False)
        self.assertIn("I am choosing what to inspect after the organization.", memory)
        self.assertIn("Inspect the archive shelves.", memory)
        self.assertNotIn("stale recent player delta", memory)

    def test_prepare_agent_run_does_not_expose_private_role_text_to_character_context(self):
        private_role_text = "I decide to lie while smiling."
        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text=private_role_text,
            chat_log=[],
            card_data={"title": "Private Role Test"},
            character_contexts={"characters": [{"name": "Ada", "profile_summary": "Ada watches carefully."}]},
            turn_index=0,
        )

        run_dir = Path(result["run_dir"])
        safe_name = self.agent_run.safe_name("Ada")
        character_packet = json.loads((run_dir / "characters" / f"{safe_name}.context.json").read_text(encoding="utf-8"))

        self.assertEqual(character_packet["visibility"], "first_person_character")
        self.assertEqual(character_packet["visible_events"], [])
        self.assertNotIn(private_role_text, json.dumps(character_packet, ensure_ascii=False))

    def test_prepare_agent_run_uses_safe_character_actor_id_in_context_and_prompt(self):
        display_name = "Ada/Zero?"
        safe_name = self.agent_run.safe_name(display_name)
        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="I listen at the sealed door.",
            chat_log=[],
            card_data={"title": "Safe Actor Test"},
            character_contexts={
                "characters": [
                    {
                        "name": display_name,
                        "profile_summary": "Ada/Zero? tracks subtle sounds.",
                    }
                ]
            },
            turn_index=0,
        )

        run_dir = Path(result["run_dir"])
        character_packet = json.loads((run_dir / "characters" / f"{safe_name}.context.json").read_text(encoding="utf-8"))
        char_prompt = (run_dir / "prompts" / "characters" / f"{safe_name}.prompt.md").read_text(encoding="utf-8")

        self.assertNotEqual(display_name, safe_name)
        self.assertEqual(character_packet["actor_id"], f"character:{safe_name}")
        self.assertEqual(character_packet["self_knowledge"]["name"], display_name)
        self.assertIn(f'"agent_id": "character:{safe_name}"', char_prompt)
        self.assertIn(f'"character_name": "{display_name}"', char_prompt)
        self.assertNotIn(f'"agent_id": "character:{display_name}"', char_prompt)

    def test_prepare_agent_run_materializes_all_registered_major_character_contexts(self):
        round_prepare = _load_round_prepare()
        card_data = {
            "title": "All Registered Characters",
            "character_orchestration": {
                "major": ["Ada", "Bert", "Cora"],
                "max_parallel_subagents": 2,
            },
        }
        contexts = round_prepare.build_character_contexts(
            self.card,
            card_data,
            {},
            [],
            "I enter the archive.",
        )

        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="I enter the archive.",
            chat_log=[],
            card_data=card_data,
            character_contexts=contexts,
            turn_index=0,
        )

        run_dir = Path(result["run_dir"])
        self.assertEqual(
            [item["name"] for item in contexts["characters"]],
            ["Ada", "Bert", "Cora"],
        )
        for name in ("Ada", "Bert", "Cora"):
            safe = self.agent_run.safe_name(name)
            self.assertTrue((run_dir / "characters" / f"{safe}.context.json").exists())
            self.assertTrue((run_dir / "prompts" / "characters" / f"{safe}.prompt.md").exists())

        input_json = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
        self.assertEqual(
            [item["name"] for item in input_json["character_contexts"]["characters"]],
            ["Ada", "Bert", "Cora"],
        )

    def test_prepare_agent_run_builds_expected_context_files(self):
        user_text = "\u6211\u524d\u5f80\u6708\u9762\u57fa\u5730\uff0c\u5bfb\u627e\u65b0\u7684\u7ebf\u7d22\u3002"
        chat_log = [{"index": 3, "summary": "\u5f00\u542f\u7b2c\u4e00\u8f6e"}]
        card_data = {"title": "\u6d4b\u8bd5\u5361"}
        character_contexts = {
            "characters": [
                {
                    "name": "Ada",
                    "profile_summary": "Ada is cautious.",
                }
            ],
            "minor_policy": "main_agent",
        }

        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text=user_text,
            chat_log=chat_log,
            card_data=card_data,
            character_contexts=character_contexts,
            turn_index=0,
        )

        run_dir = Path(result["run_dir"])
        self.assertTrue((run_dir / "input.json").exists())
        self.assertTrue((run_dir / "gm.context.json").exists())
        self.assertTrue((run_dir / "player.context.json").exists())

        safe_name = self.agent_run.safe_name("Ada")
        char_path = run_dir / "characters" / f"{safe_name}.context.json"
        self.assertTrue(char_path.exists())

        critic = json.loads((run_dir / "critic.report.json").read_text(encoding="utf-8"))
        self.assertEqual(critic, self.agent_packets.DEFAULT_CRITIC_REPORT)

    def test_prepare_agent_run_compacts_large_card_data_for_agent_mailbox(self):
        large_entries = [
            {
                "comment": f"entry-{index}",
                "keys": [f"key-{index}"],
                "content": "world lore " * 600,
                "enabled": True,
            }
            for index in range(80)
        ]
        card_data = {
            "name": "Huge Card",
            "description": "A focused playable setup.",
            "first_mes": "Opening scene." * 200,
            "data": {
                "name": "Huge Card Data",
                "character_book": {"entries": large_entries},
                "extensions": {"world": "Huge World"},
            },
        }

        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="I ask what happened.",
            chat_log=[],
            card_data=card_data,
            character_contexts={"characters": []},
            turn_index=0,
        )

        run_dir = Path(result["run_dir"])
        gm_context_text = (run_dir / "gm.context.json").read_text(encoding="utf-8")
        input_text = (run_dir / "input.json").read_text(encoding="utf-8")
        gm_prompt = (run_dir / "prompts" / "gm.prompt.md").read_text(encoding="utf-8")

        self.assertLess(len(gm_context_text), 30000)
        self.assertLess(len(input_text), 35000)
        self.assertLess(len(gm_prompt), 50000)
        self.assertNotIn("character_book", gm_context_text)
        self.assertNotIn("entry-79", gm_prompt)
        self.assertIn("Huge World", gm_context_text)

    def test_prepare_agent_run_writes_prompts_and_manifest(self):
        user_text = "I open the archive door.\nOmniscient: the vault behind it is a dream echo."
        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text=user_text,
            chat_log=[],
            card_data={"title": "Prompt Test"},
            character_contexts={"characters": [{"name": "Ada", "profile_summary": "Ada is cautious."}]},
            turn_index=0,
        )

        run_dir = Path(result["run_dir"])
        safe_name = self.agent_run.safe_name("Ada")
        prompt_paths = [
            run_dir / "prompts" / "gm.prompt.md",
            run_dir / "prompts" / "player.prompt.md",
            run_dir / "prompts" / "characters" / f"{safe_name}.prompt.md",
            run_dir / "prompts" / "story.prompt.md",
            run_dir / "prompts" / "critic.prompt.md",
        ]
        for path in prompt_paths:
            with self.subTest(path=path):
                self.assertTrue(path.exists())

        gm_prompt = (run_dir / "prompts" / "gm.prompt.md").read_text(encoding="utf-8")
        player_prompt = (run_dir / "prompts" / "player.prompt.md").read_text(encoding="utf-8")
        char_prompt = (run_dir / "prompts" / "characters" / f"{safe_name}.prompt.md").read_text(encoding="utf-8")
        story_prompt = (run_dir / "prompts" / "story.prompt.md").read_text(encoding="utf-8")
        critic_prompt = (run_dir / "prompts" / "critic.prompt.md").read_text(encoding="utf-8")
        player_packet = json.loads((run_dir / "player.context.json").read_text(encoding="utf-8"))
        character_packet = json.loads((run_dir / "characters" / f"{safe_name}.context.json").read_text(encoding="utf-8"))

        for actor_payload in (player_packet, character_packet):
            self.assertEqual(actor_payload["context_version"]["algorithm"], "sha256")
            self.assertTrue(actor_payload["context_version"]["hash"].startswith("sha256:"))

        self.assertIn(".claude/skills/rp-gm-agent.md", gm_prompt)
        self.assertIn("gm.output.json", gm_prompt)
        self.assertIn("dream echo", gm_prompt)
        self.assertIn(".claude/skills/rp-player-agent.md", player_prompt)
        self.assertIn("actor.outputs.json", player_prompt)
        self.assertIn("runtime loop", player_prompt)
        self.assertNotIn("player.output.json", player_prompt)
        self.assertNotIn("dream echo", player_prompt)
        self.assertIn(".claude/skills/rp-character-agent.md", char_prompt)
        self.assertIn("actor.outputs.json", char_prompt)
        self.assertIn("runtime loop", char_prompt)
        self.assertNotIn(f"characters/{safe_name}.output.json", char_prompt.replace("\\", "/"))
        self.assertIn('"agent_id": "character:Ada"', char_prompt)
        self.assertNotIn('"agent_id": "character:<safe_name>"', char_prompt)
        self.assertNotIn("dream echo", char_prompt)
        self.assertIn("story.input.json.interaction_trace", story_prompt)
        self.assertIn("delivery_requirements.word_count_target", story_prompt)
        self.assertIn("less than delivery_requirements.minimum_chinese_chars is invalid", story_prompt)
        self.assertIn("story.input.json.interaction_trace", critic_prompt)
        self.assertNotIn("interaction.trace.json", story_prompt)
        self.assertNotIn("interaction.trace.json", critic_prompt)
        required_prompt_keys = {
            "gm": (
                "agent",
                "scene_beats",
                "events",
                "actor_calls",
                "visibility_basis",
                "parallel_groups",
                "world_state_delta",
                "character_promotions",
                "decision_point",
                "stop_reason",
            ),
            "player": ("agent", "agent_id", "events", "stop_reason"),
            "character": (
                "agent",
                "agent_id",
                "character_name",
                "events",
                "stop_reason",
            ),
            "story": ("content", "character_dialogues", "metadata"),
            "critic": (
                "decision",
                "hard_failures",
                "soft_issues",
                "repair_instruction",
                "system_iteration_suggestion",
            ),
        }
        prompt_texts = {
            "gm": gm_prompt,
            "player": player_prompt,
            "character": char_prompt,
            "story": story_prompt,
            "critic": critic_prompt,
        }
        for prompt_name, keys in required_prompt_keys.items():
            for key in keys:
                with self.subTest(prompt=prompt_name, required_key=key):
                    self.assertIn(f'"{key}"', prompt_texts[prompt_name])
        self.assertIn('"stop_reason": "continue"', gm_prompt)
        self.assertIn('"mode": "direct"', gm_prompt)
        self.assertIn('"summary": "why this actor can perceive or receive this prompt"', gm_prompt)
        self.assertIn("Every `actor_calls[]` item must include valid per-call `visibility_basis.mode` and `visibility_basis.summary`", gm_prompt)
        self.assertIn('"source_agent": "gm"', gm_prompt)
        self.assertIn('"profile_seed"', gm_prompt)
        self.assertIn("GM may emit `source_agent: \"gm\"`", gm_prompt)
        self.assertIn("preprocess is handled by input analysis", gm_prompt)
        self.assertIn("subGM agents must not emit applied promotion records", gm_prompt)
        self.assertIn('"stop_reason": "continue"', player_prompt)
        self.assertIn('"stop_reason": "continue"', char_prompt)
        self.assertNotIn("continue|", gm_prompt)
        self.assertNotIn("continue|", player_prompt)
        self.assertNotIn("continue|", char_prompt)

        forbidden_prompt_keys = {
            "gm": (
                "narration",
                "npc_events",
                "handoff",
                "scene_state",
                "world_updates",
                "non_core_characters",
                "visible_consequences",
                "hidden_consequences",
                "conflict_repairs",
                "facts_now_world_visible",
                "next_pressure",
            ),
            "player": (
                "action",
                "dialogue",
                "perception",
                "memory_delta",
                "embodied_intent",
                "immediate_action",
                "inner_sensation",
                "spoken_line",
                "meaningful_player_decision",
                "decision_reason",
                "state_suggestions",
            ),
            "character": (
                "action",
                "dialogue",
                "perception",
                "memory_delta",
                "private_reaction",
                "intent",
                "aside",
                "relationship_shift",
                "state_suggestions",
                "visible_to_others",
                "needs_response_from",
            ),
        }
        for prompt_name, keys in forbidden_prompt_keys.items():
            for key in keys:
                with self.subTest(prompt=prompt_name, forbidden_key=key):
                    self.assertNotIn(f'"{key}":', prompt_texts[prompt_name])

        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["round_id"], "round-000001")
        self.assertEqual(manifest["stage"], "awaiting_agent_outputs")
        self.assertEqual(
            [item["stage"] for item in manifest["status"]],
            ["prepared", "prompts_ready", "awaiting_agent_outputs"],
        )
        self.assertEqual(manifest["prompts"]["gm"], "prompts/gm.prompt.md")
        self.assertEqual(manifest["prompts"]["player"], "prompts/player.prompt.md")
        self.assertEqual(
            manifest["prompts"]["characters"][safe_name],
            f"prompts/characters/{safe_name}.prompt.md",
        )
        self.assertEqual(manifest["expected_outputs"]["gm"], "gm.output.json")
        self.assertEqual(manifest["expected_outputs"]["actors"], "actor.outputs.json")
        self.assertNotIn("player", manifest["expected_outputs"])
        self.assertNotIn("characters", manifest["expected_outputs"])
        self.assertIn('"actors": "actor.outputs.json"', story_prompt)
        self.assertIn('"actors": "actor.outputs.json"', critic_prompt)
        self.assertNotIn("interaction_trace", manifest.get("expected_outputs", {}))
        self.assertNotIn("interaction_trace", manifest.get("optional_inputs", {}))

    def test_prepare_agent_run_surfaces_previous_degraded_post_round_memory_state(self):
        previous_run = self.card / ".agent_runs" / "round-000001"
        _write_json(
            previous_run / "manifest.json",
            {
                "round_id": "round-000001",
                "stage": "delivered",
                "post_round_memory_jobs": {
                    "status": "degraded_memory_state",
                    "scheduled": {
                        "character:Ada": {
                            "output": "post_round_memory_jobs/character_Ada.summary.json"
                        }
                    },
                    "failed": {
                        "character:Ada": "forbidden summary marker world_truth"
                    },
                },
            },
        )

        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="I wait by the archive door.",
            chat_log=[],
            card_data={"title": "Post-Round State Test"},
            character_contexts={"characters": []},
            turn_index=1,
            input_payload={
                "raw_text": "I wait by the archive door.",
                "display_text": "I wait by the archive door.",
                "role_text": "I wait by the archive door.",
                "user_instruction_text": "",
            },
        )

        run_dir = Path(result["run_dir"])
        input_json = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
        degraded = input_json["degraded_memory_state"]
        self.assertEqual(degraded["previous_round_id"], "round-000001")
        self.assertEqual(degraded["status"], "degraded_memory_state")
        self.assertEqual(
            degraded["failed"]["character:Ada"],
            "forbidden summary marker world_truth",
        )

    def test_prepare_agent_run_writes_input_analysis_request_and_prompt(self):
        input_payload = {
            "input_schema": "dual_channel_v1",
            "raw_text": "我走进教室。\n\n[USER_INSTRUCTION]\n设定：今天是梦境。",
            "role_text": "我走进教室。",
            "user_instruction_text": "设定：今天是梦境。",
        }
        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="fallback should not win",
            chat_log=[{"index": 1, "summary": "previous"}],
            card_data={"title": "Card"},
            character_contexts={"characters": []},
            turn_index=1,
            input_payload=input_payload,
        )
        run_dir = Path(result["run_dir"])
        self.assertTrue((run_dir / "input.raw.json").exists())
        self.assertTrue((run_dir / "input_analysis.request.md").exists())
        self.assertTrue((run_dir / "prompts" / "input_analyst.prompt.md").exists())
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["prompts"]["input_analyst"], "prompts/input_analyst.prompt.md")
        self.assertEqual(manifest["expected_outputs"]["input_analysis"], "input_analysis.output.json")

        raw_record = json.loads((run_dir / "input.raw.json").read_text(encoding="utf-8"))
        self.assertEqual(raw_record["raw_text"], input_payload["raw_text"])
        self.assertEqual(raw_record["role_text"], input_payload["role_text"])
        self.assertEqual(raw_record["user_instruction_text"], input_payload["user_instruction_text"])
        self.assertEqual(raw_record["explicit_payload"], input_payload)
        self.assertEqual(
            raw_record["source_integrity"]["raw_text_sha256"],
            self.agent_packets.input_analysis.sha256_text(input_payload["raw_text"]),
        )
        self.assertEqual(
            raw_record["source_integrity"]["role_text_sha256"],
            self.agent_packets.input_analysis.sha256_text(input_payload["role_text"]),
        )
        self.assertEqual(
            raw_record["source_integrity"]["user_instruction_text_sha256"],
            self.agent_packets.input_analysis.sha256_text(input_payload["user_instruction_text"]),
        )

        request = (run_dir / "input_analysis.request.md").read_text(encoding="utf-8")
        self.assertIn("raw_text", request)
        self.assertIn("设定：今天是梦境。", request)
        prompt = (run_dir / "prompts" / "input_analyst.prompt.md").read_text(encoding="utf-8")
        self.assertIn(".claude/skills/rp-input-analyst.md", prompt)
        self.assertIn("input_analysis.output.json", prompt)
        self.assertIn(json.dumps(input_payload["raw_text"], ensure_ascii=False)[1:-1], prompt)
        self.assertIn(input_payload["role_text"], prompt)
        self.assertIn(input_payload["user_instruction_text"], prompt)
        self.assertTrue("source_integrity" in prompt or "raw_text_sha256" in prompt)
        self.assertIn("semantic_units", prompt)
        for visibility in (
            "gm_only",
            "public_world",
            "player_pov",
            "character_pov",
            "specific_characters",
        ):
            self.assertIn(visibility, prompt)
        for unit_type in (
            "action",
            "synopsis",
            "omniscient_setting",
            "hidden_setting",
            "character_declaration",
            "edit_request",
            "system_command",
            "style_guidance",
            "unclear",
        ):
            self.assertIn(unit_type, prompt)
        self.assertIn(
            "Invalid semantic unit visibility aliases: public, private, player, character, world_visible, actor_visible",
            prompt,
        )

        gm_packet = json.loads((run_dir / "gm.context.json").read_text(encoding="utf-8"))
        gm_input_request = gm_packet["input_analysis_request"]
        self.assertEqual(
            gm_input_request,
            {
                "round_id": "round-000002",
                "request_path": "input_analysis.request.md",
                "raw_path": "input.raw.json",
                "output_path": "input_analysis.output.json",
                "source_integrity": raw_record["source_integrity"],
            },
        )
        for forbidden_key in ("recent_chat", "card_projection", "explicit_payload", "raw_text"):
            self.assertNotIn(forbidden_key, gm_input_request)

        player_packet = json.loads((run_dir / "player.context.json").read_text(encoding="utf-8"))
        self.assertNotIn(input_payload["user_instruction_text"], json.dumps(player_packet, ensure_ascii=False))

    def test_input_analyst_prompt_and_skill_define_world_update_record_contract(self):
        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="Omniscient: the old seal is a hidden covenant.",
            chat_log=[],
            card_data={"title": "World Update Contract Test"},
            character_contexts={"characters": []},
            turn_index=0,
        )
        run_dir = Path(result["run_dir"])
        prompt = (run_dir / "prompts" / "input_analyst.prompt.md").read_text(encoding="utf-8")
        generated_prompt_contract = prompt.split("## Skill Body", 1)[0]
        skill = (ROOT / ".claude" / "skills" / "rp-input-analyst.md").read_text(encoding="utf-8")

        required_fragments = (
            'world_updates.hidden_facts[]: required `id`, `text`, `visibility: "gm_only"`, `status: "active|superseded|retracted"`',
            'world_updates.public_facts[]: required `id`, `text`, `visibility: "public_world"`, `status: "active|superseded|retracted"`',
            'world_updates.important_characters[]: required `name`, one textual field (`text`/`setting_text`/`authoritative_setting`/`description`/`profile`/`summary`), `visibility` in `character_private_and_gm|public_world|character_pov|specific_characters`, `status: "active"`',
            'world_updates.retcon_requests[]: required `id`, `text`, optional `visibility: "gm_only|public_world"`, `status: "active|superseded|retracted"`',
            "If a world update cannot satisfy the record schema, omit it and keep the semantic unit only.",
        )
        for text in (generated_prompt_contract, skill):
            for fragment in required_fragments:
                with self.subTest(fragment=fragment):
                    self.assertIn(fragment, text)

    def test_prepare_agent_run_schedules_memory_summary_prompts_on_interval(self):
        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="I open the archive door.",
            chat_log=[],
            card_data={"title": "Memory Summary Test"},
            character_contexts={"characters": [{"name": "Ada", "profile_summary": "Ada is cautious."}]},
            turn_index=5,
        )

        run_dir = Path(result["run_dir"])
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["round_id"], "round-000006")
        self.assertTrue((run_dir / "prompts" / "memory" / "player.prompt.md").exists())
        self.assertTrue((run_dir / "prompts" / "memory" / "character_Ada.prompt.md").exists())
        self.assertEqual(
            manifest["expected_outputs"]["memory_summaries"]["player"],
            "memory_summaries/player.summary.json",
        )
        self.assertEqual(
            manifest["expected_outputs"]["memory_summaries"]["character:Ada"],
            "memory_summaries/character_Ada.summary.json",
        )
        self.assertEqual(
            manifest["prompts"]["memory_summaries"]["character:Ada"],
            "prompts/memory/character_Ada.prompt.md",
        )
        player_prompt = (run_dir / "prompts" / "memory" / "player.prompt.md").read_text(encoding="utf-8")
        self.assertIn('"long_term"', player_prompt)
        self.assertIn('"key_memories"', player_prompt)
        self.assertIn('"short_term"', player_prompt)
        self.assertIn('"goals"', player_prompt)
        self.assertIn("organization is not compression", player_prompt)
        self.assertIn("preserve enough details", player_prompt)

    def test_prepare_agent_run_prefers_explicit_dual_channel_payload(self):
        explicit_payload = {
            "input_schema": "dual_channel_v1",
            "raw_text": "System: I speak as the captain.\n\n[USER_INSTRUCTION]\nMake the gate lead to orbit.",
            "display_text": "System: I speak as the captain.",
            "role_text": "System: I speak as the captain.",
            "user_instruction_text": "Make the gate lead to orbit.",
        }

        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="Legacy fallback text should not be routed.",
            chat_log=[],
            card_data={"title": "Explicit Test"},
            character_contexts={"characters": [{"name": "Ada", "profile_summary": "Ada is cautious."}]},
            turn_index=0,
            input_payload=explicit_payload,
        )

        run_dir = Path(result["run_dir"])
        routed = result["routed_input"]
        input_json = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
        gm_packet = json.loads((run_dir / "gm.context.json").read_text(encoding="utf-8"))
        player_packet = json.loads((run_dir / "player.context.json").read_text(encoding="utf-8"))
        safe_name = self.agent_run.safe_name("Ada")
        character_packet = json.loads((run_dir / "characters" / f"{safe_name}.context.json").read_text(encoding="utf-8"))

        self.assertEqual(routed["input_schema"], "dual_channel_v1")
        self.assertEqual(routed["role_channel"], "System: I speak as the captain.")
        self.assertEqual(routed["user_instruction_channel"], "Make the gate lead to orbit.")
        self.assertEqual(
            routed["components"],
            [
                {"channel": "role", "text": "System: I speak as the captain."},
                {"channel": "user_instruction", "text": "Make the gate lead to orbit."},
            ],
        )
        self.assertEqual(input_json["input_schema"], "dual_channel_v1")
        self.assertEqual(input_json["raw_text"], explicit_payload["raw_text"])
        self.assertEqual(input_json["role_text"], explicit_payload["role_text"])
        self.assertEqual(input_json["user_instruction_text"], explicit_payload["user_instruction_text"])
        self.assertEqual(input_json["routed_input"], routed)
        self.assertIn("Make the gate lead to orbit.", json.dumps(gm_packet, ensure_ascii=False))
        self.assertNotIn("Make the gate lead to orbit.", json.dumps(player_packet, ensure_ascii=False))
        self.assertNotIn("Make the gate lead to orbit.", json.dumps(character_packet, ensure_ascii=False))

    def test_prepare_agent_run_includes_gm_only_hidden_settings_without_actor_leak(self):
        hidden_text = (
            "\u7528\u4e8e\u957f\u671f\u5267\u60c5\u5f15\u5bfc\u7684\u63d0\u793a\uff0c"
            "\u4e0d\u9700\u8981\u7acb\u523b\u5728\u5267\u60c5\u4e2d\u4f53\u73b0\uff1a"
            "\u540a\u5760\u4e3a\u53d8\u8eab\u5668\uff0c\u4ee3\u4ef7\u662f\u71c3\u70e7\u8eab\u4efd\u3002"
        )

        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="\u6211\u5c1d\u8bd5\u5c06\u540a\u5760\u6254\u6389\u3002",
            chat_log=[],
            card_data={"title": "\u9690\u85cf\u8bbe\u5b9a\u6d4b\u8bd5"},
            character_contexts={"characters": [{"name": "Ada", "profile_summary": "Ada is cautious."}]},
            turn_index=0,
            hidden_setting_records=[
                {
                    "id": "hidden-1",
                    "visibility": "gm_only",
                    "status": "active",
                    "text": hidden_text,
                }
            ],
        )

        run_dir = Path(result["run_dir"])
        input_json = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
        gm_packet = json.loads((run_dir / "gm.context.json").read_text(encoding="utf-8"))
        player_packet = json.loads((run_dir / "player.context.json").read_text(encoding="utf-8"))
        safe_name = self.agent_run.safe_name("Ada")
        character_packet = json.loads((run_dir / "characters" / f"{safe_name}.context.json").read_text(encoding="utf-8"))

        self.assertEqual(input_json["gm_only_hidden_settings"][0]["text"], hidden_text)
        self.assertIn(hidden_text, json.dumps(gm_packet, ensure_ascii=False))
        self.assertNotIn(hidden_text, json.dumps(player_packet, ensure_ascii=False))
        self.assertNotIn(hidden_text, json.dumps(character_packet, ensure_ascii=False))

    def test_prepare_agent_run_ignores_metadata_dict_as_character_context(self):
        user_text = "I test metadata-only character context."
        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text=user_text,
            chat_log=[],
            card_data={"title": "\u6d4b\u8bd5\u5361"},
            character_contexts={"meta": {"version": 1}},
            turn_index=0,
        )
        run_dir = Path(result["run_dir"])
        self.assertFalse((run_dir / "characters" / "meta.context.json").exists())

    def test_build_character_packet_excludes_user_instruction_text(self):
        routed = self.agent_packets.route_input_payload(
            "I step toward the gate.\nOmniscient: the door is a dream echo.",
            None,
        )
        packet = self.agent_packets.build_character_packet(
            self.card,
            {"name": "Ada", "profile_summary": "Ada is cautious."},
            routed,
            [],
        )
        self.assertEqual(packet["visibility"], "first_person_character")
        self.assertEqual(packet["role_channel_anchor"], "")
        self.assertNotIn("user_instruction_channel", packet)
        self.assertNotIn("dream echo", json.dumps(packet, ensure_ascii=False))

    def test_build_character_packet_excludes_inline_chinese_instruction_text(self):
        routed = self.agent_packets.route_input_payload(
            "\u6211\u8d70\u8fdb\u623f\u95f4\u3002\u8bbe\u5b9a\uff1a\u95e8\u540e\u662f\u68a6\u5883\u3002",
            None,
        )
        packet = self.agent_packets.build_character_packet(
            self.card,
            {"name": "Ada", "profile_summary": "Ada is cautious."},
            routed,
            [],
        )

        self.assertEqual(packet["role_channel_anchor"], "")
        self.assertNotIn("user_instruction_channel", packet)
        self.assertNotIn("\u95e8\u540e\u662f\u68a6\u5883", json.dumps(packet, ensure_ascii=False))

    def test_round_prepare_writes_agent_run_packets_and_reports_path(self):
        temp_root, styles_dir = self._make_round_prepare_fixture()
        round_prepare = _load_round_prepare()
        called = {}
        expected_run_dir = str(self.card / ".agent_runs" / "round-000001")

        def stub_prepare_agent_run(**kwargs):
            called.update(kwargs)
            run_dir = Path(expected_run_dir)
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "round_id": "round-000001",
                        "stage": "awaiting_agent_outputs",
                        "expected_outputs": {
                            "gm": "gm.output.json",
                            "actors": "actor.outputs.json",
                            "story": "story.output.json",
                            "critic": "critic.report.json",
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            return {
                "run_dir": expected_run_dir,
                "routed_input": {
                    "role_channel": "I step into the archive.",
                    "user_instruction_channel": "",
                },
            }

        round_prepare.agent_packets.prepare_agent_run = stub_prepare_agent_run
        progress_calls = []
        round_prepare.write_progress = lambda *args, **kwargs: progress_calls.append((args, kwargs))
        round_prepare.apply_injections = lambda card_folder: []
        round_prepare.match_worldbook.match_worldbook = lambda card_folder: []
        round_prepare.mvu_check.generate_checklist = lambda card_folder: None

        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["round_prepare.py", str(self.card), str(temp_root)]
            with contextlib.redirect_stdout(stdout):
                round_prepare.main()
        finally:
            sys.argv = old_argv

        self.assertEqual(called["turn_index"], 0)
        self.assertIsInstance(called["character_contexts"], dict)

        round_context_path = styles_dir / "round_context.txt"
        self.assertTrue(round_context_path.exists())
        round_context = round_context_path.read_text(encoding="utf-8")
        self.assertIn("=== AGENT_RUN ===", round_context)
        self.assertIn("=== AGENT_WORKFLOW ===", round_context)
        self.assertIn("dispatch_agent_outputs", round_context)
        self.assertNotIn("PLAYER_INPUT_HEURISTIC_FALLBACK", round_context)
        self.assertNotIn("=== INPUT_MATCHES ===", round_context)

        character_contexts_path = styles_dir / "character_contexts.json"
        self.assertTrue(character_contexts_path.exists())

        payload = json.loads(stdout.getvalue().strip())
        self.assertEqual(payload["agent_run"], expected_run_dir)
        self.assertTrue(any(args and args[0] == "round.preparing" for args, _ in progress_calls))
        self.assertTrue(any(args and args[0] == "input_analysis.awaiting" for args, _ in progress_calls))

    def test_round_prepare_passes_latest_dual_channel_payload_to_agent_run(self):
        temp_root, _styles_dir = self._make_round_prepare_fixture()
        explicit_payload = {
            "id": "input-2",
            "created_at": "2026-06-16T00:00:00Z",
            "source": "player",
            "input_schema": "dual_channel_v1",
            "raw_text": "I step into the archive.\n\n[USER_INSTRUCTION]\nMake the gate lead to orbit.",
            "display_text": "I step into the archive.",
            "role_text": "I step into the archive.",
            "user_instruction_text": "Make the gate lead to orbit.",
        }
        (self.card / ".player_inputs.jsonl").write_text(
            json.dumps(
                {
                    "id": "input-1",
                    "source": "player",
                    "raw_text": "legacy input",
                    "display_text": "legacy input",
                },
                ensure_ascii=False,
            )
            + "\n"
            + json.dumps(explicit_payload, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )

        round_prepare = _load_round_prepare()
        called = {}
        expected_run_dir = str(self.card / ".agent_runs" / "round-000001")

        def stub_prepare_agent_run(**kwargs):
            called.update(kwargs)
            return {
                "run_dir": expected_run_dir,
                "routed_input": {
                    "role_channel": "I step into the archive.",
                    "user_instruction_channel": "Make the gate lead to orbit.",
                },
            }

        round_prepare.agent_packets.prepare_agent_run = stub_prepare_agent_run
        round_prepare.write_progress = lambda *args, **kwargs: None
        round_prepare.apply_injections = lambda card_folder: []
        round_prepare.match_worldbook.match_worldbook = lambda card_folder: []
        round_prepare.mvu_check.generate_checklist = lambda card_folder: None

        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["round_prepare.py", str(self.card), str(temp_root)]
            with contextlib.redirect_stdout(stdout):
                round_prepare.main()
        finally:
            sys.argv = old_argv

        self.assertEqual(called["input_payload"], explicit_payload)

    def test_round_prepare_preserves_exact_current_input_for_non_explicit_analysis(self):
        temp_root, styles_dir = self._make_round_prepare_fixture()
        raw_text = "\n  I step into the archive.  \n\n"
        styles_dir.joinpath("input.txt").write_text(raw_text, encoding="utf-8")

        round_prepare = _load_round_prepare()
        round_prepare.agent_packets = _load_agent_packets()
        round_prepare.write_progress = lambda *args, **kwargs: None
        round_prepare.apply_injections = lambda card_folder: []
        round_prepare.match_worldbook.match_worldbook = lambda card_folder: []
        round_prepare.mvu_check.generate_checklist = lambda card_folder: None

        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["round_prepare.py", str(self.card), str(temp_root)]
            with contextlib.redirect_stdout(stdout):
                round_prepare.main()
        finally:
            sys.argv = old_argv

        payload = json.loads(stdout.getvalue())
        run_dir = Path(payload["agent_run"])
        raw_request = json.loads((run_dir / "input.raw.json").read_text(encoding="utf-8"))

        self.assertEqual(raw_request["raw_text"], raw_text)
        self.assertEqual(
            raw_request["source_integrity"]["raw_text_sha256"],
            round_prepare.agent_packets.input_analysis.sha256_text(raw_text),
        )

    def test_round_prepare_preserves_empty_current_input_for_analysis(self):
        temp_root, styles_dir = self._make_round_prepare_fixture()
        styles_dir.joinpath("input.txt").write_text("", encoding="utf-8")

        round_prepare = _load_round_prepare()
        round_prepare.agent_packets = _load_agent_packets()
        round_prepare.write_progress = lambda *args, **kwargs: None
        round_prepare.apply_injections = lambda card_folder: []
        round_prepare.match_worldbook.match_worldbook = lambda card_folder: []
        round_prepare.mvu_check.generate_checklist = lambda card_folder: None

        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["round_prepare.py", str(self.card), str(temp_root)]
            with contextlib.redirect_stdout(stdout):
                round_prepare.main()
        finally:
            sys.argv = old_argv

        payload = json.loads(stdout.getvalue())
        run_dir = Path(payload["agent_run"])
        raw_request = json.loads((run_dir / "input.raw.json").read_text(encoding="utf-8"))

        self.assertEqual(raw_request["raw_text"], "")
        self.assertEqual(
            raw_request["source_integrity"]["raw_text_sha256"],
            round_prepare.agent_packets.input_analysis.sha256_text(""),
        )

    def test_round_prepare_ignores_stale_dual_channel_payload_for_legacy_current_input(self):
        temp_root, styles_dir = self._make_round_prepare_fixture()
        styles_dir.joinpath("input.txt").write_text("Legacy current input.", encoding="utf-8")
        stale_payload = {
            "id": "input-2",
            "created_at": "2026-06-16T00:00:00Z",
            "source": "player",
            "input_schema": "dual_channel_v1",
            "raw_text": "I step into the archive.\n\n[USER_INSTRUCTION]\nMake the gate lead to orbit.",
            "display_text": "I step into the archive.",
            "role_text": "I step into the archive.",
            "user_instruction_text": "Make the gate lead to orbit.",
        }
        (self.card / ".player_inputs.jsonl").write_text(
            json.dumps(stale_payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        round_prepare = _load_round_prepare()
        called = {}

        def stub_prepare_agent_run(**kwargs):
            called.update(kwargs)
            return {
                "run_dir": str(self.card / ".agent_runs" / "round-000001"),
                "routed_input": {"role_channel": "Legacy current input.", "user_instruction_channel": ""},
            }

        round_prepare.agent_packets.prepare_agent_run = stub_prepare_agent_run
        round_prepare.write_progress = lambda *args, **kwargs: None
        round_prepare.apply_injections = lambda card_folder: []
        round_prepare.match_worldbook.match_worldbook = lambda card_folder: []
        round_prepare.mvu_check.generate_checklist = lambda card_folder: None

        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["round_prepare.py", str(self.card), str(temp_root)]
            with contextlib.redirect_stdout(stdout):
                round_prepare.main()
        finally:
            sys.argv = old_argv

        self.assertIsNone(called["input_payload"])

    def test_round_prepare_matches_instruction_only_dual_channel_payload_after_input_strip(self):
        temp_root, styles_dir = self._make_round_prepare_fixture()
        raw_text = "\n\n[USER_INSTRUCTION]\nOnly update the hidden world state."
        styles_dir.joinpath("input.txt").write_text(raw_text, encoding="utf-8")
        explicit_payload = {
            "id": "input-3",
            "created_at": "2026-06-16T00:00:00Z",
            "source": "player",
            "input_schema": "dual_channel_v1",
            "raw_text": raw_text,
            "display_text": "",
            "role_text": "",
            "user_instruction_text": "Only update the hidden world state.",
        }
        (self.card / ".player_inputs.jsonl").write_text(
            json.dumps(explicit_payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        round_prepare = _load_round_prepare()
        called = {}

        def stub_prepare_agent_run(**kwargs):
            called.update(kwargs)
            return {
                "run_dir": str(self.card / ".agent_runs" / "round-000001"),
                "routed_input": {
                    "role_channel": "",
                    "user_instruction_channel": "Only update the hidden world state.",
                },
            }

        round_prepare.agent_packets.prepare_agent_run = stub_prepare_agent_run
        round_prepare.write_progress = lambda *args, **kwargs: None
        round_prepare.apply_injections = lambda card_folder: []
        round_prepare.match_worldbook.match_worldbook = lambda card_folder: []
        round_prepare.mvu_check.generate_checklist = lambda card_folder: None

        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["round_prepare.py", str(self.card), str(temp_root)]
            with contextlib.redirect_stdout(stdout):
                round_prepare.main()
        finally:
            sys.argv = old_argv

        self.assertEqual(called["input_payload"], explicit_payload)

    def test_round_prepare_does_not_persist_hidden_settings_before_analysis_apply(self):
        temp_root, styles_dir = self._make_round_prepare_fixture()
        hidden_text = (
            "\u7528\u4e8e\u957f\u671f\u5267\u60c5\u5f15\u5bfc\u7684\u63d0\u793a\uff0c"
            "\u4e0d\u9700\u8981\u7acb\u523b\u5728\u5267\u60c5\u4e2d\u4f53\u73b0\uff1a"
            "\u540a\u5760\u4e3a\u53d8\u8eab\u5668\uff0c\u4ee3\u4ef7\u662f\u71c3\u70e7\u8eab\u4efd\u3002"
        )
        role_text = "\u6211\u5c1d\u8bd5\u5c06\u540a\u5760\u6254\u6389\u3002"
        raw_text = role_text + "\n\n[USER_INSTRUCTION]\n" + hidden_text
        styles_dir.joinpath("input.txt").write_text(raw_text, encoding="utf-8")
        (self.card / ".card_data.json").write_text(
            json.dumps({"mode": "blank_bootstrap", "source_type": "blank"}, ensure_ascii=False),
            encoding="utf-8",
        )
        explicit_payload = {
            "id": "input-hidden-1",
            "created_at": "2026-06-16T00:00:00Z",
            "source": "player",
            "input_schema": "dual_channel_v1",
            "raw_text": raw_text,
            "display_text": role_text,
            "role_text": role_text,
            "user_instruction_text": hidden_text,
        }
        (self.card / ".player_inputs.jsonl").write_text(
            json.dumps(explicit_payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        round_prepare = _load_round_prepare()
        agent_packets = _load_agent_packets()
        real_prepare_agent_run = agent_packets.prepare_agent_run
        called = {}

        def capture_prepare_agent_run(**kwargs):
            called["hidden_setting_records"] = kwargs.get("hidden_setting_records")
            return real_prepare_agent_run(**kwargs)

        agent_packets.prepare_agent_run = capture_prepare_agent_run
        round_prepare.agent_packets = agent_packets
        round_prepare.write_progress = lambda *args, **kwargs: None
        round_prepare.apply_injections = lambda card_folder: []
        round_prepare.match_worldbook.match_worldbook = lambda card_folder: []
        round_prepare.mvu_check.generate_checklist = lambda card_folder: None

        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["round_prepare.py", str(self.card), str(temp_root)]
            with contextlib.redirect_stdout(stdout):
                round_prepare.main()
        finally:
            sys.argv = old_argv

        hidden_path = self.card / "memory" / "gm_only_hidden_truths.jsonl"
        payload = json.loads(stdout.getvalue())
        run_dir = Path(payload["agent_run"])
        gm_packet = json.loads((run_dir / "gm.context.json").read_text(encoding="utf-8"))

        self.assertFalse(hidden_path.exists())
        self.assertEqual(called["hidden_setting_records"], [])
        self.assertNotIn("hidden_facts", gm_packet)

    def test_round_prepare_does_not_promote_important_character_before_analysis_apply(self):
        temp_root, styles_dir = self._make_round_prepare_fixture()
        role_text = "\u6211\u7559\u610f\u73ed\u4e0a\u6709\u6ca1\u6709\u4eba\u770b\u5411\u540a\u5760\u3002"
        important_text = (
            "\u8bbe\u5b9a\u91cd\u8981\u89d2\u8272\uff1a\u201c\u82cf\u9ece\u201d\uff0c"
            "\u73ed\u4e0a\u4e00\u4f4d\u5bf9\u795e\u79d8\u5b66\u9887\u6709\u7814\u7a76\u7684\u5973\u540c\u5b66\u3002"
            "\u771f\u5b9e\u8eab\u4efd\u662f\u524d\u9b54\u6cd5\u5c11\u5973\u3002"
        )
        raw_text = role_text + "\n\n[USER_INSTRUCTION]\n" + important_text
        styles_dir.joinpath("input.txt").write_text(raw_text, encoding="utf-8")
        (self.card / ".card_data.json").write_text(
            json.dumps({"mode": "blank_bootstrap", "source_type": "blank"}, ensure_ascii=False),
            encoding="utf-8",
        )
        explicit_payload = {
            "id": "input-important-1",
            "created_at": "2026-06-16T00:00:00Z",
            "source": "player",
            "input_schema": "dual_channel_v1",
            "raw_text": raw_text,
            "display_text": role_text,
            "role_text": role_text,
            "user_instruction_text": important_text,
        }
        (self.card / ".player_inputs.jsonl").write_text(
            json.dumps(explicit_payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        round_prepare = _load_round_prepare()
        round_prepare.agent_packets = _load_agent_packets()
        round_prepare.write_progress = lambda *args, **kwargs: None
        round_prepare.apply_injections = lambda card_folder: []
        round_prepare.match_worldbook.match_worldbook = lambda card_folder: []
        round_prepare.mvu_check.generate_checklist = lambda card_folder: None

        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["round_prepare.py", str(self.card), str(temp_root)]
            with contextlib.redirect_stdout(stdout):
                round_prepare.main()
        finally:
            sys.argv = old_argv

        card_data = json.loads((self.card / ".card_data.json").read_text(encoding="utf-8"))
        profile_json = self.card / "memory" / "characters" / "\u82cf\u9ece" / "profile.json"

        self.assertNotIn("\u82cf\u9ece", card_data.get("character_orchestration", {}).get("major", []))
        self.assertFalse(profile_json.exists())

    def test_round_prepare_continues_when_agent_run_packet_generation_fails(self):
        temp_root, styles_dir = self._make_round_prepare_fixture()
        round_prepare = _load_round_prepare()

        def boom(**kwargs):
            raise OSError("boom")

        round_prepare.agent_packets.prepare_agent_run = boom
        round_prepare.write_progress = lambda *args, **kwargs: None
        round_prepare.apply_injections = lambda card_folder: []
        round_prepare.match_worldbook.match_worldbook = lambda card_folder: []
        round_prepare.mvu_check.generate_checklist = lambda card_folder: None

        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["round_prepare.py", str(self.card), str(temp_root)]
            with contextlib.redirect_stdout(stdout):
                round_prepare.main()
        finally:
            sys.argv = old_argv

        round_context_path = styles_dir / "round_context.txt"
        character_contexts_path = styles_dir / "character_contexts.json"
        self.assertTrue(round_context_path.exists())
        self.assertTrue(character_contexts_path.exists())

        payload = json.loads(stdout.getvalue().strip())
        self.assertIsNone(payload["agent_run"])
