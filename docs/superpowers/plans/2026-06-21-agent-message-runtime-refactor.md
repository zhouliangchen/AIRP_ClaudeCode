# Agent Message Runtime Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a message-driven RP agent runtime where agents collaborate through structured messages and intents while Python enforces ACL, projection, trace, snapshot, artifact, and delivery gates.

**Architecture:** Introduce `agent_messages.py` and `agent_intents.py` as the new runtime spine, then route GM actor calls, subGM messages, projection, story, critic, and repair through that spine. Keep current artifacts during migration by materializing `gm.output.json`, `actor.outputs.json`, `story.input.json`, `story.output.json`, and `critic.report.json` from accepted messages and completed intents.

**Tech Stack:** Python standard library, JSONL file protocols, `unittest`, existing `.agent_runs/<round>/` folders, existing `agent_projection.py`, `agent_visibility_guard.py`, `agent_interactions.py`, `agent_outputs.py`, `rp_generate_cli.py`, and `round_deliver.py`.

---

## Scope Check

This plan implements one coherent subsystem: the runtime communication model. It is large, but each task produces independently testable behavior and keeps old artifacts available until the dispatcher can fully replace the fixed workflow. Do not implement UI changes, image generation changes, or new MCP wrappers in this plan; MCP wrappers can be added after the Python APIs stabilize.

## File Structure

- Create `skills/agent_messages.py`: append-only message log, envelope normalization, ACL checks, inbox indexing, read helpers, and rejection audit records.
- Create `tests/test_agent_messages.py`: unit coverage for message append, inbox indexing, ACL matrix, malformed payload rejection, and actor-facing projection enforcement markers.
- Create `skills/agent_intents.py`: intent creation, lifecycle moves, dispatcher result records, and helpers for pending/accepted/rejected/completed/blocked states.
- Create `tests/test_agent_intents.py`: unit coverage for intent creation, accept/reject/complete/block transitions, duplicate IDs, and filesystem layout.
- Modify `skills/agent_packets.py`: initialize the new message runtime during `prepare_agent_run()` and `rebuild_agent_run_from_analysis()`.
- Modify `tests/test_agent_packets.py`: assert message runtime files exist after run preparation and analysis rebuild.
- Modify `skills/agent_turn_loop.py`: add a message-backed actor dispatch lane while preserving legacy output materialization.
- Modify `tests/test_agent_turn_loop.py`: verify GM actor calls create projected actor messages, actor responses are recorded as messages, and legacy artifacts are still written.
- Modify `skills/subgm_threads.py` and `skills/subgm_turn_loop.py`: mirror subGM messages into common `messages.jsonl` and route subGM actor calls through intents.
- Modify `tests/test_subgm_threads.py` and `tests/test_subgm_turn_loop.py`: verify common message mirroring, ACL, and non-overlapping side-thread actor reservations.
- Modify `skills/agent_outputs.py`: read materialized artifacts from `artifacts/` when present and mirror legacy root paths during migration.
- Modify `tests/test_agent_outputs.py`: verify `story.input.json` can be assembled from the message-backed artifact layout.
- Modify `skills/rp_generate_cli.py`: replace story/critic repair while-loop internals with repair intents while keeping current CLI output shape.
- Modify `tests/test_rp_generate_cli.py`: verify critic revise/block creates `repair_request` intents and respects `selfRepairMode`.
- Create `skills/agent_snapshots.py`: per-turn snapshot and rollback helpers.
- Create `tests/test_agent_snapshots.py`: coverage for snapshot creation, story-only rollback, round-progression rollback, and historical branch rollback metadata.
- Modify `skills/control_plane_smoke.py`: exercise the message runtime path.
- Modify `tests/test_control_plane_smoke.py`: assert smoke output includes message, intent, projection, snapshot, and artifact evidence.
- Modify `README.md`, `CLAUDE.md`, and `AGENTS.md`: document the new runtime only after behavior is implemented.

---

### Task 1: Message Bus Foundation

**Files:**
- Create: `skills/agent_messages.py`
- Create: `tests/test_agent_messages.py`

- [ ] **Step 1: Write failing tests for message append, inbox indexing, and ACL**

Create `tests/test_agent_messages.py`:

```python
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_messages():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_messages", ROOT / "skills" / "agent_messages.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentMessagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "round-000001"
        self.run_dir.mkdir()
        self.mod = _load_agent_messages()

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_message_writes_log_and_inbox_indexes(self):
        result = self.mod.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["story", "critic"],
                "type": "message",
                "visibility": "story_facing",
                "payload": {"text": "Raw scene is ready."},
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["message"]["id"], "msg_000001")
        log_rows = self.mod.read_messages(self.run_dir)
        self.assertEqual(len(log_rows), 1)
        self.assertEqual(self.mod.read_inbox(self.run_dir, "story")[0]["id"], "msg_000001")
        self.assertEqual(self.mod.read_inbox(self.run_dir, "critic")[0]["id"], "msg_000001")

    def test_player_cannot_send_directly_to_character(self):
        result = self.mod.append_message(
            self.run_dir,
            {
                "from": "player",
                "to": ["character:Ada"],
                "type": "message",
                "visibility": "actor_facing",
                "payload": {"text": "Hi."},
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "acl_rejected")
        self.assertEqual(self.mod.read_inbox(self.run_dir, "character:Ada"), [])
        self.assertEqual(self.mod.read_messages(self.run_dir)[0]["status"], "rejected")

    def test_actor_facing_gm_message_requires_projection_marker(self):
        result = self.mod.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["character:Ada"],
                "type": "message",
                "visibility": "actor_facing",
                "payload": {"text": "You hear a bell."},
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "projection_required")

    def test_projected_message_can_reach_actor_inbox(self):
        result = self.mod.append_message(
            self.run_dir,
            {
                "from": "projection",
                "to": ["character:Ada"],
                "type": "projected_message",
                "visibility": "actor_facing",
                "source_call_id": "call-character-Ada-1",
                "payload": {"gm_prompt": "You hear a bell."},
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(self.mod.read_inbox(self.run_dir, "character:Ada")[0]["type"], "projected_message")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_messages -v
```

