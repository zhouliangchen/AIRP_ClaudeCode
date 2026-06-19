# Visibility Proof and Actor Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make actor packets visibility-proven: player and character agents must receive only events and GM call prompts that carry explicit proof that the actor can perceive, receive, or reasonably infer the information.

**Architecture:** Add a pure `skills/agent_visibility.py` helper for visibility-basis normalization and actor-specific proof checks. Wire it into GM schema validation, hidden-text sanitization, trace summaries, actor packet projection, generated dialogue transfers, and prompt documentation. Keep `agent_visibility_guard.py` as the final redaction layer, but move the primary allow/deny decision into projection-time proof rules.

**Tech Stack:** Python standard library, `unittest`, JSON artifacts under `.agent_runs/ROUND_ID/`, Claude Code skill Markdown, README.

---

## Scope

This plan implements P2 from `docs/superpowers/specs/2026-06-19-rp-control-plane-hardening-design.md`.

Included:

- Add structured visibility fields for scene beats, GM events, trace visible events, and actor-call context:
  - `scene_id`
  - `location`
  - `time_window`
  - `visible_to`
  - `sensory_channels`
  - `source_actor`
  - `target_actor`
  - `visibility_basis`
- Require every GM/subGM `actor_calls[]` item to include a compact `visibility_basis`.
- Project actor packets from visibility-proven events only.
- Keep hidden facts, GM-only notes, user-instruction facts, and world truth out of actor packets unless represented as a visible in-world disclosure event.
- Treat private dialogue as visible only to the speaker, target, and explicitly listed witnesses.
- Preserve actor-specific context buckets (`actor_visible_events`) as explicit proof for the addressed actor.
- Extend final hidden-text validation to cover `visibility_basis` and the structured visibility fields.
- Update GM-facing skill docs and README so live model outputs know the new contract.

Deferred:

- P3 perception-request closure and automatic re-call flow.
- P3 structured dialogue-transfer payloads beyond adding `visibility_basis` to generated transfer calls.
- Post-round memory jobs.
- subGM boundary proof beyond applying the same actor-call visibility-basis requirement to subGM `actor_calls`.

## Current Implementation Gaps

- `skills/agent_projection.py` treats most entries in `visible_events`, `world_visible_events`, and `public_events` as actor-visible unless they have private markers or mismatched `visible_to`. If no `visible_to` is present, `_event_visible_to_actor()` currently returns `True`.
- `skills/agent_interactions.py` stores trace visible events as `actor/type/content/target/source_call_id/causal_links` only, so scene/location/time/sensory visibility proof is lost before actor projection and story input.
- `skills/agent_schemas.py` ignores the P2 visibility fields and does not require `actor_calls[].visibility_basis`.
- `skills/agent_visibility_guard.py` redacts `content`, `metadata`, `prompt`, and `reason`, but not `visibility_basis`.
- `.claude/skills/rp-gm-visibility-policy.md` describes the hidden-fact rule, but not the structured proof fields or the fail-closed projection rule.

## Visibility Basis Contract

Use this normalized shape for every `visibility_basis`:

```json
{
  "mode": "direct",
  "summary": "Ada is in the classroom and can see the player close his hand around the pendant.",
  "scene_id": "classroom-current",
  "location": "classroom",
  "time_window": "current",
  "visible_to": ["character:Ada"],
  "sensory_channels": ["visual"],
  "source_actor": "gm",
  "target_actor": "character:Ada"
}
```

Allowed `mode` values:

- `direct`: the actor is directly addressed by GM or by an in-world message.
- `public`: explicitly public event or broadcast.
- `location`: actor is in the same location and has matching sensory access.
- `private_dialogue`: speaker, target, or explicit witness can receive the line.
- `self`: actor's own visible action/dialogue.
- `witness`: actor is explicitly listed as a witness.
- `inference`: actor can infer the situation from visible evidence described in `summary`.

Fail-closed rule:

- Missing or malformed proof is not actor-visible.
- `actor_visible_events[actor_id]` is proof for that addressed actor only.
- `visible_to` values `all`, `public`, `everyone`, and `world` are public markers.
- Private dialogue never becomes visible to unrelated actors because it came through a `world_visible` trace bucket.
- Hidden-marker keys/text and copied hidden source phrases are rejected or redacted before delivery.

## File Structure

- Create `skills/agent_visibility.py`: normalized visibility basis, actor-location/sensory extraction, and proof checks.
- Create `tests/test_agent_visibility.py`: pure unit tests for proof rules.
- Modify `skills/agent_projection.py`: delegate event filtering to `agent_visibility`, add `gm_visibility_basis` to actor packets, and remove broad `world_visible` inclusion.
- Modify `tests/test_agent_projection.py`: lock strict projection behavior and update existing generic visible-event fixtures with explicit proof.
- Modify `skills/agent_schemas.py`: validate/preserve visibility fields and require actor-call `visibility_basis`.
- Modify `skills/agent_visibility_guard.py`: sanitize `visibility_basis` and visibility metadata fields.
- Modify `skills/agent_interactions.py`: preserve structured visibility fields in trace events and story summaries.
- Modify `skills/agent_turn_loop.py`: pass actor-call `visibility_basis` into actor packets, preserve GM visibility fields in traces, and add visibility basis to generated dialogue-transfer calls.
- Modify `skills/agent_outputs.py`: reject hidden markers/hidden phrases in `visibility_basis` and new actor-facing visibility fields.
- Modify `skills/control_plane_smoke.py`: add visibility bases to deterministic GM/subGM actor calls and check captured actor packets.
- Modify `.claude/skills/rp-gm-visibility-policy.md` and `.claude/skills/rp-gm-actor-routing.md`: document the P2 contract.
- Modify `tests/test_gm_skill_contracts.py`: lock documentation requirements.
- Modify `README.md`: update the runtime architecture section to describe visibility-proven actor packets.

## Task 1: Pure Visibility-Proof Tests

**Files:**

- Create: `tests/test_agent_visibility.py`
- Read: `skills/agent_projection.py`
- Read: `docs/superpowers/specs/2026-06-19-rp-control-plane-hardening-design.md`

