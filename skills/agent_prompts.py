"""Prompt materialization for Claude Code RP subagents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import agent_memory
import agent_run
import runtime_settings


REPO_ROOT = Path(__file__).resolve().parents[1]

SKILL_PATHS = {
    "input_analyst": ".claude/skills/rp-input-analyst.md",
    "gm": ".claude/skills/rp-gm-agent.md",
    "subgm": ".claude/skills/rp-subgm-agent.md",
    "player": ".claude/skills/rp-player-agent.md",
    "character": ".claude/skills/rp-character-agent.md",
    "story": ".claude/skills/rp-story-agent.md",
    "critic": ".claude/skills/rp-critic-agent.md",
    "postprocess": ".claude/skills/rp-postprocess-agent.md",
}

AUTHORITATIVE_CONTRACT_SKILLS = {"gm", "player", "character", "subgm"}

WORLD_UPDATE_RECORD_CONTRACT = """World update record contract:

- world_updates.hidden_facts[]: required `id`, `text`, `visibility: "gm_only"`, `status: "active|superseded|retracted"`
- world_updates.public_facts[]: required `id`, `text`, `visibility: "public_world"`, `status: "active|superseded|retracted"`
- world_updates.important_characters[]: required `name`, one textual field (`text`/`setting_text`/`authoritative_setting`/`description`/`profile`/`summary`), `visibility` in `character_private_and_gm|public_world|character_pov|specific_characters`, `status: "active"`
- world_updates.retcon_requests[]: required `id`, `text`, optional `visibility: "gm_only|public_world"`, `status: "active|superseded|retracted"`

If a world update cannot satisfy the record schema, omit it and keep the semantic unit only."""


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


def _runtime_payload(context: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(context, dict):
        return runtime_settings.normalize_prompt_payload(None)
    return runtime_settings.normalize_prompt_payload({
        "settings": context.get("runtime_settings"),
        "style_profile": context.get("style_profile"),
    })


def _gm_runtime_guidance(context: Dict[str, Any]) -> str:
    payload = _runtime_payload(context)
    settings = payload["settings"]
    return (
        "\n\nRuntime creative guidance:\n"
        f"- NSFW tone option: {settings['nsfw']}\n"
        f"- soft word-count target: {settings['wordCount']}\n"
        f"- self-repair mode: {settings['selfRepairMode']}; source-code self-repair allowed: {settings['allowSourceCodeSelfRepair']}\n"
    )


def _story_runtime_guidance(context: Dict[str, Any]) -> str:
    payload = _runtime_payload(context)
    settings = payload["settings"]
    profile = payload["style_profile"]
    return (
        "\n\nRuntime creative guidance for story output:\n"
        f"- style: {settings['style']}\n"
        f"- style title: {profile.get('title', '')}\n"
        f"- style profile content: {profile.get('content', '')}\n"
        f"- story output target: {settings['wordCount']} words/Chinese-character units as a soft target, unless the scene must stop for a player decision.\n"
        f"- NSFW creative tone: {settings['nsfw']}\n"
    )


def _critic_style_guidance(context: Dict[str, Any]) -> str:
    context = context if isinstance(context, dict) else {}
    raw_style = context.get("style")
    style = raw_style.strip() if isinstance(raw_style, str) and raw_style.strip() else ""
    profile = context.get("style_profile") if isinstance(context.get("style_profile"), dict) else {}
    return (
        "\n\nRuntime style guidance for later validation:\n"
        f"- style: {style or profile.get('name', '')}\n"
        f"- style title: {profile.get('title', '')}\n"
        f"- style profile content: {profile.get('content', '')}\n"
    )


def _base_prompt(
    title: str,
    skill_key: str,
    output_path: str,
    contract: str,
    context: Dict[str, Any],
    output_instruction: str | None = None,
    contract_notes: str | None = None,
) -> str:
    skill_path = SKILL_PATHS[skill_key]
    if output_instruction is None:
        output_instruction = f"Use only the allowed context below and write the required JSON artifact to `{output_path}`."
    notes = f"\n\n{contract_notes.strip()}" if contract_notes else ""
    return f"""
