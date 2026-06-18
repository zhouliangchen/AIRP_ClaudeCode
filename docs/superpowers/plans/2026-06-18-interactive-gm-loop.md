# Interactive GM Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前一次性 GM/player/character 产物流程升级为 GM 驱动的回合内交互回路，并确保重要角色只接收严格视角投影后的上下文。

**Architecture:** Python 仍是文件协议控制面，Claude Code 仍负责 live subagent 编排。新增 actor/GM 事件 schema、角色视角投影、trace v2 和 `agent_turn_loop.py`；`rp_generate_cli.py` 改为先运行 input analysis，再进入 GM loop，最后由 story/critic/delivery 沿用现有交付门禁。

**Tech Stack:** Python standard library, `unittest`, JSON artifacts, Claude Code `.claude/skills/*.md`, existing browser bridge under `skills/styles/`.

---

## 范围

本计划实现主线交互式 GM 回路。GM 助手多实例仅保留协议文档和数据结构预留，不实现 live runner。项目处于开发期，不为旧 `.agent_runs/` 产物或旧生成存档添加兼容分支。

## 文件结构

- Create `skills/agent_projection.py`: 将完整 GM 上下文投影为 player/character 第一视角上下文。
- Create `skills/agent_turn_loop.py`: 执行有上限的 GM loop，处理 actor calls、actor events、对白转达、感知请求和 trace 写入。
- Modify `skills/agent_schemas.py`: GM 输出升级为 `scene_beats/events/actor_calls/parallel_groups/world_state_delta/decision_point/stop_reason`；actor 输出升级为 `events[]/stop_reason`。
- Modify `skills/agent_interactions.py`: trace v2，支持 target、source_call_id、causal_links、parallel_group 和 actor-visible summary。
- Modify `skills/agent_packets.py`: 生成 GM 完整包和 actor 投影包，不再把完整 recent chat 或 user instruction 放入 actor packet。
- Modify `skills/agent_prompts.py`: materialized prompt 合同与新 schema 对齐。
- Modify `skills/rp_generate_cli.py`: 将一次性 dispatch 改为 `agent_turn_loop.run_interactive_loop`。
- Modify `skills/agent_outputs.py`: `story.input.json` 从 trace v2 和 event artifacts 组装。
- Modify `skills/control_plane_smoke.py`: 覆盖多次唤起、对白转达、感知反馈和决策点停止。
- Modify `.claude/skills/rp-*.md`, `.claude/commands/rp.md`, `CLAUDE.md`: 英文化并描述新交互协议。
- Modify `README.md` and `docs/superpowers/specs/2026-06-18-interactive-gm-loop-design.md`: 与实现对齐。
- Add or modify focused tests under `tests/`.

---

### Task 1: Actor 和 GM Schema 升级

**Files:**
- Modify: `skills/agent_schemas.py`
- Modify: `tests/test_agent_schemas.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_agent_schemas.py` 增加：

```python
def test_validate_actor_output_requires_events_protocol(self):
    payload = {
        "agent": "player",
        "agent_id": "player",
        "events": [
            {"type": "action", "target": "", "content": "I keep the pendant hidden."},
            {"type": "dialogue", "target": "character:SuLi", "content": "Do you know this pendant?"},
        ],
        "stop_reason": "continue",
    }
    normalized = self.agent_schemas.validate_actor_output(payload)
    self.assertEqual(normalized["events"][0]["type"], "action")
    self.assertEqual(normalized["events"][1]["target"], "character:SuLi")


def test_validate_actor_output_rejects_legacy_single_action_protocol(self):
    payload = {
        "agent": "player",
        "agent_id": "player",
        "action": "I walk forward.",
        "dialogue": [],
        "perception": [],
        "memory_delta": [],
    }
    with self.assertRaises(self.agent_schemas.ValidationError):
        self.agent_schemas.validate_actor_output(payload)


def test_validate_gm_output_uses_interactive_event_contract(self):
    payload = {
        "agent": "gm",
        "scene_beats": [{"content": "The classroom clock clicks once."}],
        "events": [{"type": "npc_action", "target": "", "content": "A student shuts the door."}],
        "actor_calls": [
            {
                "call_id": "call-1",
                "actor_id": "character:SuLi",
                "prompt": "You notice the pendant in his hand.",
                "reason": "SuLi can see the pendant.",
            }
        ],
        "parallel_groups": [["character:SuLi", "character:ClassRep"]],
        "world_state_delta": [{"scope": "classroom", "fact": "The door is shut."}],
        "decision_point": None,
        "stop_reason": "continue",
    }
    normalized = self.agent_schemas.validate_gm_output(payload)
    self.assertEqual(normalized["actor_calls"][0]["actor_id"], "character:SuLi")
```

- [ ] **Step 2: 运行失败测试**

Run:

```powershell
python -m unittest tests.test_agent_schemas -v
```

Expected: actor event contract and GM event contract tests fail because current validators still require `action/narration`.

- [ ] **Step 3: 实现 schema helper**

在 `skills/agent_schemas.py` 增加：

```python
ACTOR_EVENT_TYPES = {
    "perceive_request",
    "dialogue",
    "action",
    "memory_delta",
    "goal_update",
    "wait_for_gm",
    "stop_for_player_decision",
}


def _normalize_event(item: Any, path: str) -> Dict[str, Any]:
    data = _require_dict(item, path)
    event_type = _require_str(data, "type", path)
    if event_type not in ACTOR_EVENT_TYPES:
        raise ValidationError(f"{_path(path, 'type')} is not an allowed actor event type")
    return {
        "type": event_type,
        "target": _optional_str(data, "target", "", path),
        "content": _require_str(data, "content", path),
        "metadata": _optional_dict(data, "metadata", path),
    }


def _normalize_events(items: Any, path: str) -> list[Dict[str, Any]]:
    events = _require_list({path: items}, path, "")
    normalized = []
    for index, item in enumerate(events):
        normalized.append(_normalize_event(item, f"{path}[{index}]"))
    if not normalized:
        raise ValidationError(f"{path} must not be empty")
    return normalized
```

