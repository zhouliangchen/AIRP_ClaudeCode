# Round State Machine and Agent Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add schema-v2 round progress, hybrid frontend progress display, post-delivery subagent cleanup, and conservative actor context invalidation to the existing Claude Code RP control plane.

**Architecture:** Keep the current file-mailbox control plane. Add `round_state.py` as the only place that declares progress state IDs and schema-v2 progress records, and add `agent_lifecycle.py` as the only place that owns round-end runtime cleanup plus actor context version hashing. Existing scripts call these helpers instead of inventing free-form progress strings or lifecycle bookkeeping.

**Tech Stack:** Python standard library, `unittest`, JSON artifacts under `.agent_runs/<round>/`, existing browser bridge in `skills/server.py`, static frontend in `skills/styles/index.html`.

---

## Final Scope

This plan implements the approved Scheme A from `docs/superpowers/specs/2026-06-20-round-state-and-agent-lifecycle-design.md`.

- Build an explicit schema-v2 state machine for progress updates while preserving the old `stage` field for compatibility.
- Surface schema-v2 progress in the frontend as a compact high-level badge plus an expandable structured detail panel.
- Add post-delivery lifecycle cleanup that pauses active subGM side threads and records cleanup results without falsely completing unfinished side stories.
- Add actor context version hashes to player/character packets and recompute them immediately before each dispatch.
- Use conservative invalidation: do not interrupt running actor calls; future calls rebuild context from latest files.
- Cover all behavior with deterministic tests and no live model calls.

## File Structure

- Create `skills/round_state.py`: declare state metadata, write schema-v2 progress JSON, keep compatibility fields, validate terminal transitions, and optionally append manifest progress entries.
- Create `tests/test_round_state.py`: unit tests for schema-v2 records, compatibility fields, terminal transition rules, repeated-state refresh, and manifest sync.
- Modify `skills/handler.py`: keep `write_progress()` as a legacy wrapper; make `read_progress()` return schema-v2 records when present and legacy idle when absent.
- Modify `skills/server.py`: replace submit/opening progress writes with declared states through the compatibility wrapper or `round_state`.
- Modify `skills/round_prepare.py`: write `round.preparing` and `input_analysis.awaiting`.
- Modify `skills/rp_generate_cli.py`: write progress around input analyst, input analysis apply, GM loop, Story, Critic, story preflight repair, delivery retry, and error paths.
- Modify `skills/agent_turn_loop.py`: write GM dispatch, subGM dispatch, actor batch, actor dispatch, player decision, retry, and completion progress; rebuild actor packets immediately before actor dispatch.
- Modify `skills/subgm_turn_loop.py`: write side-thread dispatch and side-thread actor dispatch detail.
- Create `skills/agent_lifecycle.py`: compute actor context versions, attach versions to packets, pause active side threads after delivery, write cleanup manifest data, and record stale-version warnings.
- Create `tests/test_agent_lifecycle.py`: unit tests for actor version hashing and side-thread cleanup behavior.
- Modify `skills/agent_packets.py`: expose reusable actor context version helpers and attach `context_version` to prepared player/character packets.
- Modify `tests/test_agent_packets.py`: assert prepared player/character packets include context versions and profile/memory changes alter hashes.
- Modify `tests/test_agent_turn_loop.py` and `tests/test_subgm_turn_loop.py`: assert runtime actor dispatch packets receive fresh context versions and progress detail.
- Modify `skills/round_deliver.py`: write delivery/memory/cleanup states and call lifecycle cleanup after post-round memory handling.
- Modify `tests/test_agent_memory.py`, `tests/test_multi_agent_round_e2e.py`, or `tests/test_agent_packets.py`: cover post-delivery cleanup ordering and degraded cleanup reporting.
- Modify `skills/styles/index.html`: display schema-v2 progress and structured detail while retaining schema-v1 fallback.
- Modify `tests/test_turn_state.py`: assert frontend markup and JavaScript support schema-v2 state/detail fields.
- Modify `skills/control_plane_smoke.py`: include representative progress states and lifecycle cleanup evidence in smoke output.
- Modify `tests/test_control_plane_smoke.py`: assert smoke covers schema-v2 progress and cleanup.
- Modify `README.md`, `CLAUDE.md`, and `.claude/skills/rp-orchestrator.md`: document schema-v2 progress, cleanup semantics, and conservative actor context invalidation.

---

## Task 1: Round State Core

**Files:**
- Create: `skills/round_state.py`
- Create: `tests/test_round_state.py`
- Modify: `tests/test_turn_state.py`

- [ ] **Step 1: Write failing unit tests for schema-v2 progress records**

Create `tests/test_round_state.py`:

```python
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_round_state():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("round_state", ROOT / "skills" / "round_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RoundStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.styles = self.root / "skills" / "styles"
        self.styles.mkdir(parents=True)
        self.run_dir = self.root / "card" / ".agent_runs" / "round-000001"
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "manifest.json").write_text(
            json.dumps({"round_id": "round-000001", "status": []}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.round_state = load_round_state()

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_state_emits_schema_v2_and_legacy_fields(self):
        result = self.round_state.write_progress_state(
            self.styles,
            "gm_loop.actor_dispatch",
            run_id="round-000001",
            detail={"agent": "character:Ada", "actor_call_id": "call-1"},
        )

        saved = json.loads((self.styles / "progress.json").read_text(encoding="utf-8"))
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(saved["schema_version"], 2)
        self.assertEqual(saved["state"], "gm_loop.actor_dispatch")
        self.assertEqual(saved["phase"], "gm_loop")
        self.assertEqual(saved["stage"], "gm_loop.actor_dispatch")
        self.assertEqual(saved["label"], "角色行动中")
        self.assertEqual(saved["percent"], 48)
        self.assertFalse(saved["terminal"])
        self.assertEqual(saved["detail"]["agent"], "character:Ada")

    def test_unknown_state_is_rejected(self):
        with self.assertRaisesRegex(self.round_state.RoundStateError, "unknown progress state"):
            self.round_state.write_progress_state(self.styles, "unknown.state")

    def test_complete_requires_delivered_manifest(self):
        with self.assertRaisesRegex(self.round_state.RoundStateError, "complete requires delivered"):
            self.round_state.write_progress_state(self.styles, "complete", run_dir=self.run_dir)

        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["stage"] = "delivered"
        (self.run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        saved = self.round_state.write_progress_state(self.styles, "complete", run_dir=self.run_dir)
        self.assertTrue(saved["terminal"])

    def test_repeated_state_refreshes_detail(self):
        self.round_state.write_progress_state(self.styles, "story.running", detail={"attempt": 1})
        saved = self.round_state.write_progress_state(self.styles, "story.running", detail={"attempt": 2})
        self.assertEqual(saved["detail"]["attempt"], 2)

    def test_manifest_sync_appends_progress_entry(self):
        self.round_state.write_progress_state(
            self.styles,
            "input_analysis.applied",
            run_dir=self.run_dir,
            manifest_message="Input analysis applied.",
        )
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["progress_state"], "input_analysis.applied")
        self.assertEqual(manifest["status"][-1]["stage"], "input_analysis.applied")
```

