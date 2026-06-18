"""Utilities for routing player input and building per-agent context packets."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable

import agent_projection
import agent_run
import agent_prompts
import input_analysis


INSTRUCTION_PREFIXES = (
    "系统指令",
    "用户指令",
    "上帝视角",
    "设定：",
    "设定:",
    "重要角色",
    "核心角色",
    "system:",
    "user instruction:",
    "omniscient:",
    "setting:",
    "important character:",
)

_PAREN_INNER_RE = re.compile(r"^\(\s*(.*?)\s*\)$")
_FULL_PAREN_INNER_RE = re.compile(r"^（\s*(.*?)\s*）$")
_SEGMENTS_RE = re.compile(r"(\([^()]*\)|（[^（）]*）)")
_INLINE_INSTRUCTION_RE = re.compile(
    r"(?:^|(?<=[.!?。！？]))\s*("
    + "|".join(re.escape(prefix) for prefix in sorted(INSTRUCTION_PREFIXES, key=len, reverse=True))
    + r")",
    re.IGNORECASE,
)


def _has_instruction_prefix(text: str) -> bool:
    for cue in INSTRUCTION_PREFIXES:
        if text.startswith(cue) or text.lower().startswith(cue.lower()):
            return True
    return False


def _to_text(value: Any) -> str:
    return "" if value is None else str(value)


def _clip_text(value: Any, limit: int) -> str:
    text = _to_text(value).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _first_text(*values: Any) -> str:
    for value in values:
        text = _to_text(value).strip()
        if text:
            return text
    return ""


def compact_card_data(card_data: Any) -> Dict[str, Any]:
    """Project large SillyTavern card data into a bounded per-round context."""
    if not isinstance(card_data, dict):
        return {}

    nested = card_data.get("data")
    if not isinstance(nested, dict):
        nested = {}

    extensions = {}
    for source in (nested.get("extensions"), card_data.get("extensions")):
        if isinstance(source, dict):
            extensions.update(source)

    tags = card_data.get("tags") or nested.get("tags") or []
    if not isinstance(tags, list):
        tags = []

    return {
        "projection": "compacted_card_data_v1",
        "name": _first_text(card_data.get("name"), nested.get("name"), card_data.get("title")),
        "world": _first_text(extensions.get("world"), card_data.get("world"), nested.get("world")),
        "description": _clip_text(_first_text(card_data.get("description"), nested.get("description")), 4000),
        "personality": _clip_text(_first_text(card_data.get("personality"), nested.get("personality")), 2000),
        "scenario": _clip_text(_first_text(card_data.get("scenario"), nested.get("scenario")), 3000),
        "first_mes": _clip_text(_first_text(card_data.get("first_mes"), nested.get("first_mes")), 5000),
        "creator_notes": _clip_text(
            _first_text(card_data.get("creatorcomment"), nested.get("creator_notes"), card_data.get("creator_notes")),
            1500,
        ),
        "tags": tags[:20],
    }


def _is_instruction(text: str) -> bool:
    text = _to_text(text).strip()
    if not text:
        return False
    match = _PAREN_INNER_RE.match(text) or _FULL_PAREN_INNER_RE.match(text)
    if match:
        text = match.group(1).strip()
    return _has_instruction_prefix(text)


def _split_inline_instruction(text: str) -> tuple[str, str] | None:
    text = _to_text(text).strip()
    if not text or _is_instruction(text):
        return None
    match = _INLINE_INSTRUCTION_RE.search(text)
    if not match:
        return None
    prefix_start = match.start(1)
    role_text = text[:prefix_start].rstrip()
    instruction_text = text[prefix_start:].strip()
    if not role_text or not instruction_text:
        return None
    return role_text, instruction_text


def route_player_input(text: str) -> Dict[str, Any]:
    """Split mixed player input into role and instruction channels."""
    raw = _to_text(text)
    if not raw.strip():
        return {
            "role_channel": "",
            "user_instruction_channel": "",
            "components": [],
        }

    components = []
    role_parts = []
    instruction_parts = []

    for part in re.split(_SEGMENTS_RE, raw):
        part = _to_text(part).strip()
        if not part:
            continue
        for line in part.splitlines():
            line = line.strip()
            if not line:
                continue
            if _is_instruction(line):
                instruction_parts.append(line)
                components.append({"channel": "user_instruction", "text": line})
                continue
            inline_split = _split_inline_instruction(line)
            if inline_split:
                role_text, instruction_text = inline_split
                role_parts.append(role_text)
                components.append({"channel": "role", "text": role_text})
                instruction_parts.append(instruction_text)
                components.append({"channel": "user_instruction", "text": instruction_text})
                continue
            role_parts.append(line)
            components.append({"channel": "role", "text": line})

    return {
        "role_channel": "\n".join(role_parts),
        "user_instruction_channel": "\n".join(instruction_parts),
        "components": components,
    }


def route_input_payload(user_text: str, input_payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Route explicit dual-channel payloads before falling back to heuristics."""
    payload = input_payload if isinstance(input_payload, dict) else {}
    if payload.get("input_schema") == "dual_channel_v1":
        role = _to_text(payload.get("role_text"))
        instruction = _to_text(payload.get("user_instruction_text"))
        components = []
        if role:
            components.append({"channel": "role", "text": role})
        if instruction:
            components.append({"channel": "user_instruction", "text": instruction})
        return {
            "role_channel": role,
            "user_instruction_channel": instruction,
            "components": components,
            "input_schema": "dual_channel_v1",
        }

    routed = route_player_input(user_text)
    routed["input_schema"] = "heuristic_v1"
    return routed


