# Agent Dispatcher Workflow Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed workflow next-action path with a dispatcher-first runtime where pending intents drive live `/rp` execution and `artifacts/` is the authoritative artifact store.

**Architecture:** Add `skills/agent_dispatcher.py` as the central intent executor. `round_prepare.py` creates the initial runtime message and `analyze_input` intent, `rp_generate_cli.py` repeatedly calls the dispatcher, and `agent_outputs.py` reads/writes `artifacts/` as authoritative state. `agent_workflow.py` is removed from live next-action decisions.

**Tech Stack:** Python standard library, JSON/JSONL file protocols, existing `unittest` suite, existing `.agent_runs/<round>/` runtime folders, existing Claude Code subprocess path in `rp_generate_cli.py`.

---

## Scope Check

This plan implements one migration: workflow next-action replacement by dispatcher-first intent execution. It intentionally includes artifact authority and live driver switching because the approved spec selected the aggressive path. It intentionally excludes MCP wrappers and image/UI tool packaging.

The implementation should still proceed in small commits. If a task uncovers a broad hidden dependency, stop at the task boundary, keep the current tests passing, and add a narrow follow-up task instead of expanding unrelated code.

## File Structure

- Create `skills/agent_dispatcher.py`: dispatcher result shape, pending intent selection, stalled runtime blocking, artifact helpers, and intent executors.
- Create `tests/test_agent_dispatcher.py`: deterministic tests for dispatcher transitions, follow-up intents, artifact authority, unsupported intents, and stalled runtime.
- Modify `skills/round_prepare.py`: remove `agent_workflow` import/advice and create the initial `input_received` message plus `analyze_input` intent.
- Modify `tests/test_agent_packets.py`: assert initial runtime messages/intents exist and remove workflow advice assertions.
- Modify `skills/agent_outputs.py`: make `artifacts/` authoritative, stop root fallback reads, and keep root export only where delivery still requires it.
- Modify `tests/test_agent_outputs.py`: move fixture artifacts under `artifacts/`, add conflict tests proving root files are ignored.
- Modify `skills/rp_generate_cli.py`: replace the hard-coded run loop with dispatcher driving; keep reusable Claude dispatch helpers for dispatcher executors.
- Modify `tests/test_rp_generate_cli.py`: prove `run_round()` calls dispatcher and does not call workflow advice.
- Modify `skills/control_plane_smoke.py`: drive the deterministic smoke through dispatcher evidence.
- Modify `tests/test_control_plane_smoke.py`: assert completed intent chain and no workflow next-action evidence.
- Delete or rewrite `skills/agent_workflow.py` and `tests/test_agent_workflow.py`: remove executable next-action advice from the codebase.
- Modify `README.md`, `CLAUDE.md`, and `AGENTS.md`: document dispatcher-first runtime and `artifacts/` authority.

---

### Task 1: Dispatcher Foundation

**Files:**
- Create: `skills/agent_dispatcher.py`
- Create: `tests/test_agent_dispatcher.py`

- [ ] **Step 1: Write failing tests for pending selection, unsupported blocking, and stalled runtime**

Create `tests/test_agent_dispatcher.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher -v
```

Expected: FAIL with an import error for missing `skills/agent_dispatcher.py`.

- [ ] **Step 3: Implement the dispatcher foundation**

Create `skills/agent_dispatcher.py`:

```python
"""Intent dispatcher for the per-round agent runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import agent_intents
import agent_run


SUPPORTED_INTENT_TYPES = {
    "analyze_input",
    "run_gm_turn",
    "request_projection",
    "run_actor",
    "run_subgm_thread",
    "compose_story",
    "review_critic",
    "repair_request",
    "rollback_request",
    "deliver_round",
}


class AgentDispatcherError(RuntimeError):
    """Raised when dispatcher execution cannot continue safely."""


def dispatch_next(
    run_dir: str | Path,
    card_folder: str | Path,
    root_dir: str | Path,
    *,
    run_claude: Callable[[str, str, str | Path], str] | None = None,
    run_command: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Execute one pending intent or report a terminal dispatcher state."""

    root = Path(run_dir)
    manifest = _load_manifest(root)
    if manifest.get("stage") == "delivered":
        return _result(True, "delivered", reason="", artifacts=[], created_intents=[], created_messages=[])

    pending = agent_intents.list_intents(root, "pending")
    if not pending:
        return _block_stalled(root, manifest)

    intent = pending[0]
    intent_type = str(intent.get("type") or "")
    intent_id = str(intent.get("id") or "")
    if intent_type not in SUPPORTED_INTENT_TYPES:
        blocked = agent_intents.block_intent(
            root,
            intent_id,
            "unsupported_intent_type",
            outputs={"intent_type": intent_type},
        )
        _mark_blocked(root, "unsupported_intent_type", {"intent_id": intent_id, "intent_type": intent_type})
        return _result(
            False,
            "blocked",
            intent_id=intent_id,
            intent_type=intent_type,
            reason="unsupported_intent_type",
            created_intents=[],
            created_messages=[],
            artifacts=[],
            detail=blocked.get("result", {}),
        )

    return _execute_supported_intent(root, Path(card_folder), Path(root_dir), intent, run_claude, run_command)


def artifact_path(run_dir: str | Path, relative_path: str) -> Path:
    """Return the authoritative artifact path for a run-relative artifact."""

    return Path(run_dir) / "artifacts" / relative_path


def write_artifact(run_dir: str | Path, relative_path: str, payload: dict[str, Any]) -> Path:
    path = artifact_path(run_dir, relative_path)
    agent_run.write_json(path, payload)
    return path


def read_artifact(run_dir: str | Path, relative_path: str) -> dict[str, Any]:
    path = artifact_path(run_dir, relative_path)
    data = agent_run.read_json(path)
    if not isinstance(data, dict):
        raise AgentDispatcherError(f"{path}: artifact JSON object is missing or invalid")
    return data


def _execute_supported_intent(
    run_dir: Path,
    card_folder: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str] | None,
    run_command: Callable[..., Any] | None,
) -> dict[str, Any]:
    intent_type = str(intent.get("type") or "")
    if intent_type == "assets_task":
        raise AgentDispatcherError("assets_task is not included in SUPPORTED_INTENT_TYPES")
    blocked = agent_intents.block_intent(
        run_dir,
        str(intent.get("id") or ""),
        "executor_not_wired",
        outputs={"intent_type": intent_type},
    )
    _mark_blocked(run_dir, "executor_not_wired", {"intent_id": intent.get("id"), "intent_type": intent_type})
    return _result(
        False,
        "blocked",
        intent_id=str(intent.get("id") or ""),
        intent_type=intent_type,
        reason="executor_not_wired",
        created_intents=[],
        created_messages=[],
        artifacts=[],
        detail=blocked.get("result", {}),
    )


def _block_stalled(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    _mark_blocked(run_dir, "dispatcher_stalled", {"pending_intents": 0})
    return _result(False, "stalled", reason="dispatcher_stalled", artifacts=[], created_intents=[], created_messages=[])


def _mark_blocked(run_dir: Path, reason: str, detail: dict[str, Any]) -> None:
    manifest = _load_manifest(run_dir)
    manifest["stage"] = "blocked"
    manifest["dispatcher"] = {"status": "blocked", "reason": reason, "detail": detail}
    history = manifest.setdefault("stage_history", [])
    if isinstance(history, list):
        history.append({"stage": "blocked", "reason": reason})
    agent_run.write_json(run_dir / "manifest.json", manifest)


def _load_manifest(run_dir: Path) -> dict[str, Any]:
    manifest = agent_run.read_json(run_dir / "manifest.json", {}) or {}
    if not isinstance(manifest, dict):
        raise AgentDispatcherError(f"{run_dir / 'manifest.json'}: manifest must be a JSON object")
    return manifest


def _result(
    ok: bool,
    status: str,
    *,
    intent_id: str = "",
    intent_type: str = "",
    reason: str = "",
    created_intents: list[str] | None = None,
    created_messages: list[str] | None = None,
    artifacts: list[str] | None = None,
    detail: Any = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": ok,
        "status": status,
        "intent_id": intent_id,
        "intent_type": intent_type,
        "reason": reason,
        "created_intents": list(created_intents or []),
        "created_messages": list(created_messages or []),
        "artifacts": list(artifacts or []),
    }
    if detail is not None:
        result["detail"] = detail
    return result
```

