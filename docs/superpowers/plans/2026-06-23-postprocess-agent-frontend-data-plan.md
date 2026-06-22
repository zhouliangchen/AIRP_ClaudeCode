# Postprocess Agent Frontend Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a postprocess agent after critic approval and before delivery so frontend non-prose data is generated, validated, delivered, and repaired through a dedicated contract.

**Architecture:** Introduce a `postprocess.output.json` schema/helper module, a dispatcher `run_postprocess` intent, and a postprocess prompt/skill. Delivery requires valid postprocess core data, while UI extension failures create async repair records and do not block text delivery.

**Tech Stack:** Python 3 standard library, existing `unittest` suite, current `skills/*.py` dispatcher/runtime modules, `.claude/skills/*.md` prompt contracts, `handler.py` / `content.js` / `state.js` frontend bridge.

---

## File Structure

Create:

- `skills/postprocess_outputs.py`  
  Owns postprocess schema normalization, validation, critical-action option checks, UI extension repair record creation, and extraction of handler-facing frontend payloads.

- `tests/test_postprocess_outputs.py`  
  Unit tests for postprocess core validation, UI extension repair behavior, critical-action option enforcement, and state/frontend payload extraction.

- `.claude/skills/rp-postprocess-agent.md`  
  Agent-facing contract for generating summary, options, current goal, and UI extension data without rewriting prose.

Modify:

- `skills/agent_dispatcher.py`  
  Add `run_postprocess` intent support and route critic pass to postprocess before delivery.

- `skills/agent_outputs.py`  
  Include postprocess artifacts in delivery preparation and provide deterministic critical-action evidence from `story.input.json` / `interaction.trace.json`.

- `skills/agent_schemas.py`  
  Validate postprocess output shape if central schema validation is the local pattern used by dispatcher.

- `skills/agent_prompts.py`  
  Generate postprocess prompt/context and remove story/critic ownership of summary/options.

- `skills/handler.py`  
  Consume postprocess output for `SUMMARY_TEXT`, `TURN_OPTIONS`, and `STATE.quest`; stop relying on story `<summary>` / `<options>` in the live delivery path.

- `skills/round_deliver.py`  
  Require postprocess core validity before mechanical delivery and pass the postprocess output to handler.

- `skills/round_prepare.py`  
  Surface pending postprocess repair queue items to current run context.

- `skills/control_plane_smoke.py`  
  Add deterministic postprocess fixture and require `run_postprocess` between `review_critic` and `deliver_round`.

- `.claude/skills/rp-story-agent.md`  
  Remove summary/options from story ownership.

- `.claude/skills/rp-critic-agent.md`  
  State that critic does not review or request summary/options/current-goal/UI data.

- `.claude/skills/rp-delivery.md`  
  Require postprocess core before delivery and describe UI extension repair behavior.

- `.claude/skills/rp-orchestrator.md`  
  Add postprocess after critic pass.

- `README.md`  
  Update runtime architecture and frontend data ownership.

Tests to modify:

- `tests/test_agent_dispatcher.py`
- `tests/test_agent_outputs.py`
- `tests/test_agent_prompts.py`
- `tests/test_agent_schemas.py`
- `tests/test_control_plane_smoke.py`
- `tests/test_rp_generate_cli.py`
- `tests/test_turn_state.py`

## Task 1: Add Postprocess Output Helper And Core Validation

**Files:**
- Create: `skills/postprocess_outputs.py`
- Create: `tests/test_postprocess_outputs.py`

- [ ] **Step 1: Write failing tests for valid core data**

Create `tests/test_postprocess_outputs.py`:

```python
import importlib
import json
import tempfile
import unittest
from pathlib import Path


class PostprocessOutputsTest(unittest.TestCase):
    def setUp(self):
        self.mod = importlib.import_module("postprocess_outputs")

    def test_validate_accepts_core_data(self):
        payload = {
            "schema_version": 1,
            "core": {
                "summary": "You found the archive door and must choose how to proceed.",
                "options": [
                    {"label": "Listen at the door", "source": "postprocess", "requires_confirmation": False}
                ],
                "current_goal": "Decide how to approach the archive door.",
                "state_patch": {"quest": "Decide how to approach the archive door."},
            },
            "ui_extensions": {"status_panels": {}, "custom_cards": {}, "asset_bindings": {}},
            "ui_extension_status": {"status": "ok", "issues": []},
            "repair_requests": [],
            "metadata": {"round_id": "round-000001", "source": "postprocess"},
        }

        result = self.mod.validate_postprocess_output(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(result["output"]["core"]["summary"], payload["core"]["summary"])
        self.assertEqual(result["output"]["core"]["options"][0]["label"], "Listen at the door")
        self.assertEqual(result["output"]["core"]["current_goal"], "Decide how to approach the archive door.")
```

- [ ] **Step 2: Run the test and verify import failure**

Run:

```powershell
python -m unittest tests.test_postprocess_outputs -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'postprocess_outputs'`.

- [ ] **Step 3: Implement minimal helper module**

Create `skills/postprocess_outputs.py`:

```python
"""Postprocess output validation and frontend data extraction."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any


class PostprocessOutputError(ValueError):
    """Raised when postprocess output cannot satisfy delivery requirements."""


ALLOWED_STATE_PATCH_KEYS = {"quest", "stage", "time", "location", "env", "actions"}
DEFAULT_UI_EXTENSIONS = {"status_panels": {}, "custom_cards": {}, "asset_bindings": {}}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _option_item(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        label = value.strip()
        if not label:
            return None
        return {"label": label, "source": "postprocess", "requires_confirmation": False}
    if not isinstance(value, dict):
        return None
    label = _clean_text(value.get("label"))
    if not label:
        return None
    return {
        "label": label,
        "source": _clean_text(value.get("source")) or "postprocess",
        "requires_confirmation": value.get("requires_confirmation") is True,
    }


def _normalize_options(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    options: list[dict[str, Any]] = []
    for item in value:
        normalized = _option_item(item)
        if normalized is not None:
            options.append(normalized)
    return options


def _normalize_state_patch(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, raw in value.items():
        if key not in ALLOWED_STATE_PATCH_KEYS:
            continue
        if key == "actions":
            result[key] = [str(item).strip() for item in raw if str(item).strip()] if isinstance(raw, list) else []
        else:
            result[key] = _clean_text(raw)
    return result


def _normalize_ui_extensions(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return dict(DEFAULT_UI_EXTENSIONS)
    result = dict(DEFAULT_UI_EXTENSIONS)
    for key in result:
        if isinstance(value.get(key), dict):
            result[key] = value[key]
    return result


def validate_postprocess_output(payload: Any, *, critical_action_evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "postprocess_output_not_object", "errors": ["output must be an object"]}
    core = payload.get("core") if isinstance(payload.get("core"), dict) else {}
    summary = _clean_text(core.get("summary"))
    current_goal = _clean_text(core.get("current_goal"))
    options = _normalize_options(core.get("options"))
    state_patch = _normalize_state_patch(core.get("state_patch"))
    errors: list[str] = []
    if not summary:
        errors.append("core.summary is required")
    if not current_goal:
        errors.append("core.current_goal is required")
    if not options:
        errors.append("core.options must contain at least one option")
    fixed_check = validate_critical_action_options(options, critical_action_evidence or [])
    if not fixed_check["ok"]:
        errors.extend(fixed_check["errors"])
    if errors:
        return {"ok": False, "reason": "postprocess_core_invalid", "errors": errors}
    output = {
        "schema_version": 1,
        "core": {
            "summary": summary,
            "options": options,
            "current_goal": current_goal,
            "state_patch": state_patch,
        },
        "ui_extensions": _normalize_ui_extensions(payload.get("ui_extensions")),
        "ui_extension_status": payload.get("ui_extension_status") if isinstance(payload.get("ui_extension_status"), dict) else {"status": "ok", "issues": []},
        "repair_requests": payload.get("repair_requests") if isinstance(payload.get("repair_requests"), list) else [],
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    }
    return {"ok": True, "output": output, "errors": []}


def option_matches_evidence(option: dict[str, Any], evidence: dict[str, Any]) -> bool:
    if option.get("source") == "player_agent_critical_action" and option.get("requires_confirmation") is True:
        return True
    label = _clean_text(option.get("label")).lower()
    required = _clean_text(evidence.get("required_label")).lower()
    return bool(required and required in label)


def validate_critical_action_options(options: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    for item in evidence:
        if not any(option_matches_evidence(option, item) for option in options):
            errors.append(f"missing fixed option for critical action: {item.get('id') or item.get('required_label')}")
    return {"ok": not errors, "errors": errors}
```

- [ ] **Step 4: Run the focused test**

Run:

```powershell
python -m unittest tests.test_postprocess_outputs -v
```

Expected: PASS for `test_validate_accepts_core_data`.

- [ ] **Step 5: Add failing tests for invalid core and state patch filtering**

Append to `tests/test_postprocess_outputs.py`:

```python
    def test_validate_rejects_missing_core_fields(self):
        result = self.mod.validate_postprocess_output({"schema_version": 1, "core": {"summary": "", "options": []}})

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "postprocess_core_invalid")
        self.assertIn("core.summary is required", result["errors"])
        self.assertIn("core.current_goal is required", result["errors"])
        self.assertIn("core.options must contain at least one option", result["errors"])

    def test_validate_filters_state_patch_to_frontend_safe_keys(self):
        payload = {
            "core": {
                "summary": "Summary.",
                "options": ["Wait"],
                "current_goal": "Stay alert.",
                "state_patch": {"quest": "Stay alert.", "world": "Do not change", "actions": ["Wait", ""]},
            }
        }

        result = self.mod.validate_postprocess_output(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(result["output"]["core"]["state_patch"], {"quest": "Stay alert.", "actions": ["Wait"]})
```

- [ ] **Step 6: Run tests and verify failure where code is incomplete**

Run:

```powershell
python -m unittest tests.test_postprocess_outputs -v
```

Expected: PASS if Task 1 Step 3 already covers the behavior; if it fails, update `postprocess_outputs.py` exactly according to the functions in Step 3.

- [ ] **Step 7: Commit**

Run:

```powershell
git add skills/postprocess_outputs.py tests/test_postprocess_outputs.py
git commit -m "feat: 增加 postprocess 输出校验"
```

Expected: commit succeeds.

## Task 2: Add Critical Player Action Evidence And UI Repair Records

**Files:**
- Modify: `skills/postprocess_outputs.py`
- Modify: `skills/agent_outputs.py`
- Modify: `tests/test_postprocess_outputs.py`
- Modify: `tests/test_agent_outputs.py`

- [ ] **Step 1: Add failing critical action option tests**

Append to `tests/test_postprocess_outputs.py`:

```python
    def test_validate_requires_fixed_option_for_critical_player_action(self):
        evidence = [{"id": "event-3", "required_label": "push open the sealed door"}]
        payload = {
            "core": {
                "summary": "Summary.",
                "options": [{"label": "Look around", "source": "postprocess", "requires_confirmation": False}],
                "current_goal": "Choose carefully.",
            }
        }

        result = self.mod.validate_postprocess_output(payload, critical_action_evidence=evidence)

        self.assertFalse(result["ok"])
        self.assertIn("missing fixed option for critical action: event-3", result["errors"])

    def test_validate_accepts_fixed_option_for_critical_player_action(self):
        evidence = [{"id": "event-3", "required_label": "push open the sealed door"}]
        payload = {
            "core": {
                "summary": "Summary.",
                "options": [
                    {
                        "label": "Confirm action: push open the sealed door",
                        "source": "player_agent_critical_action",
                        "requires_confirmation": True,
                    }
                ],
                "current_goal": "Choose carefully.",
            }
        }

        result = self.mod.validate_postprocess_output(payload, critical_action_evidence=evidence)

        self.assertTrue(result["ok"])
```

- [ ] **Step 2: Add failing agent_outputs evidence test**

In `tests/test_agent_outputs.py`, add:

```python
    def test_extracts_player_critical_action_evidence_from_story_input(self):
        story_input = {
            "interaction_trace": {
                "visible_events": [
                    {
                        "id": "event-3",
                        "actor": "player",
                        "type": "custom_action",
                        "content": "I push open the sealed door.",
                        "custom_action": {
                            "actor_id": "player",
                            "risk_level": "critical",
                            "visible_content": "I push open the sealed door.",
                            "requires_gm_resolution": True,
                        },
                    }
                ],
                "decision_point": {"reason": "Player must confirm.", "options": ["Confirm", "Wait"]},
            }
        }

        result = self.agent_outputs.extract_player_critical_action_evidence(story_input)

        self.assertEqual(
            result,
            [{"id": "event-3", "required_label": "I push open the sealed door.", "risk_level": "critical"}],
        )
```