def _filter_role_components(routed_input: Dict[str, Any]) -> list[Dict[str, str]]:
    return [item for item in routed_input.get("components", []) if item.get("channel") == "role"]


def _build_world_state(
    routed_input: Dict[str, Any],
    recent_chat,
    *,
    hidden_setting_records=None,
    visible_events=None,
    card_data=None,
    character_contexts=None,
) -> Dict[str, Any]:
    events = []
    if isinstance(visible_events, list):
        events.extend(visible_events)
    return {
        "role_channel": _to_text(routed_input.get("role_channel")),
        "user_instruction_channel": _to_text(routed_input.get("user_instruction_channel")),
        "recent_chat": recent_chat or [],
        "gm_only_hidden_settings": hidden_setting_records or [],
        "visible_events": events,
        "card_data": compact_card_data(card_data),
        "character_contexts": character_contexts or {},
        "components": routed_input.get("components", []),
    }


def _player_actor_state() -> Dict[str, Any]:
    return {
        "name": "player",
        "memory": [],
        "goals": [],
    }


def _character_actor_id(character: Dict[str, Any]) -> str:
    name = _to_text(character.get("name") or character.get("character_name")).strip()
    return f"character:{agent_run.safe_name(name)}" if name else "character:unknown"


def _as_memory_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    text = _to_text(value).strip()
    return [text] if text else []


def _append_memory_value(memory: Dict[str, Any], key: str, value: Any) -> None:
    items = _as_memory_items(memory.get(key))
    for item in _as_memory_items(value):
        if item not in items:
            items.append(item)
    if items:
        memory[key] = items


def _projectable_character_state(character: Dict[str, Any]) -> Dict[str, Any]:
    state = dict(character or {})
    memory_source = state.get("memory")
    memory = dict(memory_source) if isinstance(memory_source, dict) else {}
    if memory_source and not isinstance(memory_source, dict):
        _append_memory_value(memory, "long_term", memory_source)

    _append_memory_value(memory, "long_term", state.get("profile_summary"))
    profile = state.get("profile")
    if isinstance(profile, dict):
        _append_memory_value(memory, "long_term", profile.get("authoritative_setting"))
        _append_memory_value(memory, "long_term", profile.get("summary") or profile.get("description"))
    else:
        _append_memory_value(memory, "long_term", profile)
    _append_memory_value(memory, "recent", state.get("recent_state"))
    _append_memory_value(memory, "goals", state.get("goals"))

    if memory:
        state["memory"] = memory
    return state