In `tests/test_turn_state.py`, keep the existing legacy test and add:

```python
    def test_progress_state_v2_round_trips_through_handler(self):
        round_state = load_module("round_state")
        round_state.write_progress_state(self.styles_dir, "delivery.delivering", detail={"attempt": 1})

        progress = self.handler.read_progress()

        self.assertEqual(progress["schema_version"], 2)
        self.assertEqual(progress["state"], "delivery.delivering")
        self.assertEqual(progress["stage"], "delivery.delivering")
        self.assertEqual(progress["detail"]["attempt"], 1)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m unittest tests.test_round_state tests.test_turn_state.TurnStateTest.test_progress_state_v2_round_trips_through_handler -v
```

Expected: `tests.test_round_state` fails because `skills/round_state.py` does not exist, and the handler round-trip test cannot import `round_state`.

- [ ] **Step 3: Implement `skills/round_state.py`**

Create `skills/round_state.py`:

```python
"""Schema-v2 round progress state machine helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import agent_run

CST = timezone(timedelta(hours=8))


class RoundStateError(RuntimeError):
    """Raised when a progress state write violates the declared state model."""


@dataclass(frozen=True)
class StateSpec:
    phase: str
    label: str
    percent: int | None
    terminal: bool = False


STATES: dict[str, StateSpec] = {
    "idle": StateSpec("idle", "等待中", None),
    "input.received": StateSpec("input", "已接收玩家输入", 10),
    "round.preparing": StateSpec("round", "正在整理回合上下文", 30),
    "input_analysis.awaiting": StateSpec("input_analysis", "等待输入分析", 35),
    "input_analysis.running": StateSpec("input_analysis", "正在分析玩家输入", 38),
    "input_analysis.applying": StateSpec("input_analysis", "正在应用输入分析", 42),
    "input_analysis.applied": StateSpec("input_analysis", "输入分析已应用", 45),
    "gm_loop.starting": StateSpec("gm_loop", "正在启动 GM 回合", 46),
    "gm_loop.gm_dispatch": StateSpec("gm_loop", "GM 正在推进剧情", 47),
    "gm_loop.subgm_dispatch": StateSpec("gm_loop", "支线 GM 正在推进", 47),
    "gm_loop.actor_batch": StateSpec("gm_loop", "角色行动批次中", 48),
    "gm_loop.actor_dispatch": StateSpec("gm_loop", "角色行动中", 48),
    "gm_loop.waiting_player_decision": StateSpec("gm_loop", "等待玩家决策", 60),
    "gm_loop.completed": StateSpec("gm_loop", "剧情推演完成", 62),
    "gm_loop.retrying": StateSpec("gm_loop", "正在重跑剧情推演", 50),
    "story.running": StateSpec("story", "正在写作正文", 68),
    "story.preflight_repair": StateSpec("story", "正在修正文稿预检问题", 70),
    "critic.running": StateSpec("critic", "正在质检正文", 73),
    "critic.revise": StateSpec("critic", "质检要求修订", 65),
    "critic.blocked": StateSpec("critic", "质检已阻塞", 65),
    "delivery.validating": StateSpec("delivery", "正在质检回复", 75),
    "delivery.retrying": StateSpec("delivery", "交付前等待修复", 65),
    "delivery.delivering": StateSpec("delivery", "正在交付到前端", 85),
    "delivery.failed": StateSpec("delivery", "前端交付失败", 0, terminal=True),
    "memory.finalizing": StateSpec("memory", "正在更新记忆", 95),
    "memory.post_round_scheduling": StateSpec("memory", "正在安排回合后记忆", 97),
    "agent_lifecycle.cleanup": StateSpec("agent_lifecycle", "正在关闭本轮代理活动", 98),
    "complete": StateSpec("complete", "回复已完成", 100, terminal=True),
    "blocked": StateSpec("blocked", "流程已阻塞", 65, terminal=True),
    "error": StateSpec("error", "流程出错", 0, terminal=True),
}

LEGACY_STAGE_MAP = {
    "received": "input.received",
    "preparing": "round.preparing",
    "generating": "gm_loop.starting",
    "delivering": "delivery.delivering",
    "finalizing": "memory.finalizing",
    "retry": "delivery.retrying",
    "blocked": "blocked",
    "error": "error",
    "done": "complete",
    "complete": "complete",
}


def _timestamp() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def _progress_path(styles_dir: str | Path) -> Path:
    return Path(styles_dir) / "progress.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_state(state: str) -> str:
    key = str(state or "").strip()
    if key in STATES:
        return key
    mapped = LEGACY_STAGE_MAP.get(key)
    if mapped:
        return mapped
    raise RoundStateError(f"unknown progress state: {state!r}")


def _assert_transition_allowed(state: str, run_dir: str | Path | None) -> None:
    if state != "complete" or run_dir is None:
        return
    manifest = _read_json(Path(run_dir) / "manifest.json", {})
    if isinstance(manifest, dict) and manifest.get("stage") == "delivered":
        return
    raise RoundStateError("complete requires delivered manifest stage")


def build_progress_record(
    state: str,
    *,
    label: str | None = None,
    percent: int | float | None = None,
    detail: Any = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_state(state)
    spec = STATES[normalized]
    resolved_percent = spec.percent if percent is None else int(percent)
    if isinstance(resolved_percent, int):
        resolved_percent = max(0, min(100, resolved_percent))
    return {
        "schema_version": 2,
        "state": normalized,
        "phase": spec.phase,
        "label": label or spec.label,
        "percent": resolved_percent,
        "run_id": run_id or "",
        "terminal": bool(spec.terminal),
        "detail": detail if detail is not None else {},
        "updated_at": _timestamp(),
        "stage": normalized,
    }


def write_progress_state(
    styles_dir: str | Path,
    state: str,
    *,
    label: str | None = None,
    percent: int | float | None = None,
    detail: Any = None,
    run_id: str | None = None,
    run_dir: str | Path | None = None,
    manifest_message: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_state(state)
    _assert_transition_allowed(normalized, run_dir)
    if run_id is None and run_dir is not None:
        run_id = Path(run_dir).name
    record = build_progress_record(
        normalized,
        label=label,
        percent=percent,
        detail=detail,
        run_id=run_id,
    )
    _write_json(_progress_path(styles_dir), record)
    if run_dir is not None:
        root = Path(run_dir)
        manifest = agent_run.read_json(root / "manifest.json", {}) or {}
        manifest["progress_state"] = normalized
        if manifest_message:
            agent_run.append_manifest_stage(manifest, normalized, manifest_message)
        agent_run.write_json(root / "manifest.json", manifest)
    return record


def legacy_progress_record(stage: str, label: str, percent: int | float | None = None, detail: Any = None) -> dict[str, Any]:
    try:
        return build_progress_record(stage, label=label, percent=percent, detail=detail)
    except RoundStateError:
        return {
            "stage": stage,
            "label": label,
            "percent": max(0, min(100, int(percent))) if isinstance(percent, (int, float)) else percent,
            "detail": detail or "",
            "updated_at": _timestamp(),
        }
```

