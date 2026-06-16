"""Prompt materialization for Claude Code RP subagents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import agent_memory
import agent_run


REPO_ROOT = Path(__file__).resolve().parents[1]

SKILL_PATHS = {
    "gm": ".claude/skills/rp-gm-agent.md",
    "player": ".claude/skills/rp-player-agent.md",
    "character": ".claude/skills/rp-character-agent.md",
    "story": ".claude/skills/rp-story-agent.md",
    "critic": ".claude/skills/rp-critic-agent.md",
}


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _json_block(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _skill_excerpt(skill_key: str, limit: int = 6000) -> str:
    relative = SKILL_PATHS[skill_key]
    path = REPO_ROOT / relative
    if not path.exists():
        return f"(missing skill file: {relative})"
    text = path.read_text(encoding="utf-8")
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


def _gm_prompt(context: Dict[str, Any]) -> str:
    contract = _json_block({
        "agent": "gm",
        "narration": "brief neutral world narration",
        "npc_events": [],
        "world_state_delta": [],
        "handoff": {},
    })
    return _base_prompt(
        "GM Agent Prompt",
        "gm",
        "gm.output.json",
        contract,
        context,
    )


def _player_prompt(context: Dict[str, Any]) -> str:
    contract = _json_block({
        "agent": "player",
        "agent_id": "player",
        "action": "first-person action",
        "dialogue": [],
        "perception": [],
        "memory_delta": [],
    })
    return _base_prompt(
        "Player Agent Prompt",
        "player",
        "player.output.json",
        contract,
        context,
    )


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
    return _base_prompt(
        f"Character Agent Prompt: {context.get('character_name', '')}",
        "character",
        output_path,
        contract,
        context,
    )


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
) -> Dict[str, Any]:
    """Write prompt files and return the round manifest."""
    root = Path(run_dir)
    prompt_root = root / "prompts"
    characters_prompt_root = prompt_root / "characters"

    gm_prompt = prompt_root / "gm.prompt.md"
    player_prompt = prompt_root / "player.prompt.md"
    story_prompt = prompt_root / "story.prompt.md"
    critic_prompt = prompt_root / "critic.prompt.md"

    character_prompts: Dict[str, str] = {}
    character_outputs: Dict[str, str] = {}

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
            "gm": _rel(gm_prompt, root),
            "player": _rel(player_prompt, root),
            "characters": character_prompts,
            "story": _rel(story_prompt, root),
            "critic": _rel(critic_prompt, root),
        },
        "expected_outputs": {
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
