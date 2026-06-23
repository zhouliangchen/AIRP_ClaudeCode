# Immersive Projection Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an LLM-driven projection review step and immersive actor-facing context while preserving a minimal deterministic protocol gate.

**Architecture:** Keep `agent_dispatcher.py`, `agent_messages.py`, and `agent_intents.py` as the control-plane authority. Add focused helpers for objective world state, actor context rendering, and projection review; Python validates only protocol and artifact invariants while the projection agent handles semantic review and local edits.

**Tech Stack:** Python standard library, existing `unittest` suite, existing Claude Code agent dispatch path, existing JSON file mailbox runtime.

---

## File Structure

- Create `skills/actor_context_renderer.py`: render internal actor state into immersive first-person / second-person text and remove actor-facing `misconceptions`.
- Create `skills/projection_agent.py`: build projection review packets, validate projection decisions, and prepare projected actor packets.
- Create `skills/objective_world.py`: read/write save-level objective world facts under `memory/objective_world.json`.
- Modify `skills/agent_projection.py`: use rendered actor context and stop exposing `misconceptions`.
- Modify `skills/agent_prompts.py`: add projection agent prompt text and switch actor prompts to immersive context wording.
- Modify `skills/rp_generate_cli.py`: validate `projection` agent output.
- Modify `skills/agent_actor_runtime.py`: accept projection decisions and final actor messages when materializing projected messages.
- Modify `skills/agent_dispatcher.py`: run projection agent inside `request_projection`, handle `pass`, `edited`, `needs_rewrite`, and `blocked`.
- Modify `skills/agent_packets.py`: include objective world state in GM packets and remove actor-facing misconception aliases.
- Modify `skills/agent_snapshots.py`: ensure objective world file coverage is explicit in snapshot metadata.
- Modify docs: `README.md`, `docs/重构建议.md`, `.claude/skills/rp-context-projector.md`, `.claude/skills/rp-gm-agent.md`, `.claude/skills/rp-subgm-agent.md`, `.claude/skills/rp-character-agent.md`, `.claude/skills/rp-player-agent.md`.
- Test `tests/test_actor_context_renderer.py`: actor context rendering and forbidden label checks.
- Test `tests/test_projection_agent.py`: projection output validation and semantic fixture behavior.
- Modify `tests/test_agent_projection.py`: new actor packet shape.
- Modify `tests/test_agent_prompts.py` or create it if absent: projection prompt and actor prompt rendering.
- Modify `tests/test_agent_dispatcher.py`: dispatcher projection flow decisions.
- Modify `tests/test_agent_snapshots.py`: objective world snapshot coverage.
- Modify `tests/test_agent_packets.py`: objective world state appears in GM context and not actor prompt internals.

## Scope Notes

This plan intentionally does not change frontend UI, image generation, provider configuration, or postprocess behavior. It also does not let projection modify objective world files or actor memory. Projection can only approve, locally edit, request rewrite, or block the actor-facing message.

### Task 1: Actor Context Renderer

**Files:**
- Create: `skills/actor_context_renderer.py`
- Create: `tests/test_actor_context_renderer.py`
- Modify: `skills/agent_projection.py`
- Modify: `tests/test_agent_projection.py`

- [ ] **Step 1: Write failing tests for immersive rendering**

Create `tests/test_actor_context_renderer.py`:

```python
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ActorContextRendererTest(unittest.TestCase):
    def setUp(self):
        self.renderer = load_module("actor_context_renderer")

    def test_render_character_context_is_immersive_and_subjective(self):
        actor = {
            "name": "Current Paladin",
            "role": "royal paladin",
            "memory": {
                "long_term": ["I was taught that cursed heroes endanger civilians."],
                "key_memories": ["I swore to protect the market district."],
                "short_term": ["I saw an old wanted sigil on the traveler's cloak."],
                "goals": ["Keep civilians safe."],
            },
            "misconceptions": ["The old hero is cursed."],
            "sensory_context": {"sight": "Crowded market stalls block the north road."},
        }

        rendered = self.renderer.render_actor_context("character:CurrentPaladin", actor, {})
        serialized = json.dumps(rendered, ensure_ascii=False)

        self.assertEqual(rendered["actor_id"], "character:CurrentPaladin")
        self.assertIn("You are Current Paladin", rendered["immersive_context"])
        self.assertIn("You remember: I was taught that cursed heroes endanger civilians.", rendered["immersive_context"])
        self.assertIn("Your current goal is: Keep civilians safe.", rendered["immersive_context"])
        self.assertNotIn("misconceptions", serialized)
        self.assertNotIn("objective_truth", serialized)
        self.assertNotIn("gm_only", serialized)
        self.assertNotIn("belief_is_false", serialized)

    def test_render_player_context_uses_first_person_anchor(self):
        actor = {
            "name": "player",
            "memory": {"short_term": ["I stepped into the rain."]},
        }
        world = {"role_channel": "I keep my hand on the doorframe."}

        rendered = self.renderer.render_actor_context("player", actor, world)

        self.assertIn("You are the player character.", rendered["immersive_context"])
        self.assertIn("Current first-person anchor: I keep my hand on the doorframe.", rendered["immersive_context"])
        self.assertNotIn("runtime", rendered["immersive_context"].lower())
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m unittest tests.test_actor_context_renderer -v
```

Expected: FAIL with an import error for missing `actor_context_renderer.py`.

- [ ] **Step 3: Implement `skills/actor_context_renderer.py`**

Create `skills/actor_context_renderer.py`:

```python
"""Immersive actor context rendering for player and character packets."""

from __future__ import annotations

from typing import Any


FORBIDDEN_ACTOR_KEYS = {
    "misconceptions",
    "objective_truth",
    "gm_only",
    "gm_notes",
    "projection_review",
    "belief_is_false",
    "hidden_facts",
    "hidden_truth",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    text = str(value).strip()
    return [text] if text else []


def _clean_text(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    for marker in FORBIDDEN_ACTOR_KEYS:
        if marker.lower() in text.lower():
            return ""
    return text


def _append_line(lines: list[str], prefix: str, value: Any) -> None:
    text = _clean_text(value)
    if text:
        lines.append(f"{prefix}{text}")


def _memory_lines(memory: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for value in _as_list(memory.get("long_term")):
        _append_line(lines, "You remember: ", value)
    for value in _as_list(memory.get("key_memories")):
        _append_line(lines, "Important to you: ", value)
    for value in _as_list(memory.get("short_term")):
        _append_line(lines, "Recently, you remember: ", value)
    for value in _as_list(memory.get("goals")):
        _append_line(lines, "Your current goal is: ", value)
    return lines


def render_actor_context(actor_id: str, actor_state: dict[str, Any] | None, world_state: dict[str, Any] | None) -> dict[str, Any]:
    actor = _as_dict(actor_state)
    world = _as_dict(world_state)
    memory = _as_dict(actor.get("memory"))
    lines: list[str] = []

    if actor_id == "player":
        lines.append("You are the player character.")
        _append_line(lines, "Current first-person anchor: ", world.get("role_channel"))
    else:
        name = _clean_text(actor.get("name") or actor.get("character_name") or actor_id.split(":", 1)[-1])
        role = _clean_text(actor.get("role") or actor.get("identity"))
        lines.append(f"You are {name}." if name else "You are this character.")
        if role:
            lines.append(f"You understand yourself as: {role}.")

    body_state = _as_dict(actor.get("body_state"))
    for key, value in sorted(body_state.items()):
        _append_line(lines, f"Your {key}: ", value)

    relationships = _as_dict(actor.get("relationships"))
    for key, value in sorted(relationships.items()):
        _append_line(lines, f"Your relationship with {key}: ", value)

    sensory = _as_dict(actor.get("sensory_context") or world.get("sensory_context"))
    for key, value in sorted(sensory.items()):
        _append_line(lines, f"You can sense through {key}: ", value)

    lines.extend(_memory_lines(memory))

    return {
        "actor_id": str(actor_id or ""),
        "immersive_context": "\n".join(line for line in lines if line).strip(),
    }
```

- [ ] **Step 4: Run renderer tests to verify they pass**

Run:

```powershell
python -m unittest tests.test_actor_context_renderer -v
```

Expected: PASS.

- [ ] **Step 5: Modify `skills/agent_projection.py` to include `immersive_context` and remove `misconceptions`**

Add import:

```python
import actor_context_renderer
```

In `project_actor_context`, replace the return object with this shape:

```python
    rendered = actor_context_renderer.render_actor_context(actor_key, actor, world)
    return {
        "actor_id": actor_key,
        "agent": _agent_type(actor_key),
        "visibility": _actor_visibility(actor_key),
        "gm_prompt": _sanitize_prompt(gm_prompt),
        "gm_visibility_basis": _json_safe(
            agent_visibility.normalize_visibility_basis(gm_visibility_basis or {})
        ),
        "address_mode": ADDRESS_MODE,
        "immersive_context": rendered.get("immersive_context", ""),
        "self_knowledge": _self_knowledge(actor),
        "memory": _memory(actor),
        "sensory_context": _sensory_context(world, actor, actor_key),
        "visible_events": _visible_events(world, actor_key, actor),
        "role_channel_anchor": _text(world.get("role_channel")) if actor_key == "player" else "",
    }
```

- [ ] **Step 6: Update projection tests**

In `tests/test_agent_projection.py`, remove expectations for `packet["misconceptions"]`. Add this assertion to the character projection test:

```python
        serialized = _packet_json(packet)
        self.assertIn("You are SuLi.", packet["immersive_context"])
        self.assertNotIn("misconceptions", serialized)
        self.assertNotIn("former magical girl", serialized)
```

In `test_projection_handles_missing_inputs_with_stable_defaults`, remove the `"misconceptions": []` expected entry and add:

```python
                "immersive_context": "You are Missing.",
```

- [ ] **Step 7: Run focused projection tests**

Run:

```powershell
python -m unittest tests.test_actor_context_renderer tests.test_agent_projection -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```powershell
git add skills/actor_context_renderer.py skills/agent_projection.py tests/test_actor_context_renderer.py tests/test_agent_projection.py
git commit -m "feat: 渲染沉浸式 actor 上下文"
```

Expected: commit succeeds.

### Task 2: Projection Agent Contract

**Files:**
- Create: `skills/projection_agent.py`
- Create: `tests/test_projection_agent.py`

- [ ] **Step 1: Write failing tests for projection decisions**

Create `tests/test_projection_agent.py`:

```python
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ProjectionAgentTest(unittest.TestCase):
    def setUp(self):
        self.projection = load_module("projection_agent")

    def test_validate_pass_uses_original_message(self):
        output = self.projection.validate_projection_output(
            {
                "decision": "pass",
                "final_actor_message": "You see a black-robed figure at Alice's door.",
                "feedback": "",
            },
            actor_id="character:Bob",
            source_call_id="call-bob-1",
        )

        self.assertEqual(output["decision"], "pass")
        self.assertEqual(output["final_actor_message"], "You see a black-robed figure at Alice's door.")

    def test_validate_edited_requires_final_actor_message(self):
        with self.assertRaisesRegex(self.projection.ProjectionValidationError, "final_actor_message"):
            self.projection.validate_projection_output(
                {"decision": "edited", "feedback": "Use Bob's visible label."},
                actor_id="character:Bob",
                source_call_id="call-bob-1",
            )

    def test_validate_needs_rewrite_requires_feedback(self):
        with self.assertRaisesRegex(self.projection.ProjectionValidationError, "feedback"):
            self.projection.validate_projection_output(
                {"decision": "needs_rewrite", "final_actor_message": ""},
                actor_id="character:Bob",
                source_call_id="call-bob-1",
            )

    def test_build_review_packet_keeps_objective_and_subjective_context_separate(self):
        packet = self.projection.build_review_packet(
            actor_id="character:CurrentPaladin",
            source_call_id="call-paladin-1",
            requested_message="You discover the cursed hero in the market.",
            actor_packet={
                "immersive_context": "You remember: I was taught that cursed heroes endanger civilians.",
                "visible_events": [{"content": "A traveler enters the market."}],
            },
            objective_context={"facts": ["The hero was framed by the king."]},
            source_message_id="msg_1",
        )

        self.assertEqual(packet["target_actor_id"], "character:CurrentPaladin")
        self.assertIn("framed by the king", packet["objective_context"]["facts"][0])
        self.assertIn("cursed heroes", packet["actor_context"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m unittest tests.test_projection_agent -v
```

Expected: FAIL with an import error for missing `projection_agent.py`.

- [ ] **Step 3: Implement `skills/projection_agent.py`**

Create `skills/projection_agent.py`:

```python
"""Projection agent packet and output validation helpers."""

from __future__ import annotations

import copy
from typing import Any


DECISIONS = {"pass", "edited", "needs_rewrite", "blocked"}


class ProjectionValidationError(ValueError):
    """Raised when projection agent output is not usable."""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def build_review_packet(
    *,
    actor_id: str,
    source_call_id: str,
    requested_message: str,
    actor_packet: dict[str, Any],
    objective_context: dict[str, Any] | None,
    source_message_id: str,
) -> dict[str, Any]:
    actor = _as_dict(actor_packet)
    return {
        "target_actor_id": _text(actor_id),
        "source_call_id": _text(source_call_id),
        "source_message_id": _text(source_message_id),
        "requested_actor_message": _text(requested_message),
        "actor_context": _text(actor.get("immersive_context")),
        "actor_visible_events": copy.deepcopy(actor.get("visible_events") or []),
        "objective_context": copy.deepcopy(_as_dict(objective_context)),
        "instruction": (
            "Review the requested actor message. Return pass, edited, needs_rewrite, or blocked. "
            "Keep the actor immersed and do not reveal whether subjective beliefs are false."
        ),
    }


def validate_projection_output(payload: Any, *, actor_id: str, source_call_id: str) -> dict[str, Any]:
    data = _as_dict(payload)
    decision = _text(data.get("decision"))
    if decision not in DECISIONS:
        raise ProjectionValidationError("decision must be pass, edited, needs_rewrite, or blocked")

    final_message = _text(data.get("final_actor_message"))
    feedback = _text(data.get("feedback") or data.get("projection_feedback"))

    if decision in {"pass", "edited"} and not final_message:
        raise ProjectionValidationError("final_actor_message is required for pass or edited")
    if decision in {"needs_rewrite", "blocked"} and not feedback:
        raise ProjectionValidationError("feedback is required for needs_rewrite or blocked")

    output_actor_id = _text(data.get("target_actor_id") or actor_id)
    if output_actor_id != actor_id:
        raise ProjectionValidationError("target_actor_id does not match projection request")

    output_call_id = _text(data.get("source_call_id") or source_call_id)
    if output_call_id != source_call_id:
        raise ProjectionValidationError("source_call_id does not match projection request")

    return {
        "decision": decision,
        "target_actor_id": actor_id,
        "source_call_id": source_call_id,
        "final_actor_message": final_message,
        "feedback": feedback,
    }
```

- [ ] **Step 4: Run projection agent tests**

Run:

```powershell
python -m unittest tests.test_projection_agent -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add skills/projection_agent.py tests/test_projection_agent.py
git commit -m "feat: 增加 projection agent 契约"
```

Expected: commit succeeds.

### Task 3: Projection Prompt and CLI Validation

**Files:**
- Modify: `skills/agent_prompts.py`
- Modify: `skills/rp_generate_cli.py`
- Create or modify: `tests/test_agent_prompts.py`
- Modify: `tests/test_projection_agent.py`

- [ ] **Step 1: Add prompt tests**

If `tests/test_agent_prompts.py` does not exist, create it with the loader helper below. Add this test:

```python
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AgentPromptsTest(unittest.TestCase):
    def setUp(self):
        self.prompts = load_module("agent_prompts")

    def test_projection_prompt_contains_decision_contract_and_context(self):
        text = self.prompts.projection_prompt_text(
            {
                "target_actor_id": "character:Bob",
                "source_call_id": "call-bob-1",
                "requested_actor_message": "You see a vampire.",
                "actor_context": "You know only that black-robed strangers are dangerous.",
            }
        )

        self.assertIn("Projection Agent Prompt", text)
        self.assertIn('"decision"', text)
        self.assertIn('"final_actor_message"', text)
        self.assertIn("character:Bob", text)
        self.assertIn("black-robed strangers", text)
```

- [ ] **Step 2: Run prompt test to verify it fails**

Run:

```powershell
python -m unittest tests.test_agent_prompts -v
```

Expected: FAIL because `projection_prompt_text` does not exist.

- [ ] **Step 3: Add projection prompt function to `skills/agent_prompts.py`**

Add this function near `character_prompt_text`:

```python
def projection_prompt_text(context: Dict[str, Any]) -> str:
    """Return the generated projection review prompt text."""
    contract = _json_block({
        "decision": "pass",
        "target_actor_id": context.get("target_actor_id", ""),
        "source_call_id": context.get("source_call_id", ""),
        "final_actor_message": "actor-facing second-person message",
        "feedback": "",
    })
    return _base_prompt(
        "Projection Agent Prompt",
        "projection",
        "artifacts/projections/<intent_id>.json",
        contract,
        context if isinstance(context, dict) else {},
        "Review the requested actor message and return exactly one JSON projection result object. "
        "Use `pass` when no change is needed, `edited` for small safe edits, `needs_rewrite` when GM/subGM must rewrite, and `blocked` for invalid requests.",
        contract_notes=(
            "Do not reveal objective truth to the target actor. Do not tell the actor that a belief is false. "
            "Only `final_actor_message` can be delivered to the actor."
        ),
    )
```

- [ ] **Step 4: Add CLI validation for `projection`**

In `skills/rp_generate_cli.py`, add import:

```python
import projection_agent
```

In `_unwrap_payload`, add:

```python
    elif agent_key == "projection":
        wrapper = "projection_output"
```

In `_validate`, add before the final `raise AgentExecutionError`:

```python
        if agent_key == "projection":
            actor_id = str(payload.get("target_actor_id") or "")
            source_call_id = str(payload.get("source_call_id") or "")
            return projection_agent.validate_projection_output(
                payload,
                actor_id=actor_id,
                source_call_id=source_call_id,
            )
```

- [ ] **Step 5: Add projection CLI validation test**

Add to `tests/test_projection_agent.py`:

```python
    def test_rp_generate_cli_validates_projection_output(self):
        cli = load_module("rp_generate_cli")
        payload = cli._validate(
            "projection",
            {
                "decision": "edited",
                "target_actor_id": "character:Bob",
                "source_call_id": "call-bob-1",
                "final_actor_message": "You see a black-robed figure.",
                "feedback": "Changed label to Bob-visible wording.",
            },
        )

        self.assertEqual(payload["decision"], "edited")
        self.assertEqual(payload["final_actor_message"], "You see a black-robed figure.")
```

- [ ] **Step 6: Run prompt and projection tests**

Run:

```powershell
python -m unittest tests.test_agent_prompts tests.test_projection_agent -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add skills/agent_prompts.py skills/rp_generate_cli.py tests/test_agent_prompts.py tests/test_projection_agent.py
git commit -m "feat: 增加 projection agent prompt"
```

Expected: commit succeeds.

### Task 4: Dispatcher Projection Decisions

**Files:**
- Modify: `skills/agent_actor_runtime.py`
- Modify: `skills/agent_dispatcher.py`
- Modify: `tests/test_agent_dispatcher.py`

- [ ] **Step 1: Add dispatcher tests for edited projection and rewrite**

In `tests/test_agent_dispatcher.py`, add two tests near existing `request_projection` tests:

```python
    def test_request_projection_dispatches_projection_agent_and_uses_edited_message(self):
        request = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Bob-1",
                "payload": {
                    "actor_id": "character:Bob",
                    "call": {
                        "call_id": "call-character-Bob-1",
                        "actor_id": "character:Bob",
                        "prompt": "You see a vampire at Alice's door.",
                        "packet": {
                            "actor_id": "character:Bob",
                            "immersive_context": "You know black-robed strangers are dangerous.",
                            "visible_events": [],
                        },
                    },
                },
            },
        )["message"]
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Bob",
                    "source_message_id": request["id"],
                    "source_call_id": "call-character-Bob-1",
                },
            },
        )["intent"]
        dispatch_calls = []

        def fake_dispatch(agent_key, _run_dir, _root, _run_claude, extra_context):
            dispatch_calls.append((agent_key, extra_context))
            self.assertEqual(agent_key, "projection")
            return {
                "decision": "edited",
                "target_actor_id": "character:Bob",
                "source_call_id": "call-character-Bob-1",
                "final_actor_message": "You see a black-robed figure at Alice's door.",
                "feedback": "Bob lacks a basis for the vampire label.",
            }

        self.dispatcher._dispatch_agent_payload = fake_dispatch
        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "")

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent_id"], created["id"])
        self.assertEqual(dispatch_calls[0][0], "projection")
        inbox = self.dispatcher.agent_messages.read_inbox(self.run_dir, "character:Bob")
        projected = inbox[0]
        self.assertEqual(projected["payload"]["packet"]["gm_prompt"], "You see a black-robed figure at Alice's door.")
        self.assertEqual(projected["payload"]["projection"]["decision"], "edited")
        self.assertTrue((self.run_dir / "artifacts" / "projections" / f"{created['id']}.json").exists())

    def test_request_projection_needs_rewrite_creates_gm_follow_up_without_actor_dispatch(self):
        request = self.dispatcher.agent_messages.append_message(
            self.run_dir,
            {
                "from": "gm",
                "to": ["projection"],
                "type": "request_actor",
                "visibility": "gm_only",
                "source_call_id": "call-character-Bob-1",
                "payload": {
                    "actor_id": "character:Bob",
                    "call": {
                        "call_id": "call-character-Bob-1",
                        "actor_id": "character:Bob",
                        "prompt": "You understand the royal conspiracy and attack.",
                        "packet": {"actor_id": "character:Bob", "immersive_context": "You know only market rumors."},
                    },
                },
            },
        )["message"]
        created = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "gm",
                "type": "request_projection",
                "payload": {
                    "actor_id": "character:Bob",
                    "source_message_id": request["id"],
                    "source_call_id": "call-character-Bob-1",
                },
            },
        )["intent"]

        self.dispatcher._dispatch_agent_payload = lambda *_args, **_kwargs: {
            "decision": "needs_rewrite",
            "target_actor_id": "character:Bob",
            "source_call_id": "call-character-Bob-1",
            "final_actor_message": "",
            "feedback": "The message reveals conspiracy knowledge Bob lacks.",
        }

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=lambda *_args: "")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.dispatcher.agent_messages.read_inbox(self.run_dir, "character:Bob"), [])
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([intent["type"] for intent in pending], ["run_gm_turn"])
        self.assertEqual(pending[0]["payload"]["reason"], "projection_needs_rewrite")
        self.assertIn("conspiracy knowledge", pending[0]["payload"]["projection_feedback"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher.AgentDispatcherTest.test_request_projection_dispatches_projection_agent_and_uses_edited_message tests.test_agent_dispatcher.AgentDispatcherTest.test_request_projection_needs_rewrite_creates_gm_follow_up_without_actor_dispatch -v
```

Expected: FAIL because `_execute_request_projection` does not dispatch projection agent.

- [ ] **Step 3: Modify `agent_actor_runtime._projection_packet` to accept final actor message**

In `skills/agent_actor_runtime.py`, update `_projection_packet`:

```python
def _projection_packet(actor_id: str, payload: dict, call: dict) -> dict:
    packet = payload.get("packet")
    if not isinstance(packet, dict):
        packet = call.get("packet")
    if isinstance(packet, dict):
        projected = dict(packet)
        projected.setdefault("actor_id", actor_id)
    else:
        projected = {"actor_id": actor_id, "call": call}
    final_message = payload.get("final_actor_message")
    if isinstance(final_message, str) and final_message.strip():
        projected["gm_prompt"] = final_message.strip()
    return projected
```

Update `_append_projected_message` payload:

```python
            "payload": {
                "actor_id": actor_id,
                "source_message_id": source_message_id,
                "packet": packet,
                "gm_prompt": str(packet.get("gm_prompt") or call.get("prompt") or ""),
                "projection": payload.get("projection") if isinstance(payload.get("projection"), dict) else {},
            },
```

If `_append_projected_message` does not receive `payload`, change its signature to:

```python
def _append_projected_message(run_dir: Path, actor_id: str, call: dict, packet: dict, source_call_id: str, source_message_id: str, payload: dict | None = None) -> dict:
```

and pass `payload` from `project_actor_request`.

- [ ] **Step 4: Modify dispatcher to pass `run_claude` into projection executor**

In `_execute_supported_intent`, replace:

```python
    if intent_type == "request_projection":
        return _execute_request_projection(run_dir, intent)
```

with:

```python
    if intent_type == "request_projection":
        return _execute_request_projection(run_dir, root_dir, intent, run_claude)
```

Update the function signature:

```python
def _execute_request_projection(
    run_dir: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str] | None,
) -> dict[str, Any]:
```

- [ ] **Step 5: Dispatch projection agent inside `_execute_request_projection`**

Add imports:

```python
import projection_agent
```

Inside `_execute_request_projection`, after source fields are read and after accepting the intent, before `agent_actor_runtime.project_actor_request`, add:

```python
        source_message = agent_actor_runtime._find_source_message(run_dir, source_message_id)
        source_payload = source_message.get("payload") if isinstance(source_message, dict) else {}
        if not isinstance(source_payload, dict):
            source_payload = {}
        call = source_payload.get("call") if isinstance(source_payload.get("call"), dict) else {}
        packet = call.get("packet") if isinstance(call.get("packet"), dict) else source_payload.get("packet")
        if not isinstance(packet, dict):
            packet = {"actor_id": actor_id}
        review_packet = projection_agent.build_review_packet(
            actor_id=actor_id,
            source_call_id=source_call_id,
            requested_message=str(call.get("prompt") or ""),
            actor_packet=packet,
            objective_context=_projection_objective_context(run_dir),
            source_message_id=source_message_id,
        )
        raw_projection = _dispatch_agent_payload(
            "projection",
            run_dir,
            root_dir,
            run_claude,
            {"projection_packet": review_packet},
        )
        projection_result = projection_agent.validate_projection_output(
            raw_projection,
            actor_id=actor_id,
            source_call_id=source_call_id,
        )
        _write_projection_artifact(run_dir, intent_id, review_packet, projection_result)
        decision = projection_result["decision"]
        if decision == "needs_rewrite":
            follow_up = _ensure_follow_up_intent(
                run_dir,
                intent_id,
                {
                    "requested_by": "projection",
                    "type": "run_gm_turn",
                    "payload": {
                        "reason": "projection_needs_rewrite",
                        "actor_id": actor_id,
                        "source_call_id": source_call_id,
                        "projection_feedback": projection_result["feedback"],
                    },
                    "policy": {"source_intent_id": intent_id},
                },
            )
            return _complete_projection_without_actor(
                run_dir,
                intent_id,
                source_message_id,
                source_call_id,
                follow_up,
                projection_result,
            )
        if decision == "blocked":
            raise agent_actor_runtime.AgentActorProjectionError(
                "projection_agent_blocked",
                {"feedback": projection_result["feedback"], "source_call_id": source_call_id},
            )
        source_payload["final_actor_message"] = projection_result["final_actor_message"]
        source_payload["projection"] = projection_result
        source_message["payload"] = source_payload
```

Add helpers near other dispatcher helpers:

```python
def _projection_objective_context(run_dir: Path) -> dict[str, Any]:
    input_payload = agent_run.read_json(Path(run_dir) / "input.json", {}) or {}
    return {
        "gm_only_hidden_settings": input_payload.get("gm_only_hidden_settings", []),
        "world_state_delta": input_payload.get("world_state_delta", []),
        "visible_events": input_payload.get("visible_events", []),
    }


def _write_projection_artifact(
    run_dir: Path,
    intent_id: str,
    review_packet: dict[str, Any],
    projection_result: dict[str, Any],
) -> None:
    write_artifact(
        run_dir,
        f"projections/{intent_id}.json",
        {
            "review_packet": review_packet,
            "projection_result": projection_result,
        },
    )


def _complete_projection_without_actor(
    run_dir: Path,
    intent_id: str,
    source_message_id: str,
    source_call_id: str,
    follow_up: dict[str, Any],
    projection_result: dict[str, Any],
) -> dict[str, Any]:
    follow_up_id = str(follow_up.get("id") or "")
    created_intents = [follow_up_id] if follow_up.get("created") else []
    outputs = {
        "executor": "request_projection",
        "intent_type": "request_projection",
        "decision": projection_result["decision"],
        "source_message_id": source_message_id,
        "source_call_id": source_call_id,
        "follow_up_intent_id": follow_up_id,
        "created_messages": [],
        "created_intents": created_intents,
    }
    complete_failure = _request_projection_transition_failure(
        run_dir,
        intent_id,
        "request_projection_complete_failed",
        _call_intent_transition(
            agent_intents.complete_intent,
            run_dir,
            intent_id,
            outputs=outputs,
        ),
        outputs=outputs,
        created_intents=created_intents,
        created_messages=[],
    )
    if complete_failure is not None:
        return complete_failure
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="request_projection",
        reason="",
        detail=outputs,
        created_intents=created_intents,
        created_messages=[],
        artifacts=[f"artifacts/projections/{intent_id}.json"],
    )