# {title}

Skill reference: `{skill_path}`

## Operating Rule

You are a Claude Code subagent working through the file mailbox for this RP round.
{output_instruction}
Do not write final prose unless this is the story agent.

## Required Output Contract

```json
{contract}
```
{notes}

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
        "routing_requests": [],
        "risks": [],
    })
    return _base_prompt(
        "Input Analyst Prompt",
        "input_analyst",
        "input_analysis.output.json",
        contract,
        context,
        contract_notes=WORLD_UPDATE_RECORD_CONTRACT,
    ) + (
        "\n\nSemantic unit enum contract: every `semantic_units[]` item must use "
        "exactly one of the allowed `type` values and exactly one of the allowed "
        "`visibility` values below.\n"
        "\nAllowed `semantic_units[].type` values: `action`, `synopsis`, "
        "`omniscient_setting`, `hidden_setting`, `character_declaration`, "
        "`edit_request`, `system_command`, `style_guidance`, `unclear`.\n"
        "\nAllowed `semantic_units[].visibility` values: `gm_only`, "
        "`public_world`, `player_pov`, `character_pov`, `specific_characters`.\n"
        "\nInvalid semantic unit visibility aliases: public, private, player, "
        "character, world_visible, actor_visible. Do not write these aliases in "
        "`input_analysis.output.json`.\n"
        "\nRouting request contract: use top-level `routing_requests[]` only for "
        "explicit user-requested system, UI, save-data, retcon, or source-feature "
        "work that should be routed outside ordinary GM/story handling. Allowed "
        "`routing_requests[].type` values: `assets_ui_task`, "
        "`story_retcon_consult`, `card_data_edit`, `source_feature_request`. "
        "A `source_feature_request` must use `authorization_gate: "
        "\"allowSourceCodeSelfRepair\"` and `requires_authorization: true`; it "
        "does not require `selfRepairMode`. Non-source routing requests must use "
        "`authorization_gate: \"none\"`. Do not use routing requests for normal "
        "player actions.\n"
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
                "visibility_basis": {
                    "mode": "direct",
                    "summary": "why this actor can perceive or receive this prompt",
                    "target_actor": "character:Example",
                },
            }
        ],
        "parallel_groups": [],
        "world_state_delta": [],
        "character_promotions": [
            {
                "name": "ExampleName",
                "source_agent": "gm",
                "reason": "why this character now needs independent agency",
                "profile_seed": "seed used for profile text",
                "visibility": "character_private_and_gm",
                "activation": "current_turn",
            }
        ],
        "subgm_commands": [
            {
                "action": "start",
                "thread_id": "side_example",
                "title": "Off-screen pressure",
                "outline": "What the side thread covers",
                "time_window": "same scene",
                "location": "nearby room",
                "objective": "Advance a bounded off-screen development",
                "allowed_characters": ["character:Example"],
                "forbidden_characters": ["player"],
                "priority": "normal",
                "message": "Initial GM instruction for the side thread",
                "metadata": {},
            }
        ],
        "decision_point": None,
        "stop_reason": "continue",
    })
    return _base_prompt(
        "GM Agent Prompt",
        "gm",
        "gm.output.json",
        contract,
        context,
    ) + _gm_runtime_guidance(context) + (
        "\n\nAllowed `stop_reason` values: `continue`, `player_decision`, "
        "`word_target`, `complete`, `max_steps`.\n"
        "\nCharacter promotion authority: GM may emit `source_agent: \"gm\"` "
        "inside `character_promotions`; preprocess is handled by input analysis; "
        "subGM agents must not emit applied promotion records.\n"
        "\nEvery `actor_calls[]` item must include valid per-call "
        "`visibility_basis.mode` and `visibility_basis.summary`; keep the proof "
        "actor-visible, targeted to the same actor, and free of GM-only causes.\n"
        "\nsubGM side-thread authority: GM may emit `subgm_commands` with actions "
        "`start`, `message`, `accelerate`, `pause`, `resume`, `merge`, or `close`. "
        "GM remains the only root authority: subGM agents cannot create/promote "
        "important characters or spawn other subGMs; treat subGM requests as proposals "
        "to accept, reject, or revise.\n"
        "\nCompletion rule: GM must not set `stop_reason` to `complete` while active subGM side threads remain. "
        "If `side_thread_summaries` contains `running`, `merging`, `needs_gm`, or `blocked` threads, "
        "use `subgm_commands` to message, accelerate, pause, merge, or close each active thread. "
        "If any side thread still needs work after that, keep `stop_reason` as `continue` or stop at a real player decision.\n"
    )


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
        "actor.outputs.json",
        contract,
        context,
        "Use only the allowed context below and return exactly one JSON player actor output object. "
        "The runtime loop validates actor responses and aggregates them into `actor.outputs.json`.",
    ) + (
        "\n\nAllowed `stop_reason` values: `continue`, `stop_for_player_decision`.\n"
        "\nActor authority: you may emit memory_delta or goal_update events only for memory/goals. "
        "Do not edit profile, background, personality, body_facts, authoritative_setting, or character_sheet data.\n"
    )