- [ ] **Step 4: Run dispatcher foundation tests**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher -v
python -m py_compile skills/agent_dispatcher.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add skills/agent_dispatcher.py tests/test_agent_dispatcher.py
git commit -m "feat: 增加agent dispatcher基础"
```

---

### Task 2: Round Preparation Creates Initial Intent

**Files:**
- Modify: `skills/round_prepare.py`
- Modify: `tests/test_agent_packets.py`

- [ ] **Step 1: Write failing test for initial message and analyze intent**

In `tests/test_agent_packets.py`, find the round prepare integration test that already asserts `.agent_runs/<round>/` files exist. Add these assertions after `run_dir` is known:

```python
        messages = [
            json.loads(line)
            for line in (run_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(any(item["type"] == "input_received" for item in messages))
        input_messages = [item for item in messages if item["type"] == "input_received"]
        self.assertEqual(input_messages[0]["from"], "main_agent")
        self.assertIn("input_analyst", input_messages[0]["to"])

        pending_intents = list((run_dir / "intents" / "pending").glob("intent_*.json"))
        self.assertTrue(pending_intents)
        intent_payloads = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in pending_intents
        ]
        analyze = [item for item in intent_payloads if item["type"] == "analyze_input"]
        self.assertEqual(len(analyze), 1)
        self.assertEqual(analyze[0]["requested_by"], "main_agent")
        self.assertEqual(analyze[0]["payload"]["input_path"], "input.json")
```

- [ ] **Step 2: Run targeted test to verify failure**

Run the existing test name that prepares a round. If the local method name differs, use `rg -n "round_prepare_writes|prepare_agent_run|snapshot" tests/test_agent_packets.py` to find it.

Expected: FAIL because `round_prepare.py` does not create an `analyze_input` intent yet.

- [ ] **Step 3: Modify imports in `round_prepare.py`**

Replace:

```python
import agent_workflow
```

with:

```python
import agent_intents
import agent_messages
```

- [ ] **Step 4: Add initial runtime helper to `round_prepare.py`**

Add this helper near the other private helpers:

```python
def _initialize_dispatcher_runtime(run_dir):
    """Create the first message and intent for dispatcher-first execution."""

    message_result = agent_messages.append_message(
        run_dir,
        {
            "from": "main_agent",
            "to": ["input_analyst", "gm"],
            "type": "input_received",
            "visibility": "gm_only",
            "payload": {
                "input_path": "input.json",
                "input_raw_path": "input.raw.json",
            },
        },
    )
    if not message_result.get("ok"):
        raise RuntimeError(f"failed to append input_received message: {message_result}")

    message_id = (message_result.get("message") or {}).get("id", "")
    intent_result = agent_intents.create_intent(
        run_dir,
        {
            "requested_by": "main_agent",
            "type": "analyze_input",
            "source_message_id": message_id,
            "payload": {
                "input_path": "input.json",
                "input_analysis_request_path": "input_analysis.request.md",
            },
            "policy": {"source": "round_prepare"},
        },
    )
    if not intent_result.get("ok"):
        raise RuntimeError(f"failed to create analyze_input intent: {intent_result}")
    return {
        "message": message_result.get("message", {}),
        "intent": intent_result.get("intent", {}),
    }
```

- [ ] **Step 5: Call the helper after `prepare_agent_run()`**

In `round_prepare.py`, after `agent_packets.prepare_agent_run(...)` returns `agent_run_info`, derive `run_dir` as the existing code already does and call:

```python
        dispatcher_runtime = _initialize_dispatcher_runtime(run_dir)
```

Add the helper result to the JSON payload printed by `round_prepare.py`:

```python
        "dispatcher_runtime": dispatcher_runtime,
```

Remove the block that computes and prints `agent_workflow_advice`. Also remove any `AGENT_WORKFLOW_ADVICE` section from `round_context.txt`.

- [ ] **Step 6: Run targeted tests**

Run:

```powershell
python -m unittest tests.test_agent_packets -v
python -m py_compile skills/round_prepare.py
```

Expected: PASS. If broad `test_agent_packets` exposes old workflow advice assertions, update those assertions to check `dispatcher_runtime.intent.type == "analyze_input"` instead.

- [ ] **Step 7: Commit**

```powershell
git add skills/round_prepare.py tests/test_agent_packets.py
git commit -m "feat: 回合准备创建dispatcher初始意图"
```

---

### Task 3: Artifact Authority Helpers

**Files:**
- Modify: `skills/agent_outputs.py`
- Modify: `tests/test_agent_outputs.py`

- [ ] **Step 1: Add failing test that root artifact conflicts are ignored**

In `tests/test_agent_outputs.py`, add a test near the existing artifact layout tests:

```python
    def test_build_story_input_uses_artifacts_when_root_artifacts_conflict(self):
        artifacts = self.run_dir / "artifacts"
        artifacts.mkdir(exist_ok=True)
        (self.run_dir / "gm.output.json").write_text(
            json.dumps({"agent": "wrong", "outputs": []}, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.run_dir / "actor.outputs.json").write_text(
            json.dumps({"actor_outputs": {"player": []}}, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.run_dir / "gm.output.json").replace(artifacts / "gm.output.json")
        (self.run_dir / "actor.outputs.json").replace(artifacts / "actor.outputs.json")
        _write_json(self.run_dir / "gm.output.json", {"agent": "wrong", "outputs": []})
        _write_json(self.run_dir / "actor.outputs.json", {"actor_outputs": {"player": []}})

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(story_input["loop_outputs"]["gm"]["agent"], "gm_loop")
        self.assertTrue((artifacts / "story.input.json").exists())
        self.assertFalse((self.run_dir / "story.input.json").exists())
```

Adjust the setup lines to use the test class's existing fixture writers if they already write valid artifacts. The root files in this test must deliberately contain invalid/conflicting content.

- [ ] **Step 2: Run targeted test to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_outputs.AgentOutputsTest.test_build_story_input_uses_artifacts_when_root_artifacts_conflict -v
```

Expected: FAIL because `_artifact_path()` currently prefers root paths.

- [ ] **Step 3: Replace artifact path helpers**

In `skills/agent_outputs.py`, replace `_artifact_path()` and `_write_artifact_with_legacy_mirror()` with:

```python
def _artifact_path(root: Path, relative_path: str) -> Path:
    return root / "artifacts" / relative_path


def _write_artifact(root: Path, relative_path: str, payload: Dict[str, Any]) -> None:
    agent_run.write_json(root / "artifacts" / relative_path, payload)


def export_delivery_artifact(root: Path, relative_path: str) -> Path:
    """Export an authoritative artifact to the run root for a delivery boundary."""

    source = _artifact_path(root, relative_path)
    target = root / relative_path
    if not source.exists():
        raise AgentOutputError(f"{source.as_posix()}: authoritative artifact is missing")
    data = _read_json_required(source)
    agent_run.write_json(target, data)
    return target
```

Then replace:

```python
    _write_artifact_with_legacy_mirror(root, "story.input.json", story_input)
```

with:

```python
    _write_artifact(root, "story.input.json", story_input)
```

- [ ] **Step 4: Stop requiring `manifest.expected_outputs` for story input assembly**

In `build_story_input()`, replace:

```python
    _expected_outputs(manifest)
```

with:

```python
    manifest.setdefault("artifacts", {})
```

Keep `_expected_outputs()` for old tests only until the workflow cleanup task removes it. Do not use it to decide paths in new code.

- [ ] **Step 5: Update delivery artifact paths**

In `prepare_delivery()`, replace:

```python
    expected = _expected_outputs(manifest)
    story_path = _artifact_path(run_dir, expected.get("story", "story.output.json"))
    critic_path = _artifact_path(run_dir, expected.get("critic", "critic.report.json"))
```

with:

```python
    story_path = _artifact_path(run_dir, "story.output.json")
    critic_path = _artifact_path(run_dir, "critic.report.json")
```

Before writing `response.txt`, export story and critic artifacts for any boundary code that still inspects root files:

```python
    export_delivery_artifact(run_dir, "story.output.json")
    export_delivery_artifact(run_dir, "critic.report.json")
```

- [ ] **Step 6: Run agent output tests**

Run:

```powershell
python -m unittest tests.test_agent_outputs -v
python -m py_compile skills/agent_outputs.py
```

Expected: PASS. Update fixture writes from `self.run_dir / "gm.output.json"` to `self.run_dir / "artifacts" / "gm.output.json"` where the tested behavior is not specifically about root export.

- [ ] **Step 7: Commit**

```powershell
git add skills/agent_outputs.py tests/test_agent_outputs.py
git commit -m "feat: 使用artifacts作为agent产物权威路径"
```

---

### Task 4: Dispatcher Input Analysis Executor

**Files:**
- Modify: `skills/agent_dispatcher.py`
- Modify: `tests/test_agent_dispatcher.py`

- [ ] **Step 1: Write failing test for `analyze_input`**

Append to `tests/test_agent_dispatcher.py`:

```python
    def test_analyze_input_completes_and_creates_run_gm_turn(self):
        _write_json(
            self.run_dir / "input_analysis.output.json",
            {
                "analysis_mode": "fixture",
                "routing": {"send_to_gm": True},
                "applied_updates": [],
            },
        )
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "main_agent", "type": "analyze_input", "payload": {"input_path": "input.json"}},
        )["intent"]

        calls = []

        def fake_apply(card, root):
            calls.append((Path(card), Path(root)))
            return {"ok": True, "analysis": {"analysis_mode": "fixture"}}

        self.dispatcher.input_analysis_apply.apply_current_run = fake_apply
        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["intent_id"], created["id"])
        self.assertTrue((self.run_dir / "artifacts" / "input_analysis.output.json").exists())
        completed = self.intents.list_intents(self.run_dir, "completed")
        self.assertEqual([item["id"] for item in completed], [created["id"]])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_gm_turn"])
        self.assertEqual(len(calls), 1)
```

- [ ] **Step 2: Run targeted test to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher.AgentDispatcherFoundationTest.test_analyze_input_completes_and_creates_run_gm_turn -v
```

Expected: FAIL because `analyze_input` is blocked as `executor_not_wired`.

- [ ] **Step 3: Import dependencies**

At the top of `skills/agent_dispatcher.py`, add:

```python
import shutil

import agent_messages
import input_analysis_apply
```

- [ ] **Step 4: Add follow-up intent helper**

Add:

```python
def _intent_exists(run_dir: Path, intent_type: str, source_intent_id: str) -> bool:
    for state in agent_intents.VALID_STATES:
        for intent in agent_intents.list_intents(run_dir, state):
            if intent.get("type") == intent_type and (intent.get("policy") or {}).get("source_intent_id") == source_intent_id:
                return True
    return False


def _create_followup_once(
    run_dir: Path,
    *,
    requested_by: str,
    intent_type: str,
    source_intent_id: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    if _intent_exists(run_dir, intent_type, source_intent_id):
        return None
    result = agent_intents.create_intent(
        run_dir,
        {
            "requested_by": requested_by,
            "type": intent_type,
            "payload": payload,
            "policy": {"source_intent_id": source_intent_id},
        },
    )
    return result.get("intent", {})
```

- [ ] **Step 5: Add analyze executor**

Add:

```python
def _execute_analyze_input(run_dir: Path, card_folder: Path, root_dir: Path, intent: dict[str, Any]) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "analyze_input"})
    applied = input_analysis_apply.apply_current_run(card_folder, root_dir)
    source = run_dir / "input_analysis.output.json"
    artifacts = []
    if source.exists():
        target = artifact_path(run_dir, "input_analysis.output.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        artifacts.append("artifacts/input_analysis.output.json")
    message = agent_messages.append_message(
        run_dir,
        {
            "from": "input_analyst",
            "to": ["gm", "main_agent"],
            "type": "analysis_applied",
            "visibility": "gm_only",
            "payload": {"result": applied},
        },
    )
    if not message.get("ok"):
        blocked = agent_intents.block_intent(run_dir, intent_id, "analysis_message_failed", outputs={"message": message})
        _mark_blocked(run_dir, "analysis_message_failed", {"intent_id": intent_id})
        return _result(False, "blocked", intent_id=intent_id, intent_type="analyze_input", reason="analysis_message_failed", detail=blocked.get("result", {}))
    followup = _create_followup_once(
        run_dir,
        requested_by="input_analyst",
        intent_type="run_gm_turn",
        source_intent_id=intent_id,
        payload={"reason": "input_analysis_applied"},
    )
    completed = agent_intents.complete_intent(
        run_dir,
        intent_id,
        outputs={"applied": applied, "followup_intent": (followup or {}).get("id", "")},
    )
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="analyze_input",
        created_intents=[(followup or {}).get("id", "")] if followup else [],
        created_messages=[(message.get("message") or {}).get("id", "")],
        artifacts=artifacts,
        detail=completed.get("result", {}),
    )
```

- [ ] **Step 6: Wire executor into `_execute_supported_intent()`**

At the top of `_execute_supported_intent()`, after `intent_type` is assigned, add:

```python
    if intent_type == "analyze_input":
        return _execute_analyze_input(run_dir, card_folder, root_dir, intent)
```

- [ ] **Step 7: Run dispatcher tests**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher -v
python -m py_compile skills/agent_dispatcher.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add skills/agent_dispatcher.py tests/test_agent_dispatcher.py
git commit -m "feat: dispatcher执行输入分析意图"
```

---

### Task 5: Dispatcher Story, Critic, Repair, And Delivery Executors

**Files:**
- Modify: `skills/agent_dispatcher.py`
- Modify: `tests/test_agent_dispatcher.py`
- Modify: `skills/rp_generate_cli.py`

- [ ] **Step 1: Add failing tests for story, critic, repair, and delivery intent routing**

Append tests to `tests/test_agent_dispatcher.py`:

```python
    def test_compose_story_writes_story_output_artifact_and_creates_review_intent(self):
        self.intents.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "compose_story", "payload": {"story_input_path": "story.input.json"}},
        )

        def fake_build_story_input(run_dir):
            payload = {"round_id": "round-000001", "loop_outputs": {}, "player_inputs": {}}
            self.dispatcher.write_artifact(run_dir, "story.input.json", payload)
            return payload

        def fake_dispatch(agent_key, prompt_text, cwd, run_claude, extra_context=None, attempts=2):
            self.assertEqual(agent_key, "story")
            return {"content": "<content>story</content>", "character_dialogues": [], "metadata": {}}

        self.dispatcher.agent_outputs.build_story_input = fake_build_story_input
        self.dispatcher.rp_generate_cli._dispatch_agent_payload = fake_dispatch
        _write_json(self.run_dir / "manifest.json", {"round_id": "round-000001", "stage": "analysis_applied", "prompts": {"story": "prompts/story.prompt.md"}})
        (self.run_dir / "prompts").mkdir(exist_ok=True)
        (self.run_dir / "prompts" / "story.prompt.md").write_text("# story\n", encoding="utf-8")

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertTrue((self.run_dir / "artifacts" / "story.output.json").exists())
        self.assertEqual([item["type"] for item in self.intents.list_intents(self.run_dir, "pending")], ["review_critic"])

    def test_review_critic_pass_creates_deliver_round_intent(self):
        _write_json(self.run_dir / "artifacts" / "story.input.json", {"round_id": "round-000001"})
        _write_json(self.run_dir / "artifacts" / "story.output.json", {"content": "<content>story</content>", "character_dialogues": [], "metadata": {}})
        _write_json(self.run_dir / "manifest.json", {"round_id": "round-000001", "stage": "story_ready", "prompts": {"critic": "prompts/critic.prompt.md"}})
        (self.run_dir / "prompts").mkdir(exist_ok=True)
        (self.run_dir / "prompts" / "critic.prompt.md").write_text("# critic\n", encoding="utf-8")
        self.intents.create_intent(self.run_dir, {"requested_by": "story", "type": "review_critic", "payload": {}})

        def fake_dispatch(agent_key, prompt_text, cwd, run_claude, extra_context=None, attempts=2):
            self.assertEqual(agent_key, "critic")
            return {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": "", "system_iteration_suggestion": ""}

        self.dispatcher.rp_generate_cli._dispatch_agent_payload = fake_dispatch
        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertTrue((self.run_dir / "artifacts" / "critic.report.json").exists())
        self.assertEqual([item["type"] for item in self.intents.list_intents(self.run_dir, "pending")], ["deliver_round"])

    def test_deliver_round_marks_delivered_when_delivery_command_passes(self):
        _write_json(self.run_dir / "artifacts" / "critic.report.json", {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": "", "system_iteration_suggestion": ""})
        self.intents.create_intent(self.run_dir, {"requested_by": "critic", "type": "deliver_round", "payload": {}})

        def fake_run_delivery(card, root, run_command):
            return {"ok": True, "result": {"ok": True, "mode": "agent_run"}}

        self.dispatcher.rp_generate_cli._run_delivery = fake_run_delivery
        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_command=lambda *args, **kwargs: None)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "delivered")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "delivered")
```

- [ ] **Step 2: Run targeted dispatcher tests to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher.AgentDispatcherFoundationTest.test_compose_story_writes_story_output_artifact_and_creates_review_intent -v
python -m unittest tests.test_agent_dispatcher.AgentDispatcherFoundationTest.test_review_critic_pass_creates_deliver_round_intent -v
python -m unittest tests.test_agent_dispatcher.AgentDispatcherFoundationTest.test_deliver_round_marks_delivered_when_delivery_command_passes -v
```

Expected: FAIL because these executors are not wired.

- [ ] **Step 3: Import story/critic helpers in dispatcher**

At the top of `skills/agent_dispatcher.py`, add:

```python
import agent_outputs
import rp_generate_cli
```

- [ ] **Step 4: Add prompt reader and agent dispatch helper**

Add:

```python
def _read_prompt(run_dir: Path, key: str) -> str:
    manifest = _load_manifest(run_dir)
    prompts = manifest.get("prompts") if isinstance(manifest.get("prompts"), dict) else {}
    rel = prompts.get(key)
    if not isinstance(rel, str) or not rel:
        rel = f"prompts/{key}.prompt.md"
    path = run_dir / rel
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentDispatcherError(f"{path}: prompt is missing") from exc


def _dispatch_agent_payload(
    agent_key: str,
    run_dir: Path,
    root_dir: Path,
    run_claude: Callable[[str, str, str | Path], str] | None,
    extra_context: dict[str, Any],
) -> dict[str, Any]:
    if run_claude is None:
        raise AgentDispatcherError(f"{agent_key}: run_claude callback is required")
    return rp_generate_cli._dispatch_agent_payload(
        agent_key,
        _read_prompt(run_dir, agent_key),
        root_dir,
        run_claude,
        extra_context=extra_context,
    )
```

- [ ] **Step 5: Add compose story executor**

Add:

```python
def _execute_compose_story(
    run_dir: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str] | None,
) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "compose_story"})
    story_input = agent_outputs.build_story_input(run_dir)
    story = _dispatch_agent_payload(
        "story",
        run_dir,
        root_dir,
        run_claude,
        {"story_input": story_input},
    )
    write_artifact(run_dir, "story.output.json", story)
    followup = _create_followup_once(
        run_dir,
        requested_by="story",
        intent_type="review_critic",
        source_intent_id=intent_id,
        payload={"story_output_path": "artifacts/story.output.json"},
    )
    completed = agent_intents.complete_intent(
        run_dir,
        intent_id,
        outputs={"story_output_path": "artifacts/story.output.json", "followup_intent": (followup or {}).get("id", "")},
    )
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="compose_story",
        created_intents=[(followup or {}).get("id", "")] if followup else [],
        artifacts=["artifacts/story.input.json", "artifacts/story.output.json"],
        detail=completed.get("result", {}),
    )
