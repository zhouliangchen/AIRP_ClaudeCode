# AI Input Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace player-instruction keyword matching with a Claude Code-dispatched input analyst artifact while preserving explicit dual-channel authority, deterministic tests, and raw player input immutability.

**Architecture:** `round_prepare.py` creates raw input and input-analysis request artifacts. `rp_generate_cli.py` dispatches an input analyst subagent first, then calls a Python apply phase that validates the analysis, persists safe world updates, rebuilds routed input and agent packets, and only then runs GM/player/character/story/critic agents. Python validates and consumes files; Claude Code remains the only live model orchestration layer.

**Tech Stack:** Python standard library, existing file-mailbox `.agent_runs/<round>/`, Claude Code subagents, Markdown skill prompts, `unittest`, JSON schema-style validators implemented in local Python.

---

## File Structure

- Create `skills/input_analysis.py`: schema constants, hash helpers, validation, fallback analysis, conversion from analysis to routed input, and request Markdown rendering.
- Create `skills/input_analysis_apply.py`: CLI/function that loads current run analysis, validates it, persists derived control-plane updates, rebuilds `input.json`, agent context packets, prompts, and manifest.
- Create `skills/character_registry.py`: shared important-character persistence moved out of `round_prepare.py`.
- Create `.claude/skills/rp-input-analyst.md`: subagent prompt contract for semantic input analysis.
- Modify `skills/agent_packets.py`: accept validated analysis, write `input.raw.json`, `input_analysis.request.md`, and build packets from analysis-derived routing.
- Modify `skills/agent_prompts.py`: write `prompts/input_analyst.prompt.md` and include it in manifest.
- Modify `skills/hidden_settings.py`: add record-based persistence that does not depend on cue matching.
- Modify `skills/round_prepare.py`: remove high-risk hidden-setting and important-character persistence from heuristic pre-analysis; keep legacy heuristics only as fallback/debug.
- Modify `skills/rp_generate_cli.py`: dispatch input analyst before other agents, call apply phase, reload manifest, then continue existing generation.
- Modify `skills/control_plane_smoke.py`: provide deterministic analysis fixture before agent output smoke.
- Modify `.claude/skills/rp-input-router.md` and `.claude/skills/rp-orchestrator.md`: describe AI analysis as authoritative control-plane interpretation.
- Add/update tests in `tests/test_input_analysis.py`, `tests/test_agent_packets.py`, `tests/test_rp_generate_cli.py`, and `tests/test_turn_state.py`.

### Task 1: Input Analysis Schema And Validator

**Files:**
- Create: `skills/input_analysis.py`
- Create: `tests/test_input_analysis.py`

- [ ] **Step 1: Write failing validator tests**

Create `tests/test_input_analysis.py` with focused tests for valid analysis, hash mismatch, explicit channel priority, fallback risk limits, and routed input conversion:

```python
import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_input_analysis():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("input_analysis", ROOT / "skills" / "input_analysis.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InputAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.mod = _load_input_analysis()
        self.role = "我尝试将吊坠扔掉。"
        self.instruction = "用于长期剧情引导：吊坠是变身器。"
        self.raw = self.role + "\n\n[USER_INSTRUCTION]\n" + self.instruction

    def _analysis(self):
        return {
            "schema_version": 1,
            "round_id": "round-000002",
            "analysis_mode": "ai",
            "source_integrity": {
                "raw_text_sha256": self.mod.sha256_text(self.raw),
                "role_text_sha256": self.mod.sha256_text(self.role),
                "user_instruction_text_sha256": self.mod.sha256_text(self.instruction),
                "raw_preserved": True,
            },
            "semantic_units": [
                {
                    "id": "u1",
                    "source_channel": "role_input",
                    "type": "action",
                    "raw_excerpt": self.role,
                    "derived_summary": "玩家尝试丢弃吊坠。",
                    "confidence": 0.92,
                    "visibility": "player_pov",
                    "persist": False,
                },
                {
                    "id": "u2",
                    "source_channel": "user_instruction",
                    "type": "hidden_setting",
                    "raw_excerpt": self.instruction,
                    "derived_summary": "吊坠真实用途暂不公开。",
                    "confidence": 0.95,
                    "visibility": "gm_only",
                    "persist": True,
                },
            ],
            "world_updates": {
                "hidden_facts": [{"text": self.instruction, "visibility": "gm_only", "status": "active"}],
                "public_facts": [],
                "important_characters": [],
                "retcon_requests": [],
            },
            "narrative_directives": {
                "rewrite_previous_output": False,
                "expand_synopsis_before_continue": False,
                "continue_after_player_action": True,
                "must_stop_for_player_decision": False,
            },
            "routing": {
                "role_channel": self.role,
                "user_instruction_channel": self.instruction,
                "gm": True,
                "player": True,
                "characters": [],
            },
            "risks": [],
        }

    def test_validate_accepts_ai_analysis_with_matching_hashes(self):
        result = self.mod.validate_input_analysis(
            self._analysis(),
            raw_text=self.raw,
            role_text=self.role,
            user_instruction_text=self.instruction,
        )
        self.assertEqual(result["analysis_mode"], "ai")
        self.assertEqual(result["semantic_units"][0]["type"], "action")

    def test_validate_rejects_hash_mismatch(self):
        data = self._analysis()
        data["source_integrity"]["raw_text_sha256"] = hashlib.sha256(b"wrong").hexdigest()
        with self.assertRaises(self.mod.InputAnalysisError):
            self.mod.validate_input_analysis(
                data,
                raw_text=self.raw,
                role_text=self.role,
                user_instruction_text=self.instruction,
            )

    def test_routing_preserves_explicit_dual_channel_text(self):
        result = self.mod.analysis_to_routed_input(
            self._analysis(),
            explicit_payload={
                "input_schema": "dual_channel_v1",
                "role_text": self.role,
                "user_instruction_text": self.instruction,
            },
        )
        self.assertEqual(result["role_channel"], self.role)
        self.assertEqual(result["user_instruction_channel"], self.instruction)
        self.assertEqual(result["input_schema"], "analysis_v1")

    def test_fallback_blocks_high_risk_persistence(self):
        fallback = self.mod.build_fallback_analysis(
            raw_text=self.raw,
            role_text=self.role,
            user_instruction_text=self.instruction,
            round_id="round-000002",
        )
        self.assertEqual(fallback["analysis_mode"], "fallback")
        self.assertEqual(fallback["world_updates"]["hidden_facts"], [])
        self.assertEqual(fallback["world_updates"]["important_characters"], [])
        self.assertIn("fallback", fallback["risks"][0])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m unittest tests.test_input_analysis -v`

Expected: import failure because `skills/input_analysis.py` does not exist.

- [ ] **Step 3: Implement validator module**

Create `skills/input_analysis.py` with these public functions and error type:

```python
"""Structured AI input-analysis artifact validation and routing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


class InputAnalysisError(RuntimeError):
    """Raised when input analysis is missing, unsafe, or inconsistent."""


SEMANTIC_TYPES = {
    "action",
    "synopsis",
    "omniscient_setting",
    "hidden_setting",
    "character_declaration",
    "edit_request",
    "system_command",
    "style_guidance",
    "unclear",
}

VISIBILITY_VALUES = {
    "gm_only",
    "public_world",
    "player_pov",
    "character_pov",
    "specific_characters",
}

HIGH_RISK_TYPES = {"hidden_setting", "character_declaration", "edit_request", "system_command"}


def sha256_text(text: Any) -> str:
    return hashlib.sha256(("" if text is None else str(text)).encode("utf-8")).hexdigest()


def _as_dict(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise InputAnalysisError(f"{name} must be an object")
    return value


def _as_list(value: Any, name: str) -> List[Any]:
    if not isinstance(value, list):
        raise InputAnalysisError(f"{name} must be a list")
    return value


def _check_hash(integrity: Dict[str, Any], key: str, expected_text: Any) -> None:
    recorded = str(integrity.get(key) or "").strip()
    if recorded and recorded != sha256_text(expected_text):
        raise InputAnalysisError(f"source_integrity.{key} does not match source text")


def _normalize_unit(unit: Any, index: int) -> Dict[str, Any]:
    item = _as_dict(unit, f"semantic_units[{index}]")
    unit_type = str(item.get("type") or "").strip()
    if unit_type not in SEMANTIC_TYPES:
        raise InputAnalysisError(f"semantic_units[{index}].type is invalid: {unit_type}")
    visibility = str(item.get("visibility") or "").strip()
    if visibility not in VISIBILITY_VALUES:
        raise InputAnalysisError(f"semantic_units[{index}].visibility is invalid: {visibility}")
    confidence = item.get("confidence", 0.0)
    if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
        raise InputAnalysisError(f"semantic_units[{index}].confidence must be 0..1")
    raw_excerpt = str(item.get("raw_excerpt") or "")
    if not raw_excerpt:
        raise InputAnalysisError(f"semantic_units[{index}].raw_excerpt is required")
    return {
        "id": str(item.get("id") or f"u{index + 1}"),
        "source_channel": str(item.get("source_channel") or "mixed"),
        "type": unit_type,
        "raw_excerpt": raw_excerpt,
        "derived_summary": str(item.get("derived_summary") or ""),
        "confidence": float(confidence),
        "visibility": visibility,
        "persist": bool(item.get("persist", False)),
        "targets": item.get("targets", []),
    }


def validate_input_analysis(
    data: Any,
    *,
    raw_text: Any,
    role_text: Any = "",
    user_instruction_text: Any = "",
) -> Dict[str, Any]:
    payload = _as_dict(data, "input_analysis")
    if payload.get("schema_version") != 1:
        raise InputAnalysisError("schema_version must be 1")
    mode = str(payload.get("analysis_mode") or "ai")
    if mode not in {"ai", "fallback", "fixture"}:
        raise InputAnalysisError("analysis_mode must be ai, fallback, or fixture")
    integrity = _as_dict(payload.get("source_integrity"), "source_integrity")
    if integrity.get("raw_preserved") is not True:
        raise InputAnalysisError("source_integrity.raw_preserved must be true")
    _check_hash(integrity, "raw_text_sha256", raw_text)
    _check_hash(integrity, "role_text_sha256", role_text)
    _check_hash(integrity, "user_instruction_text_sha256", user_instruction_text)

    units = [_normalize_unit(unit, i) for i, unit in enumerate(_as_list(payload.get("semantic_units"), "semantic_units"))]
    world_updates = _as_dict(payload.get("world_updates"), "world_updates")
    for key in ("hidden_facts", "public_facts", "important_characters", "retcon_requests"):
        _as_list(world_updates.get(key, []), f"world_updates.{key}")
    if mode == "fallback":
        risky = [unit for unit in units if unit["type"] in HIGH_RISK_TYPES and unit["persist"]]
        if risky or world_updates.get("hidden_facts") or world_updates.get("important_characters") or world_updates.get("retcon_requests"):
            raise InputAnalysisError("fallback analysis may not request high-risk persistence")

    routing = _as_dict(payload.get("routing"), "routing")
    directives = _as_dict(payload.get("narrative_directives"), "narrative_directives")
    return {
        **payload,
        "analysis_mode": mode,
        "semantic_units": units,
        "world_updates": {
            "hidden_facts": list(world_updates.get("hidden_facts") or []),
            "public_facts": list(world_updates.get("public_facts") or []),
            "important_characters": list(world_updates.get("important_characters") or []),
            "retcon_requests": list(world_updates.get("retcon_requests") or []),
        },
        "narrative_directives": directives,
        "routing": routing,
        "risks": list(payload.get("risks") or []),
    }


def load_json(path: str | Path) -> Dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise InputAnalysisError(f"{path}: invalid JSON") from exc
    if not isinstance(data, dict):
        raise InputAnalysisError(f"{path}: JSON object required")
    return data


def analysis_to_routed_input(data: Dict[str, Any], explicit_payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = explicit_payload if isinstance(explicit_payload, dict) else {}
    routing = data.get("routing") if isinstance(data, dict) else {}
    routing = routing if isinstance(routing, dict) else {}
    if payload.get("input_schema") == "dual_channel_v1":
        role = "" if payload.get("role_text") is None else str(payload.get("role_text"))
        instruction = "" if payload.get("user_instruction_text") is None else str(payload.get("user_instruction_text"))
    else:
        role = "" if routing.get("role_channel") is None else str(routing.get("role_channel"))
        instruction = "" if routing.get("user_instruction_channel") is None else str(routing.get("user_instruction_channel"))
    components = []
    if role:
        components.append({"channel": "role", "text": role})
    if instruction:
        components.append({"channel": "user_instruction", "text": instruction})
    return {
        "role_channel": role,
        "user_instruction_channel": instruction,
        "components": components,
        "input_schema": "analysis_v1",
        "analysis_mode": str(data.get("analysis_mode") or "ai"),
    }


def build_fallback_analysis(
    *,
    raw_text: Any,
    role_text: Any = "",
    user_instruction_text: Any = "",
    round_id: str = "",
) -> Dict[str, Any]:
    role = "" if role_text is None else str(role_text)
    instruction = "" if user_instruction_text is None else str(user_instruction_text)
    raw = "" if raw_text is None else str(raw_text)
    excerpt = role or raw
    units = []
    if excerpt:
        units.append({
            "id": "fallback-role",
            "source_channel": "role_input",
            "type": "unclear",
            "raw_excerpt": excerpt,
            "derived_summary": "",
            "confidence": 0.2,
            "visibility": "player_pov",
            "persist": False,
        })
    if instruction:
        units.append({
            "id": "fallback-instruction",
            "source_channel": "user_instruction",
            "type": "unclear",
            "raw_excerpt": instruction,
            "derived_summary": "",
            "confidence": 0.2,
            "visibility": "gm_only",
            "persist": False,
        })
    return {
        "schema_version": 1,
        "round_id": round_id,
        "analysis_mode": "fallback",
        "source_integrity": {
            "raw_text_sha256": sha256_text(raw),
            "role_text_sha256": sha256_text(role),
            "user_instruction_text_sha256": sha256_text(instruction),
            "raw_preserved": True,
        },
        "semantic_units": units,
        "world_updates": {
            "hidden_facts": [],
            "public_facts": [],
            "important_characters": [],
            "retcon_requests": [],
        },
        "narrative_directives": {
            "rewrite_previous_output": False,
            "expand_synopsis_before_continue": False,
            "continue_after_player_action": bool(role or raw),
            "must_stop_for_player_decision": False,
        },
        "routing": {
            "role_channel": role or raw,
            "user_instruction_channel": instruction,
            "gm": True,
            "player": bool(role or raw),
            "characters": [],
        },
        "risks": ["fallback analysis used; high-risk persistence disabled"],
    }
```