- [ ] **Step 3: Implement deterministic evidence extraction**

Add to `skills/agent_outputs.py`:

```python
def extract_player_critical_action_evidence(story_input: dict[str, Any]) -> list[dict[str, Any]]:
    trace = story_input.get("interaction_trace") if isinstance(story_input, dict) else {}
    if not isinstance(trace, dict):
        return []
    events = trace.get("visible_events") if isinstance(trace.get("visible_events"), list) else []
    evidence: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        actor = str(event.get("actor") or "")
        custom = event.get("custom_action") if isinstance(event.get("custom_action"), dict) else {}
        risk = str(custom.get("risk_level") or "").lower()
        if actor != "player" and str(custom.get("actor_id") or "") != "player":
            continue
        if risk not in {"high", "critical"}:
            continue
        label = str(custom.get("visible_content") or event.get("content") or "").strip()
        if not label:
            continue
        evidence.append({
            "id": str(event.get("id") or f"event-{index}"),
            "required_label": label,
            "risk_level": risk,
        })
    return evidence
```

Ensure `Any` is imported from `typing` if the file does not already import it.

- [ ] **Step 4: Add UI extension repair record tests**

Append to `tests/test_postprocess_outputs.py`:

```python
    def test_record_ui_extension_repair_writes_run_artifact_and_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / ".agent_runs" / "round-000001"
            card_dir = Path(tmp)
            result = self.mod.record_ui_extension_repair(
                run_dir,
                card_dir,
                reason="missing relationship panel data",
                required_keys=["ui_extensions.status_panels.relationships"],
                source_artifacts=["artifacts/postprocess.output.json"],
            )

            self.assertTrue(result["ok"])
            artifact = Path(result["artifact"])
            self.assertTrue(artifact.exists())
            queue = card_dir / ".agent_runs" / "postprocess_repair_queue.jsonl"
            self.assertTrue(queue.exists())
            row = json.loads(queue.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["status"], "pending")
            self.assertEqual(row["scope"], "ui_extensions")
```

- [ ] **Step 5: Implement repair record writer**

Add to `skills/postprocess_outputs.py`:

```python
def record_ui_extension_repair(
    run_dir: str | Path,
    card_dir: str | Path,
    *,
    reason: str,
    required_keys: list[str],
    source_artifacts: list[str],
) -> dict[str, Any]:
    run_root = Path(run_dir)
    card_root = Path(card_dir)
    repair_id = f"postprocess-repair-{uuid.uuid4().hex}"
    record = {
        "schema_version": 1,
        "id": repair_id,
        "round_id": run_root.name,
        "status": "pending",
        "scope": "ui_extensions",
        "reason": _clean_text(reason) or "ui extension data failed validation",
        "required_keys": [str(item) for item in required_keys],
        "source_artifacts": [str(item) for item in source_artifacts],
        "attempts": 1,
    }
    artifact = run_root / "artifacts" / "postprocess_repairs" / f"{repair_id}.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    queue = card_root / ".agent_runs" / "postprocess_repair_queue.jsonl"
    queue.parent.mkdir(parents=True, exist_ok=True)
    with queue.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return {"ok": True, "repair": record, "artifact": str(artifact), "queue": str(queue)}
```

- [ ] **Step 6: Run targeted tests**

Run:

```powershell
python -m unittest tests.test_postprocess_outputs tests.test_agent_outputs -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add skills/postprocess_outputs.py skills/agent_outputs.py tests/test_postprocess_outputs.py tests/test_agent_outputs.py
git commit -m "feat: 记录 postprocess UI 修复证据"
```

Expected: commit succeeds.

## Task 3: Add Postprocess Prompt And Skill Contracts

**Files:**
- Create: `.claude/skills/rp-postprocess-agent.md`
- Modify: `.claude/skills/rp-story-agent.md`
- Modify: `.claude/skills/rp-critic-agent.md`
- Modify: `.claude/skills/rp-delivery.md`
- Modify: `.claude/skills/rp-orchestrator.md`
- Modify: `skills/agent_prompts.py`
- Modify: `tests/test_agent_prompts.py`
- Modify: `tests/test_gm_skill_contracts.py`

- [ ] **Step 1: Add failing prompt tests**

In `tests/test_agent_prompts.py`, add:

```python
    def test_postprocess_prompt_defines_frontend_data_contract(self):
        import agent_prompts

        prompt = agent_prompts.build_postprocess_prompt({
            "run_id": "round-000001",
            "postprocess_context": {
                "story_output_path": "artifacts/story.output.json",
                "critic_report_path": "artifacts/critic.report.json",
            },
        })

        self.assertIn("postprocess.output.json", prompt)
        self.assertIn("core.summary", prompt)
        self.assertIn("core.options", prompt)
        self.assertIn("core.current_goal", prompt)
        self.assertIn("Do not rewrite story prose", prompt)
        self.assertNotIn("progress.json", prompt)
```

- [ ] **Step 2: Add failing skill-contract tests**

In `tests/test_gm_skill_contracts.py`, add:

```python
    def test_postprocess_skill_owns_frontend_data_not_progress(self):
        text = (ROOT / ".claude" / "skills" / "rp-postprocess-agent.md").read_text(encoding="utf-8")

        self.assertIn("summary", text)
        self.assertIn("options", text)
        self.assertIn("current_goal", text)
        self.assertIn("ui_extensions", text)
        self.assertIn("Do not rewrite story prose", text)
        self.assertIn("Do not write progress.json", text)

    def test_story_and_critic_skills_do_not_own_postprocess_fields(self):
        story = (ROOT / ".claude" / "skills" / "rp-story-agent.md").read_text(encoding="utf-8")
        critic = (ROOT / ".claude" / "skills" / "rp-critic-agent.md").read_text(encoding="utf-8")

        self.assertIn("Do not emit <summary>", story)
        self.assertIn("Do not emit <options>", story)
        self.assertIn("frontend data is out of critic scope", critic)
```

