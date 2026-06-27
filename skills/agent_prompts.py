"""Prompt materialization for Claude Code RP subagents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import actor_memory_store
import actor_context_renderer
import agent_run
import runtime_settings


REPO_ROOT = Path(__file__).resolve().parents[1]

SKILL_PATHS = {
    "input_analyst": ".claude/skills/rp-input-analyst.md",
    "gm": ".claude/skills/rp-gm-agent.md",
    "subgm": ".claude/skills/rp-subgm-agent.md",
    "projection": ".claude/skills/rp-projection-agent.md",
    "story": ".claude/skills/rp-story-agent.md",
    "critic": ".claude/skills/rp-critic-agent.md",
    "postprocess": ".claude/skills/rp-postprocess-agent.md",
}

AUTHORITATIVE_CONTRACT_SKILLS = {"gm", "subgm"}

_GM_PROMPT_TOP_LEVEL_DUPLICATE_KEYS = {"components"}
_GM_PROMPT_WORLD_DUPLICATE_KEYS = {
    "raw_text",
    "explicit_payload",
    "routed_input",
    "role_channel",
    "user_instruction_channel",
    "components",
    "gm_only_hidden_settings",
    "objective_world",
    "recent_chat",
    "card_data",
    "character_contexts",
    "runtime_settings",
    "style_profile",
}
_GM_PROMPT_INPUT_ANALYSIS_KEEP_KEYS = (
    "schema_version",
    "round_id",
    "analysis_mode",
    "semantic_units",
    "world_updates",
    "narrative_directives",
    "routing_requests",
    "capability_requests",
    "risks",
)
_GM_PROMPT_INPUT_ANALYSIS_DUPLICATE_KEYS = {"raw_excerpt", "source_integrity", "routing", "text"}

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


def _section_text(lines: list[str], fallback: str = "暂无。") -> str:
    cleaned = [line for line in (str(item).strip() for item in lines) if line]
    return "\n".join(cleaned) if cleaned else fallback


def _actor_id_for_memory(context: Dict[str, Any], actor_name: str) -> str:
    actor_id = str(context.get("actor_id") or "").strip()
    if actor_id:
        return actor_id
    name = str(actor_name or "").strip()
    return f"character:{name}" if name else "player"


def _file_backed_actor_context(
    context: Dict[str, Any],
    actor_name: str,
) -> dict[str, Any] | None:
    card_folder = str(context.get("card_folder") or "").strip()
    if not card_folder:
        return None
    actor_id = _actor_id_for_memory(context, actor_name)
    stored = actor_memory_store.read_actor_memory(card_folder, actor_id)
    memory = actor_context_renderer.project_actor_memory(
        {
            "long_term": [stored.get("long_term")],
            "key_memories": stored.get("key_memories"),
            "short_term": [stored.get("short_term")],
            "goals": [],
        }
    )
    return {
        "display_name": str(stored.get("name") or "").strip(),
        "profile_text": str(stored.get("profile") or "").strip(),
        "memory": memory,
    }


def _strip_embedded_output_schema(text: str) -> str:
    marker = ""
    start = -1
    for candidate in ("\n## Output Schema", "\n## 输出契约"):
        start = text.find(candidate)
        if start != -1:
            marker = candidate
            break
    if start == -1:
        return text
    before = text[:start]
    after = text[start + len(marker):]
    next_heading = after.find("\n## ", 1)
    tail = after[next_heading:] if next_heading != -1 else ""
    return (
        before.rstrip()
        + "\n\n"
        + marker.lstrip()
        + "\n\n"
        + "只按本次生成的 JSON 输出契约返回结果，不写额外解释。\n"
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


def _strip_prompt_duplicate_keys(value: Any, duplicate_keys: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_prompt_duplicate_keys(item, duplicate_keys)
            for key, item in value.items()
            if str(key) not in duplicate_keys
        }
    if isinstance(value, list):
        return [_strip_prompt_duplicate_keys(item, duplicate_keys) for item in value]
    return value


def _compact_gm_input_analysis_for_prompt(analysis: Any) -> Any:
    if not isinstance(analysis, dict):
        return analysis
    compact = {
        key: analysis[key]
        for key in _GM_PROMPT_INPUT_ANALYSIS_KEEP_KEYS
        if key in analysis
    }
    return _strip_prompt_duplicate_keys(compact, _GM_PROMPT_INPUT_ANALYSIS_DUPLICATE_KEYS)


def _compact_gm_prompt_context(context: Any) -> Any:
    if not isinstance(context, dict):
        return context
    compact = {
        key: value
        for key, value in context.items()
        if str(key) not in _GM_PROMPT_TOP_LEVEL_DUPLICATE_KEYS
    }
    world_state = compact.get("world_state")
    if isinstance(world_state, dict):
        compact_world = {
            key: value
            for key, value in world_state.items()
            if str(key) not in _GM_PROMPT_WORLD_DUPLICATE_KEYS
        }
        if "input_analysis" in world_state:
            compact_world["input_analysis"] = _compact_gm_input_analysis_for_prompt(
                world_state.get("input_analysis")
            )
        compact["world_state"] = compact_world
    return compact


def _write_prompt(path: Path, body: str) -> None:
    agent_run.write_text(path, body.strip() + "\n")


def _actor_prompt_context(context: Dict[str, Any], card_folder: str | Path | None) -> Dict[str, Any]:
    result = dict(context) if isinstance(context, dict) else {}
    if card_folder is not None:
        result["card_folder"] = str(card_folder)
    return result


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


def _actor_base_prompt(
    title: str,
    context: Dict[str, Any],
    actor_name: str = "",
) -> str:
    context = context if isinstance(context, dict) else {}
    gm_prompt = str(context.get("gm_prompt") or "").strip()
    file_context = _file_backed_actor_context(context, actor_name)
    display_name = (
        file_context.get("display_name", "") if file_context else actor_name.strip()
    ) if isinstance(actor_name, str) else ""
    name_line = f"我是 {display_name}。" if display_name else "我是当前正在行动的这个人。"
    memory = file_context["memory"] if file_context else {
        "long_term": [],
        "key_memories": [],
        "short_term": [],
    }
    basic_context = (
        str(file_context.get("profile_text") or "").strip()
        if file_context
        else ""
    )
    if not basic_context:
        basic_context = "暂无额外基本设定。"
    long_term_memory = _section_text(memory["long_term"], "暂无长期记忆。")
    key_memory_cues = _section_text(memory["key_memories"], "暂无需要主动回忆的重点记忆。")
    short_term_memory = _section_text(memory["short_term"], "暂无短期记忆。")
    current_context = gm_prompt or "暂时没有新的外部话语。"
    return f"""