- [ ] **Step 4: Run validator tests**

Run: `python -m unittest tests.test_input_analysis -v`

Expected: all tests in `InputAnalysisTest` pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add skills/input_analysis.py tests/test_input_analysis.py
git commit -m "feat: 添加输入语义解析schema校验"
```

### Task 2: Input Analyst Prompt And Request Artifact

**Files:**
- Create: `.claude/skills/rp-input-analyst.md`
- Modify: `.claude/skills/rp-input-router.md`
- Modify: `skills/agent_prompts.py`
- Modify: `skills/agent_packets.py`
- Test: `tests/test_agent_packets.py`

- [ ] **Step 1: Add failing prompt/request tests**

Append tests to `AgentPacketTest` in `tests/test_agent_packets.py`:

```python
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
        request = (run_dir / "input_analysis.request.md").read_text(encoding="utf-8")
        self.assertIn("raw_text", request)
        self.assertIn("设定：今天是梦境。", request)
```

- [ ] **Step 2: Run targeted test and verify failure**

Run: `python -m unittest tests.test_agent_packets.AgentPacketTest.test_prepare_agent_run_writes_input_analysis_request_and_prompt -v`

Expected: failure because the request and prompt are not written.

- [ ] **Step 3: Create input analyst skill**

Create `.claude/skills/rp-input-analyst.md`:

```markdown
---
name: rp-input-analyst
description: Use before every RP generation turn to semantically analyze raw player input and user instructions into a strict control-plane JSON artifact.
---

## Role

You are the input analyst subagent. You do not write fiction, roleplay, summarize for display, or advance the story. Your only task is to classify the current raw player submission into structured control-plane data.

## Authority

- The player's raw text is authoritative and immutable.
- Explicit dual-channel fields override your inference.
- You may derive summaries and routing, but you must not rewrite, trim, merge, or replace the raw input.
- You must protect viewpoint isolation: hidden or omniscient facts are GM-only unless the player text explicitly makes them world-visible.

## Classification

Use semantic judgment instead of keyword matching. A sentence can contain more than one unit. Classify units as:

- `action`: immediate first-person player-character action.
- `synopsis`: first-person plot summary that story must expand before advancing.
- `omniscient_setting`: author-level world fact or rule.
- `hidden_setting`: GM-only long-term truth, future reveal, cost, premise, or secret.
- `character_declaration`: important/core character creation, promotion, demotion, or profile update.
- `edit_request`: request to repair, rewrite, branch, or reinterpret prior AI-derived content.
- `system_command`: direct instruction to Claude Code or orchestration.
- `style_guidance`: tone, genre, pacing, or prose preference.
- `unclear`: content that cannot be safely classified.

## Output

Write exactly one JSON object to `input_analysis.output.json`. No prose, Markdown, or code fences.

Required shape:

```json
{
  "schema_version": 1,
  "round_id": "round-000001",
  "analysis_mode": "ai",
  "source_integrity": {
    "raw_text_sha256": "",
    "role_text_sha256": "",
    "user_instruction_text_sha256": "",
    "raw_preserved": true
  },
  "semantic_units": [],
  "world_updates": {
    "hidden_facts": [],
    "public_facts": [],
    "important_characters": [],
    "retcon_requests": []
  },
  "narrative_directives": {
    "rewrite_previous_output": false,
    "expand_synopsis_before_continue": false,
    "continue_after_player_action": true,
    "must_stop_for_player_decision": false
  },
  "routing": {
    "role_channel": "",
    "user_instruction_channel": "",
    "gm": true,
    "player": true,
    "characters": []
  },
  "risks": []
}
```

For every `semantic_units` item include `id`, `source_channel`, `type`, `raw_excerpt`, `derived_summary`, `confidence`, `visibility`, and `persist`.
```

- [ ] **Step 4: Update prompt materialization**

In `skills/agent_prompts.py`, add the skill path and writer:

```python
SKILL_PATHS = {
    "input_analyst": ".claude/skills/rp-input-analyst.md",
    "gm": ".claude/skills/rp-gm-agent.md",
    "player": ".claude/skills/rp-player-agent.md",
    "character": ".claude/skills/rp-character-agent.md",
    "story": ".claude/skills/rp-story-agent.md",
    "critic": ".claude/skills/rp-critic-agent.md",
}
```

Add this function near the other prompt builders:

```python
def _input_analyst_prompt(context: Dict[str, Any]) -> str:
    contract = _json_block({
        "schema_version": 1,
        "round_id": context.get("round_id", ""),
        "analysis_mode": "ai",
        "source_integrity": {
            "raw_text_sha256": context.get("source_integrity", {}).get("raw_text_sha256", ""),
            "role_text_sha256": context.get("source_integrity", {}).get("role_text_sha256", ""),
            "user_instruction_text_sha256": context.get("source_integrity", {}).get("user_instruction_text_sha256", ""),
            "raw_preserved": True,
        },
        "semantic_units": [],
        "world_updates": {
            "hidden_facts": [],
            "public_facts": [],
            "important_characters": [],
            "retcon_requests": [],
        },
        "narrative_directives": {
            "rewrite_previous_output": False,
            "expand_synopsis_before_continue": False,
            "continue_after_player_action": True,
            "must_stop_for_player_decision": False,
        },
        "routing": {
            "role_channel": "",
            "user_instruction_channel": "",
            "gm": True,
            "player": True,
            "characters": [],
        },
        "risks": [],
    })
    return _base_prompt(
        "Input Analyst Prompt",
        "input_analyst",
        "input_analysis.output.json",
        contract,
        context,
    )
```

At the beginning of `write_round_prompts`, before GM/player prompts:

```python
    input_request = gm_packet.get("input_analysis_request", {}) if isinstance(gm_packet, dict) else {}
    input_analyst_prompt = prompt_root / "input_analyst.prompt.md"
    _write_prompt(input_analyst_prompt, _input_analyst_prompt(input_request))
```

In the manifest:

```python
        "prompts": {
            "input_analyst": _rel(input_analyst_prompt, root),
            "gm": _rel(gm_prompt, root),
            "player": _rel(player_prompt, root),
            "characters": character_prompts,
            "story": _rel(story_prompt, root),
            "critic": _rel(critic_prompt, root),
        },
        "expected_outputs": {
            "input_analysis": "input_analysis.output.json",
            "gm": "gm.output.json",
            "player": "player.output.json",
            "characters": character_outputs,
            "story": "story.output.json",
            "critic": "critic.report.json",
        },
