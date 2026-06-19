# Mainline Actor Batch Scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make mainline GM `parallel_groups` executable by dispatching safe actor calls in deterministic batches while downgrading unsafe groups to serial routing with traceable warnings.

**Architecture:** Add a focused `skills/agent_actor_batches.py` planning helper that converts normalized GM `actor_calls` plus `parallel_groups` into serial or parallel batches. Keep `agent_turn_loop.py` responsible for actor packet creation, dispatch, event handling, and output persistence; it will execute helper-produced batches with `ThreadPoolExecutor` only when a batch is safe and has more than one call. Trace recording stays in `agent_interactions.py` through new actor-batch and routing-warning fields.

**Tech Stack:** Python standard library, `unittest`, JSON artifacts under `.agent_runs/<round>/`, Claude Code skill Markdown, README.

---

## Scope

This plan implements P1 from `docs/superpowers/specs/2026-06-19-rp-control-plane-hardening-design.md`.

Included:

- Convert GM `actor_calls` and `parallel_groups` into deterministic dispatch batches.
- Execute safe mainline actor batches concurrently, capped by `max_parallel_subagents`.
- Force serial routing for duplicate actors, dependent calls, malformed groups, and calls outside valid groups.
- Preserve same-actor multi-call behavior, dialogue transfer continuation behavior, perception request recording, decision stops, and terminal GM stop behavior.
- Record actual actor batch decisions and routing warnings into `interaction.trace.json` and story input summaries.
- Update `rp-gm-actor-routing` and README wording so documentation matches executable parallel groups.

Deferred to later plans:

- P2 visibility proof fields and stricter actor projection.
- P3 perception feedback re-call closure, structured dialogue transfer fields, custom actor actions, and post-round memory jobs.
- subGM boundary proof fields beyond existing reservation conflict checks.

## File Structure

- Create `skills/agent_actor_batches.py`: pure helper for batch planning, max-parallel extraction, and warning normalization.
- Create `tests/test_agent_actor_batches.py`: unit tests for batch planning rules.
- Modify `skills/agent_interactions.py`: add `actor_batches` and `routing_warnings` trace recording and summary exposure.
- Modify `tests/test_agent_interactions.py`: unit tests for new trace fields and hidden-shaped ID sanitization.
- Modify `skills/agent_turn_loop.py`: replace the single serial `actor_queue` loop with batch planning plus deterministic concurrent dispatch.
- Modify `tests/test_agent_turn_loop.py`: integration tests for actual parallel dispatch, safe downgrade, deterministic outputs, and transfer ordering.
- Modify `.claude/skills/rp-gm-actor-routing.md`: replace metadata-only parallel guidance with executable scheduling guidance.
- Modify `tests/test_gm_skill_contracts.py`: lock the updated actor-routing contract.
- Modify `README.md`: document that safe `parallel_groups` execute as batches and unsafe groups downgrade to serial routing.

## Task 1: Actor Batch Planner Tests

**Files:**
- Create: `tests/test_agent_actor_batches.py`
- Read: `skills/agent_turn_loop.py:597-659`
- Read: `.claude/skills/rp-gm-actor-routing.md`

- [ ] **Step 1: Write failing planner tests**

Create `tests/test_agent_actor_batches.py`:

```python
import importlib.util
import sys
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


def call(call_id, actor_id, source_call_id=""):
    payload = {
        "call_id": call_id,
        "actor_id": actor_id,
        "prompt": f"Prompt for {actor_id}",
        "reason": "test",
    }
    if source_call_id:
        payload["source_call_id"] = source_call_id
    return payload


class ActorBatchPlannerTest(unittest.TestCase):
    def setUp(self):
        self.batches = load_module("agent_actor_batches")

    def test_parallel_group_is_chunked_by_max_parallel_and_preserves_order(self):
        calls = [
            call("call-character-Ada-1", "character:Ada"),
            call("call-character-Bea-1", "character:Bea"),
            call("call-character-Cora-1", "character:Cora"),
            call("call-character-Dana-1", "character:Dana"),
        ]

        plan = self.batches.build_actor_batches(
            calls,
            [{"group_id": "group-main", "actors": ["character:Ada", "character:Bea", "character:Cora"]}],
            max_parallel=2,
        )

        self.assertEqual(plan["warnings"], [])
        self.assertEqual(
            [
                (batch["kind"], batch["group_id"], [item["actor_id"] for item in batch["calls"]])
                for batch in plan["batches"]
            ],
            [
                ("parallel", "group-main", ["character:Ada", "character:Bea"]),
                ("serial", "group-main", ["character:Cora"]),
                ("serial", "", ["character:Dana"]),
            ],
        )

    def test_duplicate_actor_group_downgrades_to_serial_with_warning(self):
        calls = [
            call("call-character-SuLi-1", "character:SuLi"),
            call("call-character-SuLi-2", "character:SuLi"),
        ]

        plan = self.batches.build_actor_batches(
            calls,
            [{"group_id": "group-duplicate", "call_ids": ["call-character-SuLi-1", "call-character-SuLi-2"]}],
            max_parallel=2,
        )

        self.assertEqual(
            [(batch["kind"], [item["call_id"] for item in batch["calls"]]) for batch in plan["batches"]],
            [
                ("serial", ["call-character-SuLi-1"]),
                ("serial", ["call-character-SuLi-2"]),
            ],
        )
        self.assertEqual(plan["warnings"][0]["code"], "duplicate_actor_in_parallel_group")
        self.assertEqual(plan["warnings"][0]["group_id"], "group-duplicate")

    def test_dependent_call_group_downgrades_to_serial_with_warning(self):
        calls = [
            call("call-character-Ada-1", "character:Ada", source_call_id="call-player-1"),
            call("call-character-Bea-1", "character:Bea"),
        ]

        plan = self.batches.build_actor_batches(
            calls,
            [{"group_id": "group-dependent", "actors": ["character:Ada", "character:Bea"]}],
            max_parallel=2,
        )

        self.assertEqual(
            [(batch["kind"], [item["actor_id"] for item in batch["calls"]]) for batch in plan["batches"]],
            [
                ("serial", ["character:Ada"]),
                ("serial", ["character:Bea"]),
            ],
        )
        self.assertEqual(plan["warnings"][0]["code"], "dependent_call_in_parallel_group")

    def test_unknown_group_member_downgrades_without_losing_calls(self):
        calls = [
            call("call-character-Ada-1", "character:Ada"),
            call("call-character-Bea-1", "character:Bea"),
        ]

        plan = self.batches.build_actor_batches(
            calls,
            [{"group_id": "group-missing", "actors": ["character:Ada", "character:Cora"]}],
            max_parallel=2,
        )

        self.assertEqual(
            [(batch["kind"], [item["actor_id"] for item in batch["calls"]]) for batch in plan["batches"]],
            [
                ("serial", ["character:Ada"]),
                ("serial", ["character:Bea"]),
            ],
        )
        self.assertEqual(plan["warnings"][0]["code"], "unknown_parallel_group_member")

    def test_max_parallel_from_input_reads_card_orchestration_and_defaults_to_two(self):
        self.assertEqual(
            self.batches.max_parallel_from_input({
                "card_data": {"character_orchestration": {"max_parallel_subagents": 3}}
            }),
            3,
        )
        self.assertEqual(self.batches.max_parallel_from_input({"card_data": {}}), 2)
        self.assertEqual(
            self.batches.max_parallel_from_input({
                "card_data": {"character_orchestration": {"max_parallel_subagents": 0}}
            }),
            1,
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the planner tests and verify failure**

Run:

```powershell
python -m unittest tests.test_agent_actor_batches -v
```

Expected before implementation:

- Fails with `FileNotFoundError` or import failure for `skills/agent_actor_batches.py`.

- [ ] **Step 3: Commit the red tests**

Run:

```powershell
git add tests/test_agent_actor_batches.py
git commit -m "test: 覆盖主线角色批次规划"
```

## Task 2: Actor Batch Planner Implementation

**Files:**
- Create: `skills/agent_actor_batches.py`
- Test: `tests/test_agent_actor_batches.py`

- [ ] **Step 1: Implement the pure planner helper**

Create `skills/agent_actor_batches.py`:

```python
"""Deterministic mainline actor-call batch planning."""

from __future__ import annotations

from typing import Any, Iterable