- [ ] **Step 3: Create postprocess skill file**

Create `.claude/skills/rp-postprocess-agent.md`:

```markdown
---
name: rp-postprocess-agent
description: Use after critic pass to generate frontend data outside the main story prose.
---

# RP Postprocess Agent

You generate frontend support data after the critic has approved the story. Do not rewrite story prose. Do not review prose quality. Do not write progress.json.

## Inputs

Read the current run artifacts:

- `story.input.json`
- `story.output.json`
- `critic.report.json`
- `interaction.trace.json`
- `ui_manifest.json` if present
- generated asset metadata if present
- pending postprocess repair queue items if provided
- current `state.js` values if provided

## Output

Write `postprocess.output.json`:

```json
{
  "schema_version": 1,
  "core": {
    "summary": "",
    "options": [
      {
        "label": "",
        "source": "postprocess",
        "requires_confirmation": false
      }
    ],
    "current_goal": "",
    "state_patch": {
      "quest": ""
    }
  },
  "ui_extensions": {
    "status_panels": {},
    "custom_cards": {},
    "asset_bindings": {}
  },
  "ui_extension_status": {
    "status": "ok",
    "issues": []
  },
  "repair_requests": [],
  "metadata": {
    "source": "postprocess"
  }
}
```

## Rules

- `core.summary` is a concise player-visible recap of the delivered turn.
- `core.options` contains concrete next actions. If a player critical action is provided in Runtime Input, include it as a confirmation option with `source: "player_agent_critical_action"` and `requires_confirmation: true`.
- `core.current_goal` is the next immediate player-facing goal.
- `core.state_patch.quest` should mirror `core.current_goal` unless a card-specific UI requirement needs a shorter label.
- `ui_extensions` supports non-prose UI elements. If required data cannot be produced, write an issue in `ui_extension_status.issues`; delivery will create async repair records.
- Do not include hidden facts, prompt notes, user-instruction summaries, or GM-only reasoning in any visible field.
- Do not write `<content>`, `<summary>`, or `<options>` tags.
```

- [ ] **Step 4: Update story and critic skill boundaries**

In `.claude/skills/rp-story-agent.md`, replace the output tag list section so it states:

```markdown
- `<content>` for main prose.
- `<character_dialogues>` for independent source-backed subagent dialogue boxes.
- Do not emit `<summary>`; postprocess owns summary.
- Do not emit `<options>`; postprocess owns action options.
```

In `.claude/skills/rp-critic-agent.md`, add under the scope rules:

```markdown
Frontend data is out of critic scope. Do not review, request, generate, or repair summary, options, current_goal, state patches, status panels, or UI extension data. Critic reviews the story body and source-backed character dialogue only.
```

- [ ] **Step 5: Add postprocess prompt builder**

Add to `skills/agent_prompts.py`:

```python
def build_postprocess_prompt(run_summary: Dict[str, Any]) -> str:
    context = run_summary.get("postprocess_context", {}) if isinstance(run_summary, dict) else {}
    return (
        "You are the RP postprocess agent.\n"
        "Generate frontend data outside the main story prose.\n"
        "Do not rewrite story prose. Do not review prose quality. Do not write progress.json.\n\n"
        "Required output file: postprocess.output.json\n"
        "Required core fields: core.summary, core.options, core.current_goal, core.state_patch.\n"
        "Optional nonblocking fields: ui_extensions, ui_extension_status, repair_requests.\n"
        "If Runtime Input includes player critical action evidence, include a matching option with "
        "`source: \"player_agent_critical_action\"` and `requires_confirmation: true`.\n\n"
        "Runtime Input:\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )
```

Ensure `json` is imported if needed.

- [ ] **Step 6: Run prompt and contract tests**

Run:

```powershell
python -m unittest tests.test_agent_prompts tests.test_gm_skill_contracts -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add .claude/skills/rp-postprocess-agent.md .claude/skills/rp-story-agent.md .claude/skills/rp-critic-agent.md .claude/skills/rp-delivery.md .claude/skills/rp-orchestrator.md skills/agent_prompts.py tests/test_agent_prompts.py tests/test_gm_skill_contracts.py
git commit -m "feat: 增加 postprocess agent 提示契约"
```

Expected: commit succeeds.

## Task 4: Add Dispatcher `run_postprocess` Intent

**Files:**
- Modify: `skills/agent_dispatcher.py`
- Modify: `tests/test_agent_dispatcher.py`

- [ ] **Step 1: Add failing critic routing test**

In `tests/test_agent_dispatcher.py`, update or add:

```python
    def test_review_critic_pass_creates_run_postprocess_intent(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "story.output.json", {"content": "<content>Ok.</content>", "character_dialogues": []})

        def fake_dispatch(agent_key, _run_dir, _root, _run_claude, _extra_context):
            self.assertEqual(agent_key, "critic")
            return {"decision": "pass", "hard_failures": [], "soft_issues": []}

        self.dispatcher._dispatch_agent_payload = fake_dispatch
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "story", "type": "review_critic", "payload": {}},
        )["intent"]

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=self.fake_run_claude)

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent_type"], "review_critic")
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["run_postprocess"])
        self.assertEqual(pending[0]["requested_by"], "critic")
```

- [ ] **Step 2: Add failing postprocess executor test**

In `tests/test_agent_dispatcher.py`, add:

```python
    def test_run_postprocess_writes_output_and_creates_delivery_intent(self):
        self._install_dispatcher_dependencies()
        _write_json(self.run_dir / "artifacts" / "story.input.json", {"interaction_trace": {"visible_events": []}})
        _write_json(self.run_dir / "artifacts" / "story.output.json", {"content": "<content>Ok.</content>", "character_dialogues": []})
        _write_json(self.run_dir / "artifacts" / "critic.report.json", {"decision": "pass"})
        created = self.intents.create_intent(
            self.run_dir,
            {"requested_by": "critic", "type": "run_postprocess", "payload": {}},
        )["intent"]

        def fake_dispatch(agent_key, _run_dir, _root, _run_claude, extra_context):
            self.assertEqual(agent_key, "postprocess")
            self.assertIn("story_output", extra_context)
            return {
                "schema_version": 1,
                "core": {
                    "summary": "Summary.",
                    "options": ["Wait"],
                    "current_goal": "Stay alert.",
                    "state_patch": {"quest": "Stay alert."},
                },
                "ui_extensions": {},
            }

        self.dispatcher._dispatch_agent_payload = fake_dispatch

        result = self.dispatcher.dispatch_next(self.run_dir, self.card, ROOT, run_claude=self.fake_run_claude)

        self.assertTrue(result["ok"])
        self.assertEqual(result["intent_type"], "run_postprocess")
        self.assertTrue((self.run_dir / "artifacts" / "postprocess.output.json").exists())
        self.assertTrue((self.run_dir / "postprocess.output.json").exists())
        pending = self.intents.list_intents(self.run_dir, "pending")
        self.assertEqual([item["type"] for item in pending], ["deliver_round"])