def _character_prompt(context: Dict[str, Any]) -> str:
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
        "actor.outputs.json",
        contract,
        context,
        "Use only the allowed context below and return exactly one JSON character actor output object. "
        "The runtime loop validates actor responses and aggregates them into `actor.outputs.json`.",
    ) + (
        "\n\nAllowed `stop_reason` values: `continue`, `stop_for_player_decision`.\n"
        "\nActor authority: you may emit memory_delta or goal_update events only for memory/goals. "
        "Do not edit profile, background, personality, body_facts, authoritative_setting, or character_sheet data.\n"
    )


def character_prompt_text(context: Dict[str, Any]) -> str:
    """Return the generated character prompt text for a projected loop packet."""
    return _character_prompt(context if isinstance(context, dict) else {})


def _subgm_prompt(context: Dict[str, Any]) -> str:
    context = context if isinstance(context, dict) else {}
    contract = _json_block({
        "agent": "subGM",
        "thread_id": context.get("thread_id", ""),
        "status": "running",
        "scene_beats": [{"content": "side-thread-only visible beat", "metadata": {}}],
        "events": [{"type": "side_event", "target": "", "content": "side-thread event", "metadata": {}}],
        "actor_calls": [
            {
                "call_id": "call-character-Name-1",
                "actor_id": "character:Name",
                "prompt": "first-person projected prompt for an allowed important character",
                "reason": "why this allowed actor is needed inside the side thread",
                "metadata": {},
                "visibility_basis": {
                    "mode": "direct",
                    "summary": "why this actor can perceive or receive this side prompt",
                    "target_actor": "character:Name",
                },
            }
        ],
        "messages_to_gm": [{"content": "what the main GM needs to know", "metadata": {}}],
        "world_state_delta": [],
        "character_usage": [],
        "promotion_requests": [],
        "boundary_requests": [],
        "notes_for_story": ["GM/story-facing note after main GM merge"],
        "next_resume_point": "",
    })
    return _base_prompt(
        "subGM Side-Thread Prompt",
        "subgm",
        "side_threads/<thread_id>/subgm.output.json",
        contract,
        context,
        "Use only the assigned side-thread context below and return exactly one JSON subGM output object. "
        "The runtime loop validates it and persists it under the side-thread directory.",
    ) + _gm_runtime_guidance(context) + (
        "\n\nAllowed `status` values: `running`, `paused`, `completed`, `blocked`, `needs_gm`.\n"
        "\nEvery `actor_calls[]` item must include valid per-call "
        "`visibility_basis.mode` and `visibility_basis.summary`; keep the proof "
        "actor-visible, targeted to the same allowed character, and within the assigned side-thread boundary.\n"
        "\nAuthority boundary: no player participation; no `character_promotions`; no `subgm_commands`; "
        "no direct boundary mutation; no important-character creation or promotion. "
        "Only advance the assigned side-thread boundary and request main GM decisions through "
        "`messages_to_gm`, `promotion_requests`, or `boundary_requests`.\n"
    )