- [ ] **Step 1: Write failing tests for proof rules**

Create `tests/test_agent_visibility.py`:

```python
import importlib.util
import json
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


def basis(mode, summary="visible proof", **extra):
    payload = {"mode": mode, "summary": summary}
    payload.update(extra)
    return payload


class AgentVisibilityTest(unittest.TestCase):
    def setUp(self):
        self.visibility = load_module("agent_visibility")

    def test_local_visual_event_does_not_reach_actor_in_another_location(self):
        event = {
            "type": "scene",
            "content": "A red lamp flickers beside the archive door.",
            "location": "archive",
            "sensory_channels": ["visual"],
            "visibility_basis": basis(
                "location",
                location="archive",
                sensory_channels=["visual"],
            ),
        }
        actor = {"name": "Ada", "location": "courtyard"}

        self.assertFalse(
            self.visibility.event_visible_to_actor(event, "character:Ada", actor)
        )

    def test_local_visual_event_reaches_actor_in_same_location(self):
        event = {
            "type": "scene",
            "content": "A red lamp flickers beside the archive door.",
            "location": "archive",
            "sensory_channels": ["visual"],
            "visibility_basis": basis(
                "location",
                location="archive",
                sensory_channels=["visual"],
            ),
        }
        actor = {"name": "Ada", "location": "archive"}

        self.assertTrue(
            self.visibility.event_visible_to_actor(event, "character:Ada", actor)
        )

    def test_public_broadcast_reaches_configured_recipient(self):
        event = {
            "type": "announcement",
            "content": "The bell rings across the school.",
            "visible_to": ["character:Ada", "character:SuLi"],
            "sensory_channels": ["auditory"],
            "visibility_basis": basis(
                "public",
                visible_to=["character:Ada", "character:SuLi"],
                sensory_channels=["auditory"],
            ),
        }

        self.assertTrue(
            self.visibility.event_visible_to_actor(event, "character:Ada", {"location": "archive"})
        )
        self.assertFalse(
            self.visibility.event_visible_to_actor(event, "character:Eve", {"location": "archive"})
        )

    def test_public_all_marker_reaches_any_actor(self):
        event = {
            "type": "announcement",
            "content": "A siren sounds through every corridor.",
            "visible_to": ["all"],
            "visibility_basis": basis("public", visible_to=["all"]),
        }

        self.assertTrue(
            self.visibility.event_visible_to_actor(event, "character:Eve", {"location": "courtyard"})
        )

    def test_private_dialogue_reaches_speaker_target_and_explicit_witness_only(self):
        event = {
            "type": "dialogue",
            "content": "Stay close.",
            "source_actor": "character:Ada",
            "target_actor": "player",
            "visible_to": ["character:SuLi"],
            "visibility_basis": basis(
                "private_dialogue",
                source_actor="character:Ada",
                target_actor="player",
                visible_to=["character:SuLi"],
            ),
        }

        self.assertTrue(self.visibility.event_visible_to_actor(event, "character:Ada", {}))
        self.assertTrue(self.visibility.event_visible_to_actor(event, "player", {}))
        self.assertTrue(self.visibility.event_visible_to_actor(event, "character:SuLi", {}))
        self.assertFalse(self.visibility.event_visible_to_actor(event, "character:Eve", {}))

    def test_unproven_world_visible_event_fails_closed(self):
        event = {
            "actor": "gm",
            "type": "scene",
            "content": "The archive door opens.",
        }

        self.assertFalse(self.visibility.event_visible_to_actor(event, "character:Ada", {}))

    def test_actor_specific_bucket_is_visible_only_to_that_actor(self):
        event = {
            "actor": "gm",
            "type": "sound",
            "content": "You hear a hinge creak beside you.",
        }

        self.assertTrue(
            self.visibility.event_visible_to_actor(
                event,
                "character:Ada",
                {},
                source_bucket_actor_id="character:Ada",
            )
        )
        self.assertFalse(
            self.visibility.event_visible_to_actor(
                event,
                "character:SuLi",
                {},
                source_bucket_actor_id="character:Ada",
            )
        )

    def test_hidden_markers_in_basis_make_event_invisible(self):
        event = {
            "type": "scene",
            "content": "The lamp flickers.",
            "visible_to": ["all"],
            "visibility_basis": {
                "mode": "public",
                "summary": "world_truth says the lamp is fake",
            },
        }

        self.assertFalse(self.visibility.event_visible_to_actor(event, "player", {}))

    def test_normalized_basis_is_json_safe_and_compact(self):
        normalized = self.visibility.normalize_visibility_basis({
            "mode": "location",
            "summary": "Ada can hear the bell.",
            "scene_id": 99,
            "location": "archive",
            "time_window": "current",
            "visible_to": ["character:Ada", 7],
            "sensory_channels": ["auditory", "visual"],
            "source_actor": "gm",
            "target_actor": "character:Ada",
            "extra": {"ignored": True},
        })

        self.assertEqual(
            normalized,
            {
                "mode": "location",
                "summary": "Ada can hear the bell.",
                "scene_id": "99",
                "location": "archive",
                "time_window": "current",
                "visible_to": ["character:Ada", "7"],
                "sensory_channels": ["auditory", "visual"],
                "source_actor": "gm",
                "target_actor": "character:Ada",
            },
        )
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the new tests and confirm the expected failure**

Command:

```powershell
python -m unittest tests.test_agent_visibility -v
```

Expected result before implementation:

```text
ModuleNotFoundError: No module named 'agent_visibility'
```

## Task 2: Implement `agent_visibility.py`

**Files:**

- Create: `skills/agent_visibility.py`
- Read: `skills/agent_projection.py`
- Read: `skills/agent_visibility_guard.py`

- [ ] **Step 1: Add the visibility helper module**

Create `skills/agent_visibility.py` with these public functions:

```python
PUBLIC_MARKERS = {"all", "everyone", "public", "world", "world_visible"}
ALLOWED_BASIS_MODES = {
    "direct",
    "public",
    "location",
    "private_dialogue",
    "self",
    "witness",
    "inference",
}
VISIBILITY_FIELDS = (
    "scene_id",
    "location",
    "time_window",
    "visible_to",
    "sensory_channels",
    "source_actor",
    "target_actor",
    "visibility_basis",
)
DEFAULT_ACTOR_SENSORY_CHANNELS = {"visual", "auditory", "tactile", "olfactory", "taste"}
```

The module must export these names:

```python
__all__ = [
    "ALLOWED_BASIS_MODES",
    "PUBLIC_MARKERS",
    "VISIBILITY_FIELDS",
    "actor_call_basis",
    "actor_call_visible_to_actor",
    "event_visible_to_actor",
    "filter_visible_events",
    "normalize_visibility_basis",
    "visibility_fields_from_event",
]
```

Implement `normalize_visibility_basis(value, *, require_summary=False)` to return a compact dict containing only allowed P2 fields. Implement `visibility_fields_from_event(event)` to extract normalized P2 fields from scene beats, GM events, trace events, and actor calls. Implement `event_visible_to_actor(event, actor_id, actor_state, *, source_bucket_actor_id="")` and `filter_visible_events(events, actor_id, actor_state, *, source_bucket_actor_id="")` to apply the fail-closed proof rules. Implement `actor_call_basis(actor_call)` and `actor_call_visible_to_actor(actor_call, actor_id, actor_state)` as actor-call wrappers over the same proof rules.

Required implementation details:

- Use the same hidden-marker concepts as `agent_projection.py`:
  - `gm_only`
  - `world_truth`
  - `hidden_fact`
  - `hidden_facts`
  - `hidden_note`
  - `hidden_truth`
  - `user_instruction_channel`
  - `omniscient`
  - `out_of_character`
  - `private_memory`
  - `internal_state`
  - `internal_thoughts`
- Drop unknown basis keys rather than preserving arbitrary nested data.
- Convert scalar `scene_id`, `location`, `time_window`, `source_actor`, and `target_actor` to strings.
- Normalize `visible_to` and `sensory_channels` to string lists.
- Treat empty or hidden-marker `summary` as invalid when `require_summary=True`.
- Extract actor locations from:
  - `actor_state["location"]`
  - `actor_state["current_location"]`
  - `actor_state["scene_id"]`
  - `actor_state["self_knowledge"]["location"]`
  - `actor_state["metadata"]["location"]`
- Extract actor sensory channels from:
  - `actor_state["sensory_channels"]`
  - `actor_state["available_sensory_channels"]`
  - `actor_state["self_knowledge"]["sensory_channels"]`
  - default physical channels when no explicit actor channels exist.
- Allow `source_bucket_actor_id` as proof only when it equals the projected actor id or is a public marker.
- Fail closed when no proof branch matches.

- [ ] **Step 2: Verify the helper tests pass**

Command:

```powershell
python -m unittest tests.test_agent_visibility -v
```

Expected result:

```text
Ran 9 tests