Expected: FAIL with an import error for missing `skills/agent_messages.py`.

- [ ] **Step 3: Implement `agent_messages.py`**

Create `skills/agent_messages.py` with this complete initial API:

```python
"""Append-only agent message bus for one RP round."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


VALID_VISIBILITY = {"gm_only", "story_facing", "actor_facing", "public"}
ACTOR_PREFIX = "character:"
PROJECTED_TYPES = {"projected_message"}


class AgentMessageError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _messages_path(run_dir: str | Path) -> Path:
    return Path(run_dir) / "messages.jsonl"


def _inbox_path(run_dir: str | Path, agent_id: str) -> Path:
    return Path(run_dir) / "inboxes" / f"{safe_agent_filename(agent_id)}.jsonl"


def safe_agent_filename(agent_id: Any) -> str:
    text = str(agent_id or "").strip().replace(":", "_")
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in text)
    return safe or "unknown"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _next_message_id(run_dir: Path) -> str:
    return f"msg_{len(_read_jsonl(_messages_path(run_dir))) + 1:06d}"


def _agent(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise AgentMessageError("agent id is required")
    return text


def _targets(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise AgentMessageError("to must be a string or list")
    targets = [_agent(item) for item in items]
    if not targets:
        raise AgentMessageError("at least one target is required")
    return targets


def _is_actor(agent_id: str) -> bool:
    return agent_id == "player" or agent_id.startswith(ACTOR_PREFIX)


def _is_subgm(agent_id: str) -> bool:
    return agent_id.startswith("subGM:")


def acl_reason(sender: str, targets: Iterable[str], message_type: str, visibility: str) -> str:
    target_list = list(targets)
    if sender == "player" or sender.startswith(ACTOR_PREFIX):
        if any(target != "gm" and not _is_subgm(target) for target in target_list):
            return "acl_rejected"
    if _is_subgm(sender):
        allowed = {"gm", "projection", "story"}
        if any(not (target in allowed or target.startswith(ACTOR_PREFIX)) for target in target_list):
            return "acl_rejected"
    if visibility == "actor_facing" and any(_is_actor(target) for target in target_list):
        if sender != "projection" or message_type not in PROJECTED_TYPES:
            return "projection_required"
    return ""


def normalize_message(run_dir: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AgentMessageError("message payload must be an object")
    root = Path(run_dir)
    sender = _agent(payload.get("from"))
    targets = _targets(payload.get("to"))
    message_type = str(payload.get("type") or "message").strip() or "message"
    visibility = str(payload.get("visibility") or "gm_only").strip()
    if visibility not in VALID_VISIBILITY:
        raise AgentMessageError(f"visibility is not allowed: {visibility!r}")
    body = payload.get("payload", {})
    if not isinstance(body, dict):
        raise AgentMessageError("payload must be an object")
    message_id = str(payload.get("id") or "").strip() or _next_message_id(root)
    return {
        "id": message_id,
        "round_id": str(payload.get("round_id") or root.name),
        "created_at": str(payload.get("created_at") or _now()),
        "from": sender,
        "to": targets,
        "type": message_type,
        "visibility": visibility,
        "thread_id": str(payload.get("thread_id") or ""),
        "source_call_id": str(payload.get("source_call_id") or ""),
        "reply_to": str(payload.get("reply_to") or ""),
        "payload": dict(body),
        "status": "queued",
        "policy": payload.get("policy") if isinstance(payload.get("policy"), dict) else {},
    }


def append_message(run_dir: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    root = Path(run_dir)
    try:
        message = normalize_message(root, payload)
        reason = acl_reason(message["from"], message["to"], message["type"], message["visibility"])
    except AgentMessageError as exc:
        return {"ok": False, "reason": "schema_rejected", "error": str(exc)}
    if reason:
        message["status"] = "rejected"
        message["reject_reason"] = reason
        _append_jsonl(_messages_path(root), message)
        return {"ok": False, "reason": reason, "message": message}
    message["status"] = "delivered"
    _append_jsonl(_messages_path(root), message)
    for target in message["to"]:
        _append_jsonl(_inbox_path(root, target), message)
    return {"ok": True, "message": message}


def read_messages(run_dir: str | Path) -> list[dict[str, Any]]:
    return _read_jsonl(_messages_path(run_dir))


def read_inbox(run_dir: str | Path, agent_id: str) -> list[dict[str, Any]]:
    return _read_jsonl(_inbox_path(run_dir, agent_id))
```

- [ ] **Step 4: Run the tests**

Run:

```powershell
python -m unittest tests.test_agent_messages -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add skills/agent_messages.py tests/test_agent_messages.py
git commit -m "feat: 增加agent消息总线"
```

---

### Task 2: Intent Lifecycle Foundation

**Files:**
- Create: `skills/agent_intents.py`
- Create: `tests/test_agent_intents.py`

- [ ] **Step 1: Write failing tests for intent lifecycle**

Create `tests/test_agent_intents.py`:

```python
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_intents():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_intents", ROOT / "skills" / "agent_intents.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentIntentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "round-000001"
        self.run_dir.mkdir()
        self.mod = _load_agent_intents()

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_intent_writes_pending_file(self):
        result = self.mod.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "dispatch_actor",
                "source_message_id": "msg_000001",
                "payload": {"actor_id": "character:Ada"},
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent"]["id"], "intent_000001")
        self.assertTrue((self.run_dir / "intents" / "pending" / "intent_000001.json").exists())

    def test_accept_and_complete_intent_moves_files_and_records_result(self):
        created = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "gm", "type": "project_message", "payload": {"target": "player"}},
        )["intent"]

        accepted = self.mod.accept_intent(self.run_dir, created["id"], outputs={"message_id": "msg_000002"})
        self.assertTrue(accepted["ok"])
        self.assertTrue((self.run_dir / "intents" / "accepted" / f"{created['id']}.json").exists())

        completed = self.mod.complete_intent(self.run_dir, created["id"], outputs={"artifact": "actor.outputs.json"})
        self.assertTrue(completed["ok"])
        self.assertTrue((self.run_dir / "intents" / "completed" / f"{created['id']}.json").exists())
        self.assertEqual(completed["result"]["status"], "completed")

    def test_reject_intent_records_reason(self):
        created = self.mod.create_intent(
            self.run_dir,
            {"requested_by": "player", "type": "rollback", "payload": {}},
        )["intent"]
        rejected = self.mod.reject_intent(self.run_dir, created["id"], "acl_rejected")

        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["result"]["reason"], "acl_rejected")
        self.assertTrue((self.run_dir / "intents" / "rejected" / f"{created['id']}.json").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_intents -v
```

