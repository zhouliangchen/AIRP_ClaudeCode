# Agent-Driven Capability Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build agent-driven capability routing so agents decide routing semantics while Python enforces declarative capability execution, then split low-risk dispatcher executors and add bounded retcon/replay capability.

**Architecture:** Input analysis emits `capability_requests[]`; Python validates request shape, looks up a declarative registry, and converts allowed capabilities into controlled intents, messages, deferred work, or audit artifacts. Existing dispatcher behavior remains externally compatible while input, actor, delivery, and capability work move into focused modules. Retcon/replay is implemented as explicit capability artifacts and single-round execution, never as free-form file editing.

**Tech Stack:** Python standard library, `unittest`, JSON artifacts, existing `.agent_runs/<round>/` message/intent runtime, existing Claude Code skill prompts.

---

## Scope Check

This plan intentionally combines two coupled changes: agent-driven routing and control-plane simplification. The first half produces working capability routing without changing the default RP chain. The second half moves low-risk dispatcher executors and adds replay planning/execution around the same capability registry.

The plan does not add a real image-generation worker. Existing assets work becomes a registered deferred capability first; external worker execution can be added after this plan without changing the request protocol.

## File Structure

- Create `skills/capability_registry.py`: declarative capability definitions, request normalization, registry lookup, authorization checks, and old routing-request compatibility mapping.
- Create `tests/test_capability_registry.py`: unit tests for request schema, old mapping, unknown capability, authorization gates, and registry actions.
- Modify `skills/input_analysis.py`: accept `capability_requests[]` while preserving `routing_requests[]` compatibility.
- Modify `skills/input_analysis_apply.py`: normalize legacy `routing_requests` into `capability_requests`, return both fields during migration.
- Modify `skills/agent_prompts.py`: update input analyst contract and prose to prefer `capability_requests[]`.
- Modify `.claude/skills/rp-input-analyst.md`: add `capability_requests` to the output shape and remove the old fixed route-type framing.
- Replace `skills/input_routing_requests.py` behavior with capability adapter internals while keeping `process_routing_requests()` as a compatibility function name for current callers.
- Modify `tests/test_input_analysis.py`, `tests/test_input_routing_requests.py`, `tests/test_agent_dispatcher.py`, and `tests/test_agent_prompts.py` for new protocol coverage.
- Create package `skills/agent_executors/` with `__init__.py`, `input_executor.py`, `actor_executor.py`, and `delivery_executor.py`.
- Modify `skills/agent_dispatcher.py`: delegate `analyze_input`, `request_projection`, `run_actor`, and `deliver_round` to focused modules while preserving public `dispatch_next()`.
- Create `skills/replay_capabilities.py`: validate replay plan artifacts and create bounded single-round replay intents.
- Create `tests/test_replay_capabilities.py`: replay plan and execution safety tests.
- Modify `skills/control_plane_smoke.py` and `tests/test_control_plane_smoke.py`: add capability evidence without altering the default delivery chain.
- Modify `README.md`, `CLAUDE.md`, `AGENTS.md`, and `docs/agent-driven-capability-routing-design.md`: document agent-driven capability routing and dispatcher split.

## Task 1: Capability Registry

**Files:**
- Create: `skills/capability_registry.py`
- Create: `tests/test_capability_registry.py`

- [ ] **Step 1: Write failing registry tests**

Create `tests/test_capability_registry.py`:

```python
import importlib.util
import sys
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


class CapabilityRegistryTest(unittest.TestCase):
    def setUp(self):
        self.registry = _load("capability_registry")

    def test_normalizes_current_capability_request(self):
        request = {
            "id": "cap-001",
            "requested_by": "input_analyst",
            "target": "assets-ui",
            "capability": "assets.generate_image",
            "summary": "Create a rainy street image.",
            "reason": "Player requested a visual update.",
            "source_channel": "user_instruction",
            "risk": "low",
            "authorization_gate": "none",
            "payload": {"kind": "scene", "target": "scene_illustration", "prompt": "rainy street"},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "make an image"},
        }

        normalized = self.registry.normalize_capability_request(request)

        self.assertEqual(normalized["id"], "cap-001")
        self.assertEqual(normalized["capability"], "assets.generate_image")
        self.assertEqual(normalized["target"], "assets-ui")
        self.assertEqual(normalized["action"], "intent")
        self.assertEqual(normalized["intent_type"], "assets_task")

    def test_maps_legacy_assets_route_to_capability_request(self):
        legacy = {
            "id": "route-001",
            "type": "assets_ui_task",
            "source_channel": "user_instruction",
            "summary": "Create a rainy street image.",
            "target": "assets-ui",
            "payload": {"prompt": "rainy street"},
            "requires_authorization": False,
            "authorization_gate": "none",
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "make an image"},
        }

        mapped = self.registry.legacy_routing_request_to_capability(legacy)

        self.assertEqual(mapped["id"], "route-001")
        self.assertEqual(mapped["requested_by"], "input_analyst")
        self.assertEqual(mapped["capability"], "assets.generate_image")
        self.assertEqual(mapped["authorization_gate"], "none")

    def test_unknown_capability_becomes_audit_action(self):
        request = {
            "id": "cap-unknown",
            "requested_by": "input_analyst",
            "target": "weather",
            "capability": "external.weather_lookup",
            "summary": "Look up weather.",
            "reason": "User asked for weather.",
            "source_channel": "user_instruction",
            "risk": "low",
            "authorization_gate": "none",
            "payload": {},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "weather"},
        }

        normalized = self.registry.normalize_capability_request(request)

        self.assertEqual(normalized["status"], "unsupported_capability")
        self.assertEqual(normalized["action"], "audit_only")
        self.assertEqual(normalized["capability"], "external.weather_lookup")

    def test_source_change_requires_authorization(self):
        request = {
            "id": "cap-source",
            "requested_by": "input_analyst",
            "target": "main-agent",
            "capability": "source.change_request",
            "summary": "Add save export.",
            "reason": "User explicitly requested source work.",
            "source_channel": "user_instruction",
            "risk": "high",
            "authorization_gate": "allowSourceCodeSelfRepair",
            "payload": {"feature": "save_export"},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "add export"},
        }

        blocked = self.registry.authorize_capability(
            self.registry.normalize_capability_request(request),
            runtime_settings={"allowSourceCodeSelfRepair": False},
        )
        allowed = self.registry.authorize_capability(
            self.registry.normalize_capability_request(request),
            runtime_settings={"allowSourceCodeSelfRepair": True},
        )

        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["status"], "authorization_required")
        self.assertTrue(allowed["allowed"])
        self.assertEqual(allowed["status"], "authorized")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run registry tests and verify failure**

Run:

```powershell
python -m unittest tests.test_capability_registry -v
```

Expected: fails with missing `skills/capability_registry.py`.

- [ ] **Step 3: Implement registry module**

Create `skills/capability_registry.py`:

```python
"""Declarative capability registry for agent-driven routing requests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class CapabilityRegistryError(ValueError):
    """Raised when a capability request is structurally invalid."""


VALID_RISKS = {"low", "medium", "high", "critical"}
VALID_AUTHORIZATION_GATES = {"none", "manual_confirmation", "allowSourceCodeSelfRepair"}
VALID_SOURCE_CHANNELS = {"user_instruction", "role_input", "raw_input"}

CAPABILITIES: dict[str, dict[str, Any]] = {
    "assets.generate_image": {
        "target": "assets-ui",
        "action": "intent",
        "intent_type": "assets_task",
        "allowed_requesters": {"input_analyst", "gm", "story", "critic", "main_agent"},
        "authorization_gate": "none",
        "max_risk": "medium",
    },
    "source.change_request": {
        "target": "main-agent",
        "action": "intent",
        "intent_type": "system_request",
        "allowed_requesters": {"input_analyst", "critic", "main_agent"},
        "authorization_gate": "allowSourceCodeSelfRepair",
        "max_risk": "critical",
    },
    "retcon.consult": {
        "target": "story",
        "action": "message",
        "message_targets": ["gm", "story"],
        "visibility": "story_facing",
        "allowed_requesters": {"input_analyst", "gm", "main_agent"},
        "authorization_gate": "none",
        "max_risk": "high",
    },
    "replay.plan": {
        "target": "replay",
        "action": "intent",
        "intent_type": "replay_plan",
        "allowed_requesters": {"input_analyst", "story", "gm", "main_agent"},
        "authorization_gate": "manual_confirmation",
        "max_risk": "critical",
    },
    "card.patch_data": {
        "target": "card-data",
        "action": "audit_only",
        "allowed_requesters": {"input_analyst", "gm", "main_agent"},
        "authorization_gate": "manual_confirmation",
        "max_risk": "high",
    },
}

LEGACY_TYPE_MAP = {
    "assets_ui_task": "assets.generate_image",
    "source_feature_request": "source.change_request",
    "story_retcon_consult": "retcon.consult",
    "card_data_edit": "card.patch_data",
}

RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def legacy_routing_request_to_capability(request: dict[str, Any]) -> dict[str, Any]:
    data = _require_dict(request, "routing_request")
    legacy_type = _require_nonempty_str(data, "type", "routing_request")
    capability = LEGACY_TYPE_MAP.get(legacy_type, legacy_type)
    return {
        "id": _require_nonempty_str(data, "id", "routing_request"),
        "requested_by": "input_analyst",
        "target": str(data.get("target") or ""),
        "capability": capability,
        "summary": _require_nonempty_str(data, "summary", "routing_request"),
        "reason": str(data.get("reason") or data.get("summary") or ""),
        "source_channel": _require_nonempty_str(data, "source_channel", "routing_request"),
        "risk": str(data.get("risk") or ("high" if capability == "source.change_request" else "medium")),
        "authorization_gate": str(data.get("authorization_gate") or "none"),
        "payload": _optional_dict(data, "payload", "routing_request"),
        "evidence": _optional_dict(data, "evidence", "routing_request"),
        "legacy_type": legacy_type,
    }


def normalize_capability_request(request: dict[str, Any]) -> dict[str, Any]:
    data = _require_dict(request, "capability_request")
    normalized = {
        "id": _require_nonempty_str(data, "id", "capability_request"),
        "requested_by": _require_nonempty_str(data, "requested_by", "capability_request"),
        "target": _require_nonempty_str(data, "target", "capability_request"),
        "capability": _require_nonempty_str(data, "capability", "capability_request"),
        "summary": _require_nonempty_str(data, "summary", "capability_request"),
        "reason": _require_nonempty_str(data, "reason", "capability_request"),
        "source_channel": _require_nonempty_str(data, "source_channel", "capability_request"),
        "risk": _require_nonempty_str(data, "risk", "capability_request"),
        "authorization_gate": _require_nonempty_str(data, "authorization_gate", "capability_request"),
        "payload": _optional_dict(data, "payload", "capability_request"),
        "evidence": _optional_dict(data, "evidence", "capability_request"),
    }
    if normalized["source_channel"] not in VALID_SOURCE_CHANNELS:
        raise CapabilityRegistryError("capability_request.source_channel is invalid")
    if normalized["risk"] not in VALID_RISKS:
        raise CapabilityRegistryError("capability_request.risk is invalid")
    if normalized["authorization_gate"] not in VALID_AUTHORIZATION_GATES:
        raise CapabilityRegistryError("capability_request.authorization_gate is invalid")
    evidence = normalized["evidence"]
    if not isinstance(evidence.get("raw_excerpt"), str) or not evidence.get("raw_excerpt", "").strip():
        raise CapabilityRegistryError("capability_request.evidence.raw_excerpt is required")

    definition = CAPABILITIES.get(normalized["capability"])
    if definition is None:
        normalized.update({
            "status": "unsupported_capability",
            "action": "audit_only",
            "registry": {},
        })
        return normalized

    requester = normalized["requested_by"]
    if requester not in definition["allowed_requesters"]:
        normalized.update({
            "status": "requester_not_allowed",
            "action": "audit_only",
            "registry": deepcopy(definition),
        })
        return normalized
    if RISK_ORDER[normalized["risk"]] > RISK_ORDER[definition["max_risk"]]:
        normalized.update({
            "status": "risk_exceeds_capability",
            "action": "audit_only",
            "registry": deepcopy(definition),
        })
        return normalized

    normalized["status"] = "recognized"
    normalized["action"] = definition["action"]
    normalized["registry"] = deepcopy(definition)
    if "intent_type" in definition:
        normalized["intent_type"] = definition["intent_type"]
    if "message_targets" in definition:
        normalized["message_targets"] = list(definition["message_targets"])
    if "visibility" in definition:
        normalized["visibility"] = definition["visibility"]
    return normalized


def authorize_capability(request: dict[str, Any], *, runtime_settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = runtime_settings if isinstance(runtime_settings, dict) else {}
    gate = request.get("authorization_gate")
    if gate == "none":
        return {"allowed": True, "status": "authorized", "authorization_gate": gate}
    if gate == "allowSourceCodeSelfRepair":
        allowed = settings.get("allowSourceCodeSelfRepair") is True
        return {
            "allowed": allowed,
            "status": "authorized" if allowed else "authorization_required",
            "authorization_gate": gate,
            "allowSourceCodeSelfRepair": allowed,
        }
    return {"allowed": False, "status": "authorization_required", "authorization_gate": gate}


def _require_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CapabilityRegistryError(f"{path} must be an object")
    return dict(value)


def _optional_dict(payload: dict[str, Any], key: str, path: str) -> dict[str, Any]:
    value = payload.get(key, {})
    if not isinstance(value, dict):
        raise CapabilityRegistryError(f"{path}.{key} must be an object")
    return dict(value)


def _require_nonempty_str(payload: dict[str, Any], key: str, path: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CapabilityRegistryError(f"{path}.{key} must be a non-empty string")
    return value.strip()
```

- [ ] **Step 4: Run registry tests**

Run:

```powershell
python -m unittest tests.test_capability_registry -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add skills/capability_registry.py tests/test_capability_registry.py
git commit -m "feat: 增加agent能力注册表"
```

## Task 2: Input Analysis Protocol Migration

**Files:**
- Modify: `skills/input_analysis.py`
- Modify: `skills/input_analysis_apply.py`
- Modify: `skills/agent_prompts.py`
- Modify: `.claude/skills/rp-input-analyst.md`
- Modify: `tests/test_input_analysis.py`
- Modify: `tests/test_agent_prompts.py`

- [ ] **Step 1: Add failing input-analysis tests**

Append to `tests/test_input_analysis.py` inside `InputAnalysisTest`:

```python
    def test_validate_accepts_capability_requests(self):
        data = self._analysis()
        data["routing_requests"] = []
        data["capability_requests"] = [
            {
                "id": "cap-001",
                "requested_by": "input_analyst",
                "target": "assets-ui",
                "capability": "assets.generate_image",
                "summary": "Create a pendant illustration.",
                "reason": "The instruction channel explicitly requested an image.",
                "source_channel": "user_instruction",
                "risk": "low",
                "authorization_gate": "none",
                "payload": {"prompt": "silver pendant"},
                "evidence": {"semantic_unit_ids": ["u2"], "raw_excerpt": self.instruction},
            }
        ]

        validated = self._validate(data)

        self.assertEqual(validated["capability_requests"][0]["capability"], "assets.generate_image")

    def test_validate_rejects_capability_request_without_evidence_excerpt(self):
        data = self._analysis()
        data["capability_requests"] = [
            {
                "id": "cap-bad",
                "requested_by": "input_analyst",
                "target": "assets-ui",
                "capability": "assets.generate_image",
                "summary": "Create image.",
                "reason": "User asked for image.",
                "source_channel": "user_instruction",
                "risk": "low",
                "authorization_gate": "none",
                "payload": {},
                "evidence": {"semantic_unit_ids": ["u2"], "raw_excerpt": ""},
            }
        ]

        with self.assertRaisesRegex(self.mod.InputAnalysisError, r"capability_requests\\[0\\]\\.evidence\\.raw_excerpt"):
            self._validate(data)
```

Append to `tests/test_agent_prompts.py`:

```python
    def test_input_analyst_prompt_prefers_capability_requests(self):
        prompt = self.agent_prompts._input_analyst_prompt({
            "round_id": "round-000001",
            "source_integrity": {
                "raw_text_sha256": "raw",
                "role_text_sha256": "role",
                "user_instruction_text_sha256": "instruction",
            },
        })

        self.assertIn('"capability_requests": []', prompt)
        self.assertIn("capability_requests[]", prompt)
        self.assertIn("assets.generate_image", prompt)
        self.assertNotIn("Allowed `routing_requests[].type` values", prompt)
```

- [ ] **Step 2: Run targeted tests and verify failure**

Run:

```powershell
python -m unittest tests.test_input_analysis.InputAnalysisTest.test_validate_accepts_capability_requests tests.test_input_analysis.InputAnalysisTest.test_validate_rejects_capability_request_without_evidence_excerpt tests.test_agent_prompts.AgentPromptsTest.test_input_analyst_prompt_prefers_capability_requests -v
```

Expected: fails because `capability_requests` is not validated and the prompt still uses fixed `routing_requests` wording.

- [ ] **Step 3: Update `input_analysis.py`**

Import the new registry near the top:

```python
import capability_registry
```

Add validation after `_validate_routing_requests(data.get("routing_requests"))`:

```python
    _validate_capability_requests(data.get("capability_requests", []))
```

Add this helper:

```python
def _validate_capability_requests(capability_requests):
    if capability_requests is None:
        return
    if not isinstance(capability_requests, list):
        raise InputAnalysisError("capability_requests must be a list")
    seen_ids = set()
    for index, request in enumerate(capability_requests):
        path = f"capability_requests[{index}]"
        try:
            normalized = capability_registry.normalize_capability_request(request)
        except capability_registry.CapabilityRegistryError as exc:
            message = str(exc).replace("capability_request.", f"{path}.")
            raise InputAnalysisError(message) from exc
        request_id = normalized["id"]
        if request_id in seen_ids:
            raise InputAnalysisError(f"{path}.id must be unique")
        seen_ids.add(request_id)
```

Update `build_fallback_analysis()` return payload to include:

```python
        "capability_requests": [],
```

- [ ] **Step 4: Update `input_analysis_apply.py` migration**

Change `_normalize_legacy_routing_requests()` so it also creates `capability_requests` when missing:

```python
def _normalize_legacy_routing_requests(analysis: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    normalized = dict(analysis)
    changed = False
    if "routing_requests" not in normalized:
        normalized["routing_requests"] = []
        changed = True
    if "capability_requests" not in normalized:
        normalized["capability_requests"] = [
            capability_registry.legacy_routing_request_to_capability(item)
            for item in normalized.get("routing_requests", [])
            if isinstance(item, dict)
        ]
        changed = True
    return normalized, changed
```

Add `import capability_registry` near the other imports.

In the return object from `apply_current_run()`, include:

```python
        "capability_requests": analysis.get("capability_requests", []),
```

- [ ] **Step 5: Update input analyst prompt and skill**

In `skills/agent_prompts.py`, update the input analyst contract to include:

```python
        "capability_requests": [],
```

Replace the old routing request notes with this text:

```python
        "\nCapability request contract: use top-level `capability_requests[]` for "
        "explicit user-requested system, UI, save-data, retcon, replay, source-feature, "
        "or cross-agent consultation work outside ordinary GM/story handling. "
        "Python does not decide routing semantics; you choose `target`, `capability`, "
        "`reason`, `risk`, and `payload`. Common capability names: "
        "`assets.generate_image`, `retcon.consult`, `replay.plan`, "
        "`card.patch_data`, `source.change_request`. "
        "`source.change_request` must use `authorization_gate: "
        "\"allowSourceCodeSelfRepair\"`; other capabilities usually use "
        "`authorization_gate: \"none\"` or `manual_confirmation` when they would alter saved data. "
        "Do not use capability requests for normal player actions.\n"
```

In `.claude/skills/rp-input-analyst.md`, add `"capability_requests": []` to the required JSON shape after `routing`, and add this paragraph before `## Output`:

```markdown
## Capability Requests

When the input asks for system work outside ordinary GM/story handling, emit `capability_requests[]`. You choose the target and capability by semantic judgment; Python only checks the capability registry and safety gates. Use capability names such as `assets.generate_image`, `retcon.consult`, `replay.plan`, `card.patch_data`, and `source.change_request`. Do not use capability requests for normal player actions.
```

- [ ] **Step 6: Run protocol tests**

Run:

```powershell
python -m unittest tests.test_input_analysis tests.test_agent_prompts -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add skills/input_analysis.py skills/input_analysis_apply.py skills/agent_prompts.py .claude/skills/rp-input-analyst.md tests/test_input_analysis.py tests/test_agent_prompts.py
git commit -m "feat: 支持agent能力请求协议"
```

## Task 3: Capability Adapter Replaces Fixed Routing Branches

**Files:**
- Modify: `skills/input_routing_requests.py`
- Modify: `tests/test_input_routing_requests.py`
- Modify: `tests/test_agent_dispatcher.py`

- [ ] **Step 1: Add failing adapter tests**

Append to `tests/test_input_routing_requests.py` inside `InputRoutingRequestsTest`:

```python
    def test_process_capability_request_creates_assets_intent_and_capability_audit(self):
        request = {
            "id": "cap-assets",
            "requested_by": "input_analyst",
            "target": "assets-ui",
            "capability": "assets.generate_image",
            "summary": "Create a rainy street image.",
            "reason": "User asked for a visual update.",
            "source_channel": "user_instruction",
            "risk": "low",
            "authorization_gate": "none",
            "payload": {"kind": "scene", "target": "scene_illustration", "prompt": "rainy street"},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "make an image"},
        }

        result = self.mod.process_capability_requests(
            self.run_dir,
            [request],
            runtime_settings={"selfRepairMode": "off", "allowSourceCodeSelfRepair": False},
            source_intent_id="intent_000001",
        )

        self.assertEqual(result["created_intents_count"], 1)
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual(pending[0]["type"], "assets_task")
        self.assertEqual(pending[0]["payload"]["prompt"], "rainy street")
        artifact = _read_json(Path(self.run_dir) / result["results"][0]["artifact"])
        self.assertEqual(artifact["capability"], "assets.generate_image")
        self.assertEqual(artifact["status"], "queued")

    def test_unknown_capability_writes_audit_and_message_without_intent(self):
        request = {
            "id": "cap-unknown",
            "requested_by": "input_analyst",
            "target": "weather",
            "capability": "external.weather_lookup",
            "summary": "Look up weather.",
            "reason": "User asked for weather.",
            "source_channel": "user_instruction",
            "risk": "low",
            "authorization_gate": "none",
            "payload": {},
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "weather"},
        }

        result = self.mod.process_capability_requests(
            self.run_dir,
            [request],
            runtime_settings={"allowSourceCodeSelfRepair": False},
            source_intent_id="intent_000001",
        )

        self.assertEqual(result["created_intents_count"], 0)
        self.assertEqual(self.intents.list_intents(self.run_dir, "pending"), [])
        messages = self.messages.read_messages(self.run_dir)
        self.assertEqual(messages[0]["type"], "unsupported_capability")
        artifact = _read_json(Path(self.run_dir) / result["results"][0]["artifact"])
        self.assertEqual(artifact["status"], "unsupported_capability")
```

- [ ] **Step 2: Run adapter tests and verify failure**

Run:

```powershell
python -m unittest tests.test_input_routing_requests.InputRoutingRequestsTest.test_process_capability_request_creates_assets_intent_and_capability_audit tests.test_input_routing_requests.InputRoutingRequestsTest.test_unknown_capability_writes_audit_and_message_without_intent -v
```

Expected: fails because `process_capability_requests()` is missing.

- [ ] **Step 3: Refactor `input_routing_requests.py` to call registry**

Add imports:

```python
import capability_registry
```

Add constants:

```python
CAPABILITY_ARTIFACT_DIR = "capability_requests"
```

Implement a new public function:

```python
def process_capability_requests(
    run_dir: str | Path,
    capability_requests: list[dict[str, Any]],
    *,
    runtime_settings: dict[str, Any] | None = None,
    source_intent_id: str = "",
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    settings = runtime_settings if isinstance(runtime_settings, dict) else {}
    created_intents: list[str] = []
    created_messages: list[str] = []
    artifacts: list[str] = []
    results: list[dict[str, Any]] = []

    for request in capability_requests or []:
        normalized = capability_registry.normalize_capability_request(request)
        context = {
            "run_dir": run_dir,
            "request": normalized,
            "request_id": normalized["id"],
            "safe_id": _safe_request_id(normalized["id"]),
            "runtime_settings": settings,
            "source_intent_id": source_intent_id,
        }
        result = _process_capability_request(context)
        artifacts.append(result["artifact"])
        created_intents.extend(result.get("created_intents", []))
        created_messages.extend(result.get("created_messages", []))
        results.append(result)

    return {
        "ok": True,
        "processed_count": len(capability_requests or []),
        "created_intents": created_intents,
        "created_messages": created_messages,
        "artifacts": artifacts,
        "created_intents_count": len(created_intents),
        "created_messages_count": len(created_messages),
        "results": results,
    }
```

Implement `_process_capability_request(context)` with these exact actions:

```python
def _process_capability_request(context: dict[str, Any]) -> dict[str, Any]:
    request = context["request"]
    authorization = capability_registry.authorize_capability(
        request,
        runtime_settings=context["runtime_settings"],
    )
    status = request.get("status", "recognized")
    created_intents: list[str] = []
    created_messages: list[str] = []

    if status == "unsupported_capability":
        message_id = _append_capability_message(context, ["main_agent"], "gm_only", "unsupported_capability")
        created_messages.append(message_id)
    elif not authorization.get("allowed"):
        status = "authorization_required"
        message_id = _append_capability_message(context, ["main_agent"], "gm_only", "authorization_required")
        created_messages.append(message_id)
    elif request["action"] == "intent":
        intent = _create_capability_intent(context, request)
        created_intents.append(intent["id"])
        status = "queued"
        message_id = _append_capability_message(context, _message_targets_for_request(request), "gm_only", "capability_request", intent_id=intent["id"])
        created_messages.append(message_id)
        _attach_source_message(context["run_dir"], intent["id"], message_id)
    elif request["action"] == "message":
        status = "deferred"
        message_id = _append_capability_message(
            context,
            request.get("message_targets", ["gm"]),
            request.get("visibility", "gm_only"),
            "capability_request",
        )
        created_messages.append(message_id)
    else:
        status = "audit_only"

    artifact_rel = f"artifacts/{CAPABILITY_ARTIFACT_DIR}/{context['safe_id']}.json"
    artifact = _capability_audit_artifact(context, status, authorization, created_intents, created_messages)
    agent_run.write_json(context["run_dir"] / artifact_rel, artifact)
    return {
        "request_id": request["id"],
        "capability": request["capability"],
        "status": status,
        "artifact": artifact_rel,
        "created_intents": created_intents,
        "created_messages": created_messages,
    }
```

Implement helpers `_create_capability_intent`, `_message_targets_for_request`, `_append_capability_message`, and `_capability_audit_artifact` by adapting the current `_create_intent`, `_append_routing_message`, and `_audit_artifact` helpers. The assets intent payload must preserve current fields:

```python
{
    "kind": str(payload.get("kind") or "scene"),
    "target": str(payload.get("target") or request["id"]),
    "prompt": str(payload.get("prompt") or request.get("summary") or ""),
    "source": f"input_analysis.capability_requests.{request['id']}",
    "capability_request": {"id": request["id"], "capability": request["capability"]},
}
```

For `source.change_request`, create a `system_request` intent with payload:

```python
{
    "reason": "user_requested_source_feature",
    "authorization_gate": "allowSourceCodeSelfRepair",
    "selfRepairMode_required": False,
    "source": "input_analysis.capability_requests",
    "capability_request_id": request["id"],
    "summary": request["summary"],
    "target": request["target"],
    "payload": request["payload"],
    "evidence": request["evidence"],
}
```

Keep `process_routing_requests()` as a compatibility wrapper:

```python
def process_routing_requests(...):
    capability_requests = [
        capability_registry.legacy_routing_request_to_capability(item)
        for item in routing_requests or []
        if isinstance(item, dict)
    ]
    return process_capability_requests(
        run_dir,
        capability_requests,
        runtime_settings=runtime_settings,
        source_intent_id=source_intent_id,
    )
```

- [ ] **Step 4: Update dispatcher analyze-input path**

In `skills/agent_dispatcher.py`, inside `_execute_analyze_input`, replace:

```python
        routing_result = input_routing_requests.process_routing_requests(
            run_dir,
            applied.get("routing_requests", []) if isinstance(applied.get("routing_requests"), list) else [],
            runtime_settings=runtime_settings,
            source_intent_id=intent_id,
        )
```

with:

```python
        capability_requests = applied.get("capability_requests")
        if not isinstance(capability_requests, list):
            capability_requests = []
        routing_result = input_routing_requests.process_capability_requests(
            run_dir,
            capability_requests,
            runtime_settings=runtime_settings,
            source_intent_id=intent_id,
        )
```

Keep the output key `routing_requests` for one migration cycle, and add:

```python
            "capability_requests": routing_result,
```

- [ ] **Step 5: Run routing and dispatcher tests**

Run:

```powershell
python -m unittest tests.test_capability_registry tests.test_input_routing_requests tests.test_agent_dispatcher -v
```

Expected: all tests pass, including old routing compatibility tests.

- [ ] **Step 6: Commit**

```powershell
git add skills/input_routing_requests.py skills/agent_dispatcher.py tests/test_input_routing_requests.py tests/test_agent_dispatcher.py
git commit -m "feat: 用能力请求替代固定路由分支"
```

## Task 4: Low-Risk Dispatcher Executor Split

**Files:**
- Create: `skills/agent_executors/__init__.py`
- Create: `skills/agent_executors/input_executor.py`
- Create: `skills/agent_executors/actor_executor.py`
- Create: `skills/agent_executors/delivery_executor.py`
- Modify: `skills/agent_dispatcher.py`
- Test: `tests/test_agent_dispatcher.py`

- [ ] **Step 1: Add a delegation smoke assertion**

Append to `tests/test_agent_dispatcher.py` inside `AgentDispatcherFoundationTest`:

```python
    def test_dispatcher_exposes_executor_modules(self):
        self.assertTrue(hasattr(self.dispatcher, "input_executor"))
        self.assertTrue(hasattr(self.dispatcher, "actor_executor"))
        self.assertTrue(hasattr(self.dispatcher, "delivery_executor"))
```

- [ ] **Step 2: Run targeted test and verify failure**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher.AgentDispatcherFoundationTest.test_dispatcher_exposes_executor_modules -v
```

Expected: fails because executor modules are not imported by `agent_dispatcher.py`.

- [ ] **Step 3: Create executor package**

Create `skills/agent_executors/__init__.py`:

```python
"""Focused intent executors for the agent dispatcher."""
```

Create `skills/agent_executors/input_executor.py`:

```python
"""Input-analysis intent executor."""

from __future__ import annotations


def execute(dispatcher_module, run_dir, card_folder, root_dir, intent):
    return dispatcher_module._execute_analyze_input_impl(run_dir, card_folder, root_dir, intent)
```

Create `skills/agent_executors/actor_executor.py`:

```python
"""Projection and actor intent executors."""

from __future__ import annotations


def execute_request_projection(dispatcher_module, run_dir, root_dir, intent, run_claude):
    return dispatcher_module._execute_request_projection_impl(run_dir, root_dir, intent, run_claude)


def execute_run_actor(dispatcher_module, run_dir, root_dir, intent, run_claude):
    return dispatcher_module._execute_run_actor_impl(run_dir, root_dir, intent, run_claude)
```

Create `skills/agent_executors/delivery_executor.py`:

```python
"""Delivery intent executor."""

from __future__ import annotations


def execute(dispatcher_module, run_dir, card_folder, root_dir, intent, run_command):
    return dispatcher_module._execute_deliver_round_impl(run_dir, card_folder, root_dir, intent, run_command)
```

- [ ] **Step 4: Wire dispatcher delegation without behavior changes**

In `skills/agent_dispatcher.py`, add imports:

```python
from agent_executors import actor_executor, delivery_executor, input_executor
```

Rename existing functions:

- `_execute_analyze_input` to `_execute_analyze_input_impl`
- `_execute_request_projection` to `_execute_request_projection_impl`
- `_execute_run_actor` to `_execute_run_actor_impl`
- `_execute_deliver_round` to `_execute_deliver_round_impl`

Add wrapper functions with the original names:

```python
def _execute_analyze_input(run_dir: Path, card_folder: Path, root_dir: Path, intent: dict[str, Any]) -> dict[str, Any]:
    return input_executor.execute(__import__(__name__), run_dir, card_folder, root_dir, intent)


def _execute_request_projection(run_dir: Path, root_dir: Path, intent: dict[str, Any], run_claude) -> dict[str, Any]:
    return actor_executor.execute_request_projection(__import__(__name__), run_dir, root_dir, intent, run_claude)


def _execute_run_actor(run_dir: Path, root_dir: Path, intent: dict[str, Any], run_claude) -> dict[str, Any]:
    return actor_executor.execute_run_actor(__import__(__name__), run_dir, root_dir, intent, run_claude)


def _execute_deliver_round(run_dir: Path, card_folder: Path, root_dir: Path, intent: dict[str, Any], run_command) -> dict[str, Any]:
    return delivery_executor.execute(__import__(__name__), run_dir, card_folder, root_dir, intent, run_command)
```

This first split only introduces executor boundaries. It does not move function bodies yet, which keeps the diff small and testable.

- [ ] **Step 5: Run dispatcher tests**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher -v
python skills/control_plane_smoke.py --repo .
```

Expected: dispatcher tests pass; smoke JSON contains `"ok": true`.

- [ ] **Step 6: Commit**

```powershell
git add skills/agent_executors skills/agent_dispatcher.py tests/test_agent_dispatcher.py
git commit -m "refactor: 拆出dispatcher执行器边界"
```

## Task 5: Replay Capability Planning

**Files:**
- Create: `skills/replay_capabilities.py`
- Create: `tests/test_replay_capabilities.py`
- Modify: `skills/capability_registry.py`

- [ ] **Step 1: Write failing replay tests**

Create `tests/test_replay_capabilities.py`:

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


class ReplayCapabilitiesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000003"
        self.run_dir.mkdir(parents=True)
        self.mod = _load("replay_capabilities")

    def tearDown(self):
        self.tmp.cleanup()

    def test_validate_replay_plan_requires_player_input_ids_and_snapshot(self):
        plan = {
            "schema_version": 1,
            "plan_id": "replay-001",
            "scope": "single_round",
            "snapshot_id": "round-000003-20260623T000000000000Z-abc123def456",
            "affected_rounds": ["round-000003"],
            "preserved_player_input_ids": ["input-3"],
            "discard_ai_artifacts": ["gm.output.json", "actor.outputs.json", "story.input.json"],
            "reason": "Player reframed the last answer as a dream.",
            "requested_by": "input_analyst",
            "source_capability_request_id": "cap-replay",
        }

        validated = self.mod.validate_replay_plan(plan)

        self.assertEqual(validated["scope"], "single_round")
        self.assertEqual(validated["snapshot_id"], plan["snapshot_id"])

    def test_validate_replay_plan_rejects_multi_round_execution_for_first_phase(self):
        plan = {
            "schema_version": 1,
            "plan_id": "replay-002",
            "scope": "multi_round",
            "snapshot_id": "round-000003-20260623T000000000000Z-abc123def456",
            "affected_rounds": ["round-000002", "round-000003"],
            "preserved_player_input_ids": ["input-2", "input-3"],
            "discard_ai_artifacts": ["gm.output.json"],
            "reason": "Long replay.",
            "requested_by": "input_analyst",
            "source_capability_request_id": "cap-replay",
        }

        with self.assertRaisesRegex(self.mod.ReplayCapabilityError, "multi_round replay is plan-only"):
            self.mod.validate_replay_plan(plan)

    def test_materialize_replay_plan_writes_artifact_inside_run(self):
        request = {
            "id": "cap-replay",
            "requested_by": "input_analyst",
            "payload": {
                "snapshot_id": "round-000003-20260623T000000000000Z-abc123def456",
                "affected_rounds": ["round-000003"],
                "preserved_player_input_ids": ["input-3"],
                "discard_ai_artifacts": ["gm.output.json", "actor.outputs.json", "story.input.json"],
            },
            "reason": "Player reframed the last answer as a dream.",
        }

        result = self.mod.materialize_replay_plan(self.run_dir, request)

        self.assertTrue(result["ok"])
        path = self.run_dir / result["artifact"]
        self.assertTrue(path.exists())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["source_capability_request_id"], "cap-replay")
```

- [ ] **Step 2: Run replay tests and verify failure**

Run:

```powershell
python -m unittest tests.test_replay_capabilities -v
```

Expected: fails because `skills/replay_capabilities.py` is missing.

- [ ] **Step 3: Implement replay plan validation**

Create `skills/replay_capabilities.py`:

```python
"""Bounded replay capability helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import agent_run