```

- [ ] **Step 6: Add critic executor**

Add:

```python
def _execute_review_critic(
    run_dir: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str] | None,
) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "review_critic"})
    story_input = read_artifact(run_dir, "story.input.json")
    story_output = read_artifact(run_dir, "story.output.json")
    critic = _dispatch_agent_payload(
        "critic",
        run_dir,
        root_dir,
        run_claude,
        {"story_input": story_input, "story_output": story_output},
    )
    write_artifact(run_dir, "critic.report.json", critic)
    decision = str(critic.get("decision") or "")
    next_type = "deliver_round" if decision == "pass" else "repair_request"
    followup = _create_followup_once(
        run_dir,
        requested_by="critic",
        intent_type=next_type,
        source_intent_id=intent_id,
        payload={"critic_report_path": "artifacts/critic.report.json", "decision": decision},
    )
    completed = agent_intents.complete_intent(
        run_dir,
        intent_id,
        outputs={"critic_report_path": "artifacts/critic.report.json", "followup_intent": (followup or {}).get("id", "")},
    )
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="review_critic",
        created_intents=[(followup or {}).get("id", "")] if followup else [],
        artifacts=["artifacts/critic.report.json"],
        detail=completed.get("result", {}),
    )
```

- [ ] **Step 7: Add delivery executor**

Add:

```python
def _execute_deliver_round(
    run_dir: Path,
    card_folder: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_command: Callable[..., Any] | None,
) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "deliver_round"})
    delivery = rp_generate_cli._run_delivery(card_folder, root_dir, run_command)
    if not delivery.get("ok"):
        blocked = agent_intents.block_intent(run_dir, intent_id, "delivery_failed", outputs={"delivery": delivery})
        _mark_blocked(run_dir, "delivery_failed", {"intent_id": intent_id})
        return _result(False, "blocked", intent_id=intent_id, intent_type="deliver_round", reason="delivery_failed", detail=blocked.get("result", {}))
    completed = agent_intents.complete_intent(run_dir, intent_id, outputs={"delivery": delivery})
    manifest = _load_manifest(run_dir)
    manifest["stage"] = "delivered"
    manifest["dispatcher"] = {"status": "delivered", "intent_id": intent_id}
    agent_run.write_json(run_dir / "manifest.json", manifest)
    return _result(
        True,
        "delivered",
        intent_id=intent_id,
        intent_type="deliver_round",
        artifacts=["artifacts/critic.report.json"],
        detail=completed.get("result", {}),
    )