```

- [ ] **Step 3: Implement dispatcher support**

In `skills/agent_dispatcher.py`:

Add import:

```python
import postprocess_outputs
```

Add supported intent:

```python
"run_postprocess",
```

Change critic pass routing from creating `deliver_round` to creating `run_postprocess`:

```python
follow_up = agent_intents.create_intent(
    run_dir,
    {
        "requested_by": "critic",
        "type": "run_postprocess",
        "payload": {"reason": "critic_passed"},
    },
)
```

Add executor branch:

```python
if intent_type == "run_postprocess":
    return _execute_run_postprocess(run_dir, card_folder, root_dir, intent, run_claude)
```

Add executor:

```python
def _execute_run_postprocess(
    run_dir: Path,
    card_folder: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_claude,
) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "run_postprocess"})
    story_input = read_artifact(run_dir, "story.input.json", default={})
    story_output = read_artifact(run_dir, "story.output.json", default={})
    critic_report = read_artifact(run_dir, "critic.report.json", default={})
    critical_evidence = agent_outputs.extract_player_critical_action_evidence(story_input if isinstance(story_input, dict) else {})
    context = {
        "story_input": story_input,
        "story_output": story_output,
        "critic_report": critic_report,
        "critical_action_evidence": critical_evidence,
        "pending_repairs": postprocess_outputs.read_pending_repairs(card_folder),
    }
    output = _dispatch_agent_payload("postprocess", run_dir, root_dir, run_claude, {"postprocess_context": context})
    validation = postprocess_outputs.validate_postprocess_output(output, critical_action_evidence=critical_evidence)
    if not validation.get("ok"):
        detail = {"reason": validation.get("reason"), "errors": validation.get("errors", [])}
        agent_intents.block_intent(run_dir, intent_id, reason="postprocess_core_invalid", outputs=detail)
        return _result(False, "blocked", intent_id=intent_id, intent_type="run_postprocess", reason="postprocess_core_invalid", detail=detail)
    postprocess = validation["output"]
    write_artifact(run_dir, "postprocess.output.json", postprocess)
    write_json(run_dir / "postprocess.output.json", postprocess)
    if postprocess_outputs.ui_extensions_need_repair(postprocess):
        postprocess_outputs.record_ui_extension_repair(
            run_dir,
            card_folder,
            reason="postprocess ui extension validation failed",
            required_keys=postprocess_outputs.ui_extension_required_keys(postprocess),
            source_artifacts=["artifacts/postprocess.output.json"],
        )
    follow_up = agent_intents.create_intent(
        run_dir,
        {"requested_by": "postprocess", "type": "deliver_round", "payload": {"reason": "postprocess_core_valid"}},
    )
    agent_intents.complete_intent(
        run_dir,
        intent_id,
        outputs={"executor": "run_postprocess", "created_intents": [follow_up["intent"]["id"]]},
    )
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="run_postprocess",
        reason="",
        created_intents=[follow_up["intent"]["id"]],
        created_messages=[],
        artifacts=["artifacts/postprocess.output.json", "postprocess.output.json"],
        detail={"critical_action_evidence": critical_evidence},
    )
```

If local helper names differ (`read_artifact`, `write_artifact`, `_result`, `write_json`), use the existing equivalents in `agent_dispatcher.py`.

- [ ] **Step 4: Add helper functions referenced by dispatcher**

Add to `skills/postprocess_outputs.py`:

```python
def read_pending_repairs(card_dir: str | Path) -> list[dict[str, Any]]:
    queue = Path(card_dir) / ".agent_runs" / "postprocess_repair_queue.jsonl"
    if not queue.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in queue.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("status") == "pending":
            items.append(row)
    return items


def ui_extensions_need_repair(postprocess: dict[str, Any]) -> bool:
    status = postprocess.get("ui_extension_status") if isinstance(postprocess, dict) else {}
    if not isinstance(status, dict):
        return False
    return str(status.get("status") or "ok") in {"failed", "partial", "needs_repair"}


def ui_extension_required_keys(postprocess: dict[str, Any]) -> list[str]:
    status = postprocess.get("ui_extension_status") if isinstance(postprocess, dict) else {}
    issues = status.get("issues") if isinstance(status, dict) and isinstance(status.get("issues"), list) else []
    keys: list[str] = []
    for issue in issues:
        if isinstance(issue, dict) and issue.get("key"):
            keys.append(str(issue["key"]))
        elif isinstance(issue, str):
            keys.append(issue)
    return keys
```

- [ ] **Step 5: Run dispatcher tests**

Run:

```powershell
python -m unittest tests.test_agent_dispatcher tests.test_postprocess_outputs -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add skills/agent_dispatcher.py skills/postprocess_outputs.py tests/test_agent_dispatcher.py tests/test_postprocess_outputs.py
git commit -m "feat: 接入 postprocess dispatcher 环节"
```

Expected: commit succeeds.

## Task 5: Require Postprocess In Delivery And Handler

**Files:**
- Modify: `skills/round_deliver.py`
- Modify: `skills/agent_outputs.py`
- Modify: `skills/handler.py`
- Modify: `tests/test_agent_outputs.py`
- Modify: `tests/test_rp_generate_cli.py`
- Modify: `tests/test_turn_state.py`

- [ ] **Step 1: Add failing delivery preparation tests**

In `tests/test_agent_outputs.py`, add:

```python
    def test_prepare_delivery_requires_postprocess_core(self):
        self._write_story_and_critic(story_content="<content>Approved.</content>", critic_decision="pass")

        result = self.agent_outputs.prepare_delivery(self.card_dir, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "postprocess_missing")

    def test_prepare_delivery_accepts_valid_postprocess_core(self):
        self._write_story_and_critic(story_content="<content>Approved.</content>", critic_decision="pass")
        _write_json(
            self.run_dir / "artifacts" / "postprocess.output.json",
            {
                "schema_version": 1,
                "core": {"summary": "Summary.", "options": ["Wait"], "current_goal": "Stay alert."},
            },
        )

        result = self.agent_outputs.prepare_delivery(self.card_dir, self.styles_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["postprocess"]["core"]["summary"], "Summary.")