OK
```

## Task 3: Projection Tests for Strict Actor Packets

**Files:**

- Modify: `tests/test_agent_projection.py`
- Read: `skills/agent_projection.py`

- [ ] **Step 1: Add strict projection tests**

Add helper functions near `_packet_json`:

```python
def _basis(mode, summary="visible proof", **extra):
    payload = {"mode": mode, "summary": summary}
    payload.update(extra)
    return payload


def _public_event(content, **extra):
    event = {
        "actor": "gm",
        "type": "scene",
        "content": content,
        "visible_to": ["all"],
        "visibility_basis": _basis("public", visible_to=["all"]),
    }
    event.update(extra)
    return event
```

Add tests:

```python
def test_projection_drops_unproven_world_visible_event(self):
    packet = self.agent_projection.project_actor_context(
        "character:Ada",
        {"visible_events": [{"actor": "gm", "type": "scene", "content": "The archive opens."}]},
        {"name": "Ada", "location": "courtyard"},
        "You wait outside.",
        {"mode": "direct", "summary": "Ada is addressed by GM.", "target_actor": "character:Ada"},
    )

    self.assertEqual(packet["visible_events"], [])


def test_projection_keeps_location_proven_event_for_same_location_only(self):
    world = {
        "visible_events": [
            {
                "actor": "gm",
                "type": "scene",
                "content": "A red lamp flickers in the archive.",
                "location": "archive",
                "sensory_channels": ["visual"],
                "visibility_basis": _basis(
                    "location",
                    location="archive",
                    sensory_channels=["visual"],
                ),
            }
        ]
    }

    ada = self.agent_projection.project_actor_context(
        "character:Ada",
        world,
        {"name": "Ada", "location": "archive"},
        "You see the lamp.",
        {"mode": "direct", "summary": "Ada is addressed by GM.", "target_actor": "character:Ada"},
    )
    eve = self.agent_projection.project_actor_context(
        "character:Eve",
        world,
        {"name": "Eve", "location": "courtyard"},
        "You stand elsewhere.",
        {"mode": "direct", "summary": "Eve is addressed by GM.", "target_actor": "character:Eve"},
    )

    self.assertEqual(len(ada["visible_events"]), 1)
    self.assertEqual(eve["visible_events"], [])


def test_projection_includes_actor_call_visibility_basis(self):
    basis = {
        "mode": "direct",
        "summary": "Ada is addressed because she sees the player's hand close.",
        "location": "classroom",
        "visible_to": ["character:Ada"],
        "target_actor": "character:Ada",
    }

    packet = self.agent_projection.project_actor_context(
        "character:Ada",
        {},
        {"name": "Ada"},
        "You see the player's hand close.",
        basis,
    )

    self.assertEqual(packet["gm_visibility_basis"], basis)
