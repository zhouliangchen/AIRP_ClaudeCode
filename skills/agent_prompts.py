"""Prompt materialization for Claude Code RP subagents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import agent_memory
import agent_run


REPO_ROOT = Path(__file__).resolve().parents[1]

SKILL_PATHS = {
    "input_analyst": ".claude/skills/rp-input-analyst.md",
    "gm": ".claude/skills/rp-gm-agent.md",
    "player": ".claude/skills/rp-player-agent.md",
    "character": ".claude/skills/rp-character-agent.md",
    "story": ".claude/skills/rp-story-agent.md",
    "critic": ".claude/skills/rp-critic-agent.md",
}

AUTHORITATIVE_CONTRACT_SKILLS = {"gm", "player", "character"}


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _json_block(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _strip_embedded_output_schema(text: str) -> str:
    marker = "\n## Output Schema"
    if marker not in text:
        return text
    before, after = text.split(marker, 1)
    next_heading = after.find("\n## ", 1)
    tail = after[next_heading:] if next_heading != -1 else ""
    return (
        before.rstrip()
        + "\n\n## Output Schema\n\n"
        + "Use the generated `Required Output Contract` above as the only JSON schema for this run.\n"
        + tail
    )


def _skill_excerpt(skill_key: str, limit: int = 6000) -> str:
    relative = SKILL_PATHS[skill_key]
    path = REPO_ROOT / relative
    if not path.exists():
        return f"(missing skill file: {relative})"
    text = path.read_text(encoding="utf-8")
    if skill_key in AUTHORITATIVE_CONTRACT_SKILLS:
        text = _strip_embedded_output_schema(text)
    return text[:limit]


def _write_prompt(path: Path, body: str) -> None:
    agent_run.write_text(path, body.strip() + "\n")


def _base_prompt(title: str, skill_key: str, output_path: str, contract: str, context: Dict[str, Any]) -> str:
    skill_path = SKILL_PATHS[skill_key]
    return f"""
# {title}

Skill reference: `{skill_path}`

## Operating Rule

You are a Claude Code subagent working through the file mailbox for this RP round.
Use only the allowed context below and write the required JSON artifact to `{output_path}`.
Do not write final prose unless this is the story agent.

## Required Output Contract

```json
{contract}
```

## Context Packet

```json
{_json_block(context)}
```

## Skill Body