def _actor_prompt_from_role(routed_input: Dict[str, Any], *, for_character: bool = False) -> str:
    role_channel = _to_text(routed_input.get("role_channel")).strip()
    if not role_channel:
        return "Use only your projected first-person context for this turn."
    if for_character:
        return "React only to visible events and your own memory for this turn."
    return f"Current first-person role-channel anchor: {role_channel}"


def build_gm_packet(
    card_folder,
    routed_input: Dict[str, Any],
    recent_chat,
    card_data=None,
    character_contexts=None,
    hidden_setting_records=None,
):
    """Build GM packet with both role and instruction channels."""
    return {
        "agent": "gm",
        "card_folder": str(card_folder),
        "role_channel": _to_text(routed_input.get("role_channel")),
        "user_instruction_channel": _to_text(routed_input.get("user_instruction_channel")),
        "gm_only_hidden_settings": hidden_setting_records or [],
        "recent_chat": recent_chat or [],
        "card_data": compact_card_data(card_data),
        "character_contexts": character_contexts or [],
        "components": routed_input.get("components", []),
    }


def build_player_packet(
    card_folder,
    routed_input: Dict[str, Any],
    recent_chat,
    world_state: Dict[str, Any] | None = None,
    actor_state: Dict[str, Any] | None = None,
    gm_prompt: str | None = None,
):
    """Build first-person packet for player agent."""
    del card_folder
    world = world_state or _build_world_state(routed_input, recent_chat)
    actor = actor_state if isinstance(actor_state, dict) else _player_actor_state()
    prompt = gm_prompt if gm_prompt is not None else _actor_prompt_from_role(routed_input)
    return agent_projection.project_actor_context("player", world, actor, prompt)


def build_character_packet(
    card_folder,
    character: Dict[str, Any],
    routed_input: Dict[str, Any],
    recent_chat,
    world_state: Dict[str, Any] | None = None,
    gm_prompt: str | None = None,
):
    """Build first-person packet for a character subagent."""
    del card_folder
    character_data = character or {}
    world = world_state or _build_world_state(routed_input, recent_chat)
    actor_state = _projectable_character_state(character_data)
    prompt = gm_prompt if gm_prompt is not None else _actor_prompt_from_role(routed_input, for_character=True)
    return agent_projection.project_actor_context(
        _character_actor_id(actor_state),
        world,
        actor_state,
        prompt,
    )


def _iter_characters(character_contexts: Any) -> Iterable[Dict[str, Any]]:
    if not character_contexts:
        return []
    if isinstance(character_contexts, dict):
        if "characters" in character_contexts and isinstance(character_contexts["characters"], (list, tuple)):
            return character_contexts["characters"]
    if isinstance(character_contexts, (list, tuple)):
        result = []
        for item in character_contexts:
            if isinstance(item, str):
                result.append({"name": item})
            elif isinstance(item, dict):
                result.append(item)
        return result
    return []


def build_character_contexts_from_card(card_folder, card_data, chat_log, user_text):
    """Build character contexts through round_prepare, falling back conservatively."""
    try:
        import round_prepare

        card_structure = agent_run.read_json(
            Path(card_folder) / "memory" / ".card_structure.json",
            {},
        )
        contexts = round_prepare.build_character_contexts(
            card_folder,
            card_data if isinstance(card_data, dict) else {},
            card_structure or {},
            chat_log or [],
            _to_text(user_text),
        )
        if isinstance(contexts, dict):
            return contexts
    except Exception:
        pass
    return {"characters": []}


DEFAULT_CRITIC_REPORT = {
    "passed": True,
    "hard_failures": [],
    "soft_issues": [],
    "repair_instruction": "",
    "system_iteration_suggestion": "",
    "source": "default-pre-critic",
}