# {title}

{name_line}

{basic_context}

我记得：
{long_term_memory}

有些事情很重要，虽然现在只有个大概的印象，在下面列出；不过我清晰地记在了记忆的深层。如果现在需要回忆起来，需要输出思考：“我想回忆：xxx”，其中xxx是这段记忆的主题词，也许马上就能想起来了。
{key_memory_cues}

最近的事情：
{short_term_memory}

现在：
{current_context}


# 我的行动方式

我直接用自然语言对刚刚与我说话的人回应。
我不写 JSON，不写字段名，不写代码块，不用列表罗列内部状态。
我不提系统、prompt、协议、文件或外部工具，不把任何这些东西混入当前真实人生的话语。
我不把不可感知的设定、幕后原因或外部指令当成自己知道的事。
我可以自然地说出自己想记住的事或当前目标，但不修改人设、背景、人格、身体事实或权威设定。
我只写自己的想法、动作、台词和感受，不能控制他人行动，也不能让环境按照我的意愿给出结果。

现在，如果没有其他重点记忆需要回忆，那就好好想想接下来怎么办吧。
我不用“配合剧情”，我不相信世界有剧本。越自然越好，也许现实会奖励真实活着的每一个人。
我只是 {display_name or "我自己"}，不是别人；我不用扮演任何人。
我只能使用自然语言写自己想了什么、做了什么、说了什么，不能操作其他人，也不能替环境按我的意愿作出结果。
当我尝试获得更多感官信息时，比如当我想看向某个方向，只需要输出：“我想看向...”，我就能看到那个方向有什么。


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
        },
        "routing": {
            "role_channel": "",
            "role_action_channel": "",
            "narrative_guidance_channel": "",
            "user_instruction_channel": "",
            "gm": True,
            "player": True,
            "characters": [],
        },
        "routing_requests": [],
        "capability_requests": [],
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
        "\nRole-channel split contract: `routing.role_channel` preserves the "
        "complete player-authored role-channel text. Put only immediate "
        "first-person player-character action/dialogue in "
        "`routing.role_action_channel`; put first-person near-future synopsis or "
        "plot guidance in `routing.narrative_guidance_channel`. If the role "
        "channel contains both, split them semantically; do not use keyword "
        "rules. Narrative guidance is a GM/story suggestion and must not be "
        "treated as the player actor's direct reply or short-term memory.\n"
        "\nCapability request contract: use top-level `capability_requests[]` "
        "for explicit user-requested system, UI, save-data, retcon/replay, or "
        "source-feature work that should be routed outside ordinary GM/story "
        "handling. Each request must include `id`, `requested_by`, `target`, "
        "`capability`, `summary`, `reason`, `source_channel`, `risk`, "
        "`authorization_gate`, object `payload`, and object `evidence` with a "
        "non-empty `raw_excerpt`. Current registered capabilities include "
        "`assets.generate_image`, `source.change_request`, `retcon.consult`, "
        "`replay.plan`, and `card.patch_data`; unsupported capability names may "
        "be emitted only when the player explicitly requested that capability "
        "and will be audited by the registry. Canonical targets: "
        "`assets.generate_image -> assets-ui`, "
        "`source.change_request -> main-agent`, "
        "`retcon.consult -> story`, `replay.plan -> replay`, "
        "`card.patch_data -> card-data`. `source.change_request` must use "
        "`authorization_gate: \"allowSourceCodeSelfRepair\"`; `replay.plan` and "
        "`card.patch_data` require `manual_confirmation`. Keep legacy "
        "`routing_requests[]` as an empty compatibility field unless preserving "
        "an existing legacy route such as `assets_ui_task` or "
        "`source_feature_request`. Prefer `capability_requests[]` for new work. "
        "Do not use capability requests for normal player actions.\n"
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
        "\nWithin one user turn, call the player actor only when the player has not yet responded "
        "in the current loop. After any player actor reply, do not call `actor_id: \"player\"` again; "
        "use the returned player reply plus GM-visible state to finish the GM step and let story/postprocess "
        "compose the delivered text. You may still provide sensory consequences to the player in the next user turn.\n"
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
    return _actor_base_prompt("我是", context)