```

- [ ] **Step 2: Update existing projection fixtures to use explicit proof**

In `tests/test_agent_projection.py`, every event currently expected to remain visible from a broad `visible_events` bucket must get explicit proof.

Examples:

```python
{"actor": "gm", "type": "scene", "content": "The classroom is noisy."}
```

becomes:

```python
_public_event("The classroom is noisy.")
```

Actor-specific buckets may stay compact because `actor_visible_events["player"]` and `actor_visible_events["character:SuLi"]` are explicit addressed buckets. Keep the existing tests that non-dict actor-specific entries are dropped.

- [ ] **Step 3: Run projection tests and confirm failure**

Command:

```powershell
python -m unittest tests.test_agent_projection -v
```

Expected failure before implementation:

```text
TypeError: project_actor_context() takes 4 positional arguments but 5 were given
```

or assertions showing unproven `visible_events` still leak.

## Task 4: Implement Strict Actor Projection

**Files:**

- Modify: `skills/agent_projection.py`
- Read: `skills/agent_visibility.py`

- [ ] **Step 1: Import and delegate to `agent_visibility`**

Modify imports:

```python
import agent_visibility
```

Remove duplicated event proof constants/functions from `agent_projection.py` after replacing their callers:

- `PRIVATE_EVENT_TYPES`
- `PRIVATE_EVENT_VISIBILITIES`
- `PUBLIC_VISIBLE_MARKERS`
- `_normalize_marker_list`
- `_event_visible_to_actor`
- `_collect_events`

- [ ] **Step 2: Replace event collection**

Use the actor state in visibility checks:

```python
def _collect_events(value: Any, actor_id: str, actor: Dict[str, Any], *, source_bucket_actor_id: str = "") -> list[Any]:
    return [
        _json_safe(item)
        for item in agent_visibility.filter_visible_events(
            value,
            actor_id,
            actor,
            source_bucket_actor_id=source_bucket_actor_id,
        )
    ]
```

Update `_actor_specific_events()`:

```python
def _actor_specific_events(world: Dict[str, Any], actor_id: str, actor: Dict[str, Any]) -> list[Any]:
    actor_visible = _as_dict(world.get("actor_visible_events"))
    events = []
    checked_keys = []
    for key in (actor_id, _agent_type(actor_id), "all", "public"):
        if key not in checked_keys:
            checked_keys.append(key)
    for key in checked_keys:
        if key in actor_visible:
            events.extend(
                _collect_events(
                    actor_visible.get(key),
                    actor_id,
                    actor,
                    source_bucket_actor_id=key,
                )
            )
    return events
```

Update `_visible_events()`:

```python
def _visible_events(world: Dict[str, Any], actor_id: str, actor: Dict[str, Any]) -> list[Any]:
    events = []
    for key in ("visible_events", "world_visible_events", "public_events"):
        if key in world:
            events.extend(_collect_events(world.get(key), actor_id, actor))
    if "events" in world:
        events.extend(_collect_events(world.get("events"), actor_id, actor))
    events.extend(_actor_specific_events(world, actor_id, actor))
    return events
```

- [ ] **Step 3: Add `gm_visibility_basis` to actor packet contract**

Change the signature:

```python
def project_actor_context(
    actor_id: str,
    world_state: dict | None,
    actor_state: dict | None,
    gm_prompt: str,
    gm_visibility_basis: dict | None = None,
) -> dict:
```

Add to the returned packet:

```python
"gm_visibility_basis": _json_safe(
    agent_visibility.normalize_visibility_basis(gm_visibility_basis or {})
),
```

Update the missing-input default test to include:

```python
"gm_visibility_basis": {},
```

- [ ] **Step 4: Verify projection tests**

Command:

```powershell
python -m unittest tests.test_agent_projection tests.test_agent_visibility -v
```

Expected result:

```text
OK
```

## Task 5: Schema and Hidden-Visibility Validation Tests

**Files:**

- Modify: `tests/test_agent_schemas.py`
- Modify: `tests/test_agent_outputs.py`
- Modify: `tests/test_agent_visibility_guard.py`

- [ ] **Step 1: Add schema tests for visibility fields**

In `tests/test_agent_schemas.py`, add helper:

```python
def visibility_basis(actor_id="character:SuLi"):
    return {
        "mode": "direct",
        "summary": f"{actor_id} is present and can perceive the prompt.",
        "location": "classroom",
        "visible_to": [actor_id],
        "sensory_channels": ["visual"],
        "target_actor": actor_id,
    }
```

Update `test_validate_gm_output_uses_interactive_event_contract()` so the scene beat, event, and actor call include visibility fields:

```python
"scene_beats": [{
    "content": "The classroom clock clicks once.",
    "scene_id": "classroom-1",
    "location": "classroom",
    "time_window": "current",
    "visible_to": ["all"],
    "sensory_channels": ["auditory"],
    "source_actor": "gm",
    "visibility_basis": {
        "mode": "public",
        "summary": "Everyone in the classroom can hear the clock.",
        "location": "classroom",
        "visible_to": ["all"],
        "sensory_channels": ["auditory"],
    },
}],
```

Add tests:

```python
def test_validate_gm_output_requires_actor_call_visibility_basis(self):
    payload = {
        "agent": "gm",
        "scene_beats": [],
        "events": [],
        "actor_calls": [{
            "call_id": "call-1",
            "actor_id": "player",
            "prompt": "You look up.",
            "reason": "The player is present.",
        }],
        "parallel_groups": [],
        "world_state_delta": [],
        "decision_point": None,
        "stop_reason": "continue",
    }

    with self.assertRaisesRegex(self.agent_schemas.ValidationError, "visibility_basis"):
        self.agent_schemas.validate_gm_output(payload)


def test_validate_gm_output_rejects_hidden_marker_visibility_basis(self):
    payload = {
        "agent": "gm",
        "scene_beats": [],
        "events": [],
        "actor_calls": [{
            "call_id": "call-1",
            "actor_id": "player",
            "prompt": "You look up.",
            "reason": "The player is present.",
            "visibility_basis": {"mode": "direct", "summary": "world_truth says this matters"},
        }],
        "parallel_groups": [],
        "world_state_delta": [],
        "decision_point": None,
        "stop_reason": "continue",
    }

    with self.assertRaisesRegex(self.agent_schemas.ValidationError, "visibility_basis"):
        self.agent_schemas.validate_gm_output(payload)