def build_input_analysis_request(run_dir, user_text, input_payload, chat_log, card_data):
    """Build the immutable raw-input request for the input analyst subagent."""
    explicit_payload = dict(input_payload) if isinstance(input_payload, dict) else {}
    if explicit_payload.get("input_schema") == "dual_channel_v1":
        raw_text = _to_text(explicit_payload.get("raw_text"))
        role_text = _to_text(explicit_payload.get("role_text"))
        user_instruction_text = _to_text(explicit_payload.get("user_instruction_text"))
    else:
        routed = route_input_payload(user_text, None)
        raw_text = _to_text(user_text)
        role_text = _to_text(routed.get("role_channel"))
        user_instruction_text = _to_text(routed.get("user_instruction_channel"))

    return {
        "round_id": Path(run_dir).name,
        "raw_text": raw_text,
        "explicit_payload": explicit_payload,
        "role_text": role_text,
        "user_instruction_text": user_instruction_text,
        "source_integrity": {
            "raw_text_sha256": input_analysis.sha256_text(raw_text),
            "role_text_sha256": input_analysis.sha256_text(role_text),
            "user_instruction_text_sha256": input_analysis.sha256_text(user_instruction_text),
            "raw_preserved": True,
        },
        "recent_chat": chat_log or [],
        "card_projection": compact_card_data(card_data),
    }


def _input_raw_record(input_request: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "round_id": input_request.get("round_id", ""),
        "raw_text": input_request.get("raw_text", ""),
        "explicit_payload": input_request.get("explicit_payload", {}),
        "role_text": input_request.get("role_text", ""),
        "user_instruction_text": input_request.get("user_instruction_text", ""),
        "source_integrity": input_request.get("source_integrity", {}),
    }


def _input_analysis_request_markdown(input_request: Dict[str, Any]) -> str:
    return (
        "# Input Analysis Request\n\n"
        "Classify the preserved player input into `input_analysis.output.json`.\n\n"
        "```json\n"
        f"{json.dumps(input_request, ensure_ascii=False, indent=2)}\n"
        "```\n"
    )


def _input_analysis_request_reference(input_request: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "round_id": input_request.get("round_id", ""),
        "request_path": "input_analysis.request.md",
        "raw_path": "input.raw.json",
        "output_path": "input_analysis.output.json",
        "source_integrity": input_request.get("source_integrity", {}),
    }


def prepare_agent_run(
    card_folder,
    user_text,
    chat_log,
    card_data,
    character_contexts,
    turn_index=None,
    input_payload=None,
    hidden_setting_records=None,
):
    """Create one round run directory and persist agent packets."""
    routed_input = route_input_payload(user_text, input_payload)
    run_dir = agent_run.create_run_dir(card_folder, turn_index=turn_index)
    hidden_setting_records = hidden_setting_records or []
    input_request = build_input_analysis_request(run_dir, user_text, input_payload, chat_log, card_data)
    world_state = _build_world_state(
        routed_input,
        chat_log,
        hidden_setting_records=hidden_setting_records,
        card_data=card_data,
        character_contexts=character_contexts,
    )
    agent_run.write_json(run_dir / "input.raw.json", _input_raw_record(input_request))
    agent_run.write_text(run_dir / "input_analysis.request.md", _input_analysis_request_markdown(input_request))

    input_json = input_payload if isinstance(input_payload, dict) else {"raw_text": _to_text(user_text)}
    input_json = dict(input_json)
    input_json["raw_text"] = _to_text(input_json.get("raw_text", user_text))
    input_json["routed_input"] = routed_input
    input_json["gm_only_hidden_settings"] = hidden_setting_records
    input_json["recent_chat"] = chat_log or []
    input_json["card_data"] = compact_card_data(card_data)
    input_json["character_contexts"] = character_contexts or {}
    input_json["visible_events"] = world_state["visible_events"]
    agent_run.write_json(run_dir / "input.json", input_json)

    gm_packet = build_gm_packet(
        card_folder,
        routed_input,
        chat_log,
        card_data,
        character_contexts,
        hidden_setting_records=hidden_setting_records,
    )
    gm_packet["input_analysis_request"] = _input_analysis_request_reference(input_request)
    player_packet = build_player_packet(card_folder, routed_input, chat_log, world_state=world_state)
    agent_run.write_json(run_dir / "gm.context.json", gm_packet)
    agent_run.write_json(run_dir / "player.context.json", player_packet)

    character_packets = {}
    for character in _iter_characters(character_contexts):
        name = character.get("name") if isinstance(character, dict) else ""
        safe = agent_run.safe_name(name)
        packet = build_character_packet(card_folder, character, routed_input, chat_log, world_state=world_state)
        agent_run.write_json(run_dir / "characters" / f"{safe}.context.json", packet)
        character_packets[safe] = packet

    agent_run.write_json(run_dir / "critic.report.json", DEFAULT_CRITIC_REPORT)
    manifest = agent_prompts.write_round_prompts(
        run_dir,
        gm_packet,
        player_packet,
        character_packets,
        card_folder=card_folder,
        input_analysis_request=input_request,
    )
    return {
        "run_dir": str(run_dir.resolve()),
        "routed_input": routed_input,
        "gm_packet": gm_packet,
        "player_packet": player_packet,
        "manifest": manifest,
    }