def player_prompt_text(context: Dict[str, Any]) -> str:
    """Return the generated player prompt text for a projected loop packet."""
    return _player_prompt(context if isinstance(context, dict) else {})


def _character_prompt(context: Dict[str, Any]) -> str:
    context = context if isinstance(context, dict) else {}
    character_name = str(context.get("character_name") or "").strip()
    if isinstance(context, dict) and str(context.get("card_folder") or "").strip():
        stored = actor_memory_store.read_actor_memory(
            str(context.get("card_folder") or "").strip(),
            _actor_id_for_memory(context, character_name),
        )
        character_name = str(stored.get("name") or "").strip()
    title = f"我的行动提示：{character_name}" if character_name else "我的行动提示"
    return _actor_base_prompt(title, context, actor_name=character_name)


def character_prompt_text(context: Dict[str, Any]) -> str:
    """Return the generated character prompt text for a projected loop packet."""
    return _character_prompt(context if isinstance(context, dict) else {})


def projection_prompt_text(context: Dict[str, Any]) -> str:
    """Return the generated projection review prompt text."""
    context = context if isinstance(context, dict) else {}
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
        context,
        "Review the requested actor message using the natural-language actor context, "
        "memory, settings, and review reference in this prompt. Return exactly one JSON "
        "projection result object. Use `pass` when no change is needed, `edited` for "
        "small safe local edits, `needs_rewrite` when GM/subGM must rewrite or negotiate "
        "the wording again, and `blocked` for invalid requests.",
        contract_notes=(
            "Do not reveal objective truth to the target actor. "
            "Do not tell the actor that a belief is false. "
            "Only the natural-language `final_actor_message` can be delivered to the actor; "
            "never deliver context packets, visibility proofs, memory objects, or other structured data."
        ),
    )


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
        "Use only Runtime Input `story_input`, which is the sanitized story-facing projection. "
        "Do not open or recover raw `story.input.json`, `gm.output.json`, `actor.outputs.json`, "
        "trace files, memory files, or hidden settings for story prose.",
        contract_notes=(
            "Story writes only prose, source-backed character dialogues, "
            "and derived-content repair edits. Do not write `<summary>` or `<options>` "
            "in `story.output.json`; postprocess owns summary, options, current goal, "
            "and frontend data after critic pass; postprocess also owns MVU variable update commands. "
            "Do not write `<UpdateVariable>`; postprocess owns MVU variable update commands."
        ),
    ) + _story_runtime_guidance(run_summary) + (
        "\n\nUse Runtime Input `story_input.interaction_trace` when present. "
        "Preserve `visible_events`; do not use private trace content directly. "
        "The raw `story.input.json` artifact is for audit, critic, and memory boundaries, not for story-agent prompt recovery.\n"
    )


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
        "- `story.output.json` does not require `<summary>` or `<options>`; hard-failing their absence is incorrect because postprocess owns `core.summary` and `core.options` after critic pass.\n"
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
        "mvu": {
            "commands": [],
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
- `mvu.commands`
- `repair_requests`
- `metadata`

Do not rewrite story prose.
Do not review prose quality.
Do not write progress.json.
Do not write `<content>`, `<summary>`, or `<options>` tags.
Write MVU variable update commands only in `mvu.commands`; do not append `<UpdateVariable>` to story prose.

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
    _write_prompt(gm_prompt, _gm_prompt(_compact_gm_prompt_context(gm_packet)))
    _write_prompt(player_prompt, _player_prompt(_actor_prompt_context(player_packet, card_folder)))

    for safe_name, packet in character_packets.items():
        prompt_path = characters_prompt_root / f"{safe_name}.prompt.md"
        _write_prompt(prompt_path, _character_prompt(_actor_prompt_context(packet, card_folder)))
        character_prompts[safe_name] = _rel(prompt_path, root)

    story_summary = {
        "run_dir": str(root.resolve()),
        "inputs": {
            "gm": "gm.output.json",
            "actors": "actor.outputs.json",
        },
        "story_input": "Runtime Input story_input (sanitized story-facing projection)",
        "audit_story_input": "story.input.json (raw audit artifact; story agent must not read it for prose)",
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
    agent_run.append_manifest_stage(manifest, "prepared", "Agent run directory and context packets are prepared.")
    agent_run.append_manifest_stage(manifest, "prompts_ready", "Subagent prompts are materialized.")
    agent_run.append_manifest_stage(manifest, "awaiting_agent_outputs", "Waiting for Claude Code subagent output artifacts.")
    agent_run.write_json(root / "manifest.json", manifest)
    return manifest