```

- [ ] **Step 2: Add output-validation tests for hidden phrases in visibility basis**

In `tests/test_agent_outputs.py`, extend `test_build_story_input_rejects_gm_actor_call_hidden_markers_in_prompt_reason_and_metadata()` cases:

```python
("visibility_basis", "Visible prompt.", "Visible reason.", {}, {"mode": "direct", "summary": "gm_only signal"}),
```

Change the test tuple and actor call construction so each valid case includes:

```python
"visibility_basis": visibility_basis,
```

Add a copied-hidden-phrase case:

```python
def test_build_story_input_rejects_gm_actor_call_visibility_basis_copied_hidden_phrase(self):
    _write_json(
        self.run_dir / "input.json",
        {
            "raw_text": "I look at the pendant.",
            "routed_input": {
                "role_channel": "I look at the pendant.",
                "user_instruction_channel": "Hidden truth: the pendant burns identity.",
            },
            "hidden_facts": [{"fact": "The pendant burns identity."}],
        },
    )
    _write_json(
        self.run_dir / "gm.output.json",
        {
            "agent": "gm_loop",
            "outputs": [{
                "agent": "gm",
                "scene_beats": [],
                "events": [],
                "actor_calls": [{
                    "call_id": "call-player-1",
                    "actor_id": "player",
                    "prompt": "You feel heat from the pendant.",
                    "reason": "The player can feel the pendant.",
                    "visibility_basis": {
                        "mode": "direct",
                        "summary": "The pendant burns identity.",
                        "target_actor": "player",
                    },
                }],
                "parallel_groups": [],
                "world_state_delta": [],
                "decision_point": None,
                "stop_reason": "complete",
            }],
        },
    )

    with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "visibility_basis"):
        self.agent_outputs.build_story_input(self.run_dir)

    self.assertFalse((self.run_dir / "story.input.json").exists())
```

- [ ] **Step 3: Add guard sanitization tests**

In `tests/test_agent_visibility_guard.py`, add:

```python
def test_sanitize_gm_output_redacts_visibility_basis(self):
    sanitized = self.guard.sanitize_gm_output(
        {
            "agent": "gm",
            "scene_beats": [],
            "events": [],
            "actor_calls": [{
                "call_id": "call-player-1",
                "actor_id": "player",
                "prompt": "You feel heat.",
                "reason": "Visible touch.",
                "visibility_basis": {
                    "mode": "direct",
                    "summary": "The pendant burns identity.",
                    "target_actor": "player",
                },
            }],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "continue",
        },
        {"hidden_facts": [{"fact": "The pendant burns identity."}]},
    )

    self.assertEqual(
        sanitized["actor_calls"][0]["visibility_basis"]["summary"],
        "[redacted]",
    )
```

- [ ] **Step 4: Run targeted tests and confirm expected failures**

Command:

```powershell
python -m unittest tests.test_agent_schemas tests.test_agent_outputs tests.test_agent_visibility_guard -v
```

Expected failures before implementation:

- `visibility_basis is required`
- hidden markers not rejected in `visibility_basis`
- `sanitize_gm_output()` leaves hidden phrase in `visibility_basis`

## Task 6: Implement Schema, Guard, and Output Validation

**Files:**

- Modify: `skills/agent_schemas.py`
- Modify: `skills/agent_visibility_guard.py`
- Modify: `skills/agent_outputs.py`
- Read: `skills/agent_visibility.py`

- [ ] **Step 1: Add schema normalization for visibility fields**

In `skills/agent_schemas.py`, import:

```python
import agent_visibility
```

Add helper:

```python
def _normalize_visibility_fields(data: Dict[str, Any], path: str, *, require_basis: bool = False) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for field in ("scene_id", "location", "time_window", "source_actor", "target_actor"):
        if field in data:
            value = data[field]
            if not isinstance(value, str):
                value = str(value)
            normalized[field] = value
    for field in ("visible_to", "sensory_channels"):
        if field in data:
            normalized[field] = _normalize_list_items(
                _optional_list(data, field, path),
                _path(path, field),
                _normalize_nonempty_str_item,
            )
    if "visibility_basis" in data:
        basis = agent_visibility.normalize_visibility_basis(
            data["visibility_basis"],
            require_summary=require_basis,
        )
        if require_basis and not basis:
            raise ValidationError(f"{_path(path, 'visibility_basis')} must be a visibility proof object")
        normalized["visibility_basis"] = basis
    elif require_basis:
        raise ValidationError(f"{_path(path, 'visibility_basis')} is required")
    return normalized
```

Update `_normalize_gm_scene_beat()`:

```python
normalized.update(_normalize_visibility_fields(data, path, require_basis=False))
```

Update `_normalize_gm_event()`:

```python
normalized.update(_normalize_visibility_fields(data, path, require_basis=False))
```

Update `_normalize_gm_actor_call()` and `_normalize_subgm_actor_call()`:

```python
normalized.update(_normalize_visibility_fields(data, path, require_basis=True))
```

- [ ] **Step 2: Reject hidden markers in normalized visibility basis**

Use the existing `_reject_forbidden_keys()` inside `_normalize_visibility_fields()` after normalization:

```python
_reject_forbidden_keys(normalized, path)
```

This must reject `world_truth`, `gm_only`, `hidden_note`, and similar markers in `visibility_basis`, `visible_to`, `source_actor`, `target_actor`, and free-text `summary`.

- [ ] **Step 3: Extend final redaction in `agent_visibility_guard.py`**

In `sanitize_gm_output()`, include:

```python
for field in agent_visibility.VISIBILITY_FIELDS:
    _redact_optional_field(beat, field, phrases, redact_markers=True)
```

for scene beats and events, and:

```python
_redact_optional_field(call, "visibility_basis", phrases, redact_markers=True)
for field in ("scene_id", "location", "time_window", "visible_to", "sensory_channels", "source_actor", "target_actor"):
    _redact_optional_field(call, field, phrases, redact_markers=True)