```

- [ ] **Step 5: Write request artifacts from agent packets**

In `skills/agent_packets.py`, import `input_analysis` and add:

```python
import input_analysis
```

Add helper:

```python
def build_input_analysis_request(run_dir, user_text, input_payload, chat_log, card_data):
    payload = input_payload if isinstance(input_payload, dict) else {}
    raw_text = _to_text(payload.get("raw_text", user_text))
    role_text = _to_text(payload.get("role_text", ""))
    instruction_text = _to_text(payload.get("user_instruction_text", ""))
    if payload.get("input_schema") != "dual_channel_v1":
        routed = route_input_payload(user_text, None)
        role_text = _to_text(routed.get("role_channel"))
        instruction_text = _to_text(routed.get("user_instruction_channel"))
    request = {
        "round_id": Path(run_dir).name,
        "raw_text": raw_text,
        "explicit_payload": payload,
        "role_text": role_text,
        "user_instruction_text": instruction_text,
        "source_integrity": {
            "raw_text_sha256": input_analysis.sha256_text(raw_text),
            "role_text_sha256": input_analysis.sha256_text(role_text),
            "user_instruction_text_sha256": input_analysis.sha256_text(instruction_text),
            "raw_preserved": True,
        },
        "recent_chat": chat_log or [],
        "card_projection": compact_card_data(card_data),
    }
    return request
```

Inside `prepare_agent_run`, after `run_dir` exists:

```python
    input_request = build_input_analysis_request(run_dir, user_text, input_payload, chat_log, card_data)
    agent_run.write_json(run_dir / "input.raw.json", input_request)
    agent_run.write_text(
        run_dir / "input_analysis.request.md",
        "# Input Analysis Request\n\n```json\n"
        + json.dumps(input_request, ensure_ascii=False, indent=2)
        + "\n```\n",
    )
```

Add the request into the GM packet before prompts are written:

```python
    gm_packet["input_analysis_request"] = input_request
```

- [ ] **Step 6: Update input-router prompt wording**

Edit `.claude/skills/rp-input-router.md` so `Classification Notes` no longer says explicit cues are the routing mechanism. Replace it with:

```markdown
## Classification Notes

