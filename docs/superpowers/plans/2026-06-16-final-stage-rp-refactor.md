# Final-Stage RP Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the Claude Code direct-driven multi-agent RP loop so every turn can be orchestrated, audited, repaired, smoke-tested, and manually accepted without replacing Claude Code's agent orchestration model.

**Architecture:** Keep Python as the file-protocol control plane and Claude Code as the live orchestrator. Add a small workflow advisor, stricter prompt/schema alignment, bounded repair audits, and a no-live-model smoke command around the existing `.agent_runs/<round>/` mailbox. Do not introduce a backend LLM API scheduler.

**Tech Stack:** Python standard library, `unittest`, JSON file artifacts, Claude Code `.claude/skills/*.md`, existing browser bridge under `skills/styles/`.

---

## Final Scope

This final stage builds on the completed control-plane round:

- Already present: dual input channels, raw player-input authority, player/character context isolation, `interaction.trace.json`, scheduled actor memory summaries, critic repair history, improvement queue, and E2E fixture coverage.
- Remaining gap: the workflow still depends on a human/main agent reading scattered files and remembering what to do next. The final phase makes the turn state self-explanatory, aligns prompt contracts with validators, bounds failed repair loops, and provides a deterministic smoke command before live Claude Code testing.

## File Structure

- Create `skills/agent_workflow.py`: read `manifest.json` and expected artifacts; return deterministic next-action guidance for Claude Code.
- Create `tests/test_agent_workflow.py`: cover missing artifacts, ready-to-build story input, revise/block states, and delivered state.
- Modify `skills/round_prepare.py`: append workflow guidance to `skills/styles/round_context.txt` after an agent run is created.
- Modify `skills/agent_prompts.py`: align materialized prompt contracts with `agent_schemas.py` and include interaction-trace handling instructions.
- Modify `.claude/skills/rp-orchestrator.md`, `rp-gm-agent.md`, `rp-player-agent.md`, `rp-character-agent.md`, `rp-story-agent.md`, `rp-critic-agent.md`: remove schema drift and make the interaction loop operational.
- Modify `skills/agent_outputs.py`: add bounded repair-loop metadata and a terminal blocked result after repeated identical failures.
- Modify `tests/test_agent_outputs.py`: cover retry cap behavior and no duplicate queue writes.
- Create `skills/control_plane_smoke.py`: run a complete no-live-model fixture round in a temporary card folder and print JSON evidence.
- Create `tests/test_control_plane_smoke.py`: verify the smoke command exits successfully and reports delivery, trace, repair, and memory checks.
- Modify `README.md`, `CLAUDE.md`, `AGENTS.md`: document the final workflow and smoke/manual acceptance commands.

---

## Task 1: Workflow Advisor

**Files:**
- Create: `skills/agent_workflow.py`
- Create: `tests/test_agent_workflow.py`

- [ ] **Step 1: Write failing tests for next-action guidance**

Add `tests/test_agent_workflow.py`:

```python
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_workflow():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_workflow", ROOT / "skills" / "agent_workflow.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class AgentWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "round-000001"
        self.run_dir.mkdir()
        self.agent_workflow = _load_agent_workflow()
        _write_json(
            self.run_dir / "manifest.json",
            {
                "round_id": "round-000001",
                "stage": "awaiting_agent_outputs",
                "expected_outputs": {
                    "gm": "gm.output.json",
                    "player": "player.output.json",
                    "characters": {"Ada": "characters/Ada.output.json"},
                    "story": "story.output.json",
                    "critic": "critic.report.json",
                },
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_reports_missing_required_agent_outputs(self):
        advice = self.agent_workflow.advise_next_actions(self.run_dir)
        self.assertEqual(advice["stage"], "awaiting_agent_outputs")
        self.assertEqual(advice["next_action"], "dispatch_agent_outputs")
        self.assertEqual(
            sorted(item["path"] for item in advice["missing_required"]),
            ["characters/Ada.output.json", "gm.output.json", "player.output.json"],
        )

    def test_reports_build_story_input_when_actor_outputs_exist(self):
        for rel in ["gm.output.json", "player.output.json", "characters/Ada.output.json"]:
            _write_json(self.run_dir / rel, {"agent": "fixture"})
        advice = self.agent_workflow.advise_next_actions(self.run_dir)
        self.assertEqual(advice["next_action"], "build_story_input")
        self.assertEqual(advice["missing_required"], [])

    def test_reports_story_and_critic_work_after_story_ready(self):
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["stage"] = "story_ready"
        _write_json(self.run_dir / "manifest.json", manifest)
        advice = self.agent_workflow.advise_next_actions(self.run_dir)
        self.assertEqual(advice["next_action"], "dispatch_story_and_critic")
        self.assertEqual(
            sorted(item["path"] for item in advice["missing_required"]),
            ["critic.report.json", "story.output.json"],
        )

    def test_reports_repair_when_blocked(self):
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["stage"] = "blocked"
        manifest["retry_count"] = 1
        _write_json(self.run_dir / "manifest.json", manifest)
        _write_json(
            self.run_dir / "critic.report.json",
            {"decision": "revise", "hard_failures": [], "soft_issues": ["weak handoff"], "repair_instruction": "Sharpen the stop point."},
        )
        advice = self.agent_workflow.advise_next_actions(self.run_dir)
        self.assertEqual(advice["next_action"], "repair_from_critic")
        self.assertEqual(advice["critic_decision"], "revise")
        self.assertEqual(advice["retry_count"], 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_workflow -v
```