Expected: FAIL with an import error for missing `skills/agent_intents.py`.

- [ ] **Step 3: Implement `agent_intents.py`**

Create `skills/agent_intents.py`:

```python
"""Structured executable intents for the agent message runtime."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALID_STATES = {"pending", "accepted", "rejected", "completed", "blocked"}


class AgentIntentError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _dir(run_dir: str | Path, state: str) -> Path:
    if state not in VALID_STATES:
        raise AgentIntentError(f"invalid intent state: {state!r}")
    return Path(run_dir) / "intents" / state


def _path(run_dir: str | Path, state: str, intent_id: str) -> Path:
    return _dir(run_dir, state) / f"{intent_id}.json"


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AgentIntentError(f"{path}: intent must be an object")
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _next_intent_id(run_dir: Path) -> str:
    total = 0
    for state in VALID_STATES:
        total += len(list(_dir(run_dir, state).glob("intent_*.json")))
    return f"intent_{total + 1:06d}"


def _find_existing(run_dir: Path, intent_id: str) -> tuple[str, Path] | None:
    for state in VALID_STATES:
        path = _path(run_dir, state, intent_id)
        if path.exists():
            return state, path
    return None


def normalize_intent(run_dir: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AgentIntentError("intent payload must be an object")
    root = Path(run_dir)
    requested_by = str(payload.get("requested_by") or "").strip()
    intent_type = str(payload.get("type") or "").strip()
    body = payload.get("payload", {})
    if not requested_by:
        raise AgentIntentError("requested_by is required")
    if not intent_type:
        raise AgentIntentError("type is required")
    if not isinstance(body, dict):
        raise AgentIntentError("payload must be an object")
    return {
        "id": str(payload.get("id") or "").strip() or _next_intent_id(root),
        "round_id": str(payload.get("round_id") or root.name),
        "created_at": str(payload.get("created_at") or _now()),
        "requested_by": requested_by,
        "type": intent_type,
        "source_message_id": str(payload.get("source_message_id") or ""),
        "payload": dict(body),
        "policy": payload.get("policy") if isinstance(payload.get("policy"), dict) else {},
        "state": "pending",
    }


def create_intent(run_dir: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    root = Path(run_dir)
    try:
        intent = normalize_intent(root, payload)
    except AgentIntentError as exc:
        return {"ok": False, "reason": "schema_rejected", "error": str(exc)}
    target = _path(root, "pending", intent["id"])
    if _find_existing(root, intent["id"]):
        return {"ok": False, "reason": "duplicate_intent_id", "intent": intent}
    _write_json(target, intent)
    return {"ok": True, "intent": intent}


def _move_with_result(run_dir: str | Path, intent_id: str, target_state: str, *, ok: bool, reason: str = "", outputs: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(run_dir)
    found = _find_existing(root, intent_id)
    if not found:
        return {"ok": False, "reason": "intent_missing", "intent_id": intent_id}
    _state, source = found
    intent = _read_json(source)
    intent["state"] = target_state
    result = {
        "intent_id": intent_id,
        "status": target_state,
        "reason": reason,
        "outputs": outputs or {},
        "updated_at": _now(),
    }
    intent["result"] = result
    target = _path(root, target_state, intent_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        try:
            source.unlink()
        except OSError:
            pass
    _write_json(target, intent)
    return {"ok": ok, "intent": intent, "result": result}


def accept_intent(run_dir: str | Path, intent_id: str, outputs: dict[str, Any] | None = None) -> dict[str, Any]:
    return _move_with_result(run_dir, intent_id, "accepted", ok=True, outputs=outputs)


def reject_intent(run_dir: str | Path, intent_id: str, reason: str, outputs: dict[str, Any] | None = None) -> dict[str, Any]:
    return _move_with_result(run_dir, intent_id, "rejected", ok=False, reason=reason, outputs=outputs)


def complete_intent(run_dir: str | Path, intent_id: str, outputs: dict[str, Any] | None = None) -> dict[str, Any]:
    return _move_with_result(run_dir, intent_id, "completed", ok=True, outputs=outputs)


def block_intent(run_dir: str | Path, intent_id: str, reason: str, outputs: dict[str, Any] | None = None) -> dict[str, Any]:
    return _move_with_result(run_dir, intent_id, "blocked", ok=False, reason=reason, outputs=outputs)


def list_intents(run_dir: str | Path, state: str = "pending") -> list[dict[str, Any]]:
    return [_read_json(path) for path in sorted(_dir(run_dir, state).glob("intent_*.json"))]
```

- [ ] **Step 4: Run the tests**

Run:

```powershell
python -m unittest tests.test_agent_intents -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add skills/agent_intents.py tests/test_agent_intents.py
git commit -m "feat: 增加agent意图生命周期"
```

---

### Task 3: Initialize Message Runtime During Round Preparation

**Files:**
- Modify: `skills/agent_packets.py`
- Modify: `tests/test_agent_packets.py`

- [ ] **Step 1: Write failing tests for initialized messages and runtime directories**

In `tests/test_agent_packets.py`, add a focused test near the existing `prepare_agent_run` tests:

```python
    def test_prepare_agent_run_initializes_message_runtime(self):
        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="I open the door.",
            chat_log=[],
            card_data={"name": "Smoke"},
            character_contexts={"characters": [{"name": "Ada"}]},
            turn_index=1,
            input_payload={"raw_text": "I open the door.", "role_text": "I open the door.", "user_instruction_text": ""},
        )
        run_dir = Path(result["run_dir"])

        messages = [json.loads(line) for line in (run_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertGreaterEqual(len(messages), 2)
        self.assertTrue((run_dir / "inboxes" / "gm.jsonl").exists())
        self.assertTrue((run_dir / "inboxes" / "input_analyst.jsonl").exists())
        self.assertEqual(messages[0]["type"], "input_received")
        self.assertEqual(messages[1]["type"], "analysis_requested")
```