def _clear_generated_character_files(run_dir: Path) -> None:
    for pattern in (
        "characters/*.context.json",
        "prompts/characters/*.prompt.md",
    ):
        for path in run_dir.glob(pattern):
            try:
                path.unlink()
            except OSError:
                pass


def rebuild_agent_run_from_analysis(
    card_folder,
    run_dir,
    analysis: Dict[str, Any],
    routed_input: Dict[str, Any],
    raw_request: Dict[str, Any],
    *,
    chat_log=None,
    card_data=None,
    character_contexts=None,
    hidden_setting_records=None,
):
    """Rewrite final agent packets/prompts after input analysis has been applied."""
    root = Path(run_dir)
    chat_log = chat_log or []
    hidden_setting_records = hidden_setting_records or []
    card_data = card_data if isinstance(card_data, dict) else {}
    character_contexts = character_contexts or {"characters": []}
    world_state = _build_world_state(
        routed_input,
        chat_log,
        hidden_setting_records=hidden_setting_records,
        card_data=card_data,
        character_contexts=character_contexts,
    )
    _clear_generated_character_files(root)

    input_json = {
        "input_analysis": analysis,
        "routed_input": routed_input,
        "raw_text": _to_text(raw_request.get("raw_text")),
        "explicit_payload": raw_request.get("explicit_payload", {}),
        "source_integrity": raw_request.get("source_integrity", {}),
        "recent_chat": chat_log,
        "gm_only_hidden_settings": hidden_setting_records,
        "card_data": compact_card_data(card_data),
        "character_contexts": character_contexts,
        "visible_events": world_state["visible_events"],
    }
    agent_run.write_json(root / "input.json", input_json)

    gm_packet = build_gm_packet(
        card_folder,
        routed_input,
        chat_log,
        card_data,
        character_contexts,
        hidden_setting_records=hidden_setting_records,
    )
    gm_packet["input_analysis_request"] = _input_analysis_request_reference(raw_request)
    player_packet = build_player_packet(card_folder, routed_input, chat_log, world_state=world_state)
    agent_run.write_json(root / "gm.context.json", gm_packet)
    agent_run.write_json(root / "player.context.json", player_packet)

    character_packets = {}
    for character in _iter_characters(character_contexts):
        name = character.get("name") if isinstance(character, dict) else ""
        safe = agent_run.safe_name(name)
        packet = build_character_packet(card_folder, character, routed_input, chat_log, world_state=world_state)
        agent_run.write_json(root / "characters" / f"{safe}.context.json", packet)
        character_packets[safe] = packet

    manifest = agent_prompts.write_round_prompts(
        root,
        gm_packet,
        player_packet,
        character_packets,
        card_folder=card_folder,
        input_analysis_request=raw_request,
    )
    agent_run.append_manifest_stage(
        manifest,
        "analysis_applied",
        "Input analysis has been validated and applied to agent packets.",
    )
    agent_run.write_json(root / "manifest.json", manifest)
    return {
        "run_dir": str(root.resolve()),
        "routed_input": routed_input,
        "gm_packet": gm_packet,
        "player_packet": player_packet,
        "manifest": manifest,
        "character_packets": character_packets,
    }
