"""Utilities for routing player input and building per-agent context packets."""

from __future__ import annotations

from typing import Any, Dict, Iterable

import re

import agent_run


INSTRUCTION_KEYWORDS = (
    "omniscient",
    "system",
    "setting",
    "history",
    "edit",
    "rewrite",
    "important character",
    "important_character",
    "系统指令",
    "上帝视角",
    "上帝",
    "设定",
)

_INSTRUCTION_RE = re.compile(r"^\s*[()\uFF08\uFF09][\s\S]*[()\uFF08\uFF09]\s*$")
_SEGMENTS_RE = re.compile(r"([()\uFF08][^)\uFF09]*[)\uFF09])")


def _to_text(value: Any) -> str:
    return "" if value is None else str(value)


def _is_instruction(text: str) -> bool:
    text = _to_text(text).strip()
    if not text:
        return False
    if _INSTRUCTION_RE.match(text):
        return True
    lowered = text.lower()
    return any(key in lowered for key in INSTRUCTION_KEYWORDS)


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
            else:
                role_parts.append(line)
                components.append({"channel": "role", "text": line})

    return {
        "role_channel": "\n".join(role_parts),
        "user_instruction_channel": "\n".join(instruction_parts),
        "components": components,
    }


def _filter_role_components(routed_input: Dict[str, Any]) -> list[Dict[str, str]]:
    return [item for item in routed_input.get("components", []) if item.get("channel") == "role"]


def build_gm_packet(card_folder, routed_input: Dict[str, Any], recent_chat, card_data=None, character_contexts=None):
    """Build GM packet with both role and instruction channels."""
    return {
        "agent": "gm",
        "card_folder": str(card_folder),
        "role_channel": _to_text(routed_input.get("role_channel")),
        "user_instruction_channel": _to_text(routed_input.get("user_instruction_channel")),
        "recent_chat": recent_chat or [],
        "card_data": card_data or {},
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
        return [
            {"name": str(k), **(v if isinstance(v, dict) else {})}
            for k, v in character_contexts.items()
            if isinstance(v, dict)
        ]
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
    "source": "default-pre-critic",
}


def prepare_agent_run(card_folder, user_text, chat_log, card_data, character_contexts, turn_index=None):
    """Create one round run directory and persist agent packets."""
    routed_input = route_player_input(user_text)
    run_dir = agent_run.create_run_dir(card_folder, turn_index=turn_index)

    input_payload = {
        "raw_text": _to_text(user_text),
        "routed_input": routed_input,
        "recent_chat": chat_log or [],
        "card_data": card_data or {},
        "character_contexts": character_contexts or {},
    }
    agent_run.write_json(run_dir / "input.json", input_payload)

    gm_packet = build_gm_packet(card_folder, routed_input, chat_log, card_data, character_contexts)
    player_packet = build_player_packet(card_folder, routed_input, chat_log)
    agent_run.write_json(run_dir / "gm.context.json", gm_packet)
    agent_run.write_json(run_dir / "player.context.json", player_packet)

    for character in _iter_characters(character_contexts):
        name = character.get("name") if isinstance(character, dict) else ""
        safe = agent_run.safe_name(name)
        packet = build_character_packet(card_folder, character, routed_input, chat_log)
        agent_run.write_json(run_dir / "characters" / f"{safe}.context.json", packet)

    agent_run.write_json(run_dir / "critic.report.json", DEFAULT_CRITIC_REPORT)
    return {
        "run_dir": str(run_dir.resolve()),
        "routed_input": routed_input,
        "gm_packet": gm_packet,
        "player_packet": player_packet,
    }