```

- [ ] **Step 2: Implement delivery postprocess requirement**

In `skills/agent_outputs.py`, import:

```python
import postprocess_outputs
```

Inside `prepare_delivery`, after critic pass validation and before returning success:

```python
postprocess_path = run_dir / "artifacts" / "postprocess.output.json"
if not postprocess_path.exists():
    return {"ok": False, "action": "blocked", "reason": "postprocess_missing"}
postprocess_raw = read_json(postprocess_path, {})
story_input = read_json(run_dir / "artifacts" / "story.input.json", {}) or {}
critical_evidence = extract_player_critical_action_evidence(story_input if isinstance(story_input, dict) else {})
postprocess_result = postprocess_outputs.validate_postprocess_output(
    postprocess_raw,
    critical_action_evidence=critical_evidence,
)
if not postprocess_result.get("ok"):
    return {
        "ok": False,
        "action": "blocked",
        "reason": postprocess_result.get("reason") or "postprocess_core_invalid",
        "postprocess_errors": postprocess_result.get("errors", []),
    }
result["postprocess"] = postprocess_result["output"]
```

Use the file's existing return variable structure. If `prepare_delivery` returns directly, add the `postprocess` key to the final success payload.

- [ ] **Step 3: Add failing handler test for postprocess data consumption**

In `tests/test_turn_state.py`, add:

```python
    def test_handler_content_js_uses_postprocess_summary_options_and_quest(self):
        card = self.tmp / "card"
        card.mkdir()
        (card / "chat_log.json").write_text(
            json.dumps([{"index": 0, "ai": "<content>Approved.</content>", "summary": "Old summary"}], ensure_ascii=False),
            encoding="utf-8",
        )
        (card / "postprocess.output.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "core": {
                        "summary": "Post summary.",
                        "options": [{"label": "Post option", "source": "postprocess", "requires_confirmation": False}],
                        "current_goal": "Post goal.",
                        "state_patch": {"quest": "Post goal."},
                    },
                    "ui_extensions": {"status_panels": {"mood": {"value": "tense"}}},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.handler.write_content_js(card)
        content_js = (ROOT / "skills" / "styles" / "content.js").read_text(encoding="utf-8")

        self.assertIn("Post summary.", content_js)
        self.assertIn("Post option", content_js)
        self.assertIn("POSTPROCESS_UI", content_js)
```

- [ ] **Step 4: Implement handler consumption helpers**

In `skills/handler.py`, import:

```python
try:
    import postprocess_outputs
except Exception:
    postprocess_outputs = None
```

Add helper:

```python
def _load_postprocess_output(card_folder):
    path = Path(card_folder) / "postprocess.output.json"
    if not path.exists() or postprocess_outputs is None:
        return None
    data = _read_json_file(path, None)
    result = postprocess_outputs.validate_postprocess_output(data)
    return result["output"] if result.get("ok") else None
```

In `write_content_js`, after `latest_summary` and `options` are computed, prefer postprocess:

```python
    postprocess = _load_postprocess_output(card_folder)
    postprocess_ui = {}
    if isinstance(postprocess, dict):
        core = postprocess.get("core", {})
        latest_summary = core.get("summary") or latest_summary
        post_options = core.get("options") if isinstance(core.get("options"), list) else []
        if post_options:
            options = [str(item.get("label") or "").strip() for item in post_options if isinstance(item, dict) and str(item.get("label") or "").strip()]
        postprocess_ui = postprocess.get("ui_extensions") if isinstance(postprocess.get("ui_extensions"), dict) else {}
```

Add to generated JS:

```python
        "window.POSTPROCESS_UI = " + json.dumps(postprocess_ui, ensure_ascii=False) + ";\n"
```

- [ ] **Step 5: Add state quest application**

In `skills/handler.py`, add:

```python
def apply_postprocess_state_patch(card_folder, postprocess):
    if not isinstance(postprocess, dict):
        return
    core = postprocess.get("core") if isinstance(postprocess.get("core"), dict) else {}
    patch = core.get("state_patch") if isinstance(core.get("state_patch"), dict) else {}
    quest = str(patch.get("quest") or core.get("current_goal") or "").strip()
    if quest:
        update_state({"quest": quest}, card_folder=card_folder)
```

Call it from `append_turn` after the turn is written and before `write_content_js(card_folder)`:

```python
    postprocess = _load_postprocess_output(card_folder)
    apply_postprocess_state_patch(card_folder, postprocess)