- [ ] **Step 4: Run targeted tests**

Run:

```powershell
python -m unittest tests.test_round_state tests.test_turn_state.TurnStateTest.test_progress_state_v2_round_trips_through_handler -v
```

Expected: `tests.test_round_state` passes; the handler round-trip test still fails if `handler.read_progress()` filters out schema-v2 fields.

- [ ] **Step 5: Commit**

```powershell
git add skills/round_state.py tests/test_round_state.py tests/test_turn_state.py
git commit -m "feat: 增加回合状态机进度模型"
```

---

## Task 2: Handler Compatibility and Basic Progress Writers

**Files:**
- Modify: `skills/handler.py`
- Modify: `skills/server.py`
- Modify: `skills/round_prepare.py`
- Modify: `tests/test_turn_state.py`
- Modify: `tests/test_agent_packets.py`

- [ ] **Step 1: Add failing handler compatibility assertions**

In `tests/test_turn_state.py`, update `test_progress_state_round_trips` so legacy writes also expose schema-v2 fields:

```python
    def test_progress_state_round_trips(self):
        self.handler.write_progress("delivering", "正在交付到前端", percent=85)

        progress = self.handler.read_progress()

        self.assertEqual(progress["schema_version"], 2)
        self.assertEqual(progress["stage"], "delivery.delivering")
        self.assertEqual(progress["state"], "delivery.delivering")
        self.assertEqual(progress["label"], "正在交付到前端")
        self.assertEqual(progress["percent"], 85)
```

In `tests/test_agent_packets.py`, add an assertion to an existing `round_prepare` test that already captures `progress_calls`:

```python
        self.assertTrue(any(args and args[0] == "round.preparing" for args, _ in progress_calls))
        self.assertTrue(any(args and args[0] == "input_analysis.awaiting" for args, _ in progress_calls))
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m unittest tests.test_turn_state.TurnStateTest.test_progress_state_round_trips tests.test_agent_packets.AgentPacketTest.test_round_prepare_writes_agent_run_packets_and_reports_path -v
```

Expected: legacy progress still returns old `stage` values, and `round_prepare.py` still writes old free-form states.

- [ ] **Step 3: Update `handler.write_progress()` to use round_state when possible**

Modify `skills/handler.py`:

```python
try:
    import round_state
except Exception:
    round_state = None
```

Replace `write_progress()` with:

```python
def write_progress(stage, label, percent=None, detail=None):
    if round_state is not None:
        try:
            data = round_state.legacy_progress_record(stage, label, percent=percent, detail=detail)
            _write_json_file(_progress_path(), data)
            return data
        except Exception:
            pass
    data = {
        "stage": stage,
        "label": label,
        "percent": percent,
        "detail": detail or "",
        "updated_at": _utc_timestamp(),
    }
    if isinstance(percent, (int, float)):
        data["percent"] = max(0, min(100, int(percent)))
    _write_json_file(_progress_path(), data)
    return data
```

Keep `read_progress()` permissive:

```python
def read_progress():
    data = _read_json_file(_progress_path(), None)
    if isinstance(data, dict):
        return data
    return {"stage": "idle", "state": "idle", "label": "", "percent": None, "detail": ""}
```

- [ ] **Step 4: Replace basic free-form backend writes**

In `skills/server.py`, replace these calls:

```python
handler.write_progress("received", "已接收玩家输入", percent=10)
handler.write_progress("received", "已接收玩家开局设定", percent=10)
```

with:

```python
handler.write_progress("input.received", "已接收玩家输入", percent=10)
handler.write_progress("input.received", "已接收玩家开局设定", percent=10)
```

In `skills/round_prepare.py`, replace:

```python
write_progress("preparing", "正在整理回合上下文", percent=30)
write_progress("generating", "Claude Code 正在生成回复", percent=60)
```

with:

```python
write_progress("round.preparing", "正在整理回合上下文", percent=30)
write_progress("input_analysis.awaiting", "等待输入分析", percent=35)
```

- [ ] **Step 5: Run targeted tests**

Run:

```powershell
python -m unittest tests.test_turn_state tests.test_agent_packets.AgentPacketTest.test_round_prepare_writes_agent_run_packets_and_reports_path -v
```

Expected: progress round-trip tests and the touched round-prepare test pass.

- [ ] **Step 6: Commit**

```powershell
git add skills/handler.py skills/server.py skills/round_prepare.py tests/test_turn_state.py tests/test_agent_packets.py
git commit -m "feat: 兼容写入状态机进度"
```

---

## Task 3: Frontend Hybrid Progress Display

**Files:**
- Modify: `skills/styles/index.html`
- Modify: `tests/test_turn_state.py`

- [ ] **Step 1: Write failing frontend markup and JavaScript assertions**

In `tests/test_turn_state.py`, add:

```python
    def test_frontend_renders_schema_v2_progress_detail_panel(self):
        html = (ROOT / "skills" / "styles" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="reply-progress-detail"', html)
        self.assertIn("progress.schema_version === 2", html)
        self.assertIn("formatProgressDetail", html)
        self.assertIn("progress.state", html)
        self.assertIn("progress.detail", html)
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
python -m unittest tests.test_turn_state.TurnStateTest.test_frontend_renders_schema_v2_progress_detail_panel -v
```

Expected: fails because the detail panel and schema-v2 JavaScript do not exist.

- [ ] **Step 3: Add compact detail markup and CSS**

In `skills/styles/index.html`, replace the progress markup:

```html
<div id="reply-progress" aria-live="polite">
  <span id="reply-progress-text">等待中</span>
  <span id="reply-progress-bar"><span id="reply-progress-fill"></span></span>
</div>
```

with:

```html
<details id="reply-progress" aria-live="polite">
  <summary>
    <span id="reply-progress-text">等待中</span>
    <span id="reply-progress-bar"><span id="reply-progress-fill"></span></span>
  </summary>
  <span id="reply-progress-detail"></span>
</details>
```

Extend the CSS near `#reply-progress`:

```css
#reply-progress summary {
  display: flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
  list-style: none;
}
#reply-progress summary::-webkit-details-marker { display: none; }
#reply-progress-detail {
  display: block;
  margin-top: 3px;
  max-width: 260px;
  color: var(--muted);
  font-size: 0.72em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
```

- [ ] **Step 4: Add schema-v2 detail formatting**

Replace `setProgressVisible(progress)` with this version:

```javascript
function formatProgressDetail(progress) {
  if (!progress || progress.schema_version !== 2) return '';
  const parts = [];
  if (progress.state) parts.push(progress.state);
  const detail = progress.detail && typeof progress.detail === 'object' ? progress.detail : {};
  if (detail.agent) parts.push('agent=' + detail.agent);
  if (detail.subgm_thread_id) parts.push('subGM=' + detail.subgm_thread_id);
  if (detail.actor_call_id) parts.push('call=' + detail.actor_call_id);
  if (detail.batch_id) parts.push('batch=' + detail.batch_id);
  if (detail.attempt) {
    const max = detail.max_attempts ? '/' + detail.max_attempts : '';
    parts.push('attempt=' + detail.attempt + max);
  }
  if (detail.reason) parts.push('reason=' + detail.reason);
  return parts.join(' · ');
}

function setProgressVisible(progress) {
  const box = document.getElementById('reply-progress');
  const text = document.getElementById('reply-progress-text');
  const fill = document.getElementById('reply-progress-fill');
  const detailBox = document.getElementById('reply-progress-detail');
  if (!box || !text || !fill) return;

  const isV2 = progress && progress.schema_version === 2;
  const stage = isV2 ? progress.state : progress && progress.stage;
  const terminal = isV2 ? !!progress.terminal : stage === 'complete';
  const activeStages = ['received', 'preparing', 'generating', 'delivering', 'finalizing', 'retry', 'error'];
  const isActive = isV2 ? !!stage && stage !== 'idle' : activeStages.indexOf(stage) >= 0;
  const isComplete = stage === 'complete';
  if (progressHideTimer) {
    clearTimeout(progressHideTimer);
    progressHideTimer = null;
  }

  if (!isActive && !isComplete && !terminal) {
    box.classList.remove('show', 'indeterminate');
    if (detailBox) detailBox.textContent = '';
    return;
  }

  text.textContent = progress.label || '处理中';
  const detail = formatProgressDetail(progress);
  if (detailBox) {
    detailBox.textContent = detail;
    detailBox.hidden = !detail;
  }
  const pct = Number(progress.percent);
  if (Number.isFinite(pct)) {
    box.classList.remove('indeterminate');
    fill.style.width = Math.max(0, Math.min(100, pct)) + '%';
  } else {
    box.classList.add('indeterminate');
    fill.style.width = '';
  }
  box.classList.add('show');

  if (isComplete) {
    progressHideTimer = setTimeout(function() {
      box.classList.remove('show', 'indeterminate');
    }, 2200);
  }
}
```

- [ ] **Step 5: Run frontend text tests**

Run:

```powershell
python -m unittest tests.test_turn_state -v
```

Expected: all turn-state tests pass.

- [ ] **Step 6: Commit**

```powershell
git add skills/styles/index.html tests/test_turn_state.py
git commit -m "feat: 前端展示状态机进度详情"
```

---

## Task 4: Agent Lifecycle Cleanup

**Files:**
- Create: `skills/agent_lifecycle.py`
- Create: `tests/test_agent_lifecycle.py`
- Modify: `skills/round_deliver.py`
- Modify: `tests/test_agent_packets.py`

- [ ] **Step 1: Write failing lifecycle cleanup tests**

Create `tests/test_agent_lifecycle.py`:

```python
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
        write_json(self.run_dir / "manifest.json", {"round_id": "round-000001", "stage": "delivered", "status": []})
        self.subgm_threads = load_module("subgm_threads")
        self.agent_lifecycle = load_module("agent_lifecycle")

    def tearDown(self):
        self.tmp.cleanup()

    def _thread(self, thread_id, status, allowed=None):
        side = self.run_dir / "side_threads" / thread_id
        write_json(side / "state.json", {
            "thread_id": thread_id,
            "status": status,
            "title": thread_id,
            "boundary": {"location": "library"},
            "objective": "Observe the side scene.",
            "allowed_characters": allowed or ["character:Ada"],
            "forbidden_characters": [],
        })
        (side / "messages.jsonl").write_text("", encoding="utf-8")
        return side

    def test_cleanup_pauses_active_side_threads_and_releases_reservations(self):
        self._thread("side_active", "running", ["character:Ada"])
        self._thread("side_done", "completed", ["character:SuLi"])

        result = self.agent_lifecycle.cleanup_round_agents(self.card, self.run_dir, reason="delivered")

        self.assertTrue(result["ok"])
        self.assertEqual(result["paused_side_threads"], ["side_active"])
        self.assertEqual(result["already_terminal"], ["side_done"])
        self.assertEqual(self.subgm_threads.active_character_reservations(self.run_dir), {})
        active_state = json.loads((self.run_dir / "side_threads" / "side_active" / "state.json").read_text(encoding="utf-8"))
        done_state = json.loads((self.run_dir / "side_threads" / "side_done" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(active_state["status"], "paused")
        self.assertIn("resume when the main GM schedules", active_state["next_resume_point"])
        self.assertEqual(done_state["status"], "completed")
        messages = (self.run_dir / "side_threads" / "side_active" / "messages.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(messages[-1])["action"], "lifecycle_cleanup")

    def test_cleanup_records_manifest_result(self):
        self._thread("side_blocked", "blocked", ["character:Ada"])

        self.agent_lifecycle.cleanup_round_agents(self.card, self.run_dir, reason="delivered")

        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        cleanup = manifest["agent_lifecycle_cleanup"]
        self.assertEqual(cleanup["status"], "complete")
        self.assertEqual(cleanup["reason"], "delivered")
        self.assertEqual(cleanup["paused_side_threads"], ["side_blocked"])
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_lifecycle -v
```

Expected: import failure for missing `skills/agent_lifecycle.py`.

- [ ] **Step 3: Implement lifecycle cleanup**

Create `skills/agent_lifecycle.py` with cleanup support:

```python
"""Runtime lifecycle helpers for round-scoped agents and actor contexts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import agent_run
import subgm_threads

CST = timezone(timedelta(hours=8))
ACTIVE_SIDE_THREAD_STATUSES = {"running", "merging", "needs_gm", "blocked", "max_steps"}
TERMINAL_SIDE_THREAD_STATUSES = {"completed", "closed"}


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _thread_ids(run_dir: Path) -> list[str]:
    root = subgm_threads.side_threads_root(run_dir)
    if not root.exists():
        return []
    return sorted(child.name for child in root.iterdir() if child.is_dir())


def _pause_side_thread(run_dir: Path, thread_id: str, reason: str) -> None:
    state_path = subgm_threads.thread_dir(run_dir, thread_id) / "state.json"
    state = _read_json(state_path, {})
    state["status"] = "paused"
    state.setdefault("next_resume_point", "resume when the main GM schedules this side thread in a later round")
    state["lifecycle_cleanup_reason"] = reason
    state["updated_at"] = _now()
    _write_json(state_path, state)
    subgm_threads.append_subgm_message(run_dir, thread_id, {
        "action": "lifecycle_cleanup",
        "content": f"Round ended; side thread paused for {reason}.",
        "status": "paused",
        "next_resume_point": state["next_resume_point"],
        "metadata": {"reason": reason},
        "created_at": _now(),
    })


def cleanup_round_agents(card_folder: str | Path, run_dir: str | Path, *, reason: str = "delivered") -> dict[str, Any]:
    root = Path(run_dir)
    paused: list[str] = []
    already_terminal: list[str] = []
    already_paused: list[str] = []
    failed: dict[str, str] = {}

    for thread_id in _thread_ids(root):
        try:
            state = _read_json(subgm_threads.thread_dir(root, thread_id) / "state.json", {})
            status = str(state.get("status") or "")
            if status in ACTIVE_SIDE_THREAD_STATUSES:
                _pause_side_thread(root, thread_id, reason)
                paused.append(thread_id)
            elif status in TERMINAL_SIDE_THREAD_STATUSES:
                already_terminal.append(thread_id)
            elif status == "paused":
                already_paused.append(thread_id)
            else:
                failed[thread_id] = f"unknown side-thread status: {status}"
        except Exception as exc:
            failed[thread_id] = str(exc)

    result = {
        "ok": not failed,
        "status": "complete" if not failed else "degraded",
        "reason": reason,
        "paused_side_threads": paused,
        "already_terminal": already_terminal,
        "already_paused": already_paused,
        "closed_invocations": [],
        "failed": failed,
        "updated_at": _now(),
    }
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path, {}) or {}
    manifest["agent_lifecycle_cleanup"] = result
    agent_run.append_manifest_stage(
        manifest,
        "agent_lifecycle.cleanup",
        "Round-scoped agent lifecycle cleanup completed." if not failed else "Round-scoped agent lifecycle cleanup degraded.",
    )
    _write_json(manifest_path, manifest)
    return result
```

- [ ] **Step 4: Integrate cleanup after successful delivery**

In `skills/round_deliver.py`, add:

```python
import agent_lifecycle
```

After post-round memory handling and before final complete progress, add:

```python
    lifecycle_cleanup = {"ok": True, "status": "not_required"}
    try:
        current_run = agent_run.current_run_dir(card_folder)
        if current_run is not None:
            write_progress("agent_lifecycle.cleanup", "正在关闭本轮代理活动", percent=98)
            lifecycle_cleanup = agent_lifecycle.cleanup_round_agents(card_folder, current_run, reason="delivered")
    except Exception as exc:
        lifecycle_cleanup = {"ok": False, "status": "error", "error": str(exc)}
```

Include it in the final printed JSON:

```python
        "agent_lifecycle_cleanup": lifecycle_cleanup,
```

If `lifecycle_cleanup["ok"]` is false after successful frontend delivery, still keep `action: "done"` and let the manifest/progress report the degraded cleanup state.

- [ ] **Step 5: Run cleanup tests**

Run:

```powershell
python -m unittest tests.test_agent_lifecycle tests.test_agent_packets -v
```

Expected: lifecycle tests pass and existing delivery tests continue to pass after expected-output updates.

- [ ] **Step 6: Commit**

```powershell
git add skills/agent_lifecycle.py skills/round_deliver.py tests/test_agent_lifecycle.py tests/test_agent_packets.py
git commit -m "feat: 交付后清理本轮代理活动"
```

---

## Task 5: Actor Context Versioning

**Files:**
- Modify: `skills/agent_lifecycle.py`
- Modify: `skills/agent_packets.py`
- Modify: `skills/agent_turn_loop.py`
- Modify: `skills/subgm_turn_loop.py`
- Modify: `tests/test_agent_lifecycle.py`
- Modify: `tests/test_agent_packets.py`
- Modify: `tests/test_agent_turn_loop.py`
- Modify: `tests/test_subgm_turn_loop.py`

- [ ] **Step 1: Write failing actor context version tests**

Append to `tests/test_agent_lifecycle.py`:

```python
    def test_compute_actor_context_version_changes_when_memory_changes(self):
        memory = self.card / "memory" / "characters" / "Ada"
        memory.mkdir(parents=True)
        (memory / "long_term.md").write_text("Ada trusts the player.", encoding="utf-8")
        packet = {
            "agent_id": "character:Ada",
            "actor": {"name": "Ada"},
            "world": {"visible_events": []},
            "prompt": "You see the corridor.",
            "visibility_basis": {"mode": "direct", "summary": "Ada is present."},
        }

        first = self.agent_lifecycle.compute_actor_context_version(self.card, "character:Ada", packet)
        (memory / "long_term.md").write_text("Ada distrusts the player.", encoding="utf-8")
        second = self.agent_lifecycle.compute_actor_context_version(self.card, "character:Ada", packet)

        self.assertNotEqual(first["hash"], second["hash"])
        self.assertIn("memory/characters/Ada/long_term.md", first["source_paths"])
```

In `tests/test_agent_packets.py`, add assertions to the prepared packet test:

```python
        player_packet = json.loads((run_dir / "player.context.json").read_text(encoding="utf-8"))
        self.assertEqual(player_packet["context_version"]["algorithm"], "sha256")
        self.assertTrue(player_packet["context_version"]["hash"].startswith("sha256:"))
        character_packet = json.loads((run_dir / "characters" / "Ada.context.json").read_text(encoding="utf-8"))
        self.assertTrue(character_packet["context_version"]["hash"].startswith("sha256:"))
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_lifecycle.AgentLifecycleTest.test_compute_actor_context_version_changes_when_memory_changes tests.test_agent_packets.AgentPacketTest.test_prepare_agent_run_writes_prompts_and_manifest -v
```

Expected: context version function and packet field are missing.

- [ ] **Step 3: Implement actor context version helpers**

Append to `skills/agent_lifecycle.py`:

```python
ACTOR_VERSION_FILES = (
    "profile.md",
    "profile.json",
    "long_term.md",
    "key_memories.md",
    "short_term.md",
    "recent.md",
    "goals.json",
)


def actor_memory_dir(card_folder: str | Path, actor_id: str) -> Path:
    card = Path(card_folder)
    if actor_id == "player":
        return card / "memory" / "player"
    if actor_id.startswith("character:"):
        safe = agent_run.safe_name(actor_id.split(":", 1)[1] or "_unknown")
        return card / "memory" / "characters" / safe
    return card / "memory" / "_unknown"


def _relative_source_path(card: Path, path: Path) -> str:
    try:
        return path.relative_to(card).as_posix()
    except ValueError:
        return path.as_posix()


def compute_actor_context_version(card_folder: str | Path, actor_id: str, packet: dict[str, Any]) -> dict[str, Any]:
    card = Path(card_folder)
    memory_dir = actor_memory_dir(card, actor_id)
    sources: list[dict[str, str]] = []
    for filename in ACTOR_VERSION_FILES:
        path = memory_dir / filename
        if path.exists():
            sources.append({
                "path": _relative_source_path(card, path),
                "content": path.read_text(encoding="utf-8", errors="replace"),
            })
    packet_basis = {
        "actor_id": actor_id,
        "actor": packet.get("actor"),
        "world": packet.get("world"),
        "prompt": packet.get("prompt"),
        "visibility_basis": packet.get("visibility_basis"),
    }
    normalized = json.dumps({"packet": packet_basis, "sources": sources}, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return {
        "algorithm": "sha256",
        "hash": f"sha256:{digest}",
        "source_paths": [item["path"] for item in sources],
        "computed_at": _now(),
    }


def attach_actor_context_version(card_folder: str | Path, actor_id: str, packet: dict[str, Any]) -> dict[str, Any]:
    updated = dict(packet)
    updated["context_version"] = compute_actor_context_version(card_folder, actor_id, updated)
    return updated


def record_stale_actor_context_warning(run_dir: str | Path, actor_id: str, returned_hash: str, current_hash: str) -> dict[str, Any]:
    root = Path(run_dir)
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path, {}) or {}
    warnings = manifest.setdefault("actor_context_warnings", [])
    record = {
        "type": "stale_actor_context",
        "actor_id": actor_id,
        "returned_hash": returned_hash,
        "current_hash": current_hash,
        "recorded_at": _now(),
    }
    warnings.append(record)
    _write_json(manifest_path, manifest)
    return record
```

- [ ] **Step 4: Attach versions in prepared packets**

In `skills/agent_packets.py`, add:

```python
import agent_lifecycle
```

In `build_player_packet()`, replace the return line:

```python
    return agent_projection.project_actor_context("player", world, actor, prompt)
```

with:

```python
    packet = agent_projection.project_actor_context("player", world, actor, prompt)
    return agent_lifecycle.attach_actor_context_version(card_folder, "player", packet)
```

In `build_character_packet()`, replace the direct return with:

```python
    actor_id = _character_actor_id(actor_state)
    packet = agent_projection.project_actor_context(
        actor_id,
        world,
        actor_state,
        prompt,
    )
    return agent_lifecycle.attach_actor_context_version(card_folder, actor_id, packet)
```

- [ ] **Step 5: Rebuild runtime actor packets immediately before dispatch**

In `skills/agent_turn_loop.py`, add an optional `card_folder` parameter to `run_interactive_loop()`:

```python
def run_interactive_loop(
    run_dir: str | Path,
    dispatch: DispatchFn,
    *,
    max_steps: int = MAX_LOOP_STEPS,
    card_folder: str | Path | None = None,
) -> dict:
```

Read `card_folder` from `input_payload["card_folder"]` if the caller does not provide it:

```python
    card_for_versions = Path(card_folder) if card_folder is not None else Path(str(input_payload.get("card_folder") or root.parents[1]))
```

In `_dispatch_actor_call()`, add `card_folder` and attach the latest version before dispatch:

```python
    packet = agent_lifecycle.attach_actor_context_version(card_folder, actor_id, packet)
    returned = _validate_actor(actor_id, dispatch(_dispatch_actor_key(actor_id), packet))
    returned_version = returned.get("context_version") if isinstance(returned.get("context_version"), dict) else {}
    returned_hash = str(returned_version.get("hash") or "")
    if returned_hash and returned_hash != packet["context_version"]["hash"]:
        agent_lifecycle.record_stale_actor_context_warning(
            Path(card_folder) / ".agent_runs" / Path(str(packet.get("run_id", ""))).name,
            actor_id,
            returned_hash,
            packet["context_version"]["hash"],
        )
    return returned
```

When calling `_dispatch_actor_call()`, pass `card_folder=card_for_versions`.

In `skills/rp_generate_cli.py`, pass the card folder into the loop:

```python
        return agent_turn_loop.run_interactive_loop(run_dir, dispatch, card_folder=run_dir.parents[1])
```

In `skills/subgm_turn_loop.py`, attach the latest version before dispatch:

```python
        packet = agent_lifecycle.attach_actor_context_version(root.parents[1], actor_id, packet)
```

Add `import agent_lifecycle` at the top.

- [ ] **Step 6: Adjust stale-warning path cleanly**

If `_dispatch_actor_call()` does not have enough information to compute `run_dir` from `card_folder`, pass `run_dir` directly into `_dispatch_actor_call()` and use:

```python
agent_lifecycle.record_stale_actor_context_warning(run_dir, actor_id, returned_hash, packet["context_version"]["hash"])
```

This avoids deriving a path from `run_id` and keeps the warning write deterministic.

- [ ] **Step 7: Run actor packet and loop tests**

Run:

```powershell
python -m unittest tests.test_agent_lifecycle tests.test_agent_packets tests.test_agent_turn_loop tests.test_subgm_turn_loop -v
```

Expected: all targeted tests pass.

- [ ] **Step 8: Commit**

```powershell
git add skills/agent_lifecycle.py skills/agent_packets.py skills/agent_turn_loop.py skills/subgm_turn_loop.py skills/rp_generate_cli.py tests/test_agent_lifecycle.py tests/test_agent_packets.py tests/test_agent_turn_loop.py tests/test_subgm_turn_loop.py
git commit -m "feat: 为角色调度加入上下文版本"
```

---

## Task 6: Full Pipeline Progress Integration

**Files:**
- Modify: `skills/rp_generate_cli.py`
- Modify: `skills/agent_turn_loop.py`
- Modify: `skills/subgm_turn_loop.py`
- Modify: `skills/round_deliver.py`
- Modify: `tests/test_rp_generate_cli.py`
- Modify: `tests/test_agent_turn_loop.py`
- Modify: `tests/test_subgm_turn_loop.py`
- Modify: `tests/test_agent_packets.py`

- [ ] **Step 1: Write failing progress integration tests**

In `tests/test_rp_generate_cli.py`, add a fake progress collector by patching `handler.write_progress` or `round_state.write_progress_state` in the module under test. Add:

```python
    def test_run_round_writes_schema_v2_pipeline_states(self):
        states = []
        self.module.write_progress = lambda state, label, percent=None, detail=None: states.append((state, detail))
        self._write_ready_manifest_and_inputs()

        self.module.run_round(self.card, self.root, run_claude=self._successful_run_claude, run_command=self._successful_delivery_command)

        observed = [state for state, _ in states]
        self.assertIn("input_analysis.running", observed)
        self.assertIn("gm_loop.starting", observed)
        self.assertIn("story.running", observed)
        self.assertIn("critic.running", observed)
```

In `tests/test_agent_turn_loop.py`, add:

```python
    def test_loop_writes_actor_batch_and_dispatch_progress(self):
        states = []
        self.agent_turn_loop.write_progress = lambda state, label, percent=None, detail=None: states.append((state, detail))

        self.agent_turn_loop.run_interactive_loop(self.run_dir, self.dispatch)

        self.assertTrue(any(state == "gm_loop.gm_dispatch" for state, _ in states))
        self.assertTrue(any(state == "gm_loop.actor_batch" for state, _ in states))
        self.assertTrue(any(state == "gm_loop.actor_dispatch" and detail.get("actor_call_id") for state, detail in states))
```

In `tests/test_subgm_turn_loop.py`, add:

```python
    def test_side_thread_writes_subgm_dispatch_progress(self):
        states = []
        self.subgm_turn_loop.write_progress = lambda state, label, percent=None, detail=None: states.append((state, detail))

        self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", self.dispatch)

        self.assertTrue(any(state == "gm_loop.subgm_dispatch" for state, _ in states))
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m unittest tests.test_rp_generate_cli tests.test_agent_turn_loop tests.test_subgm_turn_loop -v
```

Expected: new progress assertions fail because the relevant states are not written.

- [ ] **Step 3: Add progress imports with safe fallbacks**

In `skills/rp_generate_cli.py`, `skills/agent_turn_loop.py`, and `skills/subgm_turn_loop.py`, add:

```python
try:
    from handler import write_progress
except Exception:
    def write_progress(stage, label, percent=None, detail=None):
        return {"stage": stage, "label": label, "percent": percent, "detail": detail or {}}
```

- [ ] **Step 4: Instrument `rp_generate_cli.run_round()`**

Add writes at the existing boundaries:

```python
    write_progress("input_analysis.running", "正在分析玩家输入", percent=38)
    _ensure_input_analysis(run_dir, manifest, card, root, run_claude)
    write_progress("input_analysis.applied", "输入分析已应用", percent=45)
```

Before `_run_interactive_agent_loop()`:

```python
        write_progress("gm_loop.starting", "正在启动 GM 回合", percent=46, detail={"run_id": run_dir.name})
```

Before story dispatch:

```python
            write_progress("story.running", "正在写作正文", percent=68, detail={"attempt": preflight_attempt + 1})
```

Before story preflight retry:

```python
            write_progress("story.preflight_repair", "正在修正文稿预检问题", percent=70, detail={
                "attempt": preflight_attempt + 1,
                "max_attempts": repair_policy.story_preflight_attempts,
                "issues": issues,
            })
```

Before critic dispatch:

```python
        write_progress("critic.running", "正在质检正文", percent=73)
```

Before delivery retry:

```python
        write_progress("delivery.retrying", "交付前等待修复", percent=65, detail={
            "attempt": repair_attempt,
            "max_attempts": repair_policy.delivery_repair_attempts,
            "reason": delivery_result.get("reason", ""),
        })
```

- [ ] **Step 5: Instrument `agent_turn_loop.run_interactive_loop()`**

Before GM dispatch:

```python
        write_progress("gm_loop.gm_dispatch", "GM 正在推进剧情", percent=47, detail={
            "run_id": root.name,
            "step": step_index + 1,
        })
```

Before each actor batch:

```python
                write_progress("gm_loop.actor_batch", "角色行动批次中", percent=48, detail={
                    "run_id": root.name,
                    "batch_id": str(batch.get("batch_id") or f"step-{step_index + 1}-batch-{batch_index + 1}"),
                    "kind": str(batch.get("kind") or "serial"),
                    "actors": [str(call.get("actor_id") or "") for call in calls],
                })
```

Before each actor dispatch, inside `_dispatch_actor_call()`:

```python
    write_progress("gm_loop.actor_dispatch", "角色行动中", percent=48, detail={
        "agent": actor_id,
        "actor_call_id": str(call.get("call_id") or ""),
    })
```

When player decision is reached:

```python
            write_progress("gm_loop.waiting_player_decision", "等待玩家决策", percent=60, detail={"reason": "gm_decision_point"})
```

Before returning:

```python
    write_progress("gm_loop.completed", "剧情推演完成", percent=62, detail={"stop_reason": stop_reason})
```

- [ ] **Step 6: Instrument `subgm_turn_loop`**

Before dispatching each subGM:

```python
        write_progress("gm_loop.subgm_dispatch", "支线 GM 正在推进", percent=47, detail={
            "subgm_thread_id": safe_id,
            "step": step_index + 1,
        })
```

Before side-thread actor dispatch:

```python
        write_progress("gm_loop.actor_dispatch", "支线角色行动中", percent=48, detail={
            "agent": actor_id,
            "subgm_thread_id": state.get("thread_id", ""),
            "actor_call_id": call_id,
        })
```

- [ ] **Step 7: Convert `round_deliver.py` states**

Replace free-form state calls:

```python
write_progress("delivering", "正在质检回复", percent=75)
write_progress("retry", "多代理产物未就绪，等待修复", percent=65, detail=detail)
write_progress("error", "前端交付失败", percent=0, detail=handler_output[:500])
write_progress("finalizing", "正在更新记忆", percent=95)
write_progress("complete", "回复已完成", percent=100)
```

with declared states:

```python
write_progress("delivery.validating", "正在质检回复", percent=75)
write_progress("delivery.retrying", progress_message, percent=65, detail=detail)
write_progress("delivery.failed", "前端交付失败", percent=0, detail=handler_output[:500])
write_progress("memory.finalizing", "正在更新记忆", percent=95)
write_progress("memory.post_round_scheduling", "正在安排回合后记忆", percent=97)
write_progress("complete", "回复已完成", percent=100)
```

For blocked gate results, use:

```python
write_progress("blocked", "多代理产物已终止，等待人工处理", percent=65, detail=detail)
```

- [ ] **Step 8: Run integration tests**

Run:

```powershell
python -m unittest tests.test_rp_generate_cli tests.test_agent_turn_loop tests.test_subgm_turn_loop tests.test_agent_packets -v
```

Expected: targeted progress integration tests pass.

- [ ] **Step 9: Commit**

```powershell
git add skills/rp_generate_cli.py skills/agent_turn_loop.py skills/subgm_turn_loop.py skills/round_deliver.py tests/test_rp_generate_cli.py tests/test_agent_turn_loop.py tests/test_subgm_turn_loop.py tests/test_agent_packets.py
git commit -m "feat: 串联回合流程状态机进度"
```

---

## Task 7: Smoke Evidence and Documentation

**Files:**
- Modify: `skills/control_plane_smoke.py`
- Modify: `tests/test_control_plane_smoke.py`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `.claude/skills/rp-orchestrator.md`

- [ ] **Step 1: Write failing smoke assertions**

In `tests/test_control_plane_smoke.py`, add:

```python
        self.assertEqual(payload["progress"]["schema_version"], 2)
        self.assertIn("complete", payload["progress"]["states"])
        self.assertIn("agent_lifecycle_cleanup", payload)
        self.assertIn(payload["agent_lifecycle_cleanup"]["status"], {"complete", "not_required"})
```

- [ ] **Step 2: Run smoke test to verify failure**

Run:

```powershell
python -m unittest tests.test_control_plane_smoke -v
```

Expected: fails because smoke output does not yet include schema-v2 progress and lifecycle cleanup evidence.

- [ ] **Step 3: Add smoke output evidence**

In `skills/control_plane_smoke.py`, after deterministic delivery, read progress and manifest:

```python
progress_path = styles / "progress.json"
progress = json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.exists() else {}
manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
status = manifest.get("status") if isinstance(manifest.get("status"), list) else []
states = [str(item.get("stage") or "") for item in status if isinstance(item, dict)]
```

Include in the returned payload:

```python
"progress": {
    "schema_version": progress.get("schema_version"),
    "state": progress.get("state") or progress.get("stage"),
    "states": states,
},
"agent_lifecycle_cleanup": manifest.get("agent_lifecycle_cleanup", {"status": "not_required"}),
```

- [ ] **Step 4: Update README**

In `README.md`, update the browser progress paragraph to state:

```markdown
进度条由 schema v2 状态机驱动：主界面显示稳定阶段标签和百分比，展开详情可查看当前 agent、subGM 支线、actor call、重试次数或阻塞原因。旧的 `stage` 字段仍作为兼容字段保留，但新增代码应写入 `skills/round_state.py` 中声明的状态 ID。
```

In the core character subagent section, add:

```markdown
成功交付到前端后，系统会执行回合级 agent lifecycle cleanup：仍处于 `running`、`merging`、`needs_gm`、`blocked` 或 `max_steps` 的 subGM 支线会被暂停并释放角色占用；未完成的支线不会被标记为 completed，而是保留 `next_resume_point` 供之后由主 GM 恢复。player/character actor 调度前会重新计算 `context_version`，因此人格、背景、记忆或目标文件被更新后，下一次调度会读取最新上下文；已经在运行的 actor 调用不会被强制中断。
```

- [ ] **Step 5: Update CLAUDE.md and orchestrator skill**

In `CLAUDE.md`, add one concise workflow rule:

```markdown
Progress updates must use declared schema-v2 state IDs from `skills/round_state.py`; after successful delivery, run lifecycle cleanup so active subGM side threads are paused instead of left running.
```

In `.claude/skills/rp-orchestrator.md`, add:

```markdown
Use state-machine progress updates for input analysis, GM loop, actor dispatch, subGM dispatch, Story, Critic, delivery, memory, and lifecycle cleanup. Do not invent new progress state IDs without adding them to `skills/round_state.py`. After delivery, ensure lifecycle cleanup has recorded paused side threads and actor context warnings when applicable.
```

- [ ] **Step 6: Run smoke and doc checks**

Run:

```powershell
python -m unittest tests.test_control_plane_smoke -v
git diff --check README.md CLAUDE.md .claude/skills/rp-orchestrator.md
```

Expected: smoke test passes and diff check reports no whitespace errors.

- [ ] **Step 7: Commit**

```powershell
git add skills/control_plane_smoke.py tests/test_control_plane_smoke.py README.md CLAUDE.md .claude/skills/rp-orchestrator.md
git commit -m "docs: 记录状态机进度与代理生命周期"
```

---

## Task 8: Final Verification

**Files:**
- Verify all files changed by Tasks 1-7.

- [ ] **Step 1: Run full unit suite**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Run deterministic control-plane smoke**

Run:

```powershell
python skills/control_plane_smoke.py --repo .
```

Expected: JSON output contains `"ok": true`, schema-v2 progress evidence, and `agent_lifecycle_cleanup`.

- [ ] **Step 3: Run compile checks**

Run:

```powershell
python -m py_compile skills/agent_lifecycle.py skills/round_state.py skills/agent_workflow.py skills/control_plane_smoke.py skills/agent_outputs.py skills/agent_prompts.py skills/round_prepare.py skills/input_analysis.py skills/input_analysis_apply.py skills/character_registry.py skills/rp_generate_cli.py skills/agent_turn_loop.py skills/subgm_turn_loop.py skills/round_deliver.py skills/handler.py
```

Expected: no output and exit code 0.

- [ ] **Step 4: Inspect source status**

Run:

```powershell
git status --short --branch
```

Expected: only intentional source, test, and documentation files are changed. No card folders, `.agent_runs/`, generated images, memory folders, local secrets, or runtime `skills/styles/content.js/state.js/progress.json` are staged.

- [ ] **Step 5: Manual frontend check**

Run:

```powershell
python skills/start_server.py .
```

Expected:

- `http://localhost:8765` loads.
- The printed LAN URL loads from a same-network phone or other LAN device.
- Submitting a player turn shows immediate pending input.
- The progress badge shows stable schema-v2 labels.
- Expanding the badge shows state/detail values during GM, actor, delivery, memory, and cleanup phases.

- [ ] **Step 6: Manual `/rp` acceptance**

In Claude Code, run `/rp` against a blank folder and complete at least five player turns.

Expected observations:

- Player input appears immediately and exactly.
- Independent important-character dialogue boxes still render.
- Progress updates appear through the full round.
- UI/image hot refresh still works.
- The round stops at player decisions.
- Delivered turn manifest contains `agent_lifecycle_cleanup`.
- Actor packets contain `context_version`.

- [ ] **Step 7: Final commit if verification required fixes**

If verification caused code or documentation adjustments, commit them:

```powershell
git add skills/agent_lifecycle.py skills/round_state.py skills/handler.py skills/server.py skills/round_prepare.py skills/rp_generate_cli.py skills/agent_turn_loop.py skills/subgm_turn_loop.py skills/round_deliver.py skills/agent_packets.py skills/styles/index.html skills/control_plane_smoke.py README.md CLAUDE.md .claude/skills/rp-orchestrator.md tests/test_round_state.py tests/test_agent_lifecycle.py tests/test_turn_state.py tests/test_agent_packets.py tests/test_agent_turn_loop.py tests/test_subgm_turn_loop.py tests/test_rp_generate_cli.py tests/test_control_plane_smoke.py
git commit -m "fix: 完成回合状态机验收修正"
```

If no files changed during verification, do not create an empty commit.

---

## Self-Review

- Spec coverage: Tasks 1-3 implement schema-v2 progress and hybrid frontend display. Task 4 implements post-delivery subagent cleanup. Task 5 implements conservative actor context version invalidation. Task 6 wires the state machine through the existing round pipeline. Task 7 updates smoke evidence and documentation. Task 8 defines full verification.
- Scope check: This remains one implementation project with three tightly coupled parts: progress state machine, lifecycle cleanup, and actor context versioning.
- Type consistency: Progress records use `schema_version`, `state`, `phase`, `label`, `percent`, `run_id`, `terminal`, `detail`, `updated_at`, and compatibility `stage` throughout the plan. Lifecycle cleanup result consistently uses `agent_lifecycle_cleanup`.
- Placeholder scan: no unresolved placeholder markers are intentionally left in this plan.