- [ ] **Step 2: Run the targeted test to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_packets.AgentPacketTest.test_prepare_agent_run_initializes_message_runtime -v
```

Expected: FAIL because `messages.jsonl` is not initialized.

- [ ] **Step 3: Import `agent_messages` and append initial messages**

In `skills/agent_packets.py`, add:

```python
import agent_messages
```

Inside `prepare_agent_run()`, after writing `input.json`, append:

```python
    agent_messages.append_message(
        run_dir,
        {
            "from": "main_agent",
            "to": ["gm", "input_analyst"],
            "type": "input_received",
            "visibility": "gm_only",
            "payload": {
                "input_path": "input.json",
                "raw_path": "input.raw.json",
                "raw_text_hash": input_request.get("source_integrity", {}).get("raw_text_sha256", ""),
            },
        },
    )
    agent_messages.append_message(
        run_dir,
        {
            "from": "main_agent",
            "to": ["input_analyst"],
            "type": "analysis_requested",
            "visibility": "gm_only",
            "payload": {
                "request_path": "input_analysis.request.md",
                "output_path": "input_analysis.output.json",
            },
        },
    )
```

Inside `rebuild_agent_run_from_analysis()`, after rewriting `input.json`, append:

```python
    agent_messages.append_message(
        root,
        {
            "from": "input_analyst",
            "to": ["gm"],
            "type": "analysis_applied",
            "visibility": "gm_only",
            "payload": {
                "input_path": "input.json",
                "analysis_path": "input_analysis.output.json",
                "routed_characters": _clean_text_list(routed_input.get("characters", [])),
            },
        },
    )
```

Add a local helper if needed:

```python
def _clean_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
```

- [ ] **Step 4: Run targeted packet tests**

Run:

```powershell
python -m unittest tests.test_agent_packets.AgentPacketTest.test_prepare_agent_run_initializes_message_runtime -v
python -m unittest tests.test_input_analysis.InputAnalysisApplyTest.test_apply_current_run_persists_analysis_updates_and_rebuilds_packets -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add skills/agent_packets.py tests/test_agent_packets.py
git commit -m "feat: 初始化回合消息运行时"
```

---

### Task 4: Actor Dispatch Intents And Projection Route

**Files:**
- Modify: `skills/agent_turn_loop.py`
- Modify: `tests/test_agent_turn_loop.py`

- [ ] **Step 1: Write failing test for GM actor call as projected message route**

In `tests/test_agent_turn_loop.py`, add:

```python
    def test_actor_call_creates_intent_projected_message_and_actor_response_message(self):
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "You hear a bell.",
                        "reason": "Ada can hear it.",
                        "visibility_basis": {"summary": "The bell is audible nearby."},
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "perception_responses": [],
                    "decision_point": {"reason": "Choose next.", "options": ["wait"]},
                    "stop_reason": "player_decision",
                }
            return {
                "agent": "character",
                "agent_id": "character:Ada",
                "character_name": "Ada",
                "events": [{
                    "type": "dialogue",
                    "content": "I heard it.",
                    "target": "player",
                    "source_call_id": "call-character-Ada-1",
                    "metadata": {"exact_visible_words": "I heard it."},
                }],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1, card_folder=self.card)

        self.assertTrue(result["ok"])
        messages = self.agent_messages.read_messages(self.run_dir)
        self.assertTrue(any(item["type"] == "request_actor" for item in messages))
        self.assertTrue(any(item["type"] == "projected_message" for item in messages))
        self.assertTrue(any(item["type"] == "actor_response" for item in messages))
        self.assertTrue((self.run_dir / "intents" / "completed").exists())
```

Add `self.agent_messages = _load_module("agent_messages")` in the test setup using the same helper pattern already used in the file.

- [ ] **Step 2: Run targeted test to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_turn_loop.AgentTurnLoopTest.test_actor_call_creates_intent_projected_message_and_actor_response_message -v
```

Expected: FAIL because `agent_turn_loop.py` does not write message/intents yet.

- [ ] **Step 3: Add message-backed dispatch helpers**

In `skills/agent_turn_loop.py`, import:

```python
import agent_intents
import agent_messages
```

Add helper functions near `_dispatch_actor_call()`:

```python
def _record_request_actor_intent(run_dir: Path, sender: str, actor_id: str, call: dict) -> dict:
    message_result = agent_messages.append_message(
        run_dir,
        {
            "from": sender,
            "to": ["projection"],
            "type": "request_actor",
            "visibility": "gm_only",
            "source_call_id": str(call.get("call_id") or ""),
            "payload": {
                "actor_id": actor_id,
                "call": call,
            },
        },
    )
    intent_result = agent_intents.create_intent(
        run_dir,
        {
            "requested_by": sender,
            "type": "project_message",
            "source_message_id": (message_result.get("message") or {}).get("id", ""),
            "payload": {
                "actor_id": actor_id,
                "source_call_id": str(call.get("call_id") or ""),
            },
        },
    )
    if intent_result.get("ok"):
        agent_intents.accept_intent(run_dir, intent_result["intent"]["id"])
    return {"message": message_result, "intent": intent_result}


def _record_projected_actor_message(run_dir: Path, actor_id: str, call: dict, packet: dict, intent_id: str = "") -> None:
    result = agent_messages.append_message(
        run_dir,
        {
            "from": "projection",
            "to": [actor_id],
            "type": "projected_message",
            "visibility": "actor_facing",
            "source_call_id": str(call.get("call_id") or ""),
            "payload": {
                "packet": packet,
                "gm_prompt": packet.get("gm_prompt", ""),
            },
        },
    )
    if intent_id:
        agent_intents.complete_intent(run_dir, intent_id, outputs={"projected_message_id": (result.get("message") or {}).get("id", "")})


def _record_actor_response_message(run_dir: Path, actor_id: str, call: dict, actor_output: dict) -> None:
    agent_messages.append_message(
        run_dir,
        {
            "from": actor_id,
            "to": ["gm"],
            "type": "actor_response",
            "visibility": "gm_only",
            "source_call_id": str(call.get("call_id") or ""),
            "payload": {
                "actor_id": actor_id,
                "output": actor_output,
            },
        },
    )
```