Expected: import failure for missing `skills/agent_workflow.py`.

- [ ] **Step 3: Implement the workflow advisor**

Create `skills/agent_workflow.py`:

```python
"""Deterministic next-action guidance for a Claude Code RP agent run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _artifact(path: str, required: bool = True) -> Dict[str, Any]:
    return {"path": path, "required": required}


def _expected_agent_outputs(expected: Dict[str, Any]) -> list[Dict[str, Any]]:
    items = []
    if expected.get("gm"):
        items.append(_artifact(str(expected["gm"])))
    if expected.get("player"):
        items.append(_artifact(str(expected["player"])))
    characters = expected.get("characters", {})
    if isinstance(characters, dict):
        for rel in characters.values():
            items.append(_artifact(str(rel)))
    return items


def _expected_delivery_outputs(expected: Dict[str, Any]) -> list[Dict[str, Any]]:
    items = []
    if expected.get("story"):
        items.append(_artifact(str(expected["story"])))
    if expected.get("critic"):
        items.append(_artifact(str(expected["critic"])))
    return items


def _missing(run_dir: Path, artifacts: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    missing = []
    for item in artifacts:
        rel = item["path"]
        if not (run_dir / rel).exists():
            missing.append(item)
    return missing


def advise_next_actions(run_dir: str | Path) -> Dict[str, Any]:
    root = Path(run_dir)
    manifest = _read_json(root / "manifest.json", {})
    if not isinstance(manifest, dict) or not manifest:
        return {
            "ok": False,
            "stage": "missing_manifest",
            "next_action": "create_agent_run",
            "missing_required": [{"path": "manifest.json", "required": True}],
        }

    expected = manifest.get("expected_outputs", {})
    if not isinstance(expected, dict):
        expected = {}

    stage = str(manifest.get("stage") or "")
    retry_count = int(manifest.get("retry_count", 0) or 0)
    agent_missing = _missing(root, _expected_agent_outputs(expected))
    delivery_missing = _missing(root, _expected_delivery_outputs(expected))
    critic_report = _read_json(root / str(expected.get("critic", "critic.report.json")), {})
    critic_decision = critic_report.get("decision", "") if isinstance(critic_report, dict) else ""

    if stage == "delivered":
        next_action = "none"
    elif stage in {"blocked"} and critic_decision in {"revise", "block"}:
        next_action = "repair_from_critic"
    elif agent_missing:
        next_action = "dispatch_agent_outputs"
    elif not (root / "story.input.json").exists():
        next_action = "build_story_input"
    elif delivery_missing:
        next_action = "dispatch_story_and_critic"
    else:
        next_action = "run_delivery_gate"

    return {
        "ok": True,
        "round_id": manifest.get("round_id", root.name),
        "stage": stage,
        "next_action": next_action,
        "missing_required": agent_missing if agent_missing else delivery_missing,
        "retry_count": retry_count,
        "critic_decision": critic_decision,
        "run_dir": str(root.resolve()),
    }
```

- [ ] **Step 4: Run targeted tests**

Run:

```powershell
python -m unittest tests.test_agent_workflow -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add skills/agent_workflow.py tests/test_agent_workflow.py
git commit -m "feat: 增加agent回合编排建议器"
```

---

## Task 2: Surface Workflow Guidance In Round Context

**Files:**
- Modify: `skills/round_prepare.py`
- Modify: `tests/test_agent_packets.py`

- [ ] **Step 1: Write failing test for round context guidance**

Append this assertion to `test_round_prepare_writes_agent_run_packets_and_reports_path` in `tests/test_agent_packets.py`:

```python
        context_text = round_context_path.read_text(encoding="utf-8")
        self.assertIn("=== AGENT_WORKFLOW ===", context_text)
        self.assertIn("dispatch_agent_outputs", context_text)
```

- [ ] **Step 2: Run the targeted test to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_packets.AgentPacketTest.test_round_prepare_writes_agent_run_packets_and_reports_path -v
```

Expected: failure because `AGENT_WORKFLOW` is not yet appended.

- [ ] **Step 3: Import and append workflow advice in `round_prepare.py`**

Add the import near the existing agent imports:

```python
import agent_workflow
```

After `prepare_agent_run(...)` returns and before writing `round_context.txt`, build this text:

```python
workflow_advice = None
if agent_run_result and agent_run_result.get("run_dir"):
    workflow_advice = agent_workflow.advise_next_actions(agent_run_result["run_dir"])
```

Append the section when composing `round_context.txt`:

```python
if workflow_advice:
    sections.append("=== AGENT_WORKFLOW ===")
    sections.append(json.dumps(workflow_advice, ensure_ascii=False, indent=2))
```

- [ ] **Step 4: Run targeted tests**

Run:

```powershell
python -m unittest tests.test_agent_packets.AgentPacketTest.test_round_prepare_writes_agent_run_packets_and_reports_path -v
python -m unittest tests.test_agent_workflow -v
```

Expected: both pass.

- [ ] **Step 5: Commit**

```powershell
git add skills/round_prepare.py tests/test_agent_packets.py
git commit -m "feat: 在回合上下文中暴露agent编排建议"
```

---

## Task 3: Align Prompt Contracts With Validators

**Files:**
- Modify: `skills/agent_prompts.py`
- Modify: `.claude/skills/rp-gm-agent.md`
- Modify: `.claude/skills/rp-player-agent.md`
- Modify: `.claude/skills/rp-character-agent.md`
- Modify: `.claude/skills/rp-story-agent.md`
- Modify: `.claude/skills/rp-critic-agent.md`
- Modify: `tests/test_agent_packets.py`

- [ ] **Step 1: Write failing prompt-contract assertions**

In `test_prepare_agent_run_writes_prompts_and_manifest`, add:

```python
        self.assertIn('"narration"', gm_prompt)
        self.assertIn('"world_state_delta"', gm_prompt)
        self.assertIn('"action"', player_prompt)
        self.assertIn('"perception"', player_prompt)
        self.assertIn('"agent_id"', char_prompt)
        self.assertIn('"decision"', critic_prompt)
        self.assertNotIn('"scene_state"', gm_prompt)
        self.assertNotIn('"embodied_intent"', player_prompt)
```

- [ ] **Step 2: Run targeted test to expose drift**

Run:

```powershell
python -m unittest tests.test_agent_packets.AgentPacketTest.test_prepare_agent_run_writes_prompts_and_manifest -v
```

Expected: failure if any materialized contract or skill body still advertises incompatible required keys without the validated keys.

- [ ] **Step 3: Update `agent_prompts.py` contracts**

Use these contracts in the prompt helper functions:

```python
def _gm_prompt(context: Dict[str, Any]) -> str:
    contract = _json_block({
        "agent": "gm",
        "narration": "brief world narration visible to story agent",
        "npc_events": [],
        "world_state_delta": [],
        "handoff": {
            "decision_point": "",
            "stop_reason": "decision_point|word_target|continue",
        },
    })
    return _base_prompt("GM Agent Prompt", "gm", "gm.output.json", contract, context)


def _player_prompt(context: Dict[str, Any]) -> str:
    contract = _json_block({
        "agent": "player",
        "agent_id": "player",
        "action": "first-person action already authorized by role_channel",
        "dialogue": [],
        "perception": [],
        "memory_delta": [],
    })
    return _base_prompt("Player Agent Prompt", "player", "player.output.json", contract, context)