- Explicit dual-channel UI fields are authoritative.
- When text is mixed or ambiguous, use `rp-input-analyst`; do not rely on keyword lists.
- Parentheses, genre labels, and casual phrases are not sufficient by themselves to classify a sentence.
- For a first-person synopsis, story must expand the synopsis before advancing beyond it.
- For an action, story briefly reflects the action's immediate consequence before moving forward.
- For omniscient settings, update derived data and memory even if no in-world character currently knows the fact.
```

- [ ] **Step 7: Run request/prompt tests**

Run: `python -m unittest tests.test_agent_packets.AgentPacketTest.test_prepare_agent_run_writes_input_analysis_request_and_prompt -v`

Expected: pass.

- [ ] **Step 8: Commit Task 2**

```powershell
git add .claude/skills/rp-input-analyst.md .claude/skills/rp-input-router.md skills/agent_prompts.py skills/agent_packets.py tests/test_agent_packets.py
git commit -m "feat: 增加输入分析请求与prompt产物"
```

### Task 3: Apply Analysis To Control Plane State

**Files:**
- Create: `skills/character_registry.py`
- Create: `skills/input_analysis_apply.py`
- Modify: `skills/hidden_settings.py`
- Modify: `skills/agent_packets.py`
- Test: `tests/test_input_analysis.py`
- Test: `tests/test_agent_packets.py`

- [ ] **Step 1: Add failing apply tests**

Append to `tests/test_input_analysis.py`:

```python
class InputAnalysisApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.card.mkdir()
        (self.card / ".card_data.json").write_text(
            json.dumps({"mode": "blank_bootstrap", "source_type": "blank"}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.agent_run = _load_module("agent_run")
        self.input_analysis = _load_module("input_analysis")
        self.apply = _load_module("input_analysis_apply")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_run(self, analysis):
        run_dir = self.agent_run.create_run_dir(self.card, turn_index=1)
        raw = {
            "round_id": run_dir.name,
            "raw_text": "我想找苏黎。\n\n[USER_INSTRUCTION]\n设定重要角色：苏黎，真实身份是前魔法少女。",
            "role_text": "我想找苏黎。",
            "user_instruction_text": "设定重要角色：苏黎，真实身份是前魔法少女。",
            "explicit_payload": {
                "input_schema": "dual_channel_v1",
                "raw_text": "我想找苏黎。\n\n[USER_INSTRUCTION]\n设定重要角色：苏黎，真实身份是前魔法少女。",
                "role_text": "我想找苏黎。",
                "user_instruction_text": "设定重要角色：苏黎，真实身份是前魔法少女。",
            },
            "source_integrity": {
                "raw_text_sha256": self.input_analysis.sha256_text("我想找苏黎。\n\n[USER_INSTRUCTION]\n设定重要角色：苏黎，真实身份是前魔法少女。"),
                "role_text_sha256": self.input_analysis.sha256_text("我想找苏黎。"),
                "user_instruction_text_sha256": self.input_analysis.sha256_text("设定重要角色：苏黎，真实身份是前魔法少女。"),
                "raw_preserved": True,
            },
            "recent_chat": [],
            "card_projection": {},
        }
        (run_dir / "input.raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "input_analysis.output.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        return run_dir

    def test_apply_persists_hidden_and_important_character_without_leaking_to_player(self):
        raw_text = "我想找苏黎。\n\n[USER_INSTRUCTION]\n设定重要角色：苏黎，真实身份是前魔法少女。"
        role_text = "我想找苏黎。"
        instruction = "设定重要角色：苏黎，真实身份是前魔法少女。"
        analysis = {
            "schema_version": 1,
            "round_id": "round-000002",
            "analysis_mode": "fixture",
            "source_integrity": {
                "raw_text_sha256": self.input_analysis.sha256_text(raw_text),
                "role_text_sha256": self.input_analysis.sha256_text(role_text),
                "user_instruction_text_sha256": self.input_analysis.sha256_text(instruction),
                "raw_preserved": True,
            },
            "semantic_units": [
                {"id": "u1", "source_channel": "role_input", "type": "action", "raw_excerpt": role_text, "derived_summary": "", "confidence": 0.9, "visibility": "player_pov", "persist": False},
                {"id": "u2", "source_channel": "user_instruction", "type": "character_declaration", "raw_excerpt": instruction, "derived_summary": "", "confidence": 0.96, "visibility": "gm_only", "persist": True},
                {"id": "u3", "source_channel": "user_instruction", "type": "hidden_setting", "raw_excerpt": instruction, "derived_summary": "", "confidence": 0.9, "visibility": "gm_only", "persist": True},
            ],
            "world_updates": {
                "hidden_facts": [{"text": instruction, "visibility": "gm_only", "status": "active"}],
                "public_facts": [],
                "important_characters": [{"name": "苏黎", "setting_text": instruction, "status": "major"}],
                "retcon_requests": [],
            },
            "narrative_directives": {"rewrite_previous_output": False, "expand_synopsis_before_continue": False, "continue_after_player_action": True, "must_stop_for_player_decision": False},
            "routing": {"role_channel": role_text, "user_instruction_channel": instruction, "gm": True, "player": True, "characters": ["苏黎"]},
            "risks": [],
        }
        run_dir = self._write_run(analysis)
        result = self.apply.apply_current_run(self.card)
        self.assertTrue(result["ok"])
        card_data = json.loads((self.card / ".card_data.json").read_text(encoding="utf-8"))
        self.assertIn("苏黎", card_data["character_orchestration"]["major"])
        self.assertTrue((self.card / "memory" / "characters" / "苏黎" / "profile.json").exists())
        gm_packet = json.loads((run_dir / "gm.context.json").read_text(encoding="utf-8"))
        player_packet = json.loads((run_dir / "player.context.json").read_text(encoding="utf-8"))
        self.assertIn(instruction, json.dumps(gm_packet, ensure_ascii=False))
        self.assertNotIn(instruction, json.dumps(player_packet, ensure_ascii=False))
```

Also add helper `_load_module` and `tempfile` import if absent:

```python
import tempfile


def _load_module(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```

- [ ] **Step 2: Run apply test and verify failure**

Run: `python -m unittest tests.test_input_analysis.InputAnalysisApplyTest -v`

Expected: import failure for `input_analysis_apply.py` or missing apply behavior.

- [ ] **Step 3: Create shared character registry**

Create `skills/character_registry.py`:

```python
"""Shared persistence helpers for important RP characters."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List


def safe_name(name: Any) -> str:
    text = str(name or "").strip()
    return re.sub(r'[\\/:*?"<>|]+', "_", text) or "_unknown"


def normalize_name(name: Any) -> str:
    text = str(name or "").strip()
    text = text.strip(" \t\r\n\"'“”‘’「」『』《》【】（）()")
    text = re.sub(r"\s+", " ", text)
    return text[:40]


def persist_important_characters(
    card_folder: Any,
    card_data: Dict[str, Any],
    declarations: Iterable[Dict[str, Any]],
    *,
    source_input_id: str = "",
    round_id: str = "",
) -> List[str]:
    if not isinstance(card_data, dict):
        return []
    orchestration = card_data.setdefault("character_orchestration", {})
    major = orchestration.setdefault("major", [])
    if not isinstance(major, list):
        major = []
        orchestration["major"] = major
    orchestration.setdefault("minor_policy", "main_agent")
    orchestration.setdefault("max_parallel_subagents", 2)

    persisted = []
    for declaration in declarations or []:
        name = normalize_name(declaration.get("name"))
        if not name:
            continue
        setting_text = str(declaration.get("setting_text") or declaration.get("text") or "").strip()
        if name not in major:
            major.append(name)
        char_dir = Path(card_folder) / "memory" / "characters" / safe_name(name)
        char_dir.mkdir(parents=True, exist_ok=True)
        (char_dir / "profile.md").write_text(
            "\n".join([
                f"# {name}",
                "",
                "## Authoritative Player Setting",
                "- source: input_analysis",
                f"- source_input_id: {source_input_id or ''}",
                f"- round_id: {round_id or ''}",
                "- importance: major",
                "- visibility: character_private_and_gm",
                "",
                setting_text,
                "",
            ]),
            encoding="utf-8",
        )
        (char_dir / "profile.json").write_text(
            json.dumps({
                "name": name,
                "importance": "major",
                "source": "input_analysis",
                "source_input_id": source_input_id or "",
                "round_id": round_id or "",
                "visibility": "character_private_and_gm",
                "status": "active",
                "authoritative_setting": setting_text,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        persisted.append(name)

    if persisted:
        Path(card_folder, ".card_data.json").write_text(
            json.dumps(card_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return persisted
```

- [ ] **Step 4: Add record-based hidden setting persistence**

In `skills/hidden_settings.py`, add:

```python
def persist_hidden_setting_record(
    card_folder: Any,
    record: Dict[str, Any],
    *,
    source_input_id: str = "",
    round_id: str = "",
) -> Optional[Dict[str, Any]]:
    if not isinstance(record, dict):
        return None
    body = str(record.get("text") or "").strip()
    if not body:
        return None
    item_id = _entry_id(body, source_input_id or "")
    existing = load_hidden_settings(card_folder, limit=None)
    for item in existing:
        if item.get("id") == item_id:
            return item
    entry = {
        "id": item_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "round_id": str(round_id or ""),
        "source_input_id": str(source_input_id or ""),
        "visibility": str(record.get("visibility") or "gm_only"),
        "status": str(record.get("status") or "active"),
        "text": body,
    }
    path = hidden_settings_path(card_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
```

- [ ] **Step 5: Implement apply CLI**

Create `skills/input_analysis_apply.py`:

```python
"""Apply validated input-analysis artifacts to the current RP run."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

import agent_packets
import agent_prompts
import agent_run
import character_registry
import hidden_settings
import input_analysis


def _read_card_data(card_folder: Path) -> Dict[str, Any]:
    path = card_folder / ".card_data.json"
    data = agent_run.read_json(path, {})
    return data if isinstance(data, dict) else {}


def _source_input_id(raw_request: Dict[str, Any]) -> str:
    payload = raw_request.get("explicit_payload")
    if isinstance(payload, dict):
        return str(payload.get("id") or "")
    return ""


def _load_and_validate(run_dir: Path) -> tuple[Dict[str, Any], Dict[str, Any]]:
    raw_request = agent_run.read_json(run_dir / "input.raw.json")
    if not isinstance(raw_request, dict):
        raise input_analysis.InputAnalysisError("input.raw.json is missing or invalid")
    data = input_analysis.load_json(run_dir / "input_analysis.output.json")
    validated = input_analysis.validate_input_analysis(
        data,
        raw_text=raw_request.get("raw_text", ""),
        role_text=raw_request.get("role_text", ""),
        user_instruction_text=raw_request.get("user_instruction_text", ""),
    )
    return raw_request, validated


def apply_current_run(card_folder: str | Path, root_dir: str | Path | None = None) -> Dict[str, Any]:
    card = Path(card_folder)
    run_dir = agent_run.current_run_dir(card)
    if run_dir is None:
        raise input_analysis.InputAnalysisError("current run directory is missing")
    raw_request, analysis = _load_and_validate(run_dir)
    card_data = _read_card_data(card)
    source_id = _source_input_id(raw_request)
    hidden_records = []
    for record in analysis.get("world_updates", {}).get("hidden_facts", []):
        saved = hidden_settings.persist_hidden_setting_record(
            card,
            record,
            source_input_id=source_id,
            round_id=run_dir.name,
        )
        if saved:
            hidden_records.append(saved)
    important = character_registry.persist_important_characters(
        card,
        card_data,
        analysis.get("world_updates", {}).get("important_characters", []),
        source_input_id=source_id,
        round_id=run_dir.name,
    )
    routed = input_analysis.analysis_to_routed_input(
        analysis,
        explicit_payload=raw_request.get("explicit_payload") if isinstance(raw_request.get("explicit_payload"), dict) else None,
    )
    character_contexts = agent_packets.build_character_contexts_from_card(
        card,
        card_data,
        raw_request.get("recent_chat", []),
        routed.get("role_channel", ""),
    )
    agent_packets.rebuild_agent_run_from_analysis(
        card_folder=card,
        run_dir=run_dir,
        raw_request=raw_request,
        analysis=analysis,
        routed_input=routed,
        card_data=card_data,
        character_contexts=character_contexts,
        hidden_setting_records=hidden_settings.load_hidden_settings(card),
    )
    manifest = agent_run.update_manifest_stage(run_dir, "analysis_applied", "Input analysis applied and agent packets rebuilt.")
    return {
        "ok": True,
        "run_dir": str(run_dir),
        "analysis_mode": analysis.get("analysis_mode"),
        "important_characters": important,
        "hidden_setting_count": len(hidden_records),
        "stage": manifest.get("stage"),
    }


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("Usage: python input_analysis_apply.py <card_folder> [ROOT]", file=sys.stderr)
        return 2
    try:
        payload = apply_current_run(argv[0], argv[1] if len(argv) > 1 else None)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), flush=True)
        return 1
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Add rebuild helpers to agent packets**

In `skills/agent_packets.py`, add:

```python
def build_character_contexts_from_card(card_folder, card_data, chat_log, user_text):
    try:
        import round_prepare
        return round_prepare.build_character_contexts(card_folder, card_data, {}, chat_log, user_text)
    except Exception:
        return {"characters": []}


def rebuild_agent_run_from_analysis(
    *,
    card_folder,
    run_dir,
    raw_request,
    analysis,
    routed_input,
    card_data,
    character_contexts,
    hidden_setting_records=None,
):
    run_dir = Path(run_dir)
    input_json = dict(raw_request)
    input_json["raw_text"] = _to_text(raw_request.get("raw_text", ""))
    input_json["routed_input"] = routed_input
    input_json["input_analysis"] = analysis
    input_json["gm_only_hidden_settings"] = hidden_setting_records or []
    input_json["recent_chat"] = raw_request.get("recent_chat", [])
    input_json["card_data"] = compact_card_data(card_data)
    input_json["character_contexts"] = character_contexts or {}
    agent_run.write_json(run_dir / "input.json", input_json)

    gm_packet = build_gm_packet(
        card_folder,
        routed_input,
        raw_request.get("recent_chat", []),
        card_data,
        character_contexts,
        hidden_setting_records=hidden_setting_records,
    )
    gm_packet["input_analysis"] = analysis
    player_packet = build_player_packet(card_folder, routed_input, raw_request.get("recent_chat", []))
    agent_run.write_json(run_dir / "gm.context.json", gm_packet)
    agent_run.write_json(run_dir / "player.context.json", player_packet)

    character_packets = {}
    for character in _iter_characters(character_contexts):
        name = character.get("name") if isinstance(character, dict) else ""
        safe = agent_run.safe_name(name)
        packet = build_character_packet(card_folder, character, routed_input, raw_request.get("recent_chat", []))
        agent_run.write_json(run_dir / "characters" / f"{safe}.context.json", packet)
        character_packets[safe] = packet
    agent_prompts.write_round_prompts(run_dir, gm_packet, player_packet, character_packets, card_folder=card_folder)
```

- [ ] **Step 7: Run apply tests**

Run: `python -m unittest tests.test_input_analysis.InputAnalysisApplyTest -v`

Expected: pass.

- [ ] **Step 8: Commit Task 3**

```powershell
git add skills/character_registry.py skills/input_analysis_apply.py skills/hidden_settings.py skills/agent_packets.py tests/test_input_analysis.py
git commit -m "feat: 应用输入分析产物到控制面"
```

### Task 4: Dispatch Input Analyst Before Other Agents

**Files:**
- Modify: `skills/rp_generate_cli.py`
- Test: `tests/test_rp_generate_cli.py`

- [ ] **Step 1: Add failing generate CLI tests**

Add a test that stubs dispatch order and apply:

```python
    def test_generate_turn_dispatches_input_analyst_before_gm(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            root = Path(tmp) / "root"
            card.mkdir()
            run_dir = card / ".agent_runs" / "round-000001"
            (card / ".agent_runs").mkdir()
            (card / ".agent_runs" / "current").write_text(str(run_dir), encoding="utf-8")
            (run_dir / "prompts").mkdir(parents=True)
            (run_dir / "prompts" / "input_analyst.prompt.md").write_text("input prompt", encoding="utf-8")
            (run_dir / "prompts" / "gm.prompt.md").write_text("gm prompt", encoding="utf-8")
            (run_dir / "prompts" / "player.prompt.md").write_text("player prompt", encoding="utf-8")
            (run_dir / "prompts" / "story.prompt.md").write_text("story prompt", encoding="utf-8")
            (run_dir / "prompts" / "critic.prompt.md").write_text("critic prompt", encoding="utf-8")
            (run_dir / "manifest.json").write_text(json.dumps({
                "round_id": "round-000001",
                "prompts": {
                    "input_analyst": "prompts/input_analyst.prompt.md",
                    "gm": "prompts/gm.prompt.md",
                    "player": "prompts/player.prompt.md",
                    "characters": {},
                    "story": "prompts/story.prompt.md",
                    "critic": "prompts/critic.prompt.md",
                },
                "expected_outputs": {
                    "input_analysis": "input_analysis.output.json",
                    "gm": "gm.output.json",
                    "player": "player.output.json",
                    "characters": {},
                    "story": "story.output.json",
                    "critic": "critic.report.json",
                },
            }), encoding="utf-8")
            calls = []
            original_dispatch = self.module._dispatch_and_write
            original_apply = self.module.input_analysis_apply.apply_current_run
            original_build_story_input = self.module.agent_outputs.build_story_input
            original_delivery = self.module._run_round_deliver
            try:
                def fake_dispatch(agent_key, prompt_text, cwd, output_path, validator=None, extra_context=None, attempts=2):
                    calls.append(agent_key)
                    if agent_key == "input_analyst":
                        return {"schema_version": 1, "analysis_mode": "fixture"}
                    if agent_key == "gm":
                        return {"agent": "gm", "narration": "", "npc_events": [], "world_state_delta": [], "handoff": {}}
                    if agent_key == "player":
                        return {"agent": "player", "agent_id": "player", "action": "", "dialogue": [], "perception": [], "memory_delta": []}
                    if agent_key == "story":
                        return {"content": "<content>ok</content>", "character_dialogues": [], "metadata": {}}
                    if agent_key == "critic":
                        return {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": "", "system_iteration_suggestion": ""}
                    raise AssertionError(agent_key)
                self.module._dispatch_and_write = fake_dispatch
                self.module.input_analysis_apply.apply_current_run = lambda card_folder, root_dir=None: calls.append("apply")
                self.module.agent_outputs.build_story_input = lambda run_dir_arg: {"player_inputs": {"raw_text": ""}, "actor_outputs": {"characters": {}}}
                self.module._run_round_deliver = lambda card_arg, root_arg: {"ok": True}
                self.module.generate_turn(card, root)
            finally:
                self.module._dispatch_and_write = original_dispatch
                self.module.input_analysis_apply.apply_current_run = original_apply
                self.module.agent_outputs.build_story_input = original_build_story_input
                self.module._run_round_deliver = original_delivery
            self.assertEqual(calls[:3], ["input_analyst", "apply", "gm"])
```

- [ ] **Step 2: Run test and verify failure**

Run: `python -m unittest tests.test_rp_generate_cli.RpGenerateCliTest.test_generate_turn_dispatches_input_analyst_before_gm -v`

Expected: failure because `input_analysis_apply` is not imported or input analyst is not dispatched.

- [ ] **Step 3: Import apply module and implement ensure function**

In `skills/rp_generate_cli.py`, add:

```python
import input_analysis
import input_analysis_apply
```

Add helper near `_read_prompt`:

```python
def _ensure_input_analysis(run_dir: Path, manifest: Dict[str, Any], card: Path, root: Path) -> Dict[str, Any]:
    expected = manifest.get("expected_outputs") or {}
    prompt_map = manifest.get("prompts") or {}
    output_rel = expected.get("input_analysis", "input_analysis.output.json")
    output_path = run_dir / output_rel
    if output_path.exists():
        data = agent_run.read_json(output_path)
        if isinstance(data, dict):
            input_analysis_apply.apply_current_run(card, root)
            return data
    prompt_rel = prompt_map.get("input_analyst")
    if not isinstance(prompt_rel, str) or not prompt_rel:
        raise AgentExecutionError("manifest.prompts.input_analyst is required.")
    prompt_text = (run_dir / prompt_rel).read_text(encoding="utf-8")
    analysis = _dispatch_and_write(
        "input_analyst",
        prompt_text,
        root,
        output_path,
        validator=None,
        extra_context={"required_output": output_rel},
    )
    input_analysis_apply.apply_current_run(card, root)
    return analysis
```

At the start of `generate_turn`, after manifest load:

```python
    _ensure_input_analysis(run_dir, manifest, card, root)
    manifest = _load_manifest(run_dir)
    expected = manifest.get("expected_outputs") or {}
```

- [ ] **Step 4: Run generate CLI test**

Run: `python -m unittest tests.test_rp_generate_cli.RpGenerateCliTest.test_generate_turn_dispatches_input_analyst_before_gm -v`

Expected: pass.

- [ ] **Step 5: Commit Task 4**

```powershell
git add skills/rp_generate_cli.py tests/test_rp_generate_cli.py
git commit -m "feat: 先执行输入分析再调度创作agent"
```

### Task 5: Remove High-Risk Keyword Persistence From Prepare

**Files:**
- Modify: `skills/round_prepare.py`
- Modify: `skills/hidden_settings.py`
- Modify: `skills/agent_packets.py`
- Test: `tests/test_agent_packets.py`

- [ ] **Step 1: Add regression tests for no pre-analysis persistence**

Add to `AgentPacketTest`:

```python
    def test_round_prepare_does_not_persist_hidden_settings_before_analysis_apply(self):
        temp_root, styles_dir = self._make_round_prepare_fixture()
        text = "用于长期剧情引导：吊坠是变身器。"
        styles_dir.joinpath("input.txt").write_text(text, encoding="utf-8")
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
        self.assertFalse((self.card / "memory" / "gm_only_hidden_truths.jsonl").exists())

    def test_round_prepare_does_not_promote_important_character_before_analysis_apply(self):
        temp_root, styles_dir = self._make_round_prepare_fixture()
        text = "设定重要角色：苏黎，真实身份是前魔法少女。"
        styles_dir.joinpath("input.txt").write_text(text, encoding="utf-8")
        (self.card / ".card_data.json").write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
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
        self.assertNotIn("character_orchestration", card_data)
```

- [ ] **Step 2: Run regression tests and verify failure**

Run:

```powershell
python -m unittest `
  tests.test_agent_packets.AgentPacketTest.test_round_prepare_does_not_persist_hidden_settings_before_analysis_apply `
  tests.test_agent_packets.AgentPacketTest.test_round_prepare_does_not_promote_important_character_before_analysis_apply -v
```

Expected: failure because current `round_prepare.py` persists these before AI analysis.

- [ ] **Step 3: Stop pre-analysis hidden persistence**

In `skills/round_prepare.py`, replace this block:

```python
    if hidden_instruction_text:
        try:
            hidden_settings.persist_hidden_setting(
                card_folder,
                hidden_instruction_text,
                source_input_id=latest_player_input.get("id", "") if isinstance(latest_player_input, dict) else "",
                round_id=f"round-{len(chat_log) + 1:06d}",
            )
        except Exception:
            pass
```

with:

```python
    # High-risk hidden setting persistence is handled after AI input analysis.
    # This prepare phase may route explicit channels, but it must not persist
    # hidden facts based on keywords before input_analysis.output.json exists.
```

- [ ] **Step 4: Stop pre-analysis important character promotion**

In `skills/round_prepare.py`, replace the block that calls `_extract_important_character_declarations`, builds `declared_major`, and calls `_persist_important_character_declarations` with:

```python
    # Important-character promotion is handled by input_analysis_apply.py after
    # a validated input_analysis.output.json declares the character.
    important_declarations = []
    declared_major = []
```

Keep helper functions for one commit if tests still import them, but do not call them from `main()`.

- [ ] **Step 5: Mark heuristic plan as fallback/debug**

Change the heading written to `round_context.txt` from:

```python
    dynamic_parts.append("\n=== PLAYER_INPUT_PROCESSING_PLAN (must follow before writing response.txt) ===")
```

to:

```python
    dynamic_parts.append("\n=== PLAYER_INPUT_HEURISTIC_FALLBACK (debug only; input_analysis.output.json is authoritative when present) ===")
```

This preserves debug visibility without making keyword classifications authoritative.

- [ ] **Step 6: Run regression tests**

Run the two tests from Step 2.

Expected: pass.

- [ ] **Step 7: Commit Task 5**

```powershell
git add skills/round_prepare.py tests/test_agent_packets.py
git commit -m "fix: 禁止prepare阶段关键词持久化玩家指令"
```

### Task 6: Smoke Fixture And Story Input Propagation

**Files:**
- Modify: `skills/control_plane_smoke.py`
- Modify: `skills/agent_outputs.py`
- Test: `tests/test_agent_packets.py`

- [ ] **Step 1: Add story input assertion**

In the existing smoke or agent output tests, assert `story.input.json` includes `input_analysis` under `player_inputs`:

```python
    def test_story_input_includes_input_analysis_metadata(self):
        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="I enter.",
            chat_log=[],
            card_data={"title": "Card"},
            character_contexts={"characters": []},
            turn_index=0,
            input_payload={"input_schema": "dual_channel_v1", "raw_text": "I enter.", "role_text": "I enter.", "user_instruction_text": ""},
        )
        run_dir = Path(result["run_dir"])
        analysis = {
            "schema_version": 1,
            "round_id": run_dir.name,
            "analysis_mode": "fixture",
            "source_integrity": {"raw_text_sha256": "", "role_text_sha256": "", "user_instruction_text_sha256": "", "raw_preserved": True},
            "semantic_units": [],
            "world_updates": {"hidden_facts": [], "public_facts": [], "important_characters": [], "retcon_requests": []},
            "narrative_directives": {"rewrite_previous_output": False, "expand_synopsis_before_continue": False, "continue_after_player_action": True, "must_stop_for_player_decision": False},
            "routing": {"role_channel": "I enter.", "user_instruction_channel": "", "gm": True, "player": True, "characters": []},
            "risks": [],
        }
        input_payload = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
        input_payload["input_analysis"] = analysis
        (run_dir / "input.json").write_text(json.dumps(input_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        story_input = self.agent_outputs.build_story_input(run_dir)
        self.assertEqual(story_input["player_inputs"]["input_analysis"]["analysis_mode"], "fixture")
```

- [ ] **Step 2: Modify story input assembly**

In `skills/agent_outputs.py`, update `build_story_input`:

```python
        "player_inputs": {
            "raw_text": input_payload.get("raw_text", ""),
            "routed_input": input_payload.get("routed_input", {}),
            "components": (input_payload.get("routed_input") or {}).get("components", []),
            "input_analysis": input_payload.get("input_analysis", {}),
        },
```

- [ ] **Step 3: Add smoke fixture analysis**

In `skills/control_plane_smoke.py`, after `prepare_agent_run`, write a fixture analysis and call apply:

```python
        import input_analysis
        import input_analysis_apply

        raw_text = input_payload["raw_text"]
        role_text = input_payload["role_text"]
        instruction_text = input_payload["user_instruction_text"]
        analysis = {
            "schema_version": 1,
            "round_id": run_dir.name,
            "analysis_mode": "fixture",
            "source_integrity": {
                "raw_text_sha256": input_analysis.sha256_text(raw_text),
                "role_text_sha256": input_analysis.sha256_text(role_text),
                "user_instruction_text_sha256": input_analysis.sha256_text(instruction_text),
                "raw_preserved": True,
            },
            "semantic_units": [
                {"id": "u1", "source_channel": "role_input", "type": "action", "raw_excerpt": role_text, "derived_summary": "", "confidence": 1.0, "visibility": "player_pov", "persist": False},
                {"id": "u2", "source_channel": "user_instruction", "type": "style_guidance", "raw_excerpt": instruction_text, "derived_summary": "", "confidence": 1.0, "visibility": "gm_only", "persist": False},
            ],
            "world_updates": {"hidden_facts": [], "public_facts": [], "important_characters": [], "retcon_requests": []},
            "narrative_directives": {"rewrite_previous_output": False, "expand_synopsis_before_continue": False, "continue_after_player_action": True, "must_stop_for_player_decision": False},
            "routing": {"role_channel": role_text, "user_instruction_channel": instruction_text, "gm": True, "player": True, "characters": []},
            "risks": [],
        }
        _write_json(run_dir / "input_analysis.output.json", analysis)
        input_analysis_apply.apply_current_run(card, repo)
```

- [ ] **Step 4: Run smoke and targeted tests**

Run:

```powershell
python -m unittest tests.test_agent_packets.AgentPacketTest.test_story_input_includes_input_analysis_metadata -v
python skills/control_plane_smoke.py --repo .
```

Expected: both pass and smoke returns `"ok": true`.

- [ ] **Step 5: Commit Task 6**

```powershell
git add skills/control_plane_smoke.py skills/agent_outputs.py tests/test_agent_packets.py
git commit -m "test: 覆盖输入分析控制面smoke"
```

### Task 7: Final Verification And Documentation Alignment

**Files:**
- Modify: `.claude/skills/rp-orchestrator.md`
- Modify: `.claude/commands/rp.md`
- Modify: `AGENTS.md` if command list changes during implementation

- [ ] **Step 1: Update orchestrator workflow text**

In `.claude/skills/rp-orchestrator.md`, update the turn workflow so it says:

```markdown
1. Run `python "{ROOT}/skills/round_prepare.py" "<card_folder>" "{ROOT}"`.
2. Dispatch `rp-input-analyst` using `.agent_runs/<round>/prompts/input_analyst.prompt.md`.
3. Validate and apply `input_analysis.output.json` with `python "{ROOT}/skills/input_analysis_apply.py" "<card_folder>" "{ROOT}"`.
4. Dispatch GM/player/character agents from the rebuilt prompts and packets.
5. Build `story.input.json`, dispatch story and critic, then deliver through `round_deliver.py`.
```

- [ ] **Step 2: Update `/rp` fallback notes**

In `.claude/commands/rp.md`, replace the fallback instruction:

```markdown
- For pending player input in the fallback path, run `python "{ROOT}/skills/round_prepare.py" "<card_folder>" "{ROOT}"`, then run `python "{ROOT}/skills/rp_generate_cli.py" "<card_folder>" "{ROOT}"`.
```

with:

```markdown
- For pending player input in the fallback path, run `python "{ROOT}/skills/round_prepare.py" "<card_folder>" "{ROOT}"`, then run `python "{ROOT}/skills/rp_generate_cli.py" "<card_folder>" "{ROOT}"`. The generate CLI must dispatch input analyst and apply `input_analysis.output.json` before any creative agents.
```

- [ ] **Step 3: Run full automated verification**

Run:

```powershell
python -m unittest discover -s tests -v
python skills/control_plane_smoke.py --repo .
python -m py_compile skills/input_analysis.py skills/input_analysis_apply.py skills/character_registry.py skills/agent_packets.py skills/agent_prompts.py skills/round_prepare.py skills/rp_generate_cli.py skills/control_plane_smoke.py
```

Expected: unittest reports all tests OK, smoke returns `"ok": true`, and py_compile prints no errors.

- [ ] **Step 4: Inspect git status before final commit**

Run: `git status --short`

Expected: only files touched by this plan are modified. If unrelated files such as `package.json` or local card images appear, leave them unstaged.

- [ ] **Step 5: Commit Task 7**

```powershell
git add .claude/skills/rp-orchestrator.md .claude/commands/rp.md AGENTS.md
git commit -m "docs: 对齐AI输入分析工作流"
```

- [ ] **Step 6: Final manual acceptance**

Run one no-material pending-input turn with a mixed first-person action and hidden setting. Confirm these artifacts exist in `.agent_runs/current`:

```text
input.raw.json
input_analysis.request.md
input_analysis.output.json
input.json
gm.context.json
player.context.json
story.input.json
critic.report.json
```

Confirm manually:

- `input.json.raw_text` exactly equals the player's raw submission.
- `input.json.input_analysis.analysis_mode` is `ai` during live Claude Code play and `fixture` only in deterministic tests.
- `player.context.json` and `characters/*.context.json` do not contain GM-only hidden facts.
- Important characters declared through AI analysis appear under `.card_data.json.character_orchestration.major`.
- A mixed synopsis/action input leads story to expand the synopsis before continuing.

## Self-Review Notes

- Spec coverage: schema, analyst prompt, explicit dual-channel priority, raw input immutability, hidden setting isolation, important character promotion, deterministic fixture, and failure handling are each covered by tasks.
- Scope boundary: this plan does not redesign frontend controls or introduce direct backend model calls.
- Risk boundary: fallback analysis is deliberately low-risk and cannot persist hidden facts, retcon requests, or important characters.
- Existing dirty worktree: execution must avoid staging unrelated `package.json`, local images, or pre-existing changes unless they are part of the active implementation.