```

for actor calls.

- [ ] **Step 4: Extend output hidden-leak validation**

In `skills/agent_outputs.py`, update `_validate_gm_output_visibility()`:

```python
for field in ("content", "metadata", "scene_id", "location", "time_window", "visible_to", "sensory_channels", "source_actor", "target_actor", "visibility_basis"):
    if field in beat:
        _reject_actor_facing_gm_value(beat[field], f"{context}.{field}", hidden_phrases)
```

Apply the same field list to GM events.

For actor calls, validate:

```python
for field in ("prompt", "reason", "metadata", "scene_id", "location", "time_window", "visible_to", "sensory_channels", "source_actor", "target_actor", "visibility_basis"):
    if field in call:
        _reject_actor_facing_gm_value(call[field], f"{context}.{field}", hidden_phrases)
```

- [ ] **Step 5: Update all GM/subGM actor-call fixtures**

Because `actor_calls[].visibility_basis` is now required, update every non-empty `actor_calls` fixture found by:

```powershell
rg -n '"actor_calls": \[' tests skills/control_plane_smoke.py
```

Use this minimal basis pattern for direct test prompts:

```python
{
    "call_id": "call-character-Ada-1",
    "actor_id": "character:Ada",
    "prompt": "Test prompt.",
    "reason": "Test reason.",
    "visibility_basis": {
        "mode": "direct",
        "summary": "character:Ada is directly addressed by this test GM prompt.",
        "target_actor": "character:Ada",
        "visible_to": ["character:Ada"],
    },
}
```

For other actors, copy the exact `actor_id` value into `summary`, `target_actor`, and `visible_to`. Use a more specific summary only when the test asserts hidden phrase rejection or location/sensory behavior.

- [ ] **Step 6: Verify schema/guard/output tests**

Command:

```powershell
python -m unittest tests.test_agent_schemas tests.test_agent_outputs tests.test_agent_visibility_guard -v
```

Expected result:

```text
OK
```

## Task 7: Trace Preservation and Turn-Loop Tests

**Files:**

- Modify: `tests/test_agent_interactions.py`
- Modify: `tests/test_agent_turn_loop.py`

- [ ] **Step 1: Add trace preservation tests**

In `tests/test_agent_interactions.py`, add:

```python
def test_summary_preserves_visibility_metadata_for_world_visible_events(self):
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        self.agent_interactions.init_trace(run_dir, participants=["gm", "character:Ada"])
        self.agent_interactions.append_event(
            run_dir,
            actor="gm",
            visibility="world_visible",
            event_type="scene",
            content="The bell rings.",
            target="character:Ada",
            visibility_metadata={
                "scene_id": "classroom-1",
                "location": "classroom",
                "time_window": "current",
                "visible_to": ["character:Ada"],
                "sensory_channels": ["auditory"],
                "source_actor": "gm",
                "target_actor": "character:Ada",
                "visibility_basis": {
                    "mode": "direct",
                    "summary": "Ada can hear the bell.",
                    "target_actor": "character:Ada",
                },
            },
        )

        summary = self.agent_interactions.summarize_for_story_input(run_dir)

    self.assertEqual(summary["visible_events"][0]["location"], "classroom")
    self.assertEqual(summary["visible_events"][0]["sensory_channels"], ["auditory"])
    self.assertEqual(
        summary["visible_events"][0]["visibility_basis"]["summary"],
        "Ada can hear the bell.",
    )
```

- [ ] **Step 2: Add turn-loop actor-packet basis test**

In `tests/test_agent_turn_loop.py`, add a focused test near existing actor packet tests:

```python
def test_actor_packet_receives_gm_visibility_basis(self):
    packets = []
    gm_output = {
        "agent": "gm",
        "scene_beats": [{
            "content": "Ada sees the player close his hand.",
            "location": "classroom",
            "visible_to": ["character:Ada"],
            "sensory_channels": ["visual"],
            "visibility_basis": {
                "mode": "location",
                "summary": "Ada is in the classroom and can see the player's hand.",
                "location": "classroom",
                "visible_to": ["character:Ada"],
                "sensory_channels": ["visual"],
            },
        }],
        "events": [],
        "actor_calls": [{
            "call_id": "call-character-Ada-1",
            "actor_id": "character:Ada",
            "prompt": "You see the player close his hand around something pink.",
            "reason": "Ada is in the classroom and can see the movement.",
            "visibility_basis": {
                "mode": "location",
                "summary": "Ada is in the classroom and can see the player's hand.",
                "location": "classroom",
                "visible_to": ["character:Ada"],
                "sensory_channels": ["visual"],
                "target_actor": "character:Ada",
            },
        }],
        "parallel_groups": [],
        "world_state_delta": [],
        "decision_point": None,
        "stop_reason": "complete",
    }

    def dispatch(agent_key, packet):
        if agent_key == "gm":
            return gm_output
        packets.append(packet)
        return {
            "agent": "character",
            "agent_id": "character:Ada",
            "character_name": "Ada",
            "events": [{"type": "wait_for_gm", "target": "", "content": "I stay alert."}],
            "stop_reason": "continue",
        }

    result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

    self.assertTrue(result["ok"])
    self.assertEqual(packets[0]["gm_visibility_basis"]["mode"], "location")
    self.assertEqual(packets[0]["gm_visibility_basis"]["target_actor"], "character:Ada")
```

- [ ] **Step 3: Add generated dialogue-transfer basis test**

Extend the existing dialogue transfer test or add a new focused test:

```python
self.assertEqual(
    generated_call["visibility_basis"]["mode"],
    "private_dialogue",
)
self.assertEqual(generated_call["visibility_basis"]["source_actor"], "character:Ada")
self.assertEqual(generated_call["visibility_basis"]["target_actor"], "character:SuLi")
```

Use the persisted `gm.output.json` or captured second actor packet to verify that generated transfer calls include the basis and that the target actor packet receives it.

- [ ] **Step 4: Run targeted tests and confirm failure**

Command:

```powershell
python -m unittest tests.test_agent_interactions tests.test_agent_turn_loop -v
```

Expected failures before implementation:

- `append_event()` does not accept `visibility_metadata`.
- Actor packets do not include `gm_visibility_basis`.
- Generated dialogue-transfer calls do not include `visibility_basis`.

## Task 8: Implement Trace and Turn-Loop Wiring

**Files:**

- Modify: `skills/agent_interactions.py`
- Modify: `skills/agent_turn_loop.py`
- Read: `skills/agent_visibility.py`

- [ ] **Step 1: Preserve structured visibility metadata in traces**

In `agent_interactions.append_event()`, add parameter:

```python
visibility_metadata: Dict[str, Any] | None = None,
```

Normalize and store only P2 fields:

```python
metadata = agent_visibility.visibility_fields_from_event(visibility_metadata or {})
event.update(metadata)
```

In `summarize_for_story_input()`, copy those fields into each visible event:

```python
for field in agent_visibility.VISIBILITY_FIELDS:
    if field in item:
        visible_item[field] = item[field]