def _character_prompt(context: Dict[str, Any], output_path: str) -> str:
    contract = _json_block({
        "agent": "character",
        "agent_id": "character:<safe_name>",
        "character_name": context.get("character_name", ""),
        "action": "first-person action",
        "dialogue": [],
        "perception": [],
        "memory_delta": [],
    })
    return _base_prompt(f"Character Agent Prompt: {context.get('character_name', '')}", "character", output_path, contract, context)
```

- [ ] **Step 4: Update skill files to match the same JSON contracts**

In each `.claude/skills/rp-*-agent.md`, keep the prose responsibilities, but ensure the "Output Schema" section uses the validated keys above. For GM, replace `scene_state`, `world_updates`, and `stop_reason` top-level requirements with:

```json
{
  "agent": "gm",
  "narration": "...",
  "npc_events": [],
  "world_state_delta": [],
  "handoff": {
    "decision_point": "...",
    "stop_reason": "decision_point|word_target|continue"
  }
}
```

For player/character, use `action`, `dialogue`, `perception`, and `memory_delta` as required keys. Keep richer fields only as optional nested data under `perception`, `dialogue`, or `memory_delta` when needed.

- [ ] **Step 5: Run prompt and schema tests**

Run:

```powershell
python -m unittest tests.test_agent_packets tests.test_agent_schemas -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```powershell
git add skills/agent_prompts.py .claude/skills/rp-gm-agent.md .claude/skills/rp-player-agent.md .claude/skills/rp-character-agent.md .claude/skills/rp-story-agent.md .claude/skills/rp-critic-agent.md tests/test_agent_packets.py
git commit -m "fix: 对齐subagent提示词与产物schema"
```

---

## Task 4: Bound Critic Repair Loops

**Files:**
- Modify: `skills/agent_outputs.py`
- Modify: `tests/test_agent_outputs.py`

- [ ] **Step 1: Write failing test for retry limit**

Add to `tests/test_agent_outputs.py`:

```python
    def test_prepare_delivery_returns_terminal_block_after_retry_limit(self):
        self._write_story_and_critic(
            decision="block",
            system_iteration_suggestion="Tighten prompt isolation checks.",
        )
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["retry_count"] = 2
        _write_json(self.run_dir / "manifest.json", manifest)

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "blocked")
        self.assertEqual(result["reason"], "critic_retry_limit")
        self.assertFalse((self.styles_dir / "response.txt").exists())
        final_manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(final_manifest["stage"], "blocked")
        self.assertEqual(final_manifest["retry_count"], 2)
```

- [ ] **Step 2: Run targeted test to verify failure**

Run:

```powershell
python -m unittest tests.test_agent_outputs.AgentOutputsTest.test_prepare_delivery_returns_terminal_block_after_retry_limit -v
```

Expected: failure because delivery currently keeps returning retry.

- [ ] **Step 3: Add retry limit handling**

In `skills/agent_outputs.py`, add:

```python
MAX_CRITIC_RETRIES = 2


def _blocked_result(reason: str, message: str, detail: Any = None) -> Dict[str, Any]:
    result = {
        "ok": False,
        "action": "blocked",
        "reason": reason,
        "message": message,
    }
    if detail is not None:
        result["detail"] = detail
    return result
```

Then before incrementing retry for `block` or `revise`, check:

```python
if int(manifest.get("retry_count", 0) or 0) >= MAX_CRITIC_RETRIES:
    _record_critic_repair(card_folder, run_dir, manifest, critic_report)
    _mark_blocked_without_retry(run_dir, manifest)
    return _blocked_result("critic_retry_limit", "Critic retry limit reached.", critic_report)
```

- [ ] **Step 4: Run delivery tests**

Run:

```powershell
python -m unittest tests.test_agent_outputs -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add skills/agent_outputs.py tests/test_agent_outputs.py
git commit -m "fix: 限制critic修复循环次数"
```

---

## Task 5: No-Live-Model Control-Plane Smoke Command

**Files:**
- Create: `skills/control_plane_smoke.py`
- Create: `tests/test_control_plane_smoke.py`

- [ ] **Step 1: Write smoke command test**

Create `tests/test_control_plane_smoke.py`:

```python
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ControlPlaneSmokeTest(unittest.TestCase):
    def test_smoke_command_completes_fixture_round(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "skills" / "control_plane_smoke.py"), "--repo", str(ROOT)],
            text=True,
            capture_output=True,
            check=True,
        )
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["delivery"]["mode"], "agent_run")
        self.assertEqual(payload["manifest_stage"], "delivered")
        self.assertEqual(payload["trace"]["private_event_count"], 1)
        self.assertIn("player", payload["memory_summary"]["ingested"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
python -m unittest tests.test_control_plane_smoke -v
```

Expected: failure because the smoke command does not exist.

- [ ] **Step 3: Implement `skills/control_plane_smoke.py`**

Create the command with this structure:

```python
"""Run a deterministic no-live-model RP control-plane smoke test."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_interactions
import agent_memory
import agent_outputs
import agent_packets


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run_smoke(repo: Path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        card = Path(tmp) / "card"
        styles = Path(tmp) / "root" / "skills" / "styles"
        card.mkdir()
        styles.mkdir(parents=True)
        payload = {
            "input_schema": "dual_channel_v1",
            "raw_text": "I open the archive door.\n\n[USER_INSTRUCTION]\nThe archive hides a moon base.",
            "display_text": "I open the archive door.",
            "role_text": "I open the archive door.",
            "user_instruction_text": "The archive hides a moon base.",
        }
        result = agent_packets.prepare_agent_run(
            card,
            user_text="legacy fallback",
            chat_log=[],
            card_data={"title": "Smoke"},
            character_contexts={"characters": [{"name": "Ada"}]},
            turn_index=5,
            input_payload=payload,
        )
        run_dir = Path(result["run_dir"])
        agent_interactions.init_trace(run_dir, ["gm", "player", "character:Ada"], chapter_target_words=900)
        agent_interactions.append_event(run_dir, "character:Ada", "world_visible", "dialogue", "Stay close.")
        agent_interactions.append_event(run_dir, "gm", "private", "note", "The moon base truth remains hidden.")
        agent_interactions.mark_decision_point(run_dir, "player must choose whether to enter", ["enter", "wait"])
        _write_json(run_dir / "gm.output.json", {"agent": "gm", "narration": "Cold air leaks from the archive.", "npc_events": [], "world_state_delta": [{"scope": "hidden", "fact": "archive hides a moon base"}], "handoff": {"decision_point": "enter or wait"}})
        _write_json(run_dir / "player.output.json", {"agent": "player", "agent_id": "player", "action": "I keep my hand on the doorframe.", "dialogue": [], "perception": ["I hear machinery."], "memory_delta": [{"text": "I heard machinery behind the archive door.", "source": "perceived"}]})
        _write_json(run_dir / "characters" / "Ada.output.json", {"agent": "character", "agent_id": "character:Ada", "character_name": "Ada", "action": "I lift the lamp.", "dialogue": [{"target": "player", "text": "Stay close."}], "perception": ["I see the player hesitate."], "memory_delta": [{"text": "I saw the player hesitate at the archive door.", "source": "perceived"}]})
        _write_json(run_dir / "story.output.json", {"content": "<content>Cold air leaked from the archive. Ada lifted the lamp. \"Stay close,\" she said.</content><summary>The archive door is open.</summary><options><font color=\"#5a8a9a\">wait</font></options>", "character_dialogues": [{"character": "Ada", "text": "Stay close.", "source_agent": "character:Ada"}], "metadata": {"round_id": run_dir.name}})
        _write_json(run_dir / "critic.report.json", {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": "", "system_iteration_suggestion": ""})
        delivery = agent_outputs.prepare_delivery(card, styles)
        for agent_id, rel in (json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))["expected_outputs"].get("memory_summaries") or {}).items():
            payload = {"agent_id": agent_id, "summary": "I remember the archive threshold.", "retained_goals": ["Choose whether to enter."], "forgotten_noise": [], "source": "self", "visibility": "actor"}
            if agent_id.startswith("character:"):
                payload["character_name"] = agent_id.split(":", 1)[1]
            _write_json(run_dir / rel, payload)
        memory_delta = agent_memory.ingest_memory_deltas(card, run_dir, date_str="2026-06-16 12:00")
        memory_summary = agent_memory.ingest_memory_summaries(card, run_dir)
        agent_outputs.mark_delivered(card)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        story_input = json.loads((run_dir / "story.input.json").read_text(encoding="utf-8"))
        return {
            "ok": bool(delivery.get("ok") and memory_delta.get("ok") and memory_summary.get("ok")),
            "delivery": {"mode": delivery.get("mode"), "run_dir": delivery.get("run_dir")},
            "manifest_stage": manifest.get("stage"),
            "trace": story_input.get("interaction_trace", {}),
            "memory_delta": memory_delta,
            "memory_summary": memory_summary,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    payload = run_smoke(Path(args.repo))
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run smoke test and command**

Run:

```powershell
python -m unittest tests.test_control_plane_smoke -v
python skills/control_plane_smoke.py --repo .
```

Expected: both succeed; command prints JSON with `"ok": true`.

- [ ] **Step 5: Commit**

```powershell
git add skills/control_plane_smoke.py tests/test_control_plane_smoke.py
git commit -m "test: 增加多代理控制面smoke命令"
```

---

## Task 6: Final Documentation And Acceptance Checklist

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Update command documentation**

Add the smoke command to the development command lists:

```markdown
- `python skills/control_plane_smoke.py --repo .` runs a deterministic no-live-model multi-agent control-plane smoke test.
```

- [ ] **Step 2: Document final live acceptance**

Add a concise final acceptance section:

```markdown
## Final Acceptance