class ReplayCapabilityError(ValueError):
    """Raised when replay capability data is unsafe or incomplete."""


REPLAY_DISCARD_ALLOWLIST = {
    "gm.output.json",
    "actor.outputs.json",
    "interaction.trace.json",
    "story.input.json",
    "story.output.json",
    "critic.report.json",
    "postprocess.output.json",
}


def validate_replay_plan(payload: dict[str, Any]) -> dict[str, Any]:
    data = _require_dict(payload, "replay_plan")
    if data.get("schema_version") != 1:
        raise ReplayCapabilityError("replay_plan.schema_version must be 1")
    scope = _require_nonempty_str(data, "scope", "replay_plan")
    if scope == "multi_round":
        raise ReplayCapabilityError("multi_round replay is plan-only in this implementation phase")
    if scope != "single_round":
        raise ReplayCapabilityError("replay_plan.scope must be single_round")
    affected_rounds = _require_nonempty_str_list(data, "affected_rounds", "replay_plan")
    preserved_inputs = _require_nonempty_str_list(data, "preserved_player_input_ids", "replay_plan")
    discard = _require_nonempty_str_list(data, "discard_ai_artifacts", "replay_plan")
    for item in discard:
        if item not in REPLAY_DISCARD_ALLOWLIST:
            raise ReplayCapabilityError(f"replay_plan.discard_ai_artifacts contains unsupported artifact: {item}")
    return {
        "schema_version": 1,
        "plan_id": _require_nonempty_str(data, "plan_id", "replay_plan"),
        "scope": scope,
        "snapshot_id": _require_nonempty_str(data, "snapshot_id", "replay_plan"),
        "affected_rounds": affected_rounds,
        "preserved_player_input_ids": preserved_inputs,
        "discard_ai_artifacts": discard,
        "reason": _require_nonempty_str(data, "reason", "replay_plan"),
        "requested_by": _require_nonempty_str(data, "requested_by", "replay_plan"),
        "source_capability_request_id": _require_nonempty_str(data, "source_capability_request_id", "replay_plan"),
    }