```markdown
{_skill_excerpt(skill_key)}
```
"""


def _input_analyst_prompt(context: Dict[str, Any]) -> str:
    context = context if isinstance(context, dict) else {}
    source_integrity = context.get("source_integrity", {})
    source_integrity = source_integrity if isinstance(source_integrity, dict) else {}
    contract = _json_block({
        "schema_version": 1,
        "round_id": context.get("round_id", ""),
        "analysis_mode": "ai",
        "source_integrity": {
            "raw_text_sha256": source_integrity.get("raw_text_sha256", ""),
            "role_text_sha256": source_integrity.get("role_text_sha256", ""),
            "user_instruction_text_sha256": source_integrity.get("user_instruction_text_sha256", ""),
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


def _gm_prompt(context: Dict[str, Any]) -> str:
    contract = _json_block({
        "agent": "gm",
        "scene_beats": [{"content": "brief visible scene beat", "metadata": {}}],
        "events": [{"type": "world_event", "target": "", "content": "visible or routed event", "metadata": {}}],
        "actor_calls": [
            {
                "call_id": "call-1",
                "actor_id": "character:Example",
                "prompt": "second-person visible prompt for this actor only",
                "reason": "why this actor is needed now",
                "metadata": {},
            }
        ],
        "parallel_groups": [],
        "world_state_delta": [],
        "decision_point": None,
        "stop_reason": "continue",
    })
    return _base_prompt(
        "GM Agent Prompt",
        "gm",
        "gm.output.json",
        contract,
        context,
    ) + "\n\nAllowed `stop_reason` values: `continue`, `player_decision`, `word_target`, `complete`, `max_steps`.\n"


def _player_prompt(context: Dict[str, Any]) -> str:
    contract = _json_block({
        "agent": "player",
        "agent_id": "player",
        "events": [
            {
                "type": "wait_for_gm",
                "target": "",
                "content": "first-person event content",
                "metadata": {},
            }
        ],
        "stop_reason": "continue",
    })
    return _base_prompt(
        "Player Agent Prompt",
        "player",
        "player.output.json",
        contract,
        context,
    ) + "\n\nAllowed `stop_reason` values: `continue`, `stop_for_player_decision`.\n"


def _character_prompt(context: Dict[str, Any], output_path: str) -> str:
    self_knowledge = context.get("self_knowledge", {}) if isinstance(context, dict) else {}
    if not isinstance(self_knowledge, dict):
        self_knowledge = {}
    actor_id = context.get("actor_id") or "character:unknown"
    character_name = context.get("character_name") or self_knowledge.get("name", "")
    contract = _json_block({
        "agent": "character",
        "agent_id": actor_id,
        "character_name": character_name,
        "events": [
            {
                "type": "wait_for_gm",
                "target": "",
                "content": "first-person event content",
                "metadata": {},
            }
        ],
        "stop_reason": "continue",
    })
    return _base_prompt(
        f"Character Agent Prompt: {character_name}",
        "character",
        output_path,
        contract,
        context,
    ) + "\n\nAllowed `stop_reason` values: `continue`, `stop_for_player_decision`.\n"


def _story_prompt(run_summary: Dict[str, Any]) -> str:
    contract = _json_block({
        "content": "final prose to deliver",
        "character_dialogues": [],
        "metadata": {},
    })
    return _base_prompt(
        "Story Agent Prompt",
        "story",
        "story.output.json",
        contract,
        run_summary,
    ) + "\n\nRead `story.input.json.interaction_trace` when present. Preserve `visible_events`; do not use private trace content directly.\n"


def _critic_prompt(run_summary: Dict[str, Any]) -> str:
    contract = _json_block({
        "decision": "pass",
        "hard_failures": [],
        "soft_issues": [],
        "repair_instruction": "",
        "system_iteration_suggestion": "",
    })
    return _base_prompt(
        "Critic Agent Prompt",
        "critic",
        "critic.report.json",
        contract,
        run_summary,
    ) + "\n\nRead `story.input.json.interaction_trace` when present. Preserve `visible_events`; do not use private trace content directly.\n"


def write_round_prompts(
    run_dir: str | Path,
    gm_packet: Dict[str, Any],
    player_packet: Dict[str, Any],
    character_packets: Dict[str, Dict[str, Any]],
    card_folder: str | Path | None = None,
    input_analysis_request: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Write prompt files and return the round manifest."""
    root = Path(run_dir)
    prompt_root = root / "prompts"
    characters_prompt_root = prompt_root / "characters"

    if input_analysis_request is None:
        input_request = gm_packet.get("input_analysis_request", {}) if isinstance(gm_packet, dict) else {}
    else:
        input_request = input_analysis_request
    input_analyst_prompt = prompt_root / "input_analyst.prompt.md"
    gm_prompt = prompt_root / "gm.prompt.md"
    player_prompt = prompt_root / "player.prompt.md"
    story_prompt = prompt_root / "story.prompt.md"
    critic_prompt = prompt_root / "critic.prompt.md"

    character_prompts: Dict[str, str] = {}
    character_outputs: Dict[str, str] = {}

    _write_prompt(input_analyst_prompt, _input_analyst_prompt(input_request))
    _write_prompt(gm_prompt, _gm_prompt(gm_packet))
    _write_prompt(player_prompt, _player_prompt(player_packet))

    for safe_name, packet in character_packets.items():
        output_path = f"characters/{safe_name}.output.json"
        prompt_path = characters_prompt_root / f"{safe_name}.prompt.md"
        _write_prompt(prompt_path, _character_prompt(packet, output_path))
        character_prompts[safe_name] = _rel(prompt_path, root)
        character_outputs[safe_name] = output_path

    run_summary = {
        "run_dir": str(root.resolve()),
        "inputs": {
            "gm": "gm.output.json",
            "player": "player.output.json",
            "characters": character_outputs,
        },
        "story_input": "story.input.json",
    }
    _write_prompt(story_prompt, _story_prompt(run_summary))
    _write_prompt(critic_prompt, _critic_prompt(run_summary))

    manifest = {
        "round_id": root.name,
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
    }
    if card_folder is not None and agent_memory.memory_summary_due(root.name):
        summary_agents = ["player"] + [f"character:{name}" for name in character_outputs.keys()]
        agent_memory.write_memory_summary_prompts(card_folder, root, manifest, summary_agents)

    agent_run.append_manifest_stage(manifest, "prepared", "Agent run directory and context packets are prepared.")
    agent_run.append_manifest_stage(manifest, "prompts_ready", "Subagent prompts are materialized.")
    agent_run.append_manifest_stage(manifest, "awaiting_agent_outputs", "Waiting for Claude Code subagent output artifacts.")
    agent_run.write_json(root / "manifest.json", manifest)
    return manifest