```

If `update_state` is defined later in the file, move this helper below `update_state` or avoid forward reference by applying the patch near the existing state update logic.

- [ ] **Step 6: Run delivery and handler tests**

Run:

```powershell
python -m unittest tests.test_agent_outputs tests.test_rp_generate_cli tests.test_turn_state -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add skills/agent_outputs.py skills/round_deliver.py skills/handler.py tests/test_agent_outputs.py tests/test_rp_generate_cli.py tests/test_turn_state.py
git commit -m "feat: 交付前校验 postprocess 核心数据"
```

Expected: commit succeeds.

## Task 6: Remove Live Story Summary/Options Ownership

**Files:**
- Modify: `skills/response_parser.py`
- Modify: `skills/handler.py`
- Modify: `skills/agent_prompts.py`
- Modify: `tests/test_rp_generate_cli.py`
- Modify: `tests/test_turn_state.py`

- [ ] **Step 1: Add tests proving story tags are not required for live path**

In `tests/test_rp_generate_cli.py`, update story fixture content from:

```python
"<content>Clean story.</content><summary>Clean.</summary><options>Wait</options>"
```

to:

```python
"<content>Clean story.</content>"
```

For each updated fixture, add a corresponding `postprocess.output.json` fixture:

```python
_write_json(
    run_dir / "artifacts" / "postprocess.output.json",
    {
        "schema_version": 1,
        "core": {"summary": "Clean.", "options": ["Wait"], "current_goal": "Wait and observe."},
    },
)
```

- [ ] **Step 2: Keep parser backward compatibility only for old chat display**

Leave `response_parser.parse_response` able to parse `<summary>` and `<options>` because old `chat_log.json` entries may contain them. Do not use those tags as live delivery requirements.

- [ ] **Step 3: Remove story prompt summary/options requirements**

In `skills/agent_prompts.py`, update `_story_prompt` output guidance by replacing any mention of:

```text
<summary>
<options>
```

with:

```text
Postprocess owns summary and action options after critic pass. Story must not emit <summary> or <options>.
```

- [ ] **Step 4: Search live prompt/test references**

Run:

```powershell
rg -n "<summary>|<options>|summary/options|TURN_OPTIONS" skills tests .claude README.md -g "!skills/node_modules/**"
```

Expected remaining matches:

- `response_parser.py` parser compatibility;
- handler old-chat fallback tests;
- postprocess plan/spec/docs;
- postprocess prompt/handler consumption;
- no story prompt instruction requiring story to emit summary/options.

- [ ] **Step 5: Run targeted tests**

Run:

```powershell
python -m unittest tests.test_agent_prompts tests.test_rp_generate_cli tests.test_turn_state -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add skills/agent_prompts.py skills/handler.py tests/test_rp_generate_cli.py tests/test_turn_state.py .claude/skills/rp-story-agent.md
git commit -m "fix: 移除 story 对摘要和选项的职责"
```

Expected: commit succeeds.

## Task 7: Surface Pending Postprocess Repairs To The Next Round

**Files:**
- Modify: `skills/round_prepare.py`
- Modify: `skills/agent_packets.py`
- Modify: `skills/postprocess_outputs.py`
- Modify: `tests/test_agent_packets.py`
- Modify: `tests/test_postprocess_outputs.py`

- [ ] **Step 1: Add failing queue read test**

Append to `tests/test_postprocess_outputs.py`:

```python
    def test_read_pending_repairs_returns_only_pending_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp)
            queue = card / ".agent_runs" / "postprocess_repair_queue.jsonl"
            queue.parent.mkdir(parents=True)
            queue.write_text(
                json.dumps({"id": "a", "status": "pending"}, ensure_ascii=False) + "\n"
                + json.dumps({"id": "b", "status": "completed"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            result = self.mod.read_pending_repairs(card)

        self.assertEqual([item["id"] for item in result], ["a"])
```

- [ ] **Step 2: Add failing agent packet context test**

In `tests/test_agent_packets.py`, add:

```python
    def test_prepare_agent_run_includes_pending_postprocess_repairs(self):
        card = self.tmp / "card"
        card.mkdir()
        queue = card / ".agent_runs" / "postprocess_repair_queue.jsonl"
        queue.parent.mkdir(parents=True)
        queue.write_text(
            json.dumps({"id": "repair-1", "status": "pending", "scope": "ui_extensions"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        result = self.agent_packets.prepare_agent_run(
            card_folder=card,
            user_text="I wait.",
            chat_log=[],
            card_data={"title": "Card"},
            character_contexts={"characters": []},
            turn_index=1,
        )

        input_json = json.loads((Path(result["run_dir"]) / "input.json").read_text(encoding="utf-8"))
        self.assertEqual(input_json["postprocess_repairs"][0]["id"], "repair-1")
```

- [ ] **Step 3: Implement packet inclusion**

In `skills/agent_packets.py`, import:

```python
import postprocess_outputs
```

When building `input.json`, add:

```python
"postprocess_repairs": postprocess_outputs.read_pending_repairs(card_folder),
```

- [ ] **Step 4: Ensure round_prepare preserves context**

If `round_prepare.py` rewrites or filters `input.json`, add the same `postprocess_repairs` key from `postprocess_outputs.read_pending_repairs(card_folder)`.

- [ ] **Step 5: Run targeted tests**

Run:

```powershell
python -m unittest tests.test_postprocess_outputs tests.test_agent_packets -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add skills/postprocess_outputs.py skills/agent_packets.py skills/round_prepare.py tests/test_postprocess_outputs.py tests/test_agent_packets.py
git commit -m "feat: 向 postprocess 暴露异步修复队列"
```

Expected: commit succeeds.

## Task 8: Update Control Plane Smoke

**Files:**
- Modify: `skills/control_plane_smoke.py`
- Modify: `tests/test_control_plane_smoke.py`

- [ ] **Step 1: Add failing smoke assertion**

In `tests/test_control_plane_smoke.py`, add:

```python
self.assertIn("run_postprocess", payload["dispatcher"]["completed_intent_types"])
self.assertLess(
    payload["dispatcher"]["completed_intent_types"].index("review_critic"),
    payload["dispatcher"]["completed_intent_types"].index("run_postprocess"),
)
self.assertLess(
    payload["dispatcher"]["completed_intent_types"].index("run_postprocess"),
    payload["dispatcher"]["completed_intent_types"].index("deliver_round"),
)
self.assertEqual(payload["postprocess"]["core"]["summary"], "Ada warned you about the pendant.")
```

- [ ] **Step 2: Add deterministic postprocess fixture**

In `skills/control_plane_smoke.py`, add:

```python
def _postprocess_output_fixture() -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "core": {
            "summary": "Ada warned you about the pendant.",
            "options": [
                {"label": "Ask Ada what she knows", "source": "postprocess", "requires_confirmation": False}
            ],
            "current_goal": "Decide whether to question Ada about the pendant.",
            "state_patch": {"quest": "Decide whether to question Ada about the pendant."},
        },
        "ui_extensions": {"status_panels": {}, "custom_cards": {}, "asset_bindings": {}},
        "ui_extension_status": {"status": "ok", "issues": []},
        "repair_requests": [],
        "metadata": {"source": "control_plane_smoke"},
    }
```

In `fake_dispatch`, add:

```python
if agent_key == "postprocess":
    return _postprocess_output_fixture()
```

After critic dispatch and before delivery, add a dispatcher call that expects `run_postprocess`.

Add to returned payload:

```python
"postprocess": _read_json(run_dir / "artifacts" / "postprocess.output.json"),
```

- [ ] **Step 3: Run smoke tests**

Run:

```powershell
python -m unittest tests.test_control_plane_smoke -v
python skills/control_plane_smoke.py --repo .
```

Expected: PASS and smoke JSON contains `"run_postprocess"` in completed intents.

- [ ] **Step 4: Commit**

Run:

```powershell
git add skills/control_plane_smoke.py tests/test_control_plane_smoke.py
git commit -m "test: 覆盖 postprocess 控制面链路"
```

Expected: commit succeeds.

## Task 9: Update Documentation And User Guide

**Files:**
- Modify: `README.md`
- Modify: `.claude/skills/rp-orchestrator.md`
- Modify: `.claude/skills/rp-delivery.md`

- [ ] **Step 1: Update README architecture section**

In `README.md`, update the artifact paragraph to state:

```markdown
story agent 只负责正文与来源支撑的角色对话；critic agent 只审核正文质量。critic 通过后，postprocess agent 会生成正文以外的前端数据，包括剧情摘要、下一步行动建议、当前目标、状态面板和可选 UI 扩展数据。`round_deliver.py` 在交付前要求 `postprocess.output.json.core` 有效；扩展 UI 数据失败不会阻塞正文，但会写入异步 postprocess 修复队列，下一轮尝试补齐。
```

- [ ] **Step 2: Update delivery docs**

In `.claude/skills/rp-delivery.md`, add:

```markdown
Postprocess is required before delivery. Delivery must see valid `postprocess.output.json.core` before it mirrors story prose to the frontend. Invalid UI extension data is nonblocking only when a repair record is written for the next round.
```

- [ ] **Step 3: Update orchestrator docs**

In `.claude/skills/rp-orchestrator.md`, change the chain to include:

```markdown
After critic `pass`, dispatch `run_postprocess`. Only after postprocess core validates should dispatcher create `deliver_round`.
```

- [ ] **Step 4: Run docs sanity search**

Run:

```powershell
rg -n "story agent.*summary|story.*<summary>|story.*<options>|critic.*summary|critic.*options|postprocess" README.md .claude/skills skills tests -g "!skills/node_modules/**"
```

Expected:

- postprocess docs and tests exist;
- story/critic files do not assign summary/options ownership to story or critic;
- parser fallback mentions are acceptable.

- [ ] **Step 5: Commit**

Run:

```powershell
git add README.md .claude/skills/rp-orchestrator.md .claude/skills/rp-delivery.md
git commit -m "docs: 更新 postprocess 前端数据流程"
```

Expected: commit succeeds.

## Task 10: Full Verification

**Files:**
- Modify only files required by failing verification.

- [ ] **Step 1: Run obsolete ownership search**

Run:

```powershell
rg -n "story agent.*summary|story agent.*options|critic.*current_goal|critic.*frontend data|critic.*summary|critic.*options|review_critic.*deliver_round|<summary>|<options>" skills .claude README.md tests -g "!skills/node_modules/**"
```

Expected:

- no live prompt tells story to emit summary/options;
- no critic prompt tells critic to handle frontend data;
- no direct critic-pass-to-delivery route remains;
- `<summary>` and `<options>` only remain in parser compatibility or explicit negative tests.

- [ ] **Step 2: Run full unit suite**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 3: Run deterministic smoke**

Run:

```powershell
python skills/control_plane_smoke.py --repo .
```

Expected:

- JSON includes `"ok": true`;
- dispatcher completed intents include `review_critic`, `run_postprocess`, and `deliver_round` in that order;
- payload includes valid `postprocess.core`.

- [ ] **Step 4: Run py_compile**

Run:

```powershell
python -m py_compile skills/postprocess_outputs.py skills/agent_dispatcher.py skills/agent_outputs.py skills/agent_schemas.py skills/agent_prompts.py skills/handler.py skills/round_deliver.py skills/round_prepare.py skills/agent_packets.py skills/control_plane_smoke.py
```

Expected: exits 0 with no output.

- [ ] **Step 5: Start local server and check frontend route**

Run:

```powershell
python skills/start_server.py .
```

Open:

```text
http://localhost:8765
```

Expected:

- frontend loads;
- progress bar still comes from `/api/progress`;
- summary/options/current goal render from postprocess output after a delivered fixture run.

Stop only repo-scoped `skills/server.py` and `mvu_server.js` test processes after checking.

- [ ] **Step 6: Commit verification fixes if any**

Run:

```powershell
git status --short
```

If files changed:

```powershell
git add <changed-files>
git commit -m "fix: 完成 postprocess 验证修正"
```

Expected: working tree clean except ignored runtime artifacts.

## Final Acceptance Checklist

Run before claiming completion:

```powershell
python -m unittest discover -s tests -v
python skills/control_plane_smoke.py --repo .
python -m py_compile skills/postprocess_outputs.py skills/agent_dispatcher.py skills/agent_outputs.py skills/agent_schemas.py skills/agent_prompts.py skills/handler.py skills/round_deliver.py skills/round_prepare.py skills/agent_packets.py skills/control_plane_smoke.py
```

Manual:

- Start `python skills/start_server.py .`.
- Verify `http://localhost:8765`.
- Run `/rp` against a blank folder for at least two turns.
- Confirm story prose appears, summary/options/current goal update through postprocess, progress bar still updates through `progress.json`, and UI extension repair failures do not block text delivery.

## Plan Self-Review

- Spec coverage: Tasks 1-2 cover schema, core validation, critical-action evidence, and async UI repair records. Tasks 3-4 cover prompt contracts and dispatcher flow. Tasks 5-6 move delivery/handler ownership from story tags to postprocess. Task 7 handles next-round repair context. Task 8 updates smoke. Task 9 updates documentation. Task 10 verifies the whole path.
- Incomplete-marker scan: no unfilled work markers remain in this plan.
- Type consistency: field names are consistently `core.summary`, `core.options`, `core.current_goal`, `core.state_patch`, `ui_extensions`, `ui_extension_status`, and `repair_requests`.
- Scope check: this plan is limited to one postprocess agent and frontend data ownership; it does not redesign progress-state ownership or assets generation.