Inside `_dispatch_actor_call()`, after the actor packet is built and before `dispatch(actor_id, packet)`, call:

```python
    intent_record = _record_request_actor_intent(run_dir, "gm", actor_id, call)
    intent_id = ""
    if (intent_record.get("intent") or {}).get("ok"):
        intent_id = intent_record["intent"]["intent"]["id"]
    _record_projected_actor_message(run_dir, actor_id, call, packet, intent_id=intent_id)
```

After actor output validation, call:

```python
    _record_actor_response_message(run_dir, actor_id, call, actor_output)
```

If variable names differ, adapt only to the existing `_dispatch_actor_call()` local names and keep the helper signatures unchanged.

- [ ] **Step 4: Run targeted loop tests**

Run:

```powershell
python -m unittest tests.test_agent_turn_loop.AgentTurnLoopTest.test_actor_call_creates_intent_projected_message_and_actor_response_message -v
python -m unittest tests.test_agent_turn_loop -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add skills/agent_turn_loop.py tests/test_agent_turn_loop.py
git commit -m "feat: 通过消息和意图记录actor调度"
```

---

### Task 5: subGM Message Migration

**Files:**
- Modify: `skills/subgm_threads.py`
- Modify: `skills/subgm_turn_loop.py`
- Modify: `tests/test_subgm_threads.py`
- Modify: `tests/test_subgm_turn_loop.py`

- [ ] **Step 1: Write failing tests for common message mirroring**

In `tests/test_subgm_threads.py`, add:

```python
    def test_append_subgm_message_mirrors_to_common_message_bus(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [{
            "action": "start",
            "thread_id": "side_a",
            "title": "Side A",
            "outline": "Check the roof.",
            "time_window": "same time",
            "location": "roof",
            "objective": "Find clue",
            "allowed_characters": ["character:Ada"],
            "forbidden_characters": [],
            "priority": "normal",
            "message": "Start.",
            "metadata": {},
        }])

        self.subgm_threads.append_subgm_message(
            self.run_dir,
            "side_a",
            {"content": "The clue is ready.", "status": "needs_gm", "metadata": {}},
        )

        messages = self.agent_messages.read_messages(self.run_dir)
        self.assertTrue(any(item["from"] == "subGM:side_a" and item["to"] == ["gm"] for item in messages))
```

Load `agent_messages` in setup like the other module helpers.

- [ ] **Step 2: Run targeted test to verify failure**

Run:

```powershell
python -m unittest tests.test_subgm_threads.SubgmThreadsTest.test_append_subgm_message_mirrors_to_common_message_bus -v
```

Expected: FAIL because subGM messages only write side-thread `messages.jsonl`.

- [ ] **Step 3: Mirror GM and subGM side-thread messages**

In `skills/subgm_threads.py`, import:

```python
import agent_messages
```

In `_append_gm_message()`, after writing the side-thread JSONL record, append:

```python
    agent_messages.append_message(
        run_dir,
        {
            "from": "gm",
            "to": [f"subGM:{thread_id}"],
            "type": "message",
            "visibility": "gm_only",
            "thread_id": thread_id,
            "payload": {
                "action": action,
                "content": message,
                "metadata": metadata,
            },
        },
    )
```

In `append_subgm_message()`, after the side-thread record is accepted, append:

```python
    result = _append_jsonl(_messages_path(run_dir, safe_id), record)
    agent_messages.append_message(
        run_dir,
        {
            "from": f"subGM:{safe_id}",
            "to": ["gm"],
            "type": "message",
            "visibility": "gm_only",
            "thread_id": safe_id,
            "payload": {
                "action": action,
                "content": content,
                "status": status or state.get("status", ""),
                "metadata": dict(metadata),
            },
        },
    )
    return result
```

Preserve the existing return value from `_append_jsonl()`.

- [ ] **Step 4: Run subGM tests**

Run:

```powershell
python -m unittest tests.test_subgm_threads tests.test_subgm_turn_loop -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add skills/subgm_threads.py skills/subgm_turn_loop.py tests/test_subgm_threads.py tests/test_subgm_turn_loop.py
git commit -m "feat: 将subGM通信镜像到通用消息总线"
```

---

### Task 6: Artifact Directory And Legacy Mirror

**Files:**
- Modify: `skills/agent_outputs.py`
- Modify: `tests/test_agent_outputs.py`

- [ ] **Step 1: Write failing test for `artifacts/` layout**

In `tests/test_agent_outputs.py`, add:

```python
    def test_build_story_input_reads_artifacts_directory_when_present(self):
        artifacts = self.run_dir / "artifacts"
        artifacts.mkdir()
        for name in ("gm.output.json", "actor.outputs.json"):
            source = self.run_dir / name
            target = artifacts / name
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            source.unlink()

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(story_input["round_id"], self.run_dir.name)
        self.assertTrue((self.run_dir / "story.input.json").exists())
```

- [ ] **Step 2: Run targeted test to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_outputs.AgentOutputsTest.test_build_story_input_reads_artifacts_directory_when_present -v
```

Expected: FAIL because `agent_outputs.py` currently reads root artifact paths.

- [ ] **Step 3: Add artifact path resolver**

In `skills/agent_outputs.py`, add:

```python
def _artifact_path(root: Path, relative_path: str) -> Path:
    legacy = root / relative_path
    if legacy.exists():
        return legacy
    artifact = root / "artifacts" / relative_path
    if artifact.exists():
        return artifact
    return legacy