```

- [ ] **Step 8: Wire executors**

In `_execute_supported_intent()`, add before the fallback block:

```python
    if intent_type == "compose_story":
        return _execute_compose_story(run_dir, root_dir, intent, run_claude)
    if intent_type == "review_critic":
        return _execute_review_critic(run_dir, root_dir, intent, run_claude)
    if intent_type == "deliver_round":
        return _execute_deliver_round(run_dir, card_folder, root_dir, intent, run_command)
```

- [ ] **Step 9: Run dispatcher tests**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher -v
python -m py_compile skills/agent_dispatcher.py
```

Expected: PASS.

- [ ] **Step 10: Commit**

```powershell
git add skills/agent_dispatcher.py tests/test_agent_dispatcher.py
git commit -m "feat: dispatcher执行story critic delivery意图"
```

---

### Task 6: GM Loop Intent Executor

**Files:**
- Modify: `skills/agent_dispatcher.py`
- Modify: `tests/test_agent_dispatcher.py`

- [ ] **Step 1: Add failing test for `run_gm_turn` producing compose intent**

Append:

```python
    def test_run_gm_turn_writes_artifacts_and_creates_compose_story(self):
        self.intents.create_intent(self.run_dir, {"requested_by": "input_analyst", "type": "run_gm_turn", "payload": {}})

        def fake_loop(run_dir, manifest, root, run_claude, repair_context=None):
            self.dispatcher.write_artifact(run_dir, "gm.output.json", {"agent": "gm_loop", "outputs": []})
            self.dispatcher.write_artifact(run_dir, "actor.outputs.json", {"actor_outputs": {}})
            _write_json(run_dir / "interaction.trace.json", {"schema_version": 2, "status": "decision_point", "events": []})
            return {"gm_steps": 1, "called_actors": []}

        self.dispatcher.rp_generate_cli._run_interactive_agent_loop = fake_loop
        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *args: "")

        self.assertTrue(result["ok"])
        self.assertTrue((self.run_dir / "artifacts" / "gm.output.json").exists())
        self.assertTrue((self.run_dir / "artifacts" / "actor.outputs.json").exists())
        self.assertEqual([item["type"] for item in self.intents.list_intents(self.run_dir, "pending")], ["compose_story"])
```

