"""Utilities for routing player input and building per-agent context packets."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable

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


def build_player_packet(card_folder, routed_input: Dict[str, Any], recent_chat):
    """Build first-person packet for player agent."""
    return {
        "agent": "player",
        "card_folder": str(card_folder),
        "role_channel": _to_text(routed_input.get("role_channel")),
        "recent_chat": recent_chat or [],
        "components": _filter_role_components(routed_input),
    }


def build_character_packet(card_folder, character: Dict[str, Any], routed_input: Dict[str, Any], recent_chat):
    """Build first-person packet for a character subagent."""
    character_data = character or {}
    character_name = character_data.get("name", "")
    role_hint = character_data.get("role") or character_data.get("position") or character_data.get("identity") or ""

    return {
        "agent": "character",
        "card_folder": str(card_folder),
        "character": character_data,
        "character_name": _to_text(character_name),
        "role_hint": _to_text(role_hint),
        "role_channel": _to_text(routed_input.get("role_channel")),
        "recent_chat": recent_chat or [],
        "components": _filter_role_components(routed_input),
    }


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
    player_packet = build_player_packet(card_folder, routed_input, chat_log)
    agent_run.write_json(run_dir / "gm.context.json", gm_packet)
    agent_run.write_json(run_dir / "player.context.json", player_packet)

    character_packets = {}
    for character in _iter_characters(character_contexts):
        name = character.get("name") if isinstance(character, dict) else ""
        safe = agent_run.safe_name(name)
        packet = build_character_packet(card_folder, character, routed_input, chat_log)
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