- [ ] **Step 4: 替换 actor validator**

将 `validate_actor_output` 改为：

```python
def validate_actor_output(payload: Any) -> Dict[str, Any]:
    data = _require_dict(payload, "actor_output")
    _reject_forbidden_keys(data, "actor_output")

    agent = _require_str(data, "agent", "actor_output")
    if agent not in {"player", "character"}:
        raise ValidationError("actor_output.agent must be 'player' or 'character'")

    normalized = {
        "agent": agent,
        "agent_id": _require_str(data, "agent_id", "actor_output"),
        "events": _normalize_events(data.get("events"), "actor_output.events"),
        "stop_reason": _optional_str(data, "stop_reason", "continue", "actor_output"),
    }
    if agent == "character":
        normalized["character_name"] = _optional_str(data, "character_name", path="actor_output")
    return normalized
```

- [ ] **Step 5: 替换 GM validator**

将 `validate_gm_output` 改为：

```python
def validate_gm_output(payload: Any) -> Dict[str, Any]:
    data = _require_dict(payload, "gm_output")
    return {
        "agent": _require_agent(data, "gm", "gm_output"),
        "scene_beats": _require_list(data, "scene_beats", "gm_output"),
        "events": _require_list(data, "events", "gm_output"),
        "actor_calls": _require_list(data, "actor_calls", "gm_output"),
        "parallel_groups": _optional_list(data, "parallel_groups", "gm_output"),
        "world_state_delta": _require_list(data, "world_state_delta", "gm_output"),
        "decision_point": data.get("decision_point"),
        "stop_reason": _optional_str(data, "stop_reason", "continue", "gm_output"),
    }
```

- [ ] **Step 6: 运行测试**

Run:

```powershell
python -m unittest tests.test_agent_schemas -v
```

Expected: all schema tests pass after updating any old fixture expectations in this test file to the new protocol.

- [ ] **Step 7: 提交**

```powershell
git add skills/agent_schemas.py tests/test_agent_schemas.py
git commit -m "feat: 升级agent事件协议schema"
```

---

### Task 2: 角色视角投影

**Files:**
- Create: `skills/agent_projection.py`
- Create: `tests/test_agent_projection.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_agent_projection.py`：

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


class AgentProjectionTest(unittest.TestCase):
    def setUp(self):
        self.agent_projection = load_module("agent_projection")

    def test_player_projection_removes_user_instruction_and_hidden_truth(self):
        world = {
            "role_channel": "I hide the pendant.",
            "user_instruction_channel": "Hidden truth: the pendant burns identity.",
            "recent_chat": [{"ai": "GM-only foreshadowing", "summary": "The class is normal."}],
            "gm_only_hidden_settings": [{"fact": "The pendant burns identity."}],
            "visible_events": [{"actor": "gm", "type": "scene", "content": "The classroom is noisy."}],
        }
        packet = self.agent_projection.project_actor_context(
            "player",
            world,
            actor_state={"name": "Yumeng", "memory": ["I woke up on the road."], "goals": ["Reach school."]},
            gm_prompt="You stand near your desk with the pendant in your palm.",
        )
        serialized = repr(packet)
        self.assertEqual(packet["visibility"], "first_person_player")
        self.assertIn("You stand near your desk", packet["gm_prompt"])
        self.assertNotIn("user_instruction_channel", serialized)
        self.assertNotIn("burns identity", serialized)
        self.assertNotIn("GM-only", serialized)

    def test_character_projection_keeps_own_memory_and_visible_events_only(self):
        world = {
            "role_channel": "I hide the pendant.",
            "user_instruction_channel": "SuLi is a former magical girl.",
            "visible_events": [{"actor": "player", "type": "action", "content": "He closes his hand."}],
            "private_events": [{"actor": "player", "type": "thought", "content": "I am scared."}],
        }
        packet = self.agent_projection.project_actor_context(
            "character:SuLi",
            world,
            actor_state={"name": "SuLi", "memory": ["I know old rituals."], "goals": ["Avoid attention."]},
            gm_prompt="You notice his hand close around something pink.",
        )
        serialized = repr(packet)
        self.assertEqual(packet["visibility"], "first_person_character")
        self.assertIn("old rituals", serialized)
        self.assertIn("He closes his hand", serialized)
        self.assertNotIn("former magical girl", serialized)
        self.assertNotIn("I am scared", serialized)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行失败测试**

Run:

```powershell
python -m unittest tests.test_agent_projection -v
```

Expected: import failure for missing `agent_projection.py`.

- [ ] **Step 3: 实现 `agent_projection.py`**

创建：

```python
"""Actor perspective projection for interactive RP agent calls."""

from __future__ import annotations

from typing import Any, Dict


FORBIDDEN_WORLD_KEYS = {
    "user_instruction_channel",
    "gm_only_hidden_settings",
    "hidden_facts",
    "world_truth",
    "gm_notes",
    "recent_chat",
    "private_events",
}


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _actor_visibility(actor_id: str) -> str:
    return "first_person_player" if actor_id == "player" else "first_person_character"


def _visible_events(world_state: Dict[str, Any]) -> list[Any]:
    return _list(world_state.get("visible_events")) + _list(world_state.get("world_visible_events"))


def project_actor_context(
    actor_id: str,
    world_state: Dict[str, Any] | None,
    actor_state: Dict[str, Any] | None,
    gm_prompt: str,
) -> Dict[str, Any]:
    """Return the only context an actor agent may see for one GM call."""
    world = world_state if isinstance(world_state, dict) else {}
    actor = actor_state if isinstance(actor_state, dict) else {}
    forbidden_removed = sorted(key for key in FORBIDDEN_WORLD_KEYS if key in world)
    return {
        "actor_id": str(actor_id),
        "agent": "player" if actor_id == "player" else "character",
        "visibility": _actor_visibility(str(actor_id)),
        "gm_prompt": str(gm_prompt or ""),
        "self_knowledge": {
            "name": str(actor.get("name") or actor.get("character_name") or ""),
            "identity": str(actor.get("identity") or actor.get("role") or ""),
            "body_state": actor.get("body_state", {}),
            "relationships": actor.get("relationships", {}),
        },
        "memory": {
            "long_term": _list(actor.get("memory")),
            "recent": _list(actor.get("recent_memory")),
            "goals": _list(actor.get("goals")),
        },
        "sensory_context": _list(actor.get("sensory_context")),
        "visible_events": _visible_events(world),
        "misconceptions": _list(actor.get("misconceptions")),
        "role_channel_anchor": str(world.get("role_channel") or "") if actor_id == "player" else "",
        "forbidden_removed": forbidden_removed,
    }
```