```

Keep existing ID sanitization for `target`, `source_call_id`, `causal_links`, `parallel_groups`, and routing warnings.

- [ ] **Step 2: Pass GM visibility fields into traces**

In `_record_gm_output()` in `agent_turn_loop.py`, when appending scene beats and events:

```python
visibility_metadata=agent_visibility.visibility_fields_from_event(beat)
```

and:

```python
visibility_metadata=agent_visibility.visibility_fields_from_event(event)
```

Import `agent_visibility` at the top.

- [ ] **Step 3: Pass actor-call basis into actor packets**

Change `_actor_packet()` signature:

```python
def _actor_packet(
    input_payload: dict,
    world_state: dict,
    actor_id: str,
    prompt: str,
    hidden_phrases: Iterable[str],
    visibility_basis: dict | None = None,
) -> dict:
```

Call projection with:

```python
agent_projection.project_actor_context(
    actor_id,
    world_state,
    _actor_state(actor_id, input_payload),
    safe_prompt,
    agent_visibility.actor_call_basis({"visibility_basis": visibility_basis or {}}),
)
```

In `_dispatch_actor_call()`, pass:

```python
call.get("visibility_basis") if isinstance(call, dict) else {}
```

- [ ] **Step 4: Add visibility basis to generated dialogue transfer calls**

Update `_dialogue_transfer_call()`:

```python
"visibility_basis": {
    "mode": "private_dialogue",
    "summary": f"{target} receives direct dialogue from {actor_id}.",
    "source_actor": actor_id,
    "target_actor": target,
    "visible_to": [actor_id, target],
    "sensory_channels": ["auditory"],
},
```

Update `_record_dialogue_transfer()` to include matching metadata:

```python
visibility_metadata={
    "source_actor": actor_id,
    "target_actor": target,
    "visible_to": [actor_id, target],
    "sensory_channels": ["auditory"],
    "visibility_basis": {
        "mode": "private_dialogue",
        "summary": f"{target} receives direct dialogue from {actor_id}.",
        "source_actor": actor_id,
        "target_actor": target,
        "visible_to": [actor_id, target],
        "sensory_channels": ["auditory"],
    },
}
```

- [ ] **Step 5: Verify trace and turn-loop tests**

Command:

```powershell
python -m unittest tests.test_agent_interactions tests.test_agent_turn_loop -v
```

Expected result:

```text
OK
```

## Task 9: Prompt Docs, README, and Smoke Updates

**Files:**

- Modify: `.claude/skills/rp-gm-visibility-policy.md`
- Modify: `.claude/skills/rp-gm-actor-routing.md`
- Modify: `tests/test_gm_skill_contracts.py`
- Modify: `skills/control_plane_smoke.py`
- Modify: `README.md`

- [ ] **Step 1: Update GM visibility policy**

Add a section to `.claude/skills/rp-gm-visibility-policy.md`:

```markdown
## Structured Visibility Proof

Actor-facing GM output must carry compact proof for why the actor can receive the information.

For `scene_beats[]`, `events[]`, and `actor_calls[]`, use these fields when applicable: `scene_id`, `location`, `time_window`, `visible_to`, `sensory_channels`, `source_actor`, `target_actor`, and `visibility_basis`.

`actor_calls[].visibility_basis` is required. It must explain why the target actor can perceive, receive, or reasonably infer the second-person prompt. Keep the summary concrete and visible: "Ada is in the classroom and can see the player's hand close" is valid; "Ada is near the hidden truth" is not.