Before pushing a final RP refactor:

1. Run `python -m unittest discover -s tests -v`.
2. Run `python skills/control_plane_smoke.py --repo .`.
3. Run `python -m py_compile skills/agent_workflow.py skills/control_plane_smoke.py skills/agent_outputs.py skills/agent_prompts.py skills/round_prepare.py`.
4. Start `python skills/start_server.py .` and verify `http://localhost:8765`.
5. From a phone or other LAN device, verify the printed `http://<LAN-IP>:8765` URL.
6. In Claude Code, run `/rp` against a blank folder and complete at least five player turns. Confirm immediate player-input display, independent important-character dialogue boxes, progress updates, hot UI/image refresh, and correct stopping at player decisions.
```

- [ ] **Step 3: Run markdown diff check**

Run:

```powershell
git diff --check README.md CLAUDE.md AGENTS.md
```

Expected: no whitespace errors.

- [ ] **Step 4: Commit docs**

```powershell
git add README.md CLAUDE.md AGENTS.md
git commit -m "docs: 补充最终验收流程"
```

---

## Task 7: Final Verification

**Files:**
- Verify all changed files from Tasks 1-6.

- [ ] **Step 1: Run full Python tests**

```powershell
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Run compile checks**

```powershell
python -m py_compile skills/agent_workflow.py skills/control_plane_smoke.py skills/agent_outputs.py skills/agent_prompts.py skills/round_prepare.py
```

Expected: no output and exit code 0.

- [ ] **Step 3: Run smoke command**

```powershell
python skills/control_plane_smoke.py --repo .
```

Expected: JSON output contains `"ok": true`, `"manifest_stage": "delivered"`, and `"private_event_count": 1`.

- [ ] **Step 4: Inspect status**

```powershell
git status --short --branch
```

Expected: only intentional files are changed. Leave unrelated root files such as `魔法禁书目录.png` untouched unless the user explicitly asks.

- [ ] **Step 5: Commit final verification if any files changed**

If final verification required doc or code adjustments:

```powershell
git add <changed-files>
git commit -m "fix: 完成最终阶段验证修正"
```

---

## Manual Live Acceptance

Run this only after the deterministic checks pass:

1. Create an empty temporary card folder outside source-controlled runtime artifacts.
2. Start Claude Code in that folder and run `/rp`.
3. Open `http://localhost:8765`, then open the printed LAN URL from a mobile device.
4. Submit five turns using both channels:
   - role channel: first-person action.
   - instruction channel: omniscient setting or direct Claude Code instruction.
5. Verify:
   - player input appears immediately and exactly.
   - instruction-only input does not create a fake visible player utterance.
   - player/character prompts do not contain hidden instruction text.
   - important character subagent dialogue appears in independent dialogue boxes.
   - progress updates while waiting.
   - text delivery does not wait for optional image/UI work.
   - image/UI hot refresh works without browser reload when generated assets are available.
   - story stops at real player decision points.

Record the exact command outputs and browser observations in the final response before pushing.