```

- [ ] **Step 6: Run dispatcher projection tests**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher.AgentDispatcherTest.test_request_projection_dispatches_projection_agent_and_uses_edited_message tests.test_agent_dispatcher.AgentDispatcherTest.test_request_projection_needs_rewrite_creates_gm_follow_up_without_actor_dispatch -v
```

Expected: PASS.

- [ ] **Step 7: Run existing dispatcher projection tests**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher -v
```

Expected: PASS. If existing tests that omit `run_claude` fail, update those tests to pass `run_claude=lambda *_args: ""` and stub `_dispatch_agent_payload` for `projection` with a `pass` decision.

- [ ] **Step 8: Commit**

Run:

```powershell
git add skills/agent_actor_runtime.py skills/agent_dispatcher.py tests/test_agent_dispatcher.py
git commit -m "feat: 接入 projection agent 调度"
```

Expected: commit succeeds.

### Task 5: Objective World State Archive

**Files:**
- Create: `skills/objective_world.py`
- Modify: `skills/agent_packets.py`
- Modify: `skills/agent_snapshots.py`
- Modify: `tests/test_agent_packets.py`
- Modify: `tests/test_agent_snapshots.py`

- [ ] **Step 1: Write objective world tests**

Create `tests/test_objective_world.py`:

```python
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ObjectiveWorldTest(unittest.TestCase):
    def setUp(self):
        self.objective_world = load_module("objective_world")
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name)
        (self.card / "memory").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_missing_objective_world_returns_stable_default(self):
        payload = self.objective_world.read_objective_world(self.card)
        self.assertEqual(payload, {"facts": [], "sources": []})

    def test_append_fact_persists_objective_world(self):
        self.objective_world.append_fact(
            self.card,
            scope="world.history",
            fact="The king framed the heroes after the Demon King fell.",
            source="gm",
        )

        payload = json.loads((self.card / "memory" / "objective_world.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["facts"][0]["scope"], "world.history")
        self.assertIn("framed the heroes", payload["facts"][0]["fact"])
        self.assertEqual(payload["facts"][0]["source"], "gm")
```

- [ ] **Step 2: Run objective world tests to verify they fail**

Run:

```powershell
python -m unittest tests.test_objective_world -v
```

Expected: FAIL with missing `objective_world.py`.

- [ ] **Step 3: Implement `skills/objective_world.py`**

Create:

```python
"""Save-level objective world knowledge helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


OBJECTIVE_WORLD_REL = Path("memory") / "objective_world.json"


def _path(card_folder: str | Path) -> Path:
    return Path(card_folder) / OBJECTIVE_WORLD_REL


def read_objective_world(card_folder: str | Path) -> dict[str, Any]:
    path = _path(card_folder)
    if not path.is_file():
        return {"facts": [], "sources": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {"facts": [], "sources": [{"type": "invalid_json", "path": str(OBJECTIVE_WORLD_REL)}]}
    if not isinstance(payload, dict):
        return {"facts": [], "sources": [{"type": "invalid_shape", "path": str(OBJECTIVE_WORLD_REL)}]}
    facts = payload.get("facts") if isinstance(payload.get("facts"), list) else []
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    return {"facts": facts, "sources": sources}


def write_objective_world(card_folder: str | Path, payload: dict[str, Any]) -> Path:
    path = _path(card_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def append_fact(card_folder: str | Path, *, scope: str, fact: str, source: str) -> dict[str, Any]:
    payload = read_objective_world(card_folder)
    item = {"scope": str(scope or "world"), "fact": str(fact or "").strip(), "source": str(source or "gm")}
    if item["fact"]:
        payload.setdefault("facts", []).append(item)
    write_objective_world(card_folder, payload)
    return payload
```

- [ ] **Step 4: Include objective world in GM packets**

In `skills/agent_packets.py`, import:

```python
import objective_world
```

In the GM packet builder where `input_json` is assembled, add:

```python
    input_json["objective_world"] = objective_world.read_objective_world(card_folder)
```

Do not add `objective_world` to player or character actor packets.

- [ ] **Step 5: Add packet test**

In `tests/test_agent_packets.py`, add a test near GM packet tests:

```python
    def test_gm_packet_includes_objective_world_but_actor_packets_do_not(self):
        (self.card / "memory").mkdir(exist_ok=True)
        (self.card / "memory" / "objective_world.json").write_text(
            '{"facts":[{"scope":"world.history","fact":"The king framed the heroes.","source":"gm"}],"sources":[]}',
            encoding="utf-8",
        )

        payload = self.agent_packets.build_round_input(self.card)
        self.assertIn("objective_world", payload)
        serialized_player = json.dumps(payload.get("player_context", {}), ensure_ascii=False)
        self.assertNotIn("The king framed the heroes", serialized_player)
```

Use the existing helper names in `tests/test_agent_packets.py`; if `build_round_input` is not the test helper used in that file, adapt this assertion to the existing round input builder while preserving the same expected behavior.

- [ ] **Step 6: Add explicit snapshot metadata coverage**

In `skills/agent_snapshots.py`, ensure the copied list includes `memory` when `memory/objective_world.json` exists. If `memory` is already copied as a directory, add metadata flag:

```python
        "objective_world_included": (card / "memory" / "objective_world.json").is_file(),
```

to snapshot metadata.

In `tests/test_agent_snapshots.py`, extend `test_create_snapshot_copies_card_state`:

```python
        (self.card / "memory" / "objective_world.json").write_text(
            '{"facts":[{"scope":"world","fact":"The king framed the heroes.","source":"gm"}],"sources":[]}',
            encoding="utf-8",
        )
```

and assert:

```python
        self.assertTrue((snapshot_dir / "memory" / "objective_world.json").exists())
        self.assertTrue(metadata["objective_world_included"])
```

- [ ] **Step 7: Run objective world and snapshot tests**

Run:

```powershell
python -m unittest tests.test_objective_world tests.test_agent_packets tests.test_agent_snapshots -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```powershell
git add skills/objective_world.py skills/agent_packets.py skills/agent_snapshots.py tests/test_objective_world.py tests/test_agent_packets.py tests/test_agent_snapshots.py
git commit -m "feat: 增加客观世界知识存档"
```

Expected: commit succeeds.

### Task 6: Projection Fixtures for Subjective Labels and False Beliefs

**Files:**
- Modify: `tests/test_projection_agent.py`
- Modify: `tests/test_agent_dispatcher.py`

- [ ] **Step 1: Add vampire label fixture**

Add to `tests/test_projection_agent.py`:

```python
    def test_fixture_bob_receives_black_robed_label(self):
        output = self.projection.validate_projection_output(
            {
                "decision": "edited",
                "target_actor_id": "character:Bob",
                "source_call_id": "call-bob-vampire",
                "final_actor_message": "You are hiding behind the tree. A black-robed figure stands at Alice's door.",
                "feedback": "Bob has no basis for the vampire label.",
            },
            actor_id="character:Bob",
            source_call_id="call-bob-vampire",
        )
        self.assertNotIn("vampire", output["final_actor_message"].lower())
        self.assertIn("black-robed figure", output["final_actor_message"])

    def test_fixture_alice_can_receive_vampire_label_from_private_memory(self):
        output = self.projection.validate_projection_output(
            {
                "decision": "pass",
                "target_actor_id": "character:Alice",
                "source_call_id": "call-alice-vampire",
                "final_actor_message": "You recognize the vampire you saw in childhood standing nearby.",
                "feedback": "",
            },
            actor_id="character:Alice",
            source_call_id="call-alice-vampire",
        )
        self.assertIn("vampire", output["final_actor_message"].lower())
```

- [ ] **Step 2: Add cursed hero fixture**

Add:

```python
    def test_fixture_paladin_keeps_cursed_hero_belief_without_false_label(self):
        output = self.projection.validate_projection_output(
            {
                "decision": "pass",
                "target_actor_id": "character:CurrentPaladin",
                "source_call_id": "call-paladin-hero",
                "final_actor_message": "You discover the cursed hero moving through the crowded market.",
                "feedback": "",
            },
            actor_id="character:CurrentPaladin",
            source_call_id="call-paladin-hero",
        )
        serialized = str(output)
        self.assertIn("cursed hero", output["final_actor_message"])
        self.assertNotIn("misconception", serialized.lower())
        self.assertNotIn("false", serialized.lower())
        self.assertNotIn("framed by the king", serialized.lower())
```

- [ ] **Step 3: Add dispatcher fixture for edited vampire message**

Extend the Task 4 dispatcher edited-message test or add a dedicated test asserting:

```python
        self.assertNotIn("vampire", projected["payload"]["packet"]["gm_prompt"].lower())
        self.assertIn("black-robed figure", projected["payload"]["packet"]["gm_prompt"])
```

- [ ] **Step 4: Run fixture tests**

Run:

```powershell
python -m unittest tests.test_projection_agent tests.test_agent_dispatcher -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add tests/test_projection_agent.py tests/test_agent_dispatcher.py
git commit -m "test: 覆盖主观标签 projection 场景"
```

Expected: commit succeeds.

### Task 7: Documentation and Prompt Contract Updates

**Files:**
- Modify: `README.md`
- Modify: `docs/重构建议.md`
- Modify: `.claude/skills/rp-context-projector.md`
- Modify: `.claude/skills/rp-gm-agent.md`
- Modify: `.claude/skills/rp-subgm-agent.md`
- Modify: `.claude/skills/rp-character-agent.md`
- Modify: `.claude/skills/rp-player-agent.md`
- Modify: `tests/test_gm_skill_contracts.py`

- [ ] **Step 1: Update context projector skill**

In `.claude/skills/rp-context-projector.md`, replace the `misconceptions` output requirement with:

```markdown
- `immersive_context`: first-person / second-person text only; do not expose runtime labels.
- `subjective_memory`: actor-facing memory and beliefs rendered as what the actor remembers or believes, not as truth labels.
```

Add:

```markdown
Never tell an actor that a belief is a misconception. Preserve false beliefs as in-world subjective memory.
```

- [ ] **Step 2: Update GM and subGM skills**

In `.claude/skills/rp-gm-agent.md` and `.claude/skills/rp-subgm-agent.md`, add:

```markdown
Actor requests should be written as immersive second-person natural language. Use objective world truth for simulation, but choose actor-facing labels from the target actor's memory, perception, training, and reports. If the target lacks a basis for a hidden label, use an appearance-level or belief-level label instead.
```

- [ ] **Step 3: Update actor skills**

In `.claude/skills/rp-character-agent.md` and `.claude/skills/rp-player-agent.md`, replace references to `misconceptions` with:

```markdown
Use only your rendered immersive context, your own memory and beliefs, your goals, relationships, body state, and senses.
```

- [ ] **Step 4: Update README and architecture doc**

In `README.md`, replace the deterministic-only projection paragraph with:

```markdown
Projection now has two layers: an LLM projection agent handles semantic actor-facing review and small local edits, while Python keeps a minimal deterministic protocol gate for actor id, source call id, ACL, artifact provenance, retry, and rollback invariants. Player and character agents receive immersive first-person / second-person context; internal fields such as hidden facts, projection feedback, and misconception labels are not actor-facing.
```

In `docs/重构建议.md`, update section `projection 的当前实现` to describe the same two-layer boundary.

- [ ] **Step 5: Update skill contract tests**

In `tests/test_gm_skill_contracts.py`, add assertions:

```python
    def test_projection_docs_forbid_actor_facing_misconception_labels(self):
        text = (ROOT / ".claude" / "skills" / "rp-context-projector.md").read_text(encoding="utf-8")
        self.assertIn("Never tell an actor that a belief is a misconception", text)
        self.assertIn("immersive_context", text)
```

- [ ] **Step 6: Run documentation contract tests**

Run:

```powershell
python -m unittest tests.test_gm_skill_contracts -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add README.md docs/重构建议.md .claude/skills/rp-context-projector.md .claude/skills/rp-gm-agent.md .claude/skills/rp-subgm-agent.md .claude/skills/rp-character-agent.md .claude/skills/rp-player-agent.md tests/test_gm_skill_contracts.py
git commit -m "docs: 更新沉浸式 projection 边界"
```

Expected: commit succeeds.

### Task 8: Final Verification

**Files:**
- No source changes unless verification exposes a defect.

- [ ] **Step 1: Run focused test set**

Run:

```powershell
python -m unittest tests.test_actor_context_renderer tests.test_projection_agent tests.test_agent_projection tests.test_agent_prompts tests.test_agent_dispatcher tests.test_objective_world tests.test_agent_packets tests.test_agent_snapshots tests.test_gm_skill_contracts -v
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: PASS.

- [ ] **Step 3: Run deterministic control-plane smoke**

Run:

```powershell
python skills/control_plane_smoke.py --repo .
```

Expected: command exits 0 and reports a successful deterministic control-plane smoke run.

- [ ] **Step 4: Run py_compile**

Run:

```powershell
python -m py_compile skills/actor_context_renderer.py skills/projection_agent.py skills/objective_world.py skills/agent_dispatcher.py skills/agent_actor_runtime.py skills/agent_projection.py skills/agent_prompts.py skills/rp_generate_cli.py skills/agent_packets.py skills/agent_snapshots.py
```

Expected: command exits 0.

- [ ] **Step 5: Inspect git state**

Run:

```powershell
git status --short
```

Expected: no unstaged or untracked implementation files except intentional docs or test artifacts already committed.

- [ ] **Step 6: Commit verification fixes if needed**

If verification required fixes, commit only the touched intentional files:

```powershell
git add <fixed-files>
git commit -m "fix: 完成沉浸式 projection 验证修复"
```

Expected: commit succeeds. If no fixes were needed, skip this step.

## Plan Self-Review

Spec coverage:

- Objective world authority is covered by Task 5.
- Subjective actor knowledge and removal of actor-facing `misconceptions` are covered by Tasks 1, 2, and 7.
- Immersive actor context is covered by Tasks 1 and 3.
- LLM projection agent is covered by Tasks 2, 3, 4, and 6.
- Minimal deterministic protocol gate is covered by Task 4.
- Error handling is covered by Tasks 2 and 4.
- Testing and documentation are covered by Tasks 6, 7, and 8.

Placeholder scan:

- No unfinished-marker or placeholder sections are intentionally present.

Type consistency:

- Projection decisions use `pass`, `edited`, `needs_rewrite`, and `blocked` throughout.
- Actor-facing text uses `final_actor_message` and `immersive_context`.
- Control-plane identity uses `target_actor_id`, `actor_id`, and `source_call_id` consistently by boundary.