If visibility cannot be proven, keep the information GM-only. Do not route it to an actor call, visible event, perception answer, or dialogue transfer.
```

- [ ] **Step 2: Update actor routing policy**

Add to `.claude/skills/rp-gm-actor-routing.md` under `Participation Points`:

```markdown
Every `actor_calls[]` entry must include `visibility_basis`. The call is valid only when the target actor can perceive, receive, or infer the prompt from in-world evidence available to that actor. The scheduler may parallelize calls, but visibility proof is per call and is never inherited from the group.
```

- [ ] **Step 3: Lock docs with contract tests**

In `tests/test_gm_skill_contracts.py`, extend `test_visibility_policy_blocks_hidden_fact_hints_in_actor_calls()`:

```python
self.assertIn("actor_calls[].visibility_basis", text)
self.assertIn("scene_id", text)
self.assertIn("location", text)
self.assertIn("time_window", text)
self.assertIn("visible_to", text)
self.assertIn("sensory_channels", text)
self.assertIn("If visibility cannot be proven", text)
```

Extend `test_actor_routing_skill_defines_executable_parallel_groups()`:

```python
self.assertIn("visibility_basis", text)
self.assertIn("per call", text)
```

- [ ] **Step 4: Update deterministic smoke output**

In `skills/control_plane_smoke.py`, add `visibility_basis` to every non-empty `actor_calls` entry.

For the existing Ada call, change the prompt so it no longer intentionally relies on hidden fact wording. Keep hidden-redaction coverage in unit tests, not in the smoke happy path:

```python
"prompt": "You notice the player trying to hide the pendant and respond only from Ada's visible perception.",
"reason": "Ada can see the player's hand move toward the pendant.",
"visibility_basis": {
    "mode": "location",
    "summary": "Ada is nearby and can see the player move the pendant.",
    "location": "classroom",
    "visible_to": ["character:Ada"],
    "sensory_channels": ["visual"],
    "target_actor": "character:Ada",
},
```

Assert captured actor packets include the basis and do not include hidden text:

```python
assert captured["actor_packets"][0]["gm_visibility_basis"]["target_actor"] == "character:Ada"
assert "burns identity" not in json.dumps(captured["actor_packets"], ensure_ascii=False)
```

- [ ] **Step 5: Update README**

In the architecture/runtime section that currently describes actor projection, add:

```markdown
Actor packet projection is visibility-proof based. GM-visible world state may contain hidden facts, user instructions, and world truth, but player/character packets are assembled only from actor self state, actor memory/goals, the second-person GM prompt, the prompt's `visibility_basis`, and events whose recipient/location/sensory metadata proves that the actor can perceive or receive them. If an event cannot prove visibility, it remains GM-only.
```

- [ ] **Step 6: Run docs/smoke targeted tests**

Commands:

```powershell
python -m unittest tests.test_gm_skill_contracts -v
python skills/control_plane_smoke.py --repo .
```

Expected result:

```text
OK
SMOKE OK
```

Use the exact success string printed by the current smoke script if it differs; do not mark the task complete until the command exits with code 0.

## Task 10: Final Verification and Commit

**Files:**

- Read: `README.md`
- Read: `.claude/skills/rp-gm-visibility-policy.md`
- Read: `.claude/skills/rp-gm-actor-routing.md`
- Read: `docs/superpowers/specs/2026-06-19-rp-control-plane-hardening-design.md`

- [ ] **Step 1: Run focused tests**

Commands:

```powershell
python -m unittest tests.test_agent_visibility tests.test_agent_projection tests.test_agent_schemas tests.test_agent_visibility_guard tests.test_agent_interactions tests.test_agent_turn_loop tests.test_agent_outputs tests.test_gm_skill_contracts -v
```

Expected result:

```text
OK
```

- [ ] **Step 2: Run full unit suite**

Command:

```powershell
python -m unittest discover -s tests -v
```

Expected result:

```text
OK
```

- [ ] **Step 3: Run deterministic control-plane smoke**

Command:

```powershell
python skills/control_plane_smoke.py --repo .
```

Expected result:

```text
SMOKE OK
```

If the script uses a different success line, record the actual final success line in the implementation handoff.

- [ ] **Step 4: Compile touched Python files**

Command:

```powershell
python -m py_compile skills/agent_visibility.py skills/agent_projection.py skills/agent_schemas.py skills/agent_visibility_guard.py skills/agent_interactions.py skills/agent_turn_loop.py skills/agent_outputs.py skills/control_plane_smoke.py
```

Expected result:

No stdout or stderr; exit code 0.

- [ ] **Step 5: Inspect the diff for unintended changes**

Commands:

```powershell
git status --short
git diff -- README.md .claude/skills/rp-gm-visibility-policy.md .claude/skills/rp-gm-actor-routing.md
git diff --stat
```

Expected checks:

- No generated card folders, generated images, `.agent_runs/`, memory files, or local secrets are staged.
- `docs/superpowers/**` is not modified during implementation unless this plan file is intentionally tracked.
- README changes describe the new actor projection behavior only; no unrelated workflow rewrite.

- [ ] **Step 6: Commit implementation**

Commands:

```powershell
git add README.md .claude/skills/rp-gm-visibility-policy.md .claude/skills/rp-gm-actor-routing.md skills/agent_visibility.py skills/agent_projection.py skills/agent_schemas.py skills/agent_visibility_guard.py skills/agent_interactions.py skills/agent_turn_loop.py skills/agent_outputs.py skills/control_plane_smoke.py tests/test_agent_visibility.py tests/test_agent_projection.py tests/test_agent_schemas.py tests/test_agent_visibility_guard.py tests/test_agent_interactions.py tests/test_agent_turn_loop.py tests/test_agent_outputs.py tests/test_gm_skill_contracts.py
git commit -m "fix: 增加角色可见性证明投影"
```

Expected result:

Git prints a one-commit summary whose subject is `fix: 增加角色可见性证明投影`.

## Implementation Notes

- Do not weaken the existing hidden-phrase redaction tests. P2 adds a stricter allow-list before final redaction; it does not replace redaction.
- Do not expose `recent_chat`, `world_state_delta`, `gm_only_hidden_settings`, `hidden_facts`, `world_truth`, or `user_instruction_channel` to actor packets.
- Do not treat `world_visible` as actor-visible by itself. `world_visible` is a trace/story-facing classification; actor projection requires actor-specific proof.
- Keep `visibility_basis.summary` concise. It is actor-facing and must not encode GM-only reasoning.
- Use hard rejection for missing actor-call visibility proof. Use event dropping for unproven world events during projection.
- Preserve deterministic ordering of events and actor calls.

## Acceptance Checklist

- [ ] `tests/test_agent_visibility.py` proves location, public, private-dialogue, actor-specific, and hidden-marker visibility rules.
- [ ] `tests/test_agent_projection.py` proves unproven `visible_events` no longer leak into actor packets.
- [ ] `tests/test_agent_schemas.py` proves `actor_calls[].visibility_basis` is required and normalized.
- [ ] `tests/test_agent_outputs.py` proves hidden phrases/markers are rejected in `visibility_basis`.
- [ ] `tests/test_agent_interactions.py` proves trace summaries preserve visibility metadata.
- [ ] `tests/test_agent_turn_loop.py` proves actor packets receive `gm_visibility_basis`.
- [ ] `tests/test_gm_skill_contracts.py` proves prompt docs require structured visibility proof.
- [ ] `python -m unittest discover -s tests -v` passes.
- [ ] `python skills/control_plane_smoke.py --repo .` passes.
- [ ] `python -m py_compile skills/agent_visibility.py skills/agent_projection.py skills/agent_schemas.py skills/agent_visibility_guard.py skills/agent_interactions.py skills/agent_turn_loop.py skills/agent_outputs.py skills/control_plane_smoke.py` passes.