def materialize_replay_plan(run_dir: str | Path, request: dict[str, Any]) -> dict[str, Any]:
    root = Path(run_dir)
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    request_id = str(request.get("id") or "capability-replay")
    plan = validate_replay_plan({
        "schema_version": 1,
        "plan_id": f"replay-{request_id}",
        "scope": str(payload.get("scope") or "single_round"),
        "snapshot_id": str(payload.get("snapshot_id") or ""),
        "affected_rounds": payload.get("affected_rounds", []),
        "preserved_player_input_ids": payload.get("preserved_player_input_ids", []),
        "discard_ai_artifacts": payload.get("discard_ai_artifacts", []),
        "reason": str(request.get("reason") or request.get("summary") or ""),
        "requested_by": str(request.get("requested_by") or ""),
        "source_capability_request_id": request_id,
    })
    artifact = f"artifacts/replay_plans/{plan['plan_id']}.json"
    agent_run.write_json(root / artifact, plan)
    return {"ok": True, "artifact": artifact, "plan": plan}


def _require_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReplayCapabilityError(f"{path} must be an object")
    return dict(value)


def _require_nonempty_str(payload: dict[str, Any], key: str, path: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReplayCapabilityError(f"{path}.{key} must be a non-empty string")
    return value.strip()


def _require_nonempty_str_list(payload: dict[str, Any], key: str, path: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise ReplayCapabilityError(f"{path}.{key} must be a non-empty list of strings")
    return [item.strip() for item in value]
```

- [ ] **Step 4: Verify replay capability definition**

In `skills/capability_registry.py`, confirm `CAPABILITIES` contains this exact `replay.plan` definition from Task 1:

```python
    "replay.plan": {
        "target": "replay",
        "action": "intent",
        "intent_type": "replay_plan",
        "allowed_requesters": {"input_analyst", "story", "gm", "main_agent"},
        "authorization_gate": "manual_confirmation",
        "max_risk": "critical",
    },
```

- [ ] **Step 5: Run replay tests**

Run:

```powershell
python -m unittest tests.test_replay_capabilities tests.test_capability_registry -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add skills/replay_capabilities.py tests/test_replay_capabilities.py skills/capability_registry.py
git commit -m "feat: 增加受控replay计划能力"
```

## Task 6: Replay Intent Adapter

**Files:**
- Modify: `skills/input_routing_requests.py`
- Modify: `skills/agent_dispatcher.py`
- Modify: `tests/test_input_routing_requests.py`
- Modify: `tests/test_agent_dispatcher.py`

- [ ] **Step 1: Add failing replay adapter test**

Append to `tests/test_input_routing_requests.py`:

```python
    def test_replay_plan_requires_manual_confirmation_and_does_not_create_intent_without_it(self):
        request = {
            "id": "cap-replay",
            "requested_by": "input_analyst",
            "target": "replay",
            "capability": "replay.plan",
            "summary": "Plan a replay from the previous round.",
            "reason": "Player reframed the previous answer as a dream.",
            "source_channel": "user_instruction",
            "risk": "high",
            "authorization_gate": "manual_confirmation",
            "payload": {
                "snapshot_id": "round-000001-20260623T000000000000Z-abc123def456",
                "affected_rounds": ["round-000001"],
                "preserved_player_input_ids": ["input-1"],
                "discard_ai_artifacts": ["gm.output.json", "story.input.json"],
            },
            "evidence": {"semantic_unit_ids": ["u1"], "raw_excerpt": "上一轮是梦"},
        }

        result = self.mod.process_capability_requests(
            self.run_dir,
            [request],
            runtime_settings={"allowSourceCodeSelfRepair": False},
            source_intent_id="intent_000001",
        )

        self.assertEqual(result["created_intents_count"], 0)
        artifact = _read_json(Path(self.run_dir) / result["results"][0]["artifact"])
        self.assertEqual(artifact["status"], "authorization_required")
        self.assertEqual(artifact["authorization"]["authorization_gate"], "manual_confirmation")
```

- [ ] **Step 2: Run targeted test and verify failure if manual gate is not handled**

Run:

```powershell
python -m unittest tests.test_input_routing_requests.InputRoutingRequestsTest.test_replay_plan_requires_manual_confirmation_and_does_not_create_intent_without_it -v
```

Expected: fails until `manual_confirmation` produces `authorization_required`.

- [ ] **Step 3: Keep manual confirmation non-executable in adapter**

In `capability_registry.authorize_capability()`, ensure this return for `manual_confirmation`:

```python
    if gate == "manual_confirmation":
        return {"allowed": False, "status": "authorization_required", "authorization_gate": gate}
```

No replay intent is created until a future explicit UI/main-agent confirmation path exists.

- [ ] **Step 4: Add dispatcher unsupported replay proof**

Append to `tests/test_agent_dispatcher.py`:

```python
    def test_replay_plan_intent_is_blocked_until_executor_is_wired(self):
        intent = self.intents.create_intent(
            self.run_dir,
            {
                "requested_by": "main_agent",
                "type": "replay_plan",
                "payload": {"reason": "manual test"},
            },
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unsupported_intent_type")
        blocked = self.intents.list_intents(self.run_dir, "blocked")
        self.assertEqual(blocked[0]["id"], intent["id"])
```

This test documents the first implementation phase: replay can be planned and audited, but no automatic replay executor runs without explicit follow-up work.

- [ ] **Step 5: Run tests**

Run:

```powershell
python -m unittest tests.test_input_routing_requests tests.test_agent_dispatcher tests.test_replay_capabilities -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add skills/capability_registry.py tests/test_input_routing_requests.py tests/test_agent_dispatcher.py
git commit -m "test: 固化replay能力授权边界"
```

## Task 7: Control-Plane Smoke Capability Evidence

**Files:**
- Modify: `skills/control_plane_smoke.py`
- Modify: `tests/test_control_plane_smoke.py`

- [ ] **Step 1: Add failing smoke assertions**

Append these assertions in `tests/test_control_plane_smoke.py` after the dispatcher assertions:

```python
        self.assertIn("capability_requests", payload)
        self.assertEqual(payload["capability_requests"]["unsupported_count"], 1)
        self.assertEqual(
            payload["capability_requests"]["artifacts"],
            ["artifacts/capability_requests/unknown-capability.json"],
        )
```

- [ ] **Step 2: Run smoke test and verify failure**

Run:

```powershell
python -m unittest tests.test_control_plane_smoke.ControlPlaneSmokeTest.test_control_plane_smoke_reports_delivery_evidence -v
```

Expected: fails because smoke payload lacks `capability_requests`.

- [ ] **Step 3: Add smoke capability fixture**

In `skills/control_plane_smoke.py`, import `input_routing_requests` if not already available in `run_smoke()`.

Before final payload assembly, add:

```python
        capability_result = input_routing_requests.process_capability_requests(
            run_dir,
            [
                {
                    "id": "unknown-capability",
                    "requested_by": "input_analyst",
                    "target": "weather",
                    "capability": "external.weather_lookup",
                    "summary": "Unsupported capability smoke fixture.",
                    "reason": "Prove unsupported capabilities are audited without breaking delivery.",
                    "source_channel": "user_instruction",
                    "risk": "low",
                    "authorization_gate": "none",
                    "payload": {},
                    "evidence": {"semantic_unit_ids": ["smoke"], "raw_excerpt": "weather"},
                }
            ],
            runtime_settings={"allowSourceCodeSelfRepair": False},
            source_intent_id="smoke-intent",
        )
```

In the returned payload add:

```python
            "capability_requests": {
                "unsupported_count": sum(1 for item in capability_result["results"] if item["status"] == "unsupported_capability"),
                "artifacts": capability_result["artifacts"],
            },
```

- [ ] **Step 4: Run smoke test and command**

Run:

```powershell
python -m unittest tests.test_control_plane_smoke -v
python skills/control_plane_smoke.py --repo .
```

Expected: tests pass; command JSON contains `"ok": true` and `"capability_requests"`.

- [ ] **Step 5: Commit**

```powershell
git add skills/control_plane_smoke.py tests/test_control_plane_smoke.py
git commit -m "test: smoke覆盖能力请求审计"
```

## Task 8: Documentation Sync

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`
- Modify: `docs/agent-driven-capability-routing-design.md`

- [ ] **Step 1: Update README routing language**

Replace the README paragraph that mentions `routing_requests[]` fixed request types with:

```markdown
当用户在指令通道主动请求系统能力、UI/图片更新、剧情回滚/重演协商、存档角色数据调整或源码功能实现时，input analyst 可以在 `input_analysis.output.json.capability_requests[]` 中写入 agent 自主判断后的能力请求。Python 不再通过固定关键词或固定 request type 判断路由语义，只根据 capability registry 校验能力是否存在、授权门是否满足、目标 agent/executor 是否可用，以及 artifact 路径、snapshot、projection、delivery gate 等安全不变量。源码能力 `source.change_request` 仍必须经过 `allowSourceCodeSelfRepair` 授权；需要存档改写或 replay 的能力在未确认前只写 audit/message，不直接改文件。
```

- [ ] **Step 2: Update CLAUDE and AGENTS architecture wording**

In `CLAUDE.md` and `AGENTS.md`, replace mentions that imply fixed routing request types with:

```markdown
Input analyst emits agent-driven `capability_requests[]` for system/UI/replay/card/source work outside ordinary GM/story handling. The runtime maps those requests through a declarative capability registry; Python enforces authorization, ACL, artifact, projection, snapshot, and delivery boundaries but does not decide the semantic routing strategy from player text.
```

Keep existing Chinese/English language style in each file.

- [ ] **Step 3: Update design doc implementation status**

Append to `docs/agent-driven-capability-routing-design.md`:

```markdown
## Implementation Notes

The first implementation phase introduces `capability_requests[]`, a declarative registry, compatibility mapping from legacy `routing_requests[]`, low-risk dispatcher executor boundaries, and audit-only/deferred handling for unsupported or unconfirmed capabilities. Multi-round replay remains plan-only until a separate confirmation and execution path is implemented.
```

- [ ] **Step 4: Run docs checks**

Run:

```powershell
git diff --check README.md CLAUDE.md AGENTS.md docs/agent-driven-capability-routing-design.md
```

Expected: no output.

- [ ] **Step 5: Commit docs**

```powershell
git add README.md CLAUDE.md AGENTS.md docs/agent-driven-capability-routing-design.md
git commit -m "docs: 同步agent能力路由架构"
```

## Task 9: Final Verification

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run targeted capability suite**

Run:

```powershell
python -m unittest tests.test_capability_registry tests.test_input_analysis tests.test_input_routing_requests tests.test_agent_dispatcher tests.test_replay_capabilities tests.test_agent_prompts tests.test_control_plane_smoke -v
```

Expected: all tests pass.

- [ ] **Step 2: Run full test suite**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 3: Run control-plane smoke**

Run:

```powershell
python skills/control_plane_smoke.py --repo .
```

Expected: JSON output contains `"ok": true`, `"manifest_stage": "delivered"`, `"run_postprocess"`, `"deliver_round"`, and `"capability_requests"`.

- [ ] **Step 4: Run compile checks**

Run:

```powershell
python -m py_compile skills/capability_registry.py skills/input_analysis.py skills/input_analysis_apply.py skills/input_routing_requests.py skills/agent_dispatcher.py skills/replay_capabilities.py skills/control_plane_smoke.py skills/agent_executors/__init__.py skills/agent_executors/input_executor.py skills/agent_executors/actor_executor.py skills/agent_executors/delivery_executor.py
```

Expected: no output and exit code 0.

- [ ] **Step 5: Check git status**

Run:

```powershell
git status --short --branch
```

Expected: branch is ahead by the new commits and the working tree is clean.

- [ ] **Step 6: Record final evidence**

In the final response, report:

- Full test command and pass result.
- Smoke command and `ok=true` evidence.
- Py compile command and pass result.
- Documentation files updated.