- [ ] **Step 2: Run targeted test to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher.AgentDispatcherFoundationTest.test_run_gm_turn_writes_artifacts_and_creates_compose_story -v
```

Expected: FAIL with `executor_not_wired`.

- [ ] **Step 3: Add artifact mirroring helper for existing GM loop output**

Add to `skills/agent_dispatcher.py`:

```python
def _copy_root_artifact_to_authority(run_dir: Path, relative_path: str) -> str:
    authoritative = artifact_path(run_dir, relative_path)
    if authoritative.exists():
        return f"artifacts/{relative_path}"
    source = run_dir / relative_path
    if source.exists():
        authoritative.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, authoritative)
        return f"artifacts/{relative_path}"
    raise AgentDispatcherError(f"{source}: expected executor artifact is missing")
```

- [ ] **Step 4: Add GM executor**

Add:

```python
def _execute_run_gm_turn(
    run_dir: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str] | None,
) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "run_gm_turn"})
    manifest = _load_manifest(run_dir)
    loop_result = rp_generate_cli._run_interactive_agent_loop(run_dir, manifest, root_dir, run_claude)
    artifacts = [
        _copy_root_artifact_to_authority(run_dir, "gm.output.json"),
        _copy_root_artifact_to_authority(run_dir, "actor.outputs.json"),
    ]
    followup = _create_followup_once(
        run_dir,
        requested_by="gm",
        intent_type="compose_story",
        source_intent_id=intent_id,
        payload={"loop_result": loop_result},
    )
    completed = agent_intents.complete_intent(
        run_dir,
        intent_id,
        outputs={"loop_result": loop_result, "followup_intent": (followup or {}).get("id", "")},
    )
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="run_gm_turn",
        created_intents=[(followup or {}).get("id", "")] if followup else [],
        artifacts=artifacts,
        detail=completed.get("result", {}),
    )
```

- [ ] **Step 5: Wire GM executor**

In `_execute_supported_intent()`, add:

```python
    if intent_type == "run_gm_turn":
        return _execute_run_gm_turn(run_dir, root_dir, intent, run_claude)
```

- [ ] **Step 6: Run tests**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher -v
python -m py_compile skills/agent_dispatcher.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add skills/agent_dispatcher.py tests/test_agent_dispatcher.py
git commit -m "feat: dispatcher执行GM回合意图"
```

---

### Task 7: Live Driver Switch To Dispatcher

**Files:**
- Modify: `skills/rp_generate_cli.py`
- Modify: `tests/test_rp_generate_cli.py`