def subgm_prompt_text(context: Dict[str, Any]) -> str:
    """Return the generated subGM prompt text for a side-thread loop packet."""
    return _subgm_prompt(context if isinstance(context, dict) else {})


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
        contract_notes=(
            "Story writes only prose, source-backed character dialogues, variable patches, "
            "and derived-content repair edits. Do not write `<summary>` or `<options>` "
            "in `story.output.json`; postprocess owns summary, options, current goal, "
            "and frontend data after critic pass."
        ),
    ) + _story_runtime_guidance(run_summary) + "\n\nRead `story.input.json.interaction_trace` when present. Preserve `visible_events`; do not use private trace content directly.\n"


def _critic_prompt(run_summary: Dict[str, Any]) -> str:
    contract = _json_block({
        "decision": "pass",
        "hard_failures": [],
        "soft_issues": [],
        "repair_instruction": "",
        "system_iteration_suggestion": "",
        "quality_checks": {
            "style_alignment": {
                "status": "pass",
                "expected_style": "",
                "profile_title": "",
                "notes": "",
            },
            "length": {
                "status": "pass",
                "target": 0,
                "minimum": 0,
                "current": 0,
                "exempted": False,
                "notes": "",
            },
        },
    })
    return _base_prompt(
        "Critic Agent Prompt",
        "critic",
        "critic.report.json",
        contract,
        run_summary,
    ) + _critic_style_guidance(run_summary) + (
        "\n\nRuntime quality validation:\n"
        "- The dispatcher passes `quality_metrics` in Runtime Input next to `story_input` and `story_output`.\n"
        "- Fill `quality_checks.style_alignment` from the selected style and style profile; use `pass`, `revise`, `block`, or `not_checked`.\n"
        "- Fill `quality_checks.length` from `quality_metrics.word_count`; use `pass`, `revise`, `block`, `exempt`, or `not_checked`.\n"
        "- If `quality_metrics.word_count.exempted` is true because of a player decision stop, set the length status to `exempt` or otherwise note the player decision exemption instead of requiring expansion.\n"
        "- Do not create any quality check for NSFW; it is creative tone guidance, not a critic validation requirement.\n"
        "\nRead `story.input.json.interaction_trace` when present. Preserve `visible_events`; do not use private trace content directly.\n"
    )


def build_postprocess_prompt(run_summary: Dict[str, Any]) -> str:
    """Return the generated postprocess prompt text for frontend support data."""
    run_summary = run_summary if isinstance(run_summary, dict) else {}
    runtime_input = run_summary.get("postprocess_context", {})
    if not isinstance(runtime_input, dict):
        runtime_input = {}
    contract = _json_block({
        "schema_version": 1,
        "core": {
            "summary": "player-visible recap of the delivered turn",
            "options": [
                {
                    "label": "Confirm action: visible player action",
                    "source": "player_agent_critical_action",
                    "requires_confirmation": True,
                }
            ],
            "current_goal": "current player-visible objective",
            "state_patch": {},
        },
        "ui_extensions": {
            "status_panels": {},
            "custom_cards": {},
            "asset_bindings": {},
        },
        "ui_extension_status": {
            "status": "ok",
            "issues": [],
        },
        "repair_requests": [],
        "metadata": {},
    })
    return f"""
# Postprocess Agent Prompt

Skill reference: `{SKILL_PATHS["postprocess"]}`

Write `postprocess.output.json`.

Required frontend data contract:
- `core.summary`
- `core.options`
- `core.current_goal`
- `core.state_patch`
- `ui_extensions`
- `ui_extension_status`
- `repair_requests`
- `metadata`

Do not rewrite story prose.
Do not review prose quality.
Do not write progress.json.
Do not write `<content>`, `<summary>`, or `<options>` tags.

## Required Output Contract

```json
{contract}
```

## Runtime Input JSON

```json
{_json_block(runtime_input)}
```

## Skill Body

```markdown
{_skill_excerpt("postprocess")}
```
""".strip()