```

Update artifact reads in `_load_loop_outputs()`, `prepare_delivery()`, and any direct `root / expected_path` read:

```python
gm_path = _artifact_path(root, expected.get("gm", "gm.output.json"))
actors_path = _artifact_path(root, expected.get("actors", "actor.outputs.json"))
story_path = _artifact_path(run_dir, expected.get("story", "story.output.json"))
critic_path = _artifact_path(run_dir, expected.get("critic", "critic.report.json"))
```

When writing newly materialized files during migration, write both:

```python
def _write_artifact_with_legacy_mirror(root: Path, relative_path: str, payload: Dict[str, Any]) -> None:
    agent_run.write_json(root / "artifacts" / relative_path, payload)
    agent_run.write_json(root / relative_path, payload)
```

Use this for `story.input.json` only after adapting callers carefully. Keep legacy root writes until all tests and smoke use the new path.

- [ ] **Step 4: Run output tests**

Run:

```powershell
python -m unittest tests.test_agent_outputs -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add skills/agent_outputs.py tests/test_agent_outputs.py
git commit -m "feat: 支持agent产物目录布局"
```

---

### Task 7: Snapshot And Rollback Helpers

**Files:**
- Create: `skills/agent_snapshots.py`
- Create: `tests/test_agent_snapshots.py`

- [ ] **Step 1: Write failing tests for snapshot and rollback**

Create `tests/test_agent_snapshots.py`:

```python
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_snapshots():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_snapshots", ROOT / "skills" / "agent_snapshots.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentSnapshotsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.card.mkdir()
        (self.card / "chat_log.json").write_text("[]", encoding="utf-8")
        (self.card / ".card_data.json").write_text(json.dumps({"name": "A"}, ensure_ascii=False), encoding="utf-8")
        (self.card / "memory").mkdir()
        (self.card / "memory" / "project.md").write_text("old", encoding="utf-8")
        self.mod = _load_agent_snapshots()

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_snapshot_copies_card_state(self):
        result = self.mod.create_snapshot(self.card, "round-000001", reason="before_input")

        self.assertTrue(result["ok"])
        snapshot = Path(result["snapshot_dir"])
        self.assertTrue((snapshot / "chat_log.json").exists())
        self.assertTrue((snapshot / "memory" / "project.md").exists())
        self.assertEqual(json.loads((snapshot / "snapshot.json").read_text(encoding="utf-8"))["reason"], "before_input")

    def test_restore_snapshot_restores_files(self):
        result = self.mod.create_snapshot(self.card, "round-000001", reason="before_input")
        (self.card / "memory" / "project.md").write_text("changed", encoding="utf-8")

        restored = self.mod.restore_snapshot(self.card, result["snapshot_id"], mode="round_progression")

        self.assertTrue(restored["ok"])
        self.assertEqual((self.card / "memory" / "project.md").read_text(encoding="utf-8"), "old")
```

- [ ] **Step 2: Run the test to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_snapshots -v
```

Expected: FAIL with import error for missing `skills/agent_snapshots.py`.

- [ ] **Step 3: Implement snapshot helpers**

Create `skills/agent_snapshots.py`:

```python
"""Per-turn card snapshots for message-runtime rollback."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SNAPSHOT_ITEMS = [
    "chat_log.json",
    ".card_data.json",
    ".initvar.json",
    "ui_manifest.json",
    ".beautify_template.html",
    ".beautify.json",
    ".regex_scripts.json",
    "memory",
    ".agent_runs/current",
]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _snapshots_root(card_folder: str | Path) -> Path:
    return Path(card_folder) / ".agent_runs" / "snapshots"


def _copy_item(source: Path, target: Path) -> None:
    if not source.exists():
        return
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _remove_existing(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def create_snapshot(card_folder: str | Path, round_id: str, *, reason: str) -> dict:
    card = Path(card_folder)
    snapshot_id = f"{round_id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    root = _snapshots_root(card) / snapshot_id
    root.mkdir(parents=True, exist_ok=False)
    copied = []
    for rel in SNAPSHOT_ITEMS:
        source = card / rel
        if source.exists():
            _copy_item(source, root / rel)
            copied.append(rel)
    metadata = {
        "snapshot_id": snapshot_id,
        "round_id": round_id,
        "reason": reason,
        "created_at": _now(),
        "copied": copied,
    }
    (root / "snapshot.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "snapshot_id": snapshot_id, "snapshot_dir": str(root), "copied": copied}


def restore_snapshot(card_folder: str | Path, snapshot_id: str, *, mode: str) -> dict:
    card = Path(card_folder)
    root = _snapshots_root(card) / snapshot_id
    metadata_path = root / "snapshot.json"
    if not metadata_path.exists():
        return {"ok": False, "reason": "snapshot_missing", "snapshot_id": snapshot_id}
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    copied = metadata.get("copied", [])
    if not isinstance(copied, list):
        copied = []
    for rel in copied:
        target = card / str(rel)
        source = root / str(rel)
        _remove_existing(target)
        _copy_item(source, target)
    return {"ok": True, "snapshot_id": snapshot_id, "mode": mode, "restored": copied}
```

- [ ] **Step 4: Run snapshot tests**

Run:

```powershell
python -m unittest tests.test_agent_snapshots -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add skills/agent_snapshots.py tests/test_agent_snapshots.py
git commit -m "feat: 增加回合快照与恢复工具"
```

---

### Task 8: Create Snapshot At Round Preparation

**Files:**
- Modify: `skills/round_prepare.py`
- Modify: `tests/test_agent_packets.py`

- [ ] **Step 1: Write failing test for pre-turn snapshot**

In `tests/test_agent_packets.py`, add or extend a round-prepare integration test:

```python
        snapshots = list((self.card / ".agent_runs" / "snapshots").glob("*"))
        self.assertTrue(snapshots)
        metadata = json.loads((snapshots[0] / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["reason"], "before_round_prepare")
```

- [ ] **Step 2: Run targeted test to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_packets.AgentPacketTest.test_round_prepare_writes_agent_run_packets_and_reports_path -v
```

Expected: FAIL because no snapshot is created.

- [ ] **Step 3: Add snapshot call to `round_prepare.py`**

Import:

```python
import agent_snapshots
```

Immediately before `agent_packets.prepare_agent_run(...)`, call:

```python
        snapshot_result = agent_snapshots.create_snapshot(
            card_folder,
            f"round-{turn_index:06d}" if isinstance(turn_index, int) else "round-current",
            reason="before_round_prepare",
        )