- [ ] **Step 1: Add failing tests proving `run_round()` uses dispatcher**

In `tests/test_rp_generate_cli.py`, add:

```python
    def test_run_round_drives_dispatcher_until_delivered(self):
        calls = []

        def fake_dispatch_next(run_dir, card, root, run_claude=None, run_command=None):
            calls.append(Path(run_dir).name)
            if len(calls) == 1:
                return {"ok": True, "status": "completed", "intent_id": "intent_000001", "intent_type": "analyze_input"}
            return {"ok": True, "status": "delivered", "intent_id": "intent_000002", "intent_type": "deliver_round"}

        self.module.agent_dispatcher.dispatch_next = fake_dispatch_next
        result = self.module.run_round(self.card, self.root, run_claude=lambda *args: "", run_command=lambda *args, **kwargs: None)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "generated")
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["dispatcher"]["status"], "delivered")

    def test_run_round_returns_blocked_dispatcher_result(self):
        def fake_dispatch_next(run_dir, card, root, run_claude=None, run_command=None):
            return {"ok": False, "status": "blocked", "reason": "dispatcher_stalled", "intent_id": "", "intent_type": ""}

        self.module.agent_dispatcher.dispatch_next = fake_dispatch_next
        result = self.module.run_round(self.card, self.root, run_claude=lambda *args: "", run_command=lambda *args, **kwargs: None)

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "blocked")
        self.assertEqual(result["reason"], "dispatcher_stalled")
```

- [ ] **Step 2: Run targeted tests to verify failure**

Run:

```powershell
python -m unittest tests.test_rp_generate_cli.RpGenerateCliTest.test_run_round_drives_dispatcher_until_delivered -v
python -m unittest tests.test_rp_generate_cli.RpGenerateCliTest.test_run_round_returns_blocked_dispatcher_result -v
```

Expected: FAIL because `rp_generate_cli.py` does not import or call `agent_dispatcher`.

- [ ] **Step 3: Import dispatcher**

At the top of `skills/rp_generate_cli.py`, add:

```python
import agent_dispatcher
```

- [ ] **Step 4: Add dispatcher loop helper**

Add near `_delivery_complete()`:

```python
MAX_DISPATCHER_STEPS = 64


def _run_dispatcher_loop(
    run_dir: Path,
    card: Path,
    root: Path,
    run_claude: Callable[[str, str, str | Path], str],
    run_command: Callable[..., Any],
) -> Dict[str, Any]:
    results = []
    for _index in range(MAX_DISPATCHER_STEPS):
        result = agent_dispatcher.dispatch_next(
            run_dir,
            card,
            root,
            run_claude=run_claude,
            run_command=run_command,
        )
        results.append(result)
        status = str(result.get("status") or "")
        if status in {"delivered", "blocked", "stalled"}:
            return {"status": status, "results": results, "last": result}
    return {
        "status": "blocked",
        "results": results,
        "last": {"ok": False, "status": "blocked", "reason": "dispatcher_step_limit"},
    }
```

- [ ] **Step 5: Replace `run_round()` body after debug logger setup**

In `run_round()`, keep card/root/run_dir/settings/debug logger setup. Replace the old stage-specific body from `_reset_delivery_retry_budget(...)` through the final return with:

```python
    dispatcher_result = _run_dispatcher_loop(run_dir, card, root, active_run_claude, run_command)
    last = dispatcher_result.get("last") if isinstance(dispatcher_result.get("last"), dict) else {}
    status = str(dispatcher_result.get("status") or "")
    if status == "delivered":
        return {
            "ok": True,
            "action": "generated",
            "run_dir": str(run_dir),
            "dispatcher": last,
            "dispatcher_results": dispatcher_result.get("results", []),
        }
    return {
        "ok": False,
        "action": "blocked",
        "run_dir": str(run_dir),
        "reason": str(last.get("reason") or status or "dispatcher_blocked"),
        "dispatcher": last,
        "dispatcher_results": dispatcher_result.get("results", []),
    }
```

Do not delete helper functions such as `_dispatch_agent_payload`, `_run_interactive_agent_loop`, `_run_delivery`, or story normalization helpers yet. Dispatcher executors use them.

- [ ] **Step 6: Run rp_generate_cli tests**

Run:

```powershell
python -m unittest tests.test_rp_generate_cli -v
python -m py_compile skills/rp_generate_cli.py
```

Expected: PASS. Update old tests that expected direct story/critic orchestration to either call helper functions directly or assert dispatcher results.

- [ ] **Step 7: Commit**

```powershell
git add skills/rp_generate_cli.py tests/test_rp_generate_cli.py
git commit -m "feat: 使用dispatcher驱动实时回合生成"
```

---

### Task 8: Repair And Rollback Intent Routing

**Files:**
- Modify: `skills/agent_dispatcher.py`
- Modify: `tests/test_agent_dispatcher.py`

- [ ] **Step 1: Add failing tests for repair and rollback**

Append:

```python
    def test_repair_request_story_only_creates_compose_story(self):
        _write_json(
            self.run_dir / "artifacts" / "critic.report.json",
            {
                "decision": "revise",
                "repair_instruction": "Rewrite ending.",
                "repair_routing": {"stage": "story_composition", "rollback": "story_only", "can_auto_repair": True, "risk": "low"},
            },
        )
        self.intents.create_intent(
            self.run_dir,
            {"requested_by": "critic", "type": "repair_request", "payload": {"critic_report_path": "artifacts/critic.report.json"}},
        )

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertTrue(result["ok"])
        self.assertEqual([item["type"] for item in self.intents.list_intents(self.run_dir, "pending")], ["compose_story"])

    def test_rollback_request_blocks_when_snapshot_missing(self):
        self.intents.create_intent(
            self.run_dir,
            {"requested_by": "critic", "type": "rollback_request", "payload": {"snapshot_id": "round-000001-missing", "mode": "round_progression"}},
        )

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "rollback_failed")
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        self.assertEqual(len(self.intents.list_intents(self.run_dir, "blocked")), 1)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher.AgentDispatcherFoundationTest.test_repair_request_story_only_creates_compose_story -v
python -m unittest tests.test_agent_dispatcher.AgentDispatcherFoundationTest.test_rollback_request_blocks_when_snapshot_missing -v
```

Expected: FAIL because executors are not wired.

- [ ] **Step 3: Import repair and snapshot helpers**

Add:

```python
import agent_snapshots
import self_repair
```

- [ ] **Step 4: Add repair executor**

Add:

```python
def _execute_repair_request(run_dir: Path, intent: dict[str, Any]) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "repair_request"})
    payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
    report_path = str(payload.get("critic_report_path") or "artifacts/critic.report.json")
    report = agent_run.read_json(run_dir / report_path, {}) or {}
    routing = self_repair.normalize_repair_routing(report.get("repair_routing"))
    rollback = str(routing.get("rollback") or "")
    if rollback == "round_progression":
        next_type = "rollback_request"
        next_payload = {"mode": "round_progression", "reason": "critic_repair", "critic_report_path": report_path}
    else:
        next_type = "compose_story"
        next_payload = {"repair_routing": routing, "critic_report_path": report_path}
    followup = _create_followup_once(
        run_dir,
        requested_by="critic",
        intent_type=next_type,
        source_intent_id=intent_id,
        payload=next_payload,
    )
    completed = agent_intents.complete_intent(
        run_dir,
        intent_id,
        outputs={"repair_routing": routing, "followup_intent": (followup or {}).get("id", "")},
    )
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="repair_request",
        created_intents=[(followup or {}).get("id", "")] if followup else [],
        detail=completed.get("result", {}),
    )
```