def write_round_prompts(
    run_dir: str | Path,
    gm_packet: Dict[str, Any],
    player_packet: Dict[str, Any],
    character_packets: Dict[str, Dict[str, Any]],
    card_folder: str | Path | None = None,
    input_analysis_request: Dict[str, Any] | None = None,
    runtime_settings_payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Write prompt files and return the round manifest."""
    root = Path(run_dir)
    prompt_root = root / "prompts"
    characters_prompt_root = prompt_root / "characters"

    if input_analysis_request is None:
        input_request = gm_packet.get("input_analysis_request", {}) if isinstance(gm_packet, dict) else {}
    else:
        input_request = input_analysis_request
    runtime_payload = runtime_settings.normalize_prompt_payload(runtime_settings_payload)
    input_analyst_prompt = prompt_root / "input_analyst.prompt.md"
    gm_prompt = prompt_root / "gm.prompt.md"
    player_prompt = prompt_root / "player.prompt.md"
    story_prompt = prompt_root / "story.prompt.md"
    critic_prompt = prompt_root / "critic.prompt.md"

    character_prompts: Dict[str, str] = {}

    _write_prompt(input_analyst_prompt, _input_analyst_prompt(input_request))
    _write_prompt(gm_prompt, _gm_prompt(gm_packet))
    _write_prompt(player_prompt, _player_prompt(player_packet))

    for safe_name, packet in character_packets.items():
        prompt_path = characters_prompt_root / f"{safe_name}.prompt.md"
        _write_prompt(prompt_path, _character_prompt(packet))
        character_prompts[safe_name] = _rel(prompt_path, root)

    story_summary = {
        "run_dir": str(root.resolve()),
        "inputs": {
            "gm": "gm.output.json",
            "actors": "actor.outputs.json",
        },
        "story_input": "story.input.json",
        "runtime_settings": runtime_payload["settings"],
        "style_profile": runtime_payload["style_profile"],
    }
    critic_summary = {
        "run_dir": str(root.resolve()),
        "inputs": {
            "gm": "gm.output.json",
            "actors": "actor.outputs.json",
            "story": "story.output.json",
            "story_input": "story.input.json",
        },
        "story_input": "story.input.json",
        "style": runtime_payload["settings"]["style"],
        "style_profile": runtime_payload["style_profile"],
    }
    _write_prompt(story_prompt, _story_prompt(story_summary))
    _write_prompt(critic_prompt, _critic_prompt(critic_summary))

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
            "actors": "actor.outputs.json",
            "story": "story.output.json",
            "critic": "critic.report.json",
        },
        "runtime_settings": runtime_payload["settings"],
        "style_profile": runtime_payload["style_profile"],
    }
    if card_folder is not None and agent_memory.memory_summary_due(root.name):
        summary_agents = ["player"] + [f"character:{name}" for name in character_prompts.keys()]
        agent_memory.write_memory_summary_prompts(card_folder, root, manifest, summary_agents)

    agent_run.append_manifest_stage(manifest, "prepared", "Agent run directory and context packets are prepared.")
    agent_run.append_manifest_stage(manifest, "prompts_ready", "Subagent prompts are materialized.")
    agent_run.append_manifest_stage(manifest, "awaiting_agent_outputs", "Waiting for Claude Code subagent output artifacts.")
    agent_run.write_json(root / "manifest.json", manifest)
    return manifest