- [ ] **Step 4: 运行测试**

Run:

```powershell
python -m unittest tests.test_agent_projection -v
```

Expected: pass.

- [ ] **Step 5: 提交**

```powershell
git add skills/agent_projection.py tests/test_agent_projection.py
git commit -m "feat: 增加角色视角投影"
```

---

### Task 3: Trace v2

**Files:**
- Modify: `skills/agent_interactions.py`
- Modify: `tests/test_agent_interactions.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_agent_interactions.py` 增加：

```python
def test_trace_v2_records_target_source_and_parallel_group(self):
    trace = self.agent_interactions.init_trace(self.run_dir, participants=["gm", "player"], chapter_target_words=1200)
    self.assertEqual(trace["schema_version"], 2)
    self.agent_interactions.append_event(
        self.run_dir,
        actor="gm",
        visibility="world_visible",
        event_type="dialogue_transfer",
        content="SuLi hears the question.",
        target="character:SuLi",
        source_call_id="call-player-1",
        causal_links=["event-0"],
    )
    self.agent_interactions.record_parallel_group(self.run_dir, "group-1", ["character:A", "character:B"])
    summary = self.agent_interactions.summarize_for_story_input(self.run_dir)
    event = summary["visible_events"][0]
    self.assertEqual(event["target"], "character:SuLi")
    self.assertEqual(event["source_call_id"], "call-player-1")
    self.assertEqual(summary["parallel_groups"][0]["actors"], ["character:A", "character:B"])
```

- [ ] **Step 2: 运行失败测试**

Run:

```powershell
python -m unittest tests.test_agent_interactions -v
```

Expected: failure because trace has no `schema_version`, target/source fields, or parallel group support.

- [ ] **Step 3: 修改 trace 初始化**

在 `init_trace` 返回对象中加入：

```python
"schema_version": 2,
"parallel_groups": [],
"actor_calls": [],
```

- [ ] **Step 4: 扩展 `append_event` 签名**

改为：

```python
def append_event(
    run_dir: str | Path,
    actor: str,
    visibility: str,
    event_type: str,
    content: str,
    target: str = "",
    source_call_id: str = "",
    causal_links: Iterable[str] | None = None,
) -> Dict[str, Any]:
```

事件对象加入：

```python
"target": str(target),
"source_call_id": str(source_call_id),
"causal_links": list(causal_links or []),
```

- [ ] **Step 5: 增加并行组记录**

新增：

```python
def record_parallel_group(run_dir: str | Path, group_id: str, actors: Iterable[str]) -> Dict[str, Any]:
    trace = _read(run_dir) or init_trace(run_dir)
    groups = trace.get("parallel_groups")
    if not isinstance(groups, list):
        groups = []
        trace["parallel_groups"] = groups
    groups.append({"group_id": str(group_id), "actors": list(actors)})
    trace["updated_at"] = _now()
    return _write(run_dir, trace)
```

- [ ] **Step 6: 扩展 summary**

`summarize_for_story_input` 中的 visible event 输出加入 `target`、`source_call_id`、`causal_links`，返回对象加入：

```python
"schema_version": int(trace.get("schema_version", 1) or 1),
"parallel_groups": trace.get("parallel_groups", []) if isinstance(trace.get("parallel_groups"), list) else [],
```

- [ ] **Step 7: 运行测试**

Run:

```powershell
python -m unittest tests.test_agent_interactions -v
```

Expected: pass.

- [ ] **Step 8: 提交**

```powershell
git add skills/agent_interactions.py tests/test_agent_interactions.py
git commit -m "feat: 扩展交互轨迹v2"
```

---

### Task 4: GM Loop 控制面

**Files:**
- Create: `skills/agent_turn_loop.py`
- Create: `tests/test_agent_turn_loop.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_agent_turn_loop.py`：

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


class AgentTurnLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "round-000001"
        self.run_dir.mkdir()
        self.agent_run = load_module("agent_run")
        self.agent_turn_loop = load_module("agent_turn_loop")
        self.agent_run.write_json(self.run_dir / "input.json", {
            "routed_input": {"role_channel": "I ask SuLi about the pendant.", "user_instruction_channel": ""},
            "recent_chat": [{"summary": "Morning classroom."}],
            "gm_only_hidden_settings": [{"fact": "Pendant is dangerous."}],
            "character_contexts": {"characters": [{"name": "SuLi", "memory": ["I know old rituals."]}]},
        })

    def tearDown(self):
        self.tmp.cleanup()

    def test_loop_routes_dialogue_to_target_character_and_stops_at_decision(self):
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            if agent_key == "gm":
                if len([c for c in calls if c[0] == "gm"]) == 1:
                    return {
                        "agent": "gm",
                        "scene_beats": [{"content": "The classroom noise thins."}],
                        "events": [],
                        "actor_calls": [{"call_id": "call-player-1", "actor_id": "player", "prompt": "You decide to ask SuLi quietly.", "reason": "Player intent."}],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "continue",
                    }
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [{"type": "dialogue_transfer", "target": "character:SuLi", "content": "SuLi hears the pendant question."}],
                    "actor_calls": [{"call_id": "call-suli-1", "actor_id": "character:SuLi", "prompt": "You hear him ask about the pink pendant.", "reason": "Dialogue target."}],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": {"reason": "The player must decide whether to reveal the pendant.", "options": ["show it", "hide it"]},
                    "stop_reason": "player_decision",
                }
            if agent_key == "player":
                return {
                    "agent": "player",
                    "agent_id": "player",
                    "events": [{"type": "dialogue", "target": "character:SuLi", "content": "Do you know what this pendant is?"}],
                    "stop_reason": "continue",
                }
            return {
                "agent": "character",
                "agent_id": "character:SuLi",
                "character_name": "SuLi",
                "events": [{"type": "dialogue", "target": "player", "content": "Where did you get that?"}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=5)
        self.assertEqual(result["stop_reason"], "player_decision")
        self.assertIn("character:SuLi", result["called_actors"])
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        contents = [event["content"] for event in trace["events"]]
        self.assertIn("Do you know what this pendant is?", contents)
        self.assertIn("Where did you get that?", contents)
```

- [ ] **Step 2: 运行失败测试**

Run:

```powershell
python -m unittest tests.test_agent_turn_loop -v
```

Expected: import failure for missing `agent_turn_loop.py`.

- [ ] **Step 3: 实现 loop 框架**

创建 `skills/agent_turn_loop.py`：

```python
"""Interactive GM-driven turn loop for Claude Code RP rounds."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict

import agent_interactions
import agent_projection
import agent_run
import agent_schemas


MAX_LOOP_STEPS = 8
MAX_ACTOR_CALLS = 12


DispatchFn = Callable[[str, Dict[str, Any]], Dict[str, Any]]


def _read_input(run_dir: Path) -> Dict[str, Any]:
    data = agent_run.read_json(run_dir / "input.json", {})
    return data if isinstance(data, dict) else {}


def _characters_by_actor_id(input_payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    contexts = input_payload.get("character_contexts")
    if not isinstance(contexts, dict):
        return {}
    result = {}
    for item in contexts.get("characters") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            result[f"character:{agent_run.safe_name(name)}"] = item
            result[f"character:{name}"] = item
    return result


def _actor_state(actor_id: str, input_payload: Dict[str, Any]) -> Dict[str, Any]:
    if actor_id == "player":
        return {"name": "player", "memory": [], "goals": []}
    return _characters_by_actor_id(input_payload).get(actor_id, {"name": actor_id.split(":", 1)[-1]})


def _record_actor_events(run_dir: Path, actor_id: str, events: list[Dict[str, Any]], source_call_id: str) -> None:
    for event in events:
        visibility = "world_visible" if event.get("type") in {"dialogue", "action"} else "actor_visible"
        agent_interactions.append_event(
            run_dir,
            actor=actor_id,
            visibility=visibility,
            event_type=str(event.get("type") or ""),
            content=str(event.get("content") or ""),
            target=str(event.get("target") or ""),
            source_call_id=source_call_id,
        )


def _write_outputs(run_dir: Path, gm_outputs: list[Dict[str, Any]], actor_outputs: Dict[str, list[Dict[str, Any]]]) -> None:
    agent_run.write_json(run_dir / "gm.output.json", {"agent": "gm_loop", "outputs": gm_outputs})
    agent_run.write_json(run_dir / "actor.outputs.json", actor_outputs)


def run_interactive_loop(run_dir: str | Path, dispatch: DispatchFn, max_steps: int = MAX_LOOP_STEPS) -> Dict[str, Any]:
    root = Path(run_dir)
    input_payload = _read_input(root)
    routed = input_payload.get("routed_input") if isinstance(input_payload.get("routed_input"), dict) else {}
    world_state: Dict[str, Any] = {
        "role_channel": routed.get("role_channel", ""),
        "user_instruction_channel": routed.get("user_instruction_channel", ""),
        "recent_chat": input_payload.get("recent_chat", []),
        "gm_only_hidden_settings": input_payload.get("gm_only_hidden_settings", []),
        "visible_events": [],
    }
    trace = agent_interactions.init_trace(root, participants=["gm"], chapter_target_words=0)
    gm_outputs: list[Dict[str, Any]] = []
    actor_outputs: Dict[str, list[Dict[str, Any]]] = {}
    called_actors: list[str] = []
    stop_reason = "max_steps"

    for step in range(max_steps):
        gm_payload = dispatch("gm", {"world_state": world_state, "step": step})
        gm_output = agent_schemas.validate_gm_output(gm_payload)
        gm_outputs.append(gm_output)
        for beat in gm_output.get("scene_beats", []):
            agent_interactions.append_event(root, "gm", "world_visible", "scene_beat", str(beat.get("content", beat)))
        for event in gm_output.get("events", []):
            agent_interactions.append_event(
                root,
                "gm",
                "world_visible",
                str(event.get("type", "gm_event")),
                str(event.get("content", "")),
                target=str(event.get("target", "")),
            )
        for group_index, actors in enumerate(gm_output.get("parallel_groups", [])):
            agent_interactions.record_parallel_group(root, f"step-{step}-group-{group_index}", actors)
        if gm_output.get("decision_point"):
            decision = gm_output["decision_point"]
            agent_interactions.mark_decision_point(root, str(decision.get("reason", "")), decision.get("options", []))
            stop_reason = "player_decision"
            break
        actor_calls = gm_output.get("actor_calls", [])
        for call in actor_calls:
            actor_id = str(call.get("actor_id") or "")
            if not actor_id:
                continue
            packet = agent_projection.project_actor_context(
                actor_id,
                world_state,
                _actor_state(actor_id, input_payload),
                str(call.get("prompt") or ""),
            )
            agent_key = "player" if actor_id == "player" else actor_id
            actor_payload = dispatch(agent_key, packet)
            actor_output = agent_schemas.validate_actor_output(actor_payload)
            called_actors.append(actor_id)
            actor_outputs.setdefault(actor_id, []).append(actor_output)
            _record_actor_events(root, actor_id, actor_output["events"], str(call.get("call_id") or ""))
            world_state["visible_events"] = agent_interactions.summarize_for_story_input(root)["visible_events"]
            if actor_output.get("stop_reason") == "stop_for_player_decision":
                agent_interactions.mark_decision_point(root, "Actor requested a real player decision.", [])
                stop_reason = "player_decision"
                break
        if stop_reason == "player_decision":
            break
        if gm_output.get("stop_reason") in {"word_target", "complete"}:
            stop_reason = str(gm_output.get("stop_reason"))
            break

    _write_outputs(root, gm_outputs, actor_outputs)
    return {
        "ok": True,
        "stop_reason": stop_reason,
        "called_actors": called_actors,
        "gm_steps": len(gm_outputs),
    }
```

- [ ] **Step 4: 运行测试**

Run:

```powershell
python -m unittest tests.test_agent_turn_loop -v
```

Expected: pass with `agent_turn_loop.py` importing `agent_interactions`, `agent_projection`, `agent_run`, and `agent_schemas` from the local `skills/` path.

- [ ] **Step 5: 提交**

```powershell
git add skills/agent_turn_loop.py tests/test_agent_turn_loop.py
git commit -m "feat: 增加交互式GM回路控制面"
```

---

### Task 5: Agent Packets 与 Prompt 合同改造

**Files:**
- Modify: `skills/agent_packets.py`
- Modify: `skills/agent_prompts.py`
- Modify: `tests/test_agent_packets.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_agent_packets.py` 增加：

```python
def test_actor_packets_are_projected_and_do_not_expose_hidden_instruction(self):
    result = self.agent_packets.prepare_agent_run(
        self.card,
        user_text="I hide the pendant.",
        chat_log=[{"ai": "Hidden GM-only old output", "summary": "Classroom morning."}],
        card_data={"name": "Smoke"},
        character_contexts={"characters": [{"name": "SuLi", "memory": ["I know rituals."]}]},
        input_payload={
            "input_schema": "dual_channel_v1",
            "raw_text": "I hide the pendant.",
            "role_text": "I hide the pendant.",
            "user_instruction_text": "Hidden: pendant burns identity.",
        },
        hidden_setting_records=[{"fact": "pendant burns identity"}],
    )
    run_dir = Path(result["run_dir"])
    player_packet = json.loads((run_dir / "player.context.json").read_text(encoding="utf-8"))
    char_packet = json.loads((run_dir / "characters" / "SuLi.context.json").read_text(encoding="utf-8"))
    self.assertEqual(player_packet["visibility"], "first_person_player")
    self.assertEqual(char_packet["visibility"], "first_person_character")
    serialized = json.dumps({"player": player_packet, "character": char_packet}, ensure_ascii=False)
    self.assertNotIn("user_instruction_text", serialized)
    self.assertNotIn("pendant burns identity", serialized)
    self.assertNotIn("Hidden GM-only", serialized)
```

- [ ] **Step 2: 运行失败测试**

Run:

```powershell
python -m unittest tests.test_agent_packets -v
```

Expected: failure because current actor packets include direct `recent_chat` and role/input fields.

- [ ] **Step 3: 修改 packet 生成**

在 `skills/agent_packets.py` 引入：

```python
import agent_projection
```

将 `build_player_packet` 改为：

```python
def build_player_packet(card_folder, routed_input: Dict[str, Any], recent_chat, actor_state=None, world_state=None):
    world = dict(world_state or {})
    world["role_channel"] = _to_text(routed_input.get("role_channel"))
    return agent_projection.project_actor_context(
        "player",
        world,
        actor_state or {"name": "player", "memory": [], "goals": []},
        _to_text(routed_input.get("role_channel")),
    )
```

将 `build_character_packet` 改为：

```python
def build_character_packet(card_folder, character: Dict[str, Any], routed_input: Dict[str, Any], recent_chat, world_state=None):
    character_data = character or {}
    name = _to_text(character_data.get("name"))
    return agent_projection.project_actor_context(
        f"character:{agent_run.safe_name(name)}",
        world_state or {},
        character_data,
        f"You are present in the current scene if the GM calls you. Wait for the GM's second-person scene prompt.",
    )
```

在 `prepare_agent_run` 和 `rebuild_agent_run_from_analysis` 构造：

```python
world_state = {
    "role_channel": routed_input.get("role_channel", ""),
    "user_instruction_channel": routed_input.get("user_instruction_channel", ""),
    "recent_chat": chat_log or [],
    "gm_only_hidden_settings": hidden_setting_records,
    "visible_events": [],
}
```

GM packet 保留完整信息；player/character packet 使用投影函数。

- [ ] **Step 4: 修改 prompt 合同**

在 `skills/agent_prompts.py` 中将 GM contract 改为新事件协议，将 player/character contract 改为：

```python
{
    "agent": "player",
    "agent_id": "player",
    "events": [{"type": "action", "target": "", "content": "...", "metadata": {}}],
    "stop_reason": "continue"
}
```

character contract 同理加 `character_name`。

- [ ] **Step 5: 运行测试**

Run:

```powershell
python -m unittest tests.test_agent_packets tests.test_agent_projection tests.test_agent_schemas -v
```

Expected: pass.

- [ ] **Step 6: 提交**

```powershell
git add skills/agent_packets.py skills/agent_prompts.py tests/test_agent_packets.py
git commit -m "fix: 使用视角投影生成actor上下文"
```

---

### Task 6: Story Input 组装改为 Trace/Event 中心

**Files:**
- Modify: `skills/agent_outputs.py`
- Modify: `tests/test_agent_outputs.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_agent_outputs.py` 增加：

```python
def test_build_story_input_uses_loop_outputs_and_trace_v2(self):
    self.agent_run.write_json(self.run_dir / "gm.output.json", {
        "agent": "gm_loop",
        "outputs": [
            {
                "agent": "gm",
                "scene_beats": [{"content": "The classroom goes quiet."}],
                "events": [],
                "actor_calls": [],
                "parallel_groups": [],
                "world_state_delta": [],
                "decision_point": None,
                "stop_reason": "complete",
            }
        ],
    })
    self.agent_run.write_json(self.run_dir / "actor.outputs.json", {
        "player": [
            {
                "agent": "player",
                "agent_id": "player",
                "events": [{"type": "dialogue", "target": "character:SuLi", "content": "Do you know this?"}],
                "stop_reason": "continue",
            }
        ],
        "character:SuLi": [
            {
                "agent": "character",
                "agent_id": "character:SuLi",
                "character_name": "SuLi",
                "events": [{"type": "dialogue", "target": "player", "content": "Where did you get that?"}],
                "stop_reason": "continue",
            }
        ],
    })
    self.agent_interactions.init_trace(self.run_dir, ["gm", "player", "character:SuLi"], 1200)
    self.agent_interactions.append_event(self.run_dir, "player", "world_visible", "dialogue", "Do you know this?", target="character:SuLi")
    story_input = self.agent_outputs.build_story_input(self.run_dir)
    self.assertIn("loop_outputs", story_input)
    self.assertEqual(story_input["interaction_trace"]["schema_version"], 2)
    self.assertEqual(story_input["loop_outputs"]["actors"]["character:SuLi"][0]["events"][0]["content"], "Where did you get that?")
```

- [ ] **Step 2: 运行失败测试**

Run:

```powershell
python -m unittest tests.test_agent_outputs -v
```

Expected: failure because `build_story_input` still expects legacy player/character output files.

- [ ] **Step 3: 增加 loop artifact 加载**

在 `skills/agent_outputs.py` 增加：

```python
def _load_loop_outputs(root: Path) -> Dict[str, Any]:
    gm_loop = _read_json_required(root / "gm.output.json")
    actor_outputs = _read_json_required(root / "actor.outputs.json")
    return {"gm": gm_loop, "actors": actor_outputs}
```

将 `build_story_input` 改为优先读取 `actor.outputs.json`；如果文件缺失则报错，不回退旧 actor 文件。

- [ ] **Step 4: 更新 memory delta 提取**

新增：

```python
def _memory_deltas_from_events(actor_outputs: Dict[str, Any], gm_loop: Dict[str, Any]) -> Dict[str, Any]:
    actor_memory = {}
    for actor_id, outputs in actor_outputs.items():
        items = []
        for output in outputs or []:
            for event in output.get("events", []):
                if event.get("type") in {"memory_delta", "goal_update"}:
                    items.append(event)
        actor_memory[actor_id] = items
    world = []
    for output in gm_loop.get("outputs", []):
        world.extend(output.get("world_state_delta", []))
    return {"actors": actor_memory, "world": world}
```

`story_input` 新结构包含：

```python
"loop_outputs": loop_outputs,
"memory_deltas": _memory_deltas_from_events(loop_outputs["actors"], loop_outputs["gm"]),
"interaction_trace": agent_interactions.summarize_for_story_input(root),
```

- [ ] **Step 5: 运行测试**

Run:

```powershell
python -m unittest tests.test_agent_outputs tests.test_agent_interactions -v
```

Expected: pass after updating old fixtures to loop artifacts.

- [ ] **Step 6: 提交**

```powershell
git add skills/agent_outputs.py tests/test_agent_outputs.py
git commit -m "feat: 基于交互轨迹组装story输入"
```

---

### Task 7: CLI Runner 接入 GM Loop

**Files:**
- Modify: `skills/rp_generate_cli.py`
- Modify: `tests/test_rp_generate_cli.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_rp_generate_cli.py` 增加 fake dispatch 测试：

```python
def test_run_round_uses_interactive_loop_before_story(self):
    dispatch_order = []

    def fake_run_claude(agent_key, prompt, cwd):
        dispatch_order.append(agent_key)
        if agent_key == "input_analyst":
            return json.dumps({"schema_version": 1, "analysis_mode": "fallback", "source_integrity": {"raw_preserved": True}, "semantic_units": [], "world_updates": {"hidden_facts": [], "public_facts": [], "important_characters": [], "retcon_requests": []}, "narrative_directives": {}, "routing": {"gm": True, "player": True, "characters": []}, "risks": []})
        if agent_key == "gm":
            return json.dumps({"agent": "gm", "scene_beats": [{"content": "The room quiets."}], "events": [], "actor_calls": [{"call_id": "call-player-1", "actor_id": "player", "prompt": "You ask quietly.", "reason": "role input"}], "parallel_groups": [], "world_state_delta": [], "decision_point": {"reason": "Choose whether to show the pendant.", "options": ["show", "hide"]}, "stop_reason": "player_decision"})
        if agent_key == "player":
            return json.dumps({"agent": "player", "agent_id": "player", "events": [{"type": "action", "target": "", "content": "I keep my hand closed."}], "stop_reason": "continue"})
        if agent_key == "story":
            return json.dumps({"content": "<content>你把手合拢。</content><summary>你还没有展示吊坠。</summary><options><font color=\"#5a8a9a\">展示</font></options>", "character_dialogues": [], "metadata": {}})
        if agent_key == "critic":
            return json.dumps({"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": "", "system_iteration_suggestion": ""})
        raise AssertionError(agent_key)

    result = self.rp_generate_cli.run_round(self.card, ROOT, run_claude=fake_run_claude, run_command=self.fake_delivery_command)
    self.assertTrue(result["ok"])
    self.assertIn("gm", dispatch_order)
    self.assertIn("player", dispatch_order)
    self.assertLess(dispatch_order.index("gm"), dispatch_order.index("story"))
```

- [ ] **Step 2: 运行失败测试**

Run:

```powershell
python -m unittest tests.test_rp_generate_cli -v
```

Expected: failure because `run_round` still dispatches legacy actor outputs directly.

- [ ] **Step 3: 引入 `agent_turn_loop`**

在 `skills/rp_generate_cli.py` import：

```python
import agent_turn_loop
```

新增适配 dispatch：

```python
def _loop_dispatch(run_dir: Path, manifest: Dict[str, Any], root: Path, run_claude, agent_key: str, packet: Dict[str, Any]) -> Dict[str, Any]:
    prompt_key = "gm" if agent_key == "gm" else ("player" if agent_key == "player" else "character")
    if prompt_key == "character":
        prompt_text = _read_prompt(run_dir, manifest, "character_call")
    else:
        prompt_text = _read_prompt(run_dir, manifest, prompt_key)
    return _dispatch_and_write(
        agent_key,
        run_dir / "loop.tmp.json",
        prompt_text,
        root,
        run_claude,
        {"actor_context": packet},
    )
```

如果不单独生成 `character_call.prompt.md`，先复用 `prompts/characters/<safe>.prompt.md`：当 `agent_key.startswith("character:")` 时从 `manifest["prompts"]["characters"]` 查找 safe name。

- [ ] **Step 4: 替换 legacy actor dispatch**

在 `run_round` 中 `_ensure_input_analysis` 后，删除直接 dispatch `gm/player/characters` 的代码块，改为：

```python
loop_result = agent_turn_loop.run_interactive_loop(
    run_dir,
    lambda agent_key, packet: _loop_dispatch(run_dir, manifest, root, run_claude, agent_key, packet),
)
story_input = agent_outputs.build_story_input(run_dir)
```

返回 artifacts 改为：

```python
"artifacts": {
    "loop": loop_result,
    "story": bool(story),
    "critic": bool(critic),
},
```

- [ ] **Step 5: 运行测试**

Run:

```powershell
python -m unittest tests.test_rp_generate_cli tests.test_agent_turn_loop tests.test_agent_outputs -v
```

Expected: pass.

- [ ] **Step 6: 提交**

```powershell
git add skills/rp_generate_cli.py tests/test_rp_generate_cli.py
git commit -m "feat: 接入交互式GM回合runner"
```

---

### Task 8: Prompt 和 Skill 英文化并对齐新协议

**Files:**
- Modify: `.claude/skills/rp-orchestrator.md`
- Modify: `.claude/skills/rp-context-projector.md`
- Modify: `.claude/skills/rp-gm-agent.md`
- Modify: `.claude/skills/rp-player-agent.md`
- Modify: `.claude/skills/rp-character-agent.md`
- Modify: `.claude/skills/rp-story-agent.md`
- Modify: `.claude/skills/rp-critic-agent.md`
- Modify: `.claude/commands/rp.md`
- Modify: `CLAUDE.md`
- Create: `tests/test_prompt_language.py`

- [ ] **Step 1: 写语言和协议测试**

创建 `tests/test_prompt_language.py`：

```python
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CJK_RE = re.compile(r"[\u3400-\u9fff]")


class PromptLanguageTest(unittest.TestCase):
    def test_claude_and_skill_files_are_english(self):
        paths = [ROOT / "CLAUDE.md"] + list((ROOT / ".claude" / "skills").glob("rp-*.md"))
        offenders = []
        for path in paths:
            text = path.read_text(encoding="utf-8")
            if CJK_RE.search(text):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(offenders, [])

    def test_actor_skills_describe_events_contract(self):
        player = (ROOT / ".claude" / "skills" / "rp-player-agent.md").read_text(encoding="utf-8")
        character = (ROOT / ".claude" / "skills" / "rp-character-agent.md").read_text(encoding="utf-8")
        self.assertIn('"events"', player)
        self.assertIn('"stop_reason"', player)
        self.assertIn('"events"', character)
        self.assertNotIn('"action": "first-person action"', player)
```

- [ ] **Step 2: 运行失败测试**

Run:

```powershell
python -m unittest tests.test_prompt_language -v
```

Expected: failure because current skills and `CLAUDE.md` contain Chinese text and legacy contracts.

- [ ] **Step 3: 改写 skill 合同**

将 GM skill 的 Output Schema 改为：

```json
{
  "agent": "gm",
  "scene_beats": [],
  "events": [],
  "actor_calls": [],
  "parallel_groups": [],
  "world_state_delta": [],
  "decision_point": null,
  "stop_reason": "continue"
}
```

将 player/character skill 的 Output Schema 改为：

```json
{
  "agent": "player",
  "agent_id": "player",
  "events": [
    {"type": "action", "target": "", "content": "...", "metadata": {}}
  ],
  "stop_reason": "continue"
}
```

character 版本额外包含 `character_name`。

- [ ] **Step 4: 英文化 `CLAUDE.md` 和 skills**

保留技术含义但全部改为自然英文。允许文件路径、标签名和命令保持原样。不要修改 UI 文案或测试剧情文本。

- [ ] **Step 5: 运行测试**

Run:

```powershell
python -m unittest tests.test_prompt_language tests.test_agent_packets -v
```

Expected: pass.

- [ ] **Step 6: 提交**

```powershell
git add .claude/skills/rp-orchestrator.md .claude/skills/rp-context-projector.md .claude/skills/rp-gm-agent.md .claude/skills/rp-player-agent.md .claude/skills/rp-character-agent.md .claude/skills/rp-story-agent.md .claude/skills/rp-critic-agent.md .claude/commands/rp.md CLAUDE.md tests/test_prompt_language.py
git commit -m "docs: 英文化claude工作流提示"
```

---

### Task 9: Control Plane Smoke 覆盖交互回路

**Files:**
- Modify: `skills/control_plane_smoke.py`
- Modify: `tests/test_control_plane_smoke.py`

- [ ] **Step 1: 更新 smoke 断言**

在 `tests/test_control_plane_smoke.py` 中断言：

```python
self.assertTrue(payload["ok"])
self.assertEqual(payload["manifest_stage"], "delivered")
self.assertEqual(payload["trace"]["schema_version"], 2)
self.assertGreaterEqual(payload["loop"]["gm_steps"], 1)
self.assertIn("player", payload["loop"]["called_actors"])
self.assertIn("dialogue_transfer", [event["type"] for event in payload["trace"]["visible_events"]])
self.assertIsNotNone(payload["trace"]["decision_point"])
```

- [ ] **Step 2: 运行失败测试**

Run:

```powershell
python -m unittest tests.test_control_plane_smoke -v
```

Expected: failure because smoke still writes legacy artifacts directly.

- [ ] **Step 3: 改造 smoke fixture**

在 `skills/control_plane_smoke.py` 中使用 `agent_turn_loop.run_interactive_loop` 和 fake dispatch，至少覆盖：

```python
def _fake_dispatch(agent_key, packet):
    if agent_key == "gm":
        return {
            "agent": "gm",
            "scene_beats": [{"content": "The archive lamp flickers."}],
            "events": [{"type": "dialogue_transfer", "target": "player", "content": "Ada warns the player."}],
            "actor_calls": [{"call_id": "call-player-1", "actor_id": "player", "prompt": "You stand at the archive door.", "reason": "player action"}],
            "parallel_groups": [],
            "world_state_delta": [{"scope": "archive", "fact": "The door is open."}],
            "decision_point": {"reason": "The player must choose whether to enter.", "options": ["enter", "wait"]},
            "stop_reason": "player_decision",
        }
    if agent_key == "player":
        return {
            "agent": "player",
            "agent_id": "player",
            "events": [{"type": "action", "target": "", "content": "I hold the lamp near the threshold."}],
            "stop_reason": "continue",
        }
    return {
        "agent": "character",
        "agent_id": agent_key,
        "character_name": agent_key.split(":", 1)[-1],
        "events": [{"type": "dialogue", "target": "player", "content": "Stay close."}],
        "stop_reason": "continue",
    }
```

写入 story/critic 后继续调用 `agent_outputs.prepare_delivery` 和 `mark_delivered`。

- [ ] **Step 4: 运行 smoke**

Run:

```powershell
python -m unittest tests.test_control_plane_smoke -v
python skills/control_plane_smoke.py --repo .
```

Expected: tests pass and command prints JSON with `"ok": true`.

- [ ] **Step 5: 提交**

```powershell
git add skills/control_plane_smoke.py tests/test_control_plane_smoke.py
git commit -m "test: 覆盖交互式GM回路smoke"
```

---

### Task 10: README、设计文档和验收清单同步

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-06-18-interactive-gm-loop-design.md`
- Modify: `AGENTS.md` only if the current task revealed a recurring project-level rule that is not already recorded.

- [ ] **Step 1: 更新 README 的核心角色说明**

把 `README.md` 中“核心角色 subagent”段落改为说明：

```markdown
Claude Code 工作流现在以交互式 GM 回路推进每轮剧情。GM 可以读取完整剧情和用户指令，负责推进世界、判断重要角色参与点，并向 player/character agent 发出第二人称场景转述。player/character agent 只接收视角投影后的可感知信息、自己的记忆和目标，不直接读取完整聊天记录、隐藏设定或用户指令。
```

并补充：

```markdown
重要角色在同一回合内可以被多次唤起。其输出以事件流保存，包括行动、对白、感知请求、记忆写入、目标更新和停止原因。Story agent 只能基于 GM loop 和 trace 中的合法可见事件整理正文，不得替重要角色编造核心回应。
```

- [ ] **Step 2: 更新 spec 的实现状态**

在 `docs/superpowers/specs/2026-06-18-interactive-gm-loop-design.md` 的“状态”后追加：

```markdown
实现完成后，本设计对应的控制面入口为 `skills/agent_turn_loop.py`，角色投影入口为 `skills/agent_projection.py`，确定性验收入口为 `python skills/control_plane_smoke.py --repo .`。
```

- [ ] **Step 3: 运行文档 diff 检查**

Run:

```powershell
git diff --check README.md docs\\superpowers\\specs\\2026-06-18-interactive-gm-loop-design.md AGENTS.md
```

Expected: no whitespace errors.

- [ ] **Step 4: 提交文档**

```powershell
git add README.md docs/superpowers/specs/2026-06-18-interactive-gm-loop-design.md
git commit -m "docs: 对齐交互式GM回路说明"
```

---

### Task 11: 最终验证

**Files:**
- Verify all files changed by Tasks 1-10.

- [ ] **Step 1: 运行完整单元测试**

```powershell
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: 运行控制面 smoke**

```powershell
python skills/control_plane_smoke.py --repo .
```

Expected: JSON contains `"ok": true`, `"trace": {"schema_version": 2, ...}`, and `"manifest_stage": "delivered"`.

- [ ] **Step 3: 编译检查**

```powershell
python -m py_compile skills/agent_schemas.py skills/agent_projection.py skills/agent_interactions.py skills/agent_turn_loop.py skills/agent_packets.py skills/agent_prompts.py skills/agent_outputs.py skills/rp_generate_cli.py skills/control_plane_smoke.py
```

Expected: no output and exit code 0.

- [ ] **Step 4: 检查工作区**

```powershell
git status --short
```

Expected: only intentional files changed. Do not stage card folders, generated images, `.agent_runs/`, `魔法禁书目录.png`, local secrets, or unrelated existing edits.

- [ ] **Step 5: 真实游玩验收**

Run:

```powershell
python skills/start_server.py .
```

Then in a blank card/story folder, start Claude Code and run `/rp`. Use a short four-turn scenario:

1. Player defines a protagonist and a strange object.
2. User instruction declares one important character with hidden knowledge.
3. Player asks that character a question.
4. Player responds to the character's answer.

Expected observations:

- The browser shows player input immediately and exactly.
- GM loop calls `player` and the important character at least once.
- The important character can be called more than once in the same turn when the dialogue continues.
- Actor context files do not contain hidden user instructions or full recent chat.
- Important character dialogue boxes are sourced from `character:<safe_name>` events.
- Final prose uses the configured perspective and stops at a real player decision.

- [ ] **Step 6: 如有验证修正则提交**

If verification required fixes:

```powershell
git add <changed-files>
git commit -m "fix: 完成交互式GM回路验证修正"
```

---

## 执行建议

优先使用 Subagent-Driven 执行。每个 task 都有独立测试和提交点，适合由新 subagent 执行、主 agent 复核 diff 和测试输出。不要一次性跨多个 task 修改 runner、schema 和 prompt；这些层耦合强，批量修改会让失败来源难以定位。