- [ ] **Step 5: Add rollback executor**

Add:

```python
def _execute_rollback_request(run_dir: Path, card_folder: Path, intent: dict[str, Any]) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "rollback_request"})
    payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
    snapshot_id = str(payload.get("snapshot_id") or "")
    mode = str(payload.get("mode") or payload.get("rollback") or "round_progression")
    restored = agent_snapshots.restore_snapshot(card_folder, snapshot_id, mode=mode)
    if not restored.get("ok"):
        blocked = agent_intents.block_intent(run_dir, intent_id, "rollback_failed", outputs={"restore": restored})
        _mark_blocked(run_dir, "rollback_failed", {"intent_id": intent_id, "restore": restored})
        return _result(False, "blocked", intent_id=intent_id, intent_type="rollback_request", reason="rollback_failed", detail=blocked.get("result", {}))
    followup_type = "compose_story" if mode == "story_only" else "run_gm_turn"
    followup = _create_followup_once(
        run_dir,
        requested_by="dispatcher",
        intent_type=followup_type,
        source_intent_id=intent_id,
        payload={"rollback": restored},
    )
    completed = agent_intents.complete_intent(
        run_dir,
        intent_id,
        outputs={"restore": restored, "followup_intent": (followup or {}).get("id", "")},
    )
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="rollback_request",
        created_intents=[(followup or {}).get("id", "")] if followup else [],
        detail=completed.get("result", {}),
    )
```

- [ ] **Step 6: Wire repair and rollback executors**

In `_execute_supported_intent()`, add:

```python
    if intent_type == "repair_request":
        return _execute_repair_request(run_dir, intent)
    if intent_type == "rollback_request":
        return _execute_rollback_request(run_dir, card_folder, intent)
```