```

Add the snapshot result into the printed JSON payload:

```python
            "snapshot": snapshot_result,
```

If `turn_index` is not in scope, derive it from the existing value passed to `prepare_agent_run()`.

- [ ] **Step 4: Run targeted tests**

Run:

```powershell
python -m unittest tests.test_agent_packets.AgentPacketTest.test_round_prepare_writes_agent_run_packets_and_reports_path -v
python -m unittest tests.test_agent_snapshots -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add skills/round_prepare.py tests/test_agent_packets.py
git commit -m "feat: 回合准备前创建存档快照"
```

---

### Task 9: Repair Requests As Intents

**Files:**
- Modify: `skills/agent_outputs.py`
- Modify: `skills/rp_generate_cli.py`
- Modify: `tests/test_agent_outputs.py`
- Modify: `tests/test_rp_generate_cli.py`

- [ ] **Step 1: Write failing test for critic repair intent**

In `tests/test_agent_outputs.py`, add:

```python
    def test_prepare_delivery_writes_repair_request_intent_for_critic_revise(self):
        self._write_story_and_critic(
            decision="revise",
            repair_instruction="Rewrite the stop point.",
            repair_routing={"stage": "story_composition", "rollback": "story_only", "risk": "low"},
        )

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "retry")
        pending = list((self.run_dir / "intents" / "pending").glob("intent_*.json"))
        self.assertTrue(pending)
        intent = json.loads(pending[0].read_text(encoding="utf-8"))
        self.assertEqual(intent["type"], "repair_request")
        self.assertEqual(intent["requested_by"], "critic")
```

- [ ] **Step 2: Run targeted test to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_outputs.AgentOutputsTest.test_prepare_delivery_writes_repair_request_intent_for_critic_revise -v
```

Expected: FAIL because no repair intent is written.

- [ ] **Step 3: Write repair intents in delivery gate**

In `skills/agent_outputs.py`, import:

```python
import agent_intents
import agent_messages
```

Add helper:

```python
def _record_repair_request_intent(card_folder: str | Path, run_dir: Path, critic_report: Dict[str, Any]) -> Dict[str, Any]:
    message = agent_messages.append_message(
        run_dir,
        {
            "from": "critic",
            "to": ["story", "gm", "main_agent"],
            "type": "repair_request",
            "visibility": "gm_only",
            "payload": {
                "decision": critic_report.get("decision", ""),
                "repair_instruction": critic_report.get("repair_instruction", ""),
                "repair_routing": self_repair.normalize_repair_routing(critic_report.get("repair_routing")),
            },
        },
    )
    return agent_intents.create_intent(
        run_dir,
        {
            "requested_by": "critic",
            "type": "repair_request",
            "source_message_id": (message.get("message") or {}).get("id", ""),
            "payload": {
                "critic_report_path": "critic.report.json",
                "repair_routing": self_repair.normalize_repair_routing(critic_report.get("repair_routing")),
            },
        },
    )
```

Call this helper in both `decision == "block"` and `decision == "revise"` before returning retry or blocked.

- [ ] **Step 4: Run delivery and CLI repair tests**

Run:

```powershell
python -m unittest tests.test_agent_outputs.AgentOutputsTest.test_prepare_delivery_writes_repair_request_intent_for_critic_revise -v
python -m unittest tests.test_agent_outputs -v
python -m unittest tests.test_rp_generate_cli -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add skills/agent_outputs.py skills/rp_generate_cli.py tests/test_agent_outputs.py tests/test_rp_generate_cli.py
git commit -m "feat: 将critic修复请求记录为意图"
```

---

### Task 10: Dispatcher Uses Repair Intent Metadata

**Files:**
- Modify: `skills/rp_generate_cli.py`
- Modify: `tests/test_rp_generate_cli.py`

- [ ] **Step 1: Write failing test for repair intent status transition**

In `tests/test_rp_generate_cli.py`, extend the existing critic revise test or add:

```python
    def test_run_round_completes_repair_intent_after_story_rewrite(self):
        result = self.module.run_round(
            self.card,
            self.root,
            run_claude=self.fake_run_claude_with_critic_revise_then_pass,
            run_command=self.fake_run_command,
        )

        completed = list((self.current_run / "intents" / "completed").glob("intent_*.json"))
        self.assertTrue(completed)
        self.assertTrue(any(json.loads(path.read_text(encoding="utf-8"))["type"] == "repair_request" for path in completed))
        self.assertTrue(result["ok"])
```

Use the fixture helpers already present in `tests/test_rp_generate_cli.py`; do not introduce live model calls.

- [ ] **Step 2: Run targeted test to verify failure**

Run:

```powershell
python -m unittest tests.test_rp_generate_cli.RpGenerateCliTest.test_run_round_completes_repair_intent_after_story_rewrite -v
```

Expected: FAIL because retry repair intents stay pending.

- [ ] **Step 3: Complete or block repair intents in `rp_generate_cli.py`**

Import:

```python
import agent_intents
```

Add helper:

```python
def _pending_repair_intents(run_dir: Path) -> list[dict[str, Any]]:
    return [
        item
        for item in agent_intents.list_intents(run_dir, "pending")
        if item.get("type") == "repair_request"
    ]


def _complete_pending_repair_intents(run_dir: Path, outputs: Dict[str, Any]) -> None:
    for intent in _pending_repair_intents(run_dir):
        agent_intents.complete_intent(run_dir, str(intent.get("id") or ""), outputs=outputs)


def _block_pending_repair_intents(run_dir: Path, reason: str, outputs: Dict[str, Any]) -> None:
    for intent in _pending_repair_intents(run_dir):
        agent_intents.block_intent(run_dir, str(intent.get("id") or ""), reason, outputs=outputs)
```

After a retry succeeds, call:

```python
        _complete_pending_repair_intents(run_dir, {"delivery": delivery_result})
```

If policy blocks route or retry budget ends, call:

```python
        _block_pending_repair_intents(run_dir, "repair_not_completed", {"delivery": delivery_result})
```

- [ ] **Step 4: Run CLI tests**

Run:

```powershell
python -m unittest tests.test_rp_generate_cli -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add skills/rp_generate_cli.py tests/test_rp_generate_cli.py
git commit -m "feat: 让生成流程推进修复意图状态"
```

---

### Task 11: Message Runtime Control-Plane Smoke

**Files:**
- Modify: `skills/control_plane_smoke.py`
- Modify: `tests/test_control_plane_smoke.py`

- [ ] **Step 1: Write failing smoke assertions**

In `tests/test_control_plane_smoke.py`, extend the payload assertions:

```python
        self.assertGreaterEqual(payload["messages"]["total"], 1)
        self.assertIn("projected_message", payload["messages"]["types"])
        self.assertGreaterEqual(payload["intents"]["completed"], 1)
        self.assertTrue(payload["snapshot"]["ok"])
```

- [ ] **Step 2: Run smoke test to verify failure**

Run:

```powershell
python -m unittest tests.test_control_plane_smoke -v
```

Expected: FAIL because smoke output does not expose message runtime evidence.

- [ ] **Step 3: Add message, intent, and snapshot evidence to smoke output**

In `skills/control_plane_smoke.py`, import:

```python
import agent_intents
import agent_messages
import agent_snapshots
```

After card setup and before `prepare_agent_run()`, create a snapshot:

```python
snapshot = agent_snapshots.create_snapshot(card, "round-smoke", reason="control_plane_smoke")
```

After the loop and delivery, collect:

```python
messages = agent_messages.read_messages(run_dir)
intent_counts = {
    "pending": len(agent_intents.list_intents(run_dir, "pending")),
    "accepted": len(agent_intents.list_intents(run_dir, "accepted")),
    "rejected": len(agent_intents.list_intents(run_dir, "rejected")),
    "completed": len(agent_intents.list_intents(run_dir, "completed")),
    "blocked": len(agent_intents.list_intents(run_dir, "blocked")),
}
```

Add to returned payload:

```python
            "messages": {
                "total": len(messages),
                "types": sorted({str(item.get("type") or "") for item in messages}),
            },
            "intents": intent_counts,
            "snapshot": snapshot,
```

- [ ] **Step 4: Run smoke checks**

Run:

```powershell
python -m unittest tests.test_control_plane_smoke -v
python skills/control_plane_smoke.py --repo .
```

Expected: PASS and command JSON includes `"messages"`, `"intents"`, and `"snapshot"`.

- [ ] **Step 5: Commit**

```powershell
git add skills/control_plane_smoke.py tests/test_control_plane_smoke.py
git commit -m "test: 在smoke中覆盖消息运行时"
```

---

### Task 12: Documentation Update

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Update architecture documentation**

In `README.md`, update the core subagent section to describe:

```markdown
Each round creates a message-driven runtime under `.agent_runs/<round>/`. `messages.jsonl` is the append-only communication log, `inboxes/` stores per-agent delivery indexes, `intents/` stores executable control-plane requests, and `artifacts/` stores materialized delivery files. Agents can request collaboration through messages and intents, while Python keeps ACL, projection, trace, snapshot, schema, and delivery gates authoritative.
```

In `CLAUDE.md`, update the data flow line to include:

```markdown
GM, subGM, story, critic, player, and character agents collaborate through `messages.jsonl` and `intents/`; actor-facing delivery must pass through projection before reaching `inboxes/player.jsonl` or `inboxes/character_<id>.jsonl`.
```

In `AGENTS.md`, update project structure with:

```markdown
`agent_messages.py`, `agent_intents.py`, and `agent_snapshots.py` implement the message runtime, executable intent lifecycle, and rollback snapshots.
```

- [ ] **Step 2: Run markdown whitespace check**

Run:

```powershell
git diff --check README.md CLAUDE.md AGENTS.md
```

Expected: no output.

- [ ] **Step 3: Commit docs**

```powershell
git add README.md CLAUDE.md AGENTS.md
git commit -m "docs: 记录agent消息运行时架构"
```

---

### Task 13: Final Verification

**Files:**
- Verify all files touched in Tasks 1-12.

- [ ] **Step 1: Run full unit tests**

```powershell
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Run control-plane smoke**

```powershell
python skills/control_plane_smoke.py --repo .
```

Expected: JSON output includes `"ok": true`, `"manifest_stage": "delivered"`, `"messages"`, `"intents"`, and `"snapshot"`.

- [ ] **Step 3: Run compile checks**

```powershell
python -m py_compile skills/agent_messages.py skills/agent_intents.py skills/agent_snapshots.py skills/agent_turn_loop.py skills/subgm_threads.py skills/subgm_turn_loop.py skills/agent_outputs.py skills/rp_generate_cli.py skills/round_prepare.py skills/agent_packets.py
```

Expected: no output and exit code 0.

- [ ] **Step 4: Check git status**

```powershell
git status --short --branch
```

Expected: only intentional files are changed. `重构建议.md` may remain untracked if the user has not asked to commit it.

- [ ] **Step 5: Commit final verification fixes if any**

If verification required code or docs adjustments:

```powershell
git add <changed-files>
git commit -m "fix: 完成消息运行时验证修正"
```

---

## Self-Review

Spec coverage:

- Message bus is covered by Tasks 1, 3, 4, 5, and 11.
- Intent dispatcher is covered by Tasks 2, 4, 9, 10, and 11.
- Projection as mandatory actor-facing route is covered by Tasks 1 and 4.
- Snapshot and rollback foundation is covered by Tasks 7 and 8.
- Artifact materialization is covered by Task 6.
- Story and critic repair as intents is covered by Tasks 9 and 10.
- Documentation and final acceptance are covered by Tasks 12 and 13.

Placeholder scan:

- This plan contains no unresolved marker words or unnamed implementation steps.
- Code snippets define every new public function referenced by later tasks.

Type consistency:

- Message IDs use `msg_000001`.
- Intent IDs use `intent_000001`.
- Agent IDs use existing runtime names: `gm`, `player`, `character:<name>`, `subGM:<thread_id>`, `story`, `critic`, `projection`, `main_agent`, and `assets_ui`.
- New modules are `agent_messages.py`, `agent_intents.py`, and `agent_snapshots.py`.