DEFAULT_MAX_PARALLEL = 2


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def max_parallel_from_input(input_payload: dict, default: int = DEFAULT_MAX_PARALLEL) -> int:
    card_data = _dict(input_payload.get("card_data"))
    orchestration = _dict(card_data.get("character_orchestration"))
    raw = orchestration.get("max_parallel_subagents", default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(1, value)


def _chunk(items: list[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _group_id(group: Any, fallback: str) -> str:
    if isinstance(group, dict):
        return _text(group.get("group_id")) or fallback
    return fallback


def _group_actor_ids(group: Any) -> list[str]:
    if isinstance(group, dict):
        raw = group.get("actors") or group.get("actor_ids") or []
    else:
        raw = group
    if isinstance(raw, (str, bytes, dict)):
        return []
    return [_text(item) for item in _list(list(raw) if not isinstance(raw, list) else raw) if _text(item)]


def _group_call_ids(group: Any) -> list[str]:
    if not isinstance(group, dict):
        return []
    raw = group.get("call_ids") or []
    if isinstance(raw, (str, bytes, dict)):
        return []
    return [_text(item) for item in _list(list(raw) if not isinstance(raw, list) else raw) if _text(item)]


def _warning(code: str, group_id: str, actors: list[str], call_ids: list[str], message: str) -> dict:
    return {
        "code": code,
        "group_id": group_id,
        "actors": actors,
        "call_ids": call_ids,
        "message": message,
    }


def _serial_batch(call: dict) -> dict:
    return {"kind": "serial", "group_id": "", "calls": [call]}


def _resolve_group_indices(
    group: Any,
    group_id: str,
    calls: list[dict],
    consumed: set[int],
) -> tuple[list[int], dict | None]:
    ids = _group_call_ids(group)
    actors = _group_actor_ids(group)
    call_ids = [_text(call.get("call_id")) for call in calls]
    actor_ids = [_text(call.get("actor_id")) for call in calls]

    if ids:
        indices = []
        missing = []
        for raw_id in ids:
            if raw_id in call_ids:
                index = call_ids.index(raw_id)
                if index not in consumed:
                    indices.append(index)
            else:
                missing.append(raw_id)
        if missing:
            return [], _warning(
                "unknown_parallel_group_member",
                group_id,
                actors,
                ids,
                "parallel group referenced call_ids that are not present in actor_calls",
            )
        return sorted(indices), None

    if actors:
        indices = []
        missing = []
        for actor_id in actors:
            matches = [
                index
                for index, candidate in enumerate(actor_ids)
                if candidate == actor_id and index not in consumed
            ]
            if len(matches) != 1:
                missing.append(actor_id)
            else:
                indices.append(matches[0])
        if missing:
            return [], _warning(
                "unknown_parallel_group_member",
                group_id,
                actors,
                ids,
                "parallel group referenced actors that do not map to exactly one pending actor_call",
            )
        return sorted(indices), None

    return [], _warning(
        "empty_parallel_group",
        group_id,
        actors,
        ids,
        "parallel group did not list actors, actor_ids, or call_ids",
    )


def _unsafe_group_warning(group_id: str, indices: list[int], calls: list[dict]) -> dict | None:
    actors = [_text(calls[index].get("actor_id")) for index in indices]
    call_ids = [_text(calls[index].get("call_id")) for index in indices]
    if len(set(actors)) != len(actors):
        return _warning(
            "duplicate_actor_in_parallel_group",
            group_id,
            actors,
            call_ids,
            "same actor cannot be dispatched concurrently with itself",
        )
    if any(_text(calls[index].get("source_call_id")) for index in indices):
        return _warning(
            "dependent_call_in_parallel_group",
            group_id,
            actors,
            call_ids,
            "actor_calls with source_call_id are dependent continuations and must run serially",
        )
    if len(indices) < 2:
        return _warning(
            "parallel_group_too_small",
            group_id,
            actors,
            call_ids,
            "parallel group needs at least two safe calls to execute concurrently",
        )
    return None


def build_actor_batches(
    actor_calls: list[dict],
    parallel_groups: list[Any],
    *,
    max_parallel: int = DEFAULT_MAX_PARALLEL,
) -> dict:
    calls = [call for call in actor_calls if isinstance(call, dict)]
    limit = max(1, int(max_parallel or 1))
    consumed: set[int] = set()
    group_by_first_index: dict[int, tuple[str, list[int]]] = {}
    warnings: list[dict] = []

    for group_number, group in enumerate(_list(parallel_groups), start=1):
        group_id = _group_id(group, f"group-1-{group_number}")
        indices, warning = _resolve_group_indices(group, group_id, calls, consumed)
        if warning:
            warnings.append(warning)
            continue
        unsafe = _unsafe_group_warning(group_id, indices, calls)
        if unsafe:
            warnings.append(unsafe)
            continue
        for index in indices:
            consumed.add(index)
        group_by_first_index[min(indices)] = (group_id, indices)

    batches: list[dict] = []
    emitted: set[int] = set()
    for index, call in enumerate(calls):
        if index in emitted:
            continue
        group_info = group_by_first_index.get(index)
        if not group_info:
            batches.append(_serial_batch(call))
            emitted.add(index)
            continue
        group_id, indices = group_info
        for chunk in _chunk(indices, limit):
            chunk_calls = [calls[item] for item in chunk]
            batches.append({
                "kind": "parallel" if len(chunk_calls) > 1 else "serial",
                "group_id": group_id,
                "calls": chunk_calls,
            })
            emitted.update(chunk)

    return {"batches": batches, "warnings": warnings}
```

- [ ] **Step 2: Run planner tests**

Run:

```powershell
python -m unittest tests.test_agent_actor_batches -v
```

Expected:

```text
OK
```

- [ ] **Step 3: Run py_compile for the new module**

Run:

```powershell
python -m py_compile skills/agent_actor_batches.py
```

Expected: exit code `0` and no output.

- [ ] **Step 4: Commit the planner implementation**

Run:

```powershell
git add skills/agent_actor_batches.py
git commit -m "feat: 增加主线角色批次规划器"
```

## Task 3: Interaction Trace Batch Tests

**Files:**
- Modify: `tests/test_agent_interactions.py`
- Read: `skills/agent_interactions.py:91-120`
- Read: `skills/agent_interactions.py:165-181`

- [ ] **Step 1: Add failing trace tests**

Add these tests to `AgentInteractionTraceTest` in `tests/test_agent_interactions.py`:

```python
    def test_trace_records_actor_batches_and_routing_warnings(self):
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "character:Ada", "character:Bea"],
            chapter_target_words=1800,
        )

        self.agent_interactions.record_actor_batch(
            self.run_dir,
            batch_id="batch-1-1",
            kind="parallel",
            actors=["character:Ada", "character:Bea"],
            call_ids=["call-character-Ada-1", "call-character-Bea-1"],
            group_id="group-main",
        )
        self.agent_interactions.record_routing_warning(
            self.run_dir,
            code="dependent_call_in_parallel_group",
            message="dependent calls run serially",
            group_id="group-main",
            actors=["character:Ada"],
            call_ids=["call-character-Ada-2"],
        )

        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        summary = self.agent_interactions.summarize_for_story_input(self.run_dir)

        self.assertEqual(trace["actor_batches"], [{
            "batch_id": "batch-1-1",
            "kind": "parallel",
            "group_id": "group-main",
            "actors": ["character:Ada", "character:Bea"],
            "call_ids": ["call-character-Ada-1", "call-character-Bea-1"],
        }])
        self.assertEqual(summary["actor_batches"], trace["actor_batches"])
        self.assertEqual(summary["routing_warnings"][0]["code"], "dependent_call_in_parallel_group")
        self.assertEqual(summary["routing_warnings"][0]["group_id"], "group-main")

    def test_trace_summary_sanitizes_hidden_shaped_batch_and_warning_ids(self):
        self.agent_interactions.init_trace(self.run_dir, participants=["gm"])
        self.agent_interactions.record_actor_batch(
            self.run_dir,
            batch_id="HiddenTruthBatch",
            kind="parallel",
            actors=["character:Ada", "HiddenTruthActor"],
            call_ids=["call-character-Ada-1", "worldTruthCall"],
            group_id="HiddenTruthGroup",
        )
        self.agent_interactions.record_routing_warning(
            self.run_dir,
            code="dependent_call_in_parallel_group",
            message="GM-only moon base",
            group_id="HiddenTruthGroup",
            actors=["character:Ada", "HiddenTruthActor"],
            call_ids=["call-character-Ada-1", "worldTruthCall"],
        )

        summary = self.agent_interactions.summarize_for_story_input(self.run_dir)

        self.assertEqual(summary["actor_batches"], [])
        self.assertEqual(summary["routing_warnings"], [{
            "code": "dependent_call_in_parallel_group",
            "message": "[redacted]",
            "group_id": "",
            "actors": ["character:Ada"],
            "call_ids": ["call-character-Ada-1"],
        }])
```

- [ ] **Step 2: Run the trace tests and verify failure**

Run:

```powershell
python -m unittest tests.test_agent_interactions.AgentInteractionTraceTest.test_trace_records_actor_batches_and_routing_warnings tests.test_agent_interactions.AgentInteractionTraceTest.test_trace_summary_sanitizes_hidden_shaped_batch_and_warning_ids -v
```

Expected before implementation:

- Fails with `AttributeError` because `record_actor_batch` and `record_routing_warning` do not exist.

- [ ] **Step 3: Commit the red trace tests**

Run:

```powershell
git add tests/test_agent_interactions.py
git commit -m "test: 覆盖角色批次轨迹记录"
```

## Task 4: Interaction Trace Batch Implementation

**Files:**
- Modify: `skills/agent_interactions.py`
- Test: `tests/test_agent_interactions.py`

- [ ] **Step 1: Extend safe ID patterns**

In `skills/agent_interactions.py`, add this regex to `SAFE_ID_PATTERNS` after the existing `group-...` pattern:

```python
    re.compile(r"^batch-[a-z0-9]+(?:-[a-z0-9]+)*$"),
```

- [ ] **Step 2: Add normalizers for actor batches and routing warnings**

Add these helper functions after `_parallel_groups`:

```python
def _safe_warning_code(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"[a-z][a-z0-9_]*", text):
        return text
    return ""


def _safe_warning_message(value: Any) -> str:
    text = str(value or "")
    compact = re.sub(r"[^a-z0-9]", "", text.lower())
    if any(token in compact for token in HIDDEN_ID_TOKENS):
        return "[redacted]"
    return text[:500]


def _actor_batches(trace: Dict[str, Any]) -> list[Dict[str, Any]]:
    batches = trace.get("actor_batches", [])
    if not isinstance(batches, list):
        return []
    normalized = []
    for item in batches:
        if not isinstance(item, dict):
            continue
        batch_id = _safe_id(item.get("batch_id", ""))
        if not batch_id:
            continue
        kind = str(item.get("kind") or "")
        if kind not in {"serial", "parallel"}:
            kind = "serial"
        normalized.append({
            "batch_id": batch_id,
            "kind": kind,
            "group_id": _safe_id(item.get("group_id", "")),
            "actors": _safe_id_list(item.get("actors", [])),
            "call_ids": _safe_id_list(item.get("call_ids", [])),
        })
    return normalized


def _routing_warnings(trace: Dict[str, Any]) -> list[Dict[str, Any]]:
    warnings = trace.get("routing_warnings", [])
    if not isinstance(warnings, list):
        return []
    normalized = []
    for item in warnings:
        if not isinstance(item, dict):
            continue
        code = _safe_warning_code(item.get("code", ""))
        if not code:
            continue
        normalized.append({
            "code": code,
            "message": _safe_warning_message(item.get("message", "")),
            "group_id": _safe_id(item.get("group_id", "")),
            "actors": _safe_id_list(item.get("actors", [])),
            "call_ids": _safe_id_list(item.get("call_ids", [])),
        })
    return normalized
```

- [ ] **Step 3: Initialize the new trace fields**

In `init_trace`, add these keys to the `trace` dict:

```python
        "actor_batches": [],
        "routing_warnings": [],
```

In `append_event` and `mark_decision_point`, after the existing `parallel_groups` list guard, add:

```python
    if not isinstance(trace.get("actor_batches"), list):
        trace["actor_batches"] = []
    if not isinstance(trace.get("routing_warnings"), list):
        trace["routing_warnings"] = []
```

- [ ] **Step 4: Add public recording functions**

Add these functions after `record_parallel_group`:

```python
def record_actor_batch(
    run_dir: str | Path,
    batch_id: str,
    kind: str,
    actors: Iterable[str],
    call_ids: Iterable[str],
    group_id: str = "",
) -> Dict[str, Any]:
    trace = _read(run_dir) or init_trace(run_dir)
    trace["schema_version"] = 2
    batches = trace.get("actor_batches")
    if not isinstance(batches, list):
        batches = []
        trace["actor_batches"] = batches
    safe_batch_id = _safe_id(batch_id)
    if safe_batch_id:
        batch_kind = str(kind or "")
        if batch_kind not in {"serial", "parallel"}:
            batch_kind = "serial"
        batches.append({
            "batch_id": safe_batch_id,
            "kind": batch_kind,
            "group_id": _safe_id(group_id),
            "actors": _safe_id_list(actors),
            "call_ids": _safe_id_list(call_ids),
        })
    trace["updated_at"] = _now()
    return _write(run_dir, trace)


def record_routing_warning(
    run_dir: str | Path,
    code: str,
    message: str,
    group_id: str = "",
    actors: Iterable[str] | None = None,
    call_ids: Iterable[str] | None = None,
) -> Dict[str, Any]:
    trace = _read(run_dir) or init_trace(run_dir)
    trace["schema_version"] = 2
    warnings = trace.get("routing_warnings")
    if not isinstance(warnings, list):
        warnings = []
        trace["routing_warnings"] = warnings
    safe_code = _safe_warning_code(code)
    if safe_code:
        warnings.append({
            "code": safe_code,
            "message": _safe_warning_message(message),
            "group_id": _safe_id(group_id),
            "actors": _safe_id_list(actors or []),
            "call_ids": _safe_id_list(call_ids or []),
        })
    trace["updated_at"] = _now()
    return _write(run_dir, trace)
```

- [ ] **Step 5: Expose new fields in summaries**

In every return value from `summarize_for_story_input`, add:

```python
            "actor_batches": [],
            "routing_warnings": [],
```

for missing or invalid traces, and add these keys to the normal summary return:

```python
        "actor_batches": _actor_batches(trace),
        "routing_warnings": _routing_warnings(trace),
```

- [ ] **Step 6: Run trace tests**

Run:

```powershell
python -m unittest tests.test_agent_interactions.AgentInteractionTraceTest -v
```

Expected:

```text
OK
```

- [ ] **Step 7: Run py_compile**

Run:

```powershell
python -m py_compile skills/agent_interactions.py
```

Expected: exit code `0` and no output.

- [ ] **Step 8: Commit trace implementation**

Run:

```powershell
git add skills/agent_interactions.py
git commit -m "feat: 记录角色批次调度轨迹"
```

## Task 5: Turn Loop Parallel Dispatch Tests

**Files:**
- Modify: `tests/test_agent_turn_loop.py`
- Read: `skills/agent_turn_loop.py:546-686`

- [ ] **Step 1: Add imports for concurrency tests**

At the top of `tests/test_agent_turn_loop.py`, add:

```python
import threading
```

- [ ] **Step 2: Add failing parallel-dispatch and downgrade tests**

Add these test methods to `AgentTurnLoopTest`:

```python
    def test_parallel_group_dispatches_safe_actor_calls_concurrently(self):
        self.register_characters("Ada", "Bea")
        barrier = threading.Barrier(2)
        actor_entries = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "Ada and Bea react independently."}],
                    "events": [],
                    "actor_calls": [
                        {
                            "call_id": "call-character-Ada-1",
                            "actor_id": "character:Ada",
                            "prompt": "You see the north door.",
                            "reason": "Independent visible stimulus.",
                        },
                        {
                            "call_id": "call-character-Bea-1",
                            "actor_id": "character:Bea",
                            "prompt": "You see the south door.",
                            "reason": "Independent visible stimulus.",
                        },
                    ],
                    "parallel_groups": [{
                        "group_id": "group-main",
                        "actors": ["character:Ada", "character:Bea"],
                    }],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertIn(agent_key, {"character:Ada", "character:Bea"})
            actor_entries.append(agent_key)
            barrier.wait(timeout=2)
            return {
                "agent": "character",
                "agent_id": agent_key,
                "character_name": agent_key.split(":", 1)[1],
                "events": [{"type": "action", "target": "", "content": f"{agent_key} reacts independently."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertEqual(result["called_actors"], ["character:Ada", "character:Bea"])
        self.assertCountEqual(actor_entries, ["character:Ada", "character:Bea"])
        trace = self.agent_run.read_json(self.run_dir / "interaction.trace.json")
        self.assertEqual(trace["actor_batches"][0]["kind"], "parallel")
        self.assertEqual(trace["actor_batches"][0]["actors"], ["character:Ada", "character:Bea"])
        self.assertEqual(trace.get("routing_warnings", []), [])

    def test_dependent_parallel_group_is_downgraded_to_serial_and_warned(self):
        self.register_characters("Ada", "Bea")
        actor_order = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "A dependent transfer is pending."}],
                    "events": [],
                    "actor_calls": [
                        {
                            "call_id": "call-character-Ada-1",
                            "actor_id": "character:Ada",
                            "prompt": "You answer the prior line.",
                            "reason": "Dependent response.",
                            "source_call_id": "call-player-1",
                        },
                        {
                            "call_id": "call-character-Bea-1",
                            "actor_id": "character:Bea",
                            "prompt": "You wait nearby.",
                            "reason": "Independent witness.",
                        },
                    ],
                    "parallel_groups": [{
                        "group_id": "group-dependent",
                        "actors": ["character:Ada", "character:Bea"],
                    }],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            actor_order.append(agent_key)
            return {
                "agent": "character",
                "agent_id": agent_key,
                "character_name": agent_key.split(":", 1)[1],
                "events": [{"type": "action", "target": "", "content": f"{agent_key} responds."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertEqual(actor_order, ["character:Ada", "character:Bea"])
        self.assertEqual(result["called_actors"], ["character:Ada", "character:Bea"])
        trace = self.agent_run.read_json(self.run_dir / "interaction.trace.json")
        self.assertEqual([batch["kind"] for batch in trace["actor_batches"]], ["serial", "serial"])
        self.assertEqual(trace["routing_warnings"][0]["code"], "dependent_call_in_parallel_group")

    def test_parallel_batch_outputs_merge_in_call_order_and_schedule_transfer_after_batch(self):
        self.register_characters("Ada", "Bea", "Cora")
        barrier = threading.Barrier(2)
        actor_order = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "Ada and Bea speak before Cora answers."}],
                    "events": [],
                    "actor_calls": [
                        {
                            "call_id": "call-character-Ada-1",
                            "actor_id": "character:Ada",
                            "prompt": "You speak first.",
                            "reason": "Independent opening line.",
                        },
                        {
                            "call_id": "call-character-Bea-1",
                            "actor_id": "character:Bea",
                            "prompt": "You speak second.",
                            "reason": "Independent opening line.",
                        },
                    ],
                    "parallel_groups": [{
                        "group_id": "group-openers",
                        "actors": ["character:Ada", "character:Bea"],
                    }],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            actor_order.append(agent_key)
            if agent_key in {"character:Ada", "character:Bea"}:
                barrier.wait(timeout=2)
            if agent_key == "character:Ada":
                events = [{"type": "dialogue", "target": "character:Cora", "content": "Cora, check the door."}]
            else:
                events = [{"type": "action", "target": "", "content": f"{agent_key} watches."}]
            return {
                "agent": "character",
                "agent_id": agent_key,
                "character_name": agent_key.split(":", 1)[1],
                "events": events,
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertEqual(result["called_actors"], ["character:Ada", "character:Bea", "character:Cora"])
        self.assertEqual(actor_order[-1], "character:Cora")
        actor_outputs = self.agent_run.read_json(self.run_dir / "actor.outputs.json")
        self.assertEqual(list(actor_outputs), ["character:Ada", "character:Bea", "character:Cora"])
```

- [ ] **Step 3: Run the new turn-loop tests and verify failure**

Run:

```powershell
python -m unittest tests.test_agent_turn_loop.AgentTurnLoopTest.test_parallel_group_dispatches_safe_actor_calls_concurrently tests.test_agent_turn_loop.AgentTurnLoopTest.test_dependent_parallel_group_is_downgraded_to_serial_and_warned tests.test_agent_turn_loop.AgentTurnLoopTest.test_parallel_batch_outputs_merge_in_call_order_and_schedule_transfer_after_batch -v
```

Expected before implementation:

- The first and third tests fail with `threading.BrokenBarrierError` because actor calls are still serial.
- The downgrade test fails because no `actor_batches` or `routing_warnings` are recorded.

- [ ] **Step 4: Commit the red turn-loop tests**

Run:

```powershell
git add tests/test_agent_turn_loop.py
git commit -m "test: 覆盖主线角色并行批次调度"
```

## Task 6: Turn Loop Batch Execution Implementation

**Files:**
- Modify: `skills/agent_turn_loop.py`
- Test: `tests/test_agent_turn_loop.py`
- Read: `skills/agent_actor_batches.py`
- Read: `skills/agent_interactions.py`

- [ ] **Step 1: Add imports**

In `skills/agent_turn_loop.py`, add:

```python
import concurrent.futures
```

and add:

```python
import agent_actor_batches
```

- [ ] **Step 2: Add helpers for batch trace recording**

Add these helpers above `run_interactive_loop`:

```python
def _batch_actors(batch: dict) -> list[str]:
    return [str(call.get("actor_id") or "") for call in batch.get("calls", []) if isinstance(call, dict)]


def _batch_call_ids(batch: dict) -> list[str]:
    return [str(call.get("call_id") or "") for call in batch.get("calls", []) if isinstance(call, dict)]


def _record_actor_batch_plan(run_dir: Path, step_index: int, batch_index: int, batch: dict) -> None:
    agent_interactions.record_actor_batch(
        run_dir,
        batch_id=f"batch-{step_index + 1}-{batch_index + 1}",
        kind=str(batch.get("kind") or "serial"),
        actors=_batch_actors(batch),
        call_ids=_batch_call_ids(batch),
        group_id=str(batch.get("group_id") or ""),
    )


def _record_routing_warnings(run_dir: Path, warnings: list[dict]) -> None:
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        agent_interactions.record_routing_warning(
            run_dir,
            code=str(warning.get("code") or ""),
            message=str(warning.get("message") or ""),
            group_id=str(warning.get("group_id") or ""),
            actors=[str(item) for item in warning.get("actors") or []],
            call_ids=[str(item) for item in warning.get("call_ids") or []],
        )
```

- [ ] **Step 3: Add a helper to dispatch one actor call without recording output events**

Add this helper above `run_interactive_loop`:

```python
def _dispatch_actor_call(
    *,
    input_payload: dict,
    world_state: dict,
    actor_id: str,
    call: dict,
    hidden_phrases: Iterable[str],
    dispatch: DispatchFn,
) -> dict:
    packet = _actor_packet(
        input_payload,
        world_state,
        actor_id,
        str(call.get("prompt") or ""),
        hidden_phrases,
    )
    return _validate_actor(actor_id, dispatch(_dispatch_actor_key(actor_id), packet))
```

- [ ] **Step 4: Add a helper to process one actor output in call order**

Add this helper above `run_interactive_loop`:

```python
def _process_actor_output(
    *,
    run_dir: Path,
    actor_id: str,
    actor_output: dict,
    call_id: str,
    registered_actor_targets: set[str],
    seen_transfers: set[tuple[str, str, str]],
    generated_transfer_limit: int,
    generated_transfers_used: int,
    generated_call_counts: dict[str, int],
    used_actor_call_ids: set[str],
    world_state: dict,
) -> dict:
    transfer_calls = []
    actor_requested_decision = False
    stop_reason = ""
    for event in actor_output.get("events", []):
        _record_actor_event(run_dir, actor_id, event, call_id)
        event_type = str(event.get("type") or "")
        target = str(event.get("target") or "")
        content = _event_content(event)

        if (
            event_type == "dialogue"
            and target in registered_actor_targets
            and target != actor_id
        ):
            _record_dialogue_transfer(run_dir, actor_id, target, content, call_id)
            transfer_key = (actor_id, target, content)
            if transfer_key not in seen_transfers:
                seen_transfers.add(transfer_key)
                if generated_transfers_used < generated_transfer_limit:
                    generated_transfers_used += 1
                    transfer_calls.append(
                        _dialogue_transfer_call(
                            actor_id,
                            target,
                            event,
                            call_id,
                            generated_call_counts,
                            used_actor_call_ids,
                        )
                    )
                else:
                    stop_reason = "max_steps"
        elif event_type == "perceive_request":
            _record_perception_continuation(run_dir, actor_id, event, call_id, world_state)
        elif event_type == "stop_for_player_decision":
            actor_requested_decision = True

    return {
        "transfer_calls": transfer_calls,
        "actor_requested_decision": actor_requested_decision,
        "generated_transfers_used": generated_transfers_used,
        "stop_reason": stop_reason,
    }
```

- [ ] **Step 5: Replace the serial actor queue loop with batch execution**

Inside `run_interactive_loop`, replace the block from:

```python
        actor_queue: Deque[dict] = deque(gm_output.get("actor_calls") or [])
        while actor_queue:
```

through the matching end of the actor queue loop before:

```python
        if stop_reason in STOP_REASONS:
```

with:

```python
        max_parallel = agent_actor_batches.max_parallel_from_input(input_payload)
        actor_queue: Deque[dict] = deque(gm_output.get("actor_calls") or [])
        while actor_queue:
            queued_calls = list(actor_queue)
            actor_queue.clear()
            batch_plan = agent_actor_batches.build_actor_batches(
                queued_calls,
                gm_output.get("parallel_groups") or [],
                max_parallel=max_parallel,
            )
            _record_routing_warnings(root, batch_plan.get("warnings", []))

            for batch_index, batch in enumerate(batch_plan.get("batches", [])):
                calls = [call for call in batch.get("calls", []) if isinstance(call, dict)]
                if not calls:
                    continue
                _record_actor_batch_plan(root, step_index, batch_index, batch)

                dispatch_results: list[tuple[str, str, dict]] = []
                if str(batch.get("kind") or "") == "parallel" and len(calls) > 1:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as executor:
                        futures = []
                        for call in calls:
                            actor_id = str(call.get("actor_id") or "")
                            call_id = str(call.get("call_id") or "") or _safe_actor_call_id(actor_id, generated_call_counts)
                            futures.append((
                                actor_id,
                                call_id,
                                executor.submit(
                                    _dispatch_actor_call,
                                    input_payload=input_payload,
                                    world_state=dict(world_state),
                                    actor_id=actor_id,
                                    call=call,
                                    hidden_phrases=hidden_phrases,
                                    dispatch=dispatch,
                                ),
                            ))
                        for actor_id, call_id, future in futures:
                            dispatch_results.append((actor_id, call_id, future.result()))
                else:
                    for call in calls:
                        actor_id = str(call.get("actor_id") or "")
                        call_id = str(call.get("call_id") or "") or _safe_actor_call_id(actor_id, generated_call_counts)
                        dispatch_results.append((
                            actor_id,
                            call_id,
                            _dispatch_actor_call(
                                input_payload=input_payload,
                                world_state=world_state,
                                actor_id=actor_id,
                                call=call,
                                hidden_phrases=hidden_phrases,
                                dispatch=dispatch,
                            ),
                        ))

                batch_transfer_calls = []
                actor_requested_decision = False
                for actor_id, call_id, actor_output in dispatch_results:
                    called_actors.append(actor_id)
                    actor_outputs.setdefault(actor_id, []).append(actor_output)
                    processed = _process_actor_output(
                        run_dir=root,
                        actor_id=actor_id,
                        actor_output=actor_output,
                        call_id=call_id,
                        registered_actor_targets=registered_actor_targets,
                        seen_transfers=seen_transfers,
                        generated_transfer_limit=generated_transfer_limit,
                        generated_transfers_used=generated_transfers_used,
                        generated_call_counts=generated_call_counts,
                        used_actor_call_ids=used_actor_call_ids,
                        world_state=world_state,
                    )
                    generated_transfers_used = int(processed.get("generated_transfers_used") or generated_transfers_used)
                    if processed.get("stop_reason"):
                        stop_reason = str(processed["stop_reason"])
                    if processed.get("actor_requested_decision"):
                        actor_requested_decision = True
                    batch_transfer_calls.extend(processed.get("transfer_calls") or [])

                _update_visible_events(root, world_state)
                for transfer_call in reversed(batch_transfer_calls):
                    actor_queue.appendleft(transfer_call)

                if stop_reason in STOP_REASONS:
                    actor_queue.clear()
                    break
                if actor_requested_decision or any(
                    output.get("stop_reason") == "stop_for_player_decision"
                    for _actor_id, _call_id, output in dispatch_results
                ):
                    decision_point = _mark_decision(root, None, "Actor requested a real player decision.")
                    stop_reason = "player_decision"
                    actor_queue.clear()
                    break
```

- [ ] **Step 6: Run the new turn-loop tests**

Run:

```powershell
python -m unittest tests.test_agent_turn_loop.AgentTurnLoopTest.test_parallel_group_dispatches_safe_actor_calls_concurrently tests.test_agent_turn_loop.AgentTurnLoopTest.test_dependent_parallel_group_is_downgraded_to_serial_and_warned tests.test_agent_turn_loop.AgentTurnLoopTest.test_parallel_batch_outputs_merge_in_call_order_and_schedule_transfer_after_batch -v
```

Expected:

```text
OK
```

- [ ] **Step 7: Run the full turn-loop suite**

Run:

```powershell
python -m unittest tests.test_agent_turn_loop.AgentTurnLoopTest -v
```

Expected:

```text
OK
```

- [ ] **Step 8: Run py_compile**

Run:

```powershell
python -m py_compile skills/agent_turn_loop.py skills/agent_actor_batches.py
```

Expected: exit code `0` and no output.

- [ ] **Step 9: Commit turn-loop implementation**

Run:

```powershell
git add skills/agent_turn_loop.py
git commit -m "feat: 执行主线角色安全并行批次"
```

## Task 7: Documentation and Prompt Contract Updates

**Files:**
- Modify: `.claude/skills/rp-gm-actor-routing.md`
- Modify: `tests/test_gm_skill_contracts.py`
- Modify: `README.md`
- Test: `tests/test_gm_skill_contracts.py`

- [ ] **Step 1: Add failing skill contract test**

In `tests/test_gm_skill_contracts.py`, add:

```python
    def test_actor_routing_skill_defines_executable_parallel_groups(self):
        text = self.read(".claude/skills/rp-gm-actor-routing.md")
        self.assertIn("Executable Parallel Groups", text)
        self.assertIn("The runtime scheduler may execute safe groups concurrently", text)
        self.assertIn("downgrade unsafe groups to serial routing", text)
        self.assertNotIn("metadata only", text.lower())
```

- [ ] **Step 2: Run the failing contract test**

Run:

```powershell
python -m unittest tests.test_gm_skill_contracts.GmSkillContractsTest.test_actor_routing_skill_defines_executable_parallel_groups -v
```

Expected before documentation update:

- Fails because the skill still contains metadata-only wording and does not contain the new executable-group text.

- [ ] **Step 3: Update `rp-gm-actor-routing.md`**

Replace the section:

```markdown
## Metadata-Only Parallel Groups

`parallel_groups` is metadata only. It may declare that several calls can be dispatched together because they do not depend on each other's outputs, but it must not smuggle extra prompts, hidden facts, or scheduling instructions outside `actor_calls`.
```

with:

```markdown
## Executable Parallel Groups

`parallel_groups` declares which existing `actor_calls` are safe to dispatch together. The runtime scheduler may execute safe groups concurrently when every call is independent, targets a different actor, has no `source_call_id` dependency, and does not conflict with active subGM reservations.

The runtime may split a large safe group by `max_parallel_subagents`, and it will downgrade unsafe groups to serial routing with traceable routing warnings. Do not rely on a parallel group for correctness; every actor call must still contain its own second-person visible prompt, reason, and target actor.

Use `call_ids` when a group needs exact call identity. Use `actors` or `actor_ids` only when each listed actor appears exactly once in the current `actor_calls`.
```

- [ ] **Step 4: Update README paragraph**

In `README.md`, replace this sentence:

```markdown
每轮会为已注册的重要角色生成隔离上下文；`max_parallel_subagents` 只限制运行时同一批次最多并行调度多少角色，不限制已注册重要角色的上下文数量。
```

with:

```markdown
每轮会为已注册的重要角色生成隔离上下文；`max_parallel_subagents` 只限制运行时同一安全批次最多并行调度多少角色，不限制已注册重要角色的上下文数量。GM 输出的 `parallel_groups` 会被控制面校验；互不依赖、不同角色且无 subGM 占用冲突的 actor calls 可并行执行，不安全的并行声明会降级为串行并写入路由警告。
```

Leave the rest of the paragraph intact.

- [ ] **Step 5: Run documentation tests**

Run:

```powershell
python -m unittest tests.test_gm_skill_contracts.GmSkillContractsTest -v
```

Expected:

```text
OK
```

- [ ] **Step 6: Verify README wording**

Run:

```powershell
python -c "from pathlib import Path; text = Path('README.md').read_text(encoding='utf-8'); assert '可并行执行' in text; assert '降级为串行并写入路由警告' in text"
```

Expected: exit code `0`.

- [ ] **Step 7: Commit docs and prompt contract updates**

Run:

```powershell
git add .claude/skills/rp-gm-actor-routing.md tests/test_gm_skill_contracts.py README.md
git commit -m "docs: 更新角色并行调度契约"
```

## Task 8: Final Verification

**Files:**
- Verify: `skills/agent_actor_batches.py`
- Verify: `skills/agent_interactions.py`
- Verify: `skills/agent_turn_loop.py`
- Verify: `tests/test_agent_actor_batches.py`
- Verify: `tests/test_agent_interactions.py`
- Verify: `tests/test_agent_turn_loop.py`
- Verify: `tests/test_gm_skill_contracts.py`
- Verify: `.claude/skills/rp-gm-actor-routing.md`
- Verify: `README.md`

- [ ] **Step 1: Run py_compile for touched runtime files**

Run:

```powershell
python -m py_compile skills/agent_actor_batches.py skills/agent_interactions.py skills/agent_turn_loop.py
```

Expected: exit code `0` and no output.

- [ ] **Step 2: Run focused tests**

Run:

```powershell
python -m unittest tests.test_agent_actor_batches tests.test_agent_interactions.AgentInteractionTraceTest tests.test_agent_turn_loop.AgentTurnLoopTest tests.test_gm_skill_contracts.GmSkillContractsTest -v
```

Expected:

```text
OK
```

- [ ] **Step 3: Run full unit suite**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected:

```text
OK
```

- [ ] **Step 4: Run deterministic control-plane smoke**

Run:

```powershell
python skills/control_plane_smoke.py --repo .
```

Expected:

- JSON output includes `"ok": true`.

- [ ] **Step 5: Run acceptance py_compile set**

Run:

```powershell
python -m py_compile skills/agent_workflow.py skills/control_plane_smoke.py skills/agent_outputs.py skills/agent_prompts.py skills/round_prepare.py skills/input_analysis.py skills/input_analysis_apply.py skills/character_registry.py skills/rp_generate_cli.py skills/self_repair.py skills/agent_actor_batches.py skills/agent_interactions.py skills/agent_turn_loop.py
```

Expected: exit code `0` and no output.

- [ ] **Step 6: Check git status**

Run:

```powershell
git status --short
```

Expected: no output.

## Plan Self-Review

Spec coverage:

- Safe executable actor batches are covered by Tasks 1, 2, 5, and 6.
- Same-actor and dependency serial downgrades are covered by Tasks 1 and 5.
- Runtime cap from `max_parallel_subagents` is covered by Task 1 and used by Task 6.
- subGM reservation conflicts are preserved by existing preflight checks and not changed by this plan.
- Actual batch and warning trace recording is covered by Tasks 3, 4, and 6.
- Documentation alignment is covered by Task 7.

Intentional gaps:

- P2 visibility proof and P3 perception/dialogue/memory jobs are separate plans.
- This plan does not change subGM side-thread dispatch; `subgm_turn_loop.py` already has its own side-thread batching.

Completion scan:

- Every task contains concrete file paths, code snippets, commands, expected outcomes, and commit commands.