- [ ] **Step 7: Run dispatcher tests**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher -v
python -m py_compile skills/agent_dispatcher.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add skills/agent_dispatcher.py tests/test_agent_dispatcher.py
git commit -m "feat: dispatcher处理修复与回滚意图"
```

---

### Task 9: Remove Workflow Next-Action Layer

**Files:**
- Delete or rewrite: `skills/agent_workflow.py`
- Delete or rewrite: `tests/test_agent_workflow.py`
- Modify: `skills/round_prepare.py`
- Modify: `tests/test_agent_packets.py`
- Modify: `tests/test_rp_generate_cli.py`

- [ ] **Step 1: Search for workflow references**

Run:

```powershell
rg -n "agent_workflow|advise_next_actions|dispatch_agent_outputs|build_story_input" skills tests README.md CLAUDE.md AGENTS.md --glob "!skills/node_modules/**"
```

Expected: references remain in docs and workflow tests.

- [ ] **Step 2: Delete executable workflow module and tests**

Remove:

```powershell
git rm skills/agent_workflow.py tests/test_agent_workflow.py
```

If a retained import still needs a module, replace `skills/agent_workflow.py` with this diagnostic-only file instead of deleting it:

```python
"""Read-only diagnostics for legacy workflow-era run directories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def inspect_runtime(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir)
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    return {
        "run_dir": str(root),
        "manifest_stage": manifest.get("stage") if isinstance(manifest, dict) else None,
        "pending_intents": len(list((root / "intents" / "pending").glob("intent_*.json"))),
        "messages": (root / "messages.jsonl").exists(),
        "artifacts": sorted(path.name for path in (root / "artifacts").glob("*.json")) if (root / "artifacts").exists() else [],
    }
```

Do not keep `advise_next_actions()`.

- [ ] **Step 3: Update tests that assert workflow advice**

In `tests/test_agent_packets.py`, replace workflow advice assertions with `dispatcher_runtime` assertions from Task 2.

In `tests/test_rp_generate_cli.py`, remove old expectations that `manifest.expected_outputs` drives direct story/critic dispatch. These tests should now either:

- call `_dispatch_agent_payload()` and validation helpers directly, or
- mock `agent_dispatcher.dispatch_next()` and assert `run_round()` response shape.

- [ ] **Step 4: Run workflow reference search again**

Run:

```powershell
rg -n "agent_workflow|advise_next_actions|dispatch_agent_outputs" skills tests --glob "!skills/node_modules/**"
```

Expected: no output, unless `inspect_runtime` was retained without `advise_next_actions`.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python -m unittest tests.test_agent_packets tests.test_rp_generate_cli tests.test_agent_dispatcher -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add -A skills tests
git commit -m "refactor: 移除workflow下一步决策层"
```

---

### Task 10: Dispatcher-First Control Plane Smoke

**Files:**
- Modify: `skills/control_plane_smoke.py`
- Modify: `tests/test_control_plane_smoke.py`

- [ ] **Step 1: Write failing smoke assertions for dispatcher evidence**

In `tests/test_control_plane_smoke.py`, extend the smoke payload assertions:

```python
        self.assertIn("dispatcher", payload)
        self.assertEqual(payload["dispatcher"]["status"], "delivered")
        self.assertIn("analyze_input", payload["dispatcher"]["completed_intent_types"])
        self.assertIn("run_gm_turn", payload["dispatcher"]["completed_intent_types"])
        self.assertIn("compose_story", payload["dispatcher"]["completed_intent_types"])
        self.assertIn("review_critic", payload["dispatcher"]["completed_intent_types"])
        self.assertIn("deliver_round", payload["dispatcher"]["completed_intent_types"])
        self.assertNotIn("workflow_advice", payload)
```

- [ ] **Step 2: Run smoke test to verify failure**

Run:

```powershell
python -m unittest tests.test_control_plane_smoke -v
```

Expected: FAIL because smoke output does not include dispatcher evidence yet.

- [ ] **Step 3: Add dispatcher evidence helper**

In `skills/control_plane_smoke.py`, import `agent_intents` if not already imported and add:

```python
def _dispatcher_evidence(run_dir: Path) -> dict:
    completed = agent_intents.list_intents(run_dir, "completed")
    blocked = agent_intents.list_intents(run_dir, "blocked")
    pending = agent_intents.list_intents(run_dir, "pending")
    return {
        "status": "delivered" if not pending and not blocked else "blocked" if blocked else "pending",
        "completed_intent_types": [str(item.get("type") or "") for item in completed],
        "blocked_intent_types": [str(item.get("type") or "") for item in blocked],
        "pending_intent_types": [str(item.get("type") or "") for item in pending],
    }
```

- [ ] **Step 4: Make smoke create completed dispatcher intents**

Where the smoke currently builds deterministic artifacts directly, wrap each major step with intent creation and completion:

```python
        intent = agent_intents.create_intent(
            run_dir,
            {"requested_by": "smoke", "type": "analyze_input", "payload": {"fixture": True}},
        )["intent"]
        agent_intents.complete_intent(run_dir, intent["id"], outputs={"fixture": "input_analysis"})
```

Repeat the same pattern for `run_gm_turn`, `compose_story`, `review_critic`, and `deliver_round`. Use outputs that name the artifact created by that smoke section, for example `{"artifact": "artifacts/story.output.json"}`.

- [ ] **Step 5: Add evidence to returned payload**

Add:

```python
            "dispatcher": _dispatcher_evidence(run_dir),
```

Remove any `workflow_advice` payload field if present.

- [ ] **Step 6: Run smoke checks**

Run:

```powershell
python -m unittest tests.test_control_plane_smoke -v
python skills/control_plane_smoke.py --repo .
```

Expected: PASS. Command JSON includes `"dispatcher"` and no `"workflow_advice"`.

- [ ] **Step 7: Commit**

```powershell
git add skills/control_plane_smoke.py tests/test_control_plane_smoke.py
git commit -m "test: smoke覆盖dispatcher运行时证据"
```

---

### Task 11: Documentation Update

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Update README architecture text**

In `README.md`, replace the paragraph that says `agent_workflow.py` decides the next step with:

```markdown
`agent_dispatcher.py` 是每轮运行时的执行主干。每轮从 `intents/pending/` 中取出下一个可执行 intent，完成输入分析、GM 推进、projection、actor 调用、story 写作、critic 审核、repair/rollback 和最终交付。`manifest.json` 只记录状态摘要；下一步动作由 pending intents 决定。权威 agent 产物位于 `.agent_runs/<round>/artifacts/`，根目录同名文件只可作为最终交付边界导出，不参与控制面决策。
```

- [ ] **Step 2: Update CLAUDE data flow**

In `CLAUDE.md`, replace mentions of `agent_workflow.py` next-action advice with:

```markdown
Claude Code drives live rounds through `agent_dispatcher.dispatch_next(...)`: pending intents are the only executable next-action source. Do not use `agent_workflow.py` or manifest stages to decide whether to run GM, actor, story, critic, repair, rollback, or delivery. Read and write authoritative agent artifacts under `.agent_runs/<round>/artifacts/`.
```

- [ ] **Step 3: Update AGENTS project structure**

In `AGENTS.md`, add `agent_dispatcher.py` to the runtime module list and replace `agent_workflow.py` next-action language with:

```markdown
`agent_dispatcher.py` is the dispatcher-first runtime spine: it consumes pending intents, writes authoritative artifacts under `artifacts/`, and blocks stalled or unsafe runs with audit evidence.
```

- [ ] **Step 4: Run documentation checks**

Run:

```powershell
git diff --check README.md CLAUDE.md AGENTS.md
rg -n "agent_workflow|advise_next_actions|dispatch_agent_outputs" README.md CLAUDE.md AGENTS.md
```

Expected: `git diff --check` has no output. The `rg` command has no output except historical changelog text if one exists.

- [ ] **Step 5: Commit**

```powershell
git add README.md CLAUDE.md AGENTS.md
git commit -m "docs: 记录dispatcher优先运行时"
```

---

### Task 12: Final Verification And Cleanup

**Files:**
- Verify every file touched in Tasks 1-11.

- [ ] **Step 1: Run full unit tests**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Run deterministic smoke**

Run:

```powershell
python skills/control_plane_smoke.py --repo .
```

Expected: JSON includes `"ok": true`, `"manifest_stage": "delivered"`, `"dispatcher"`, `"messages"`, `"intents"`, and `"snapshot"`.

- [ ] **Step 3: Run compile checks**

Run:

```powershell
python -m py_compile skills/agent_dispatcher.py skills/agent_messages.py skills/agent_intents.py skills/agent_outputs.py skills/agent_turn_loop.py skills/subgm_threads.py skills/subgm_turn_loop.py skills/rp_generate_cli.py skills/round_prepare.py skills/round_deliver.py skills/control_plane_smoke.py
```

Expected: no output and exit code 0.

- [ ] **Step 4: Check workflow references**

Run:

```powershell
rg -n "agent_workflow|advise_next_actions|dispatch_agent_outputs|manifest.expected_outputs is required" skills tests README.md CLAUDE.md AGENTS.md --glob "!skills/node_modules/**"
```

Expected: no executable next-action references remain. If `manifest.expected_outputs` remains only in tests for older memory summary scheduling, ensure the tests do not use it to drive dispatcher decisions.

- [ ] **Step 5: Check root artifact authority**

Run:

```powershell
rg -n "_artifact_path\\(|_write_artifact_with_legacy_mirror|root / \"story.input.json\"|run_dir / \"story.input.json\"" skills tests --glob "!skills/node_modules/**"
```

Expected: no helper writes root story input as authoritative. Root artifact references may remain only in explicit delivery export tests or old fixture migration tests that were deliberately retained.

- [ ] **Step 6: Check git status**

Run:

```powershell
git status --short --branch
```

Expected: only intentional files are changed.

- [ ] **Step 7: Commit verification fixes if needed**

If any verification step required changes:

```powershell
git add <changed-files>
git commit -m "fix: 完成dispatcher迁移验证修正"
```

---

## Self-Review

Spec coverage:

- Dispatcher-first execution is covered by Tasks 1, 4, 5, 6, 7, and 10.
- Initial pending intent creation is covered by Task 2.
- `artifacts/` authority and root artifact demotion are covered by Task 3 and Task 12.
- `agent_workflow.py` removal from live decisions is covered by Tasks 7, 9, 11, and 12.
- Repair and rollback intent routing is covered by Task 8.
- Smoke and final acceptance are covered by Tasks 10 and 12.
- Documentation updates are covered by Task 11.
- MCP wrapper exclusion is represented by absence from implementation tasks and documentation in Task 11.

Deferred-risk notes:

- This plan keeps existing `agent_turn_loop.py` as the GM/actor execution helper during migration. A later plan can split GM, projection, actor, and subGM executors into smaller modules after dispatcher-first behavior is stable.
- This plan initially copies GM/actor root outputs into authoritative `artifacts/` in Task 6 because the existing GM loop writes root outputs. Task 3 ensures control-plane reads prefer `artifacts/`; a later cleanup can make `agent_turn_loop.py` write directly to `artifacts/`.

Type consistency:

- Dispatcher entry point is `dispatch_next(run_dir, card_folder, root_dir, run_claude=None, run_command=None)`.
- Intent type names match the spec: `analyze_input`, `run_gm_turn`, `request_projection`, `run_actor`, `run_subgm_thread`, `compose_story`, `review_critic`, `repair_request`, `rollback_request`, `deliver_round`, `assets_task`.
- Authoritative artifact paths are `artifacts/input_analysis.output.json`, `artifacts/gm.output.json`, `artifacts/actor.outputs.json`, `artifacts/story.input.json`, `artifacts/story.output.json`, and `artifacts/critic.report.json`.
- Terminal dispatcher statuses are `delivered`, `blocked`, and `stalled`.
