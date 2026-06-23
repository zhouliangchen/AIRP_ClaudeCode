"""Immersive actor context rendering for player and character packets."""

from __future__ import annotations

import re
from typing import Any

import agent_visibility


PROJECTION_CONTROL_MARKERS = {
    "misconceptions",
    "objective_truth",
    "projection_review",
    "belief_is_false",
    "visibility_basis",
    "audit",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    text = str(value).strip()
    return [text] if text else []


def _marker_tokens(value: Any) -> list[str]:
    raw = str(value or "")
    acronym_separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", raw)
    camel_separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", acronym_separated)
    return re.findall(r"[a-z0-9]+", camel_separated.lower())


def _compact_marker(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _control_marker_name(value: Any) -> str:
    tokens = _marker_tokens(value)
    compact_tokens = [_compact_marker(token) for token in tokens]
    for marker in sorted(PROJECTION_CONTROL_MARKERS, key=lambda item: (-len(_marker_tokens(item)), item)):
        marker_tokens = _marker_tokens(marker)
        if not marker_tokens:
            continue
        for index in range(0, len(tokens) - len(marker_tokens) + 1):
            if tuple(tokens[index:index + len(marker_tokens)]) == tuple(marker_tokens):
                return marker
        marker_compact = _compact_marker(marker)
        if marker_compact and marker_compact in compact_tokens:
            return marker
    return ""


def _actor_marker_name(value: Any) -> str:
    return agent_visibility.hidden_marker_name(value) or _control_marker_name(value)


def _clean_text(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return "" if _actor_marker_name(text) else text


def _is_forbidden_key(value: Any) -> bool:
    return bool(_actor_marker_name(value))


def _clean_value(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {
            key: _clean_value(child)
            for key, child in value.items()
            if not _is_forbidden_key(key)
        }
        return {key: child for key, child in cleaned.items() if child not in ("", {}, [])}
    if isinstance(value, (list, tuple)):
        cleaned = [_clean_value(item) for item in value]
        return [item for item in cleaned if item not in ("", {}, [])]
    return _clean_text(value)


def _append_line(lines: list[str], prefix: str, value: Any) -> None:
    cleaned = _clean_value(value)
    if isinstance(cleaned, dict) and "content" in cleaned:
        cleaned = cleaned.get("content")
    text = _clean_text(cleaned)
    if text:
        lines.append(f"{prefix}{text}")


def _memory_lines(memory: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for value in _as_list(memory.get("long_term")):
        _append_line(lines, "You remember: ", value)
    for value in _as_list(memory.get("key_memories")):
        _append_line(lines, "Important to you: ", value)
    for value in _as_list(memory.get("short_term")):
        _append_line(lines, "Recently, you remember: ", value)
    for value in _as_list(memory.get("goals")):
        _append_line(lines, "Your current goal is: ", value)
    return lines


def render_actor_context(actor_id: str, actor_state: dict[str, Any] | None, world_state: dict[str, Any] | None) -> dict[str, Any]:
    actor = _as_dict(actor_state)
    world = _as_dict(world_state)
    memory = _as_dict(actor.get("memory"))
    lines: list[str] = []

    if actor_id == "player":
        lines.append("You are the player character.")
        _append_line(lines, "Current first-person anchor: ", world.get("role_channel"))
    else:
        name = _clean_text(actor.get("name") or actor.get("character_name") or actor_id.split(":", 1)[-1])
        role = _clean_text(actor.get("role") or actor.get("identity"))
        lines.append(f"You are {name}." if name else "You are this character.")
        if role:
            lines.append(f"You understand yourself as: {role}.")

    body_state = _as_dict(actor.get("body_state"))
    for key, value in sorted(body_state.items()):
        if _is_forbidden_key(key):
            continue
        _append_line(lines, f"Your {key}: ", value)

    relationships = _as_dict(actor.get("relationships"))
    for key, value in sorted(relationships.items()):
        if _is_forbidden_key(key):
            continue
        _append_line(lines, f"Your relationship with {key}: ", value)

    sensory = _as_dict(actor.get("sensory_context") or world.get("sensory_context"))
    for key, value in sorted(sensory.items()):
        if _is_forbidden_key(key):
            continue
        _append_line(lines, f"You can sense through {key}: ", value)

    lines.extend(_memory_lines(memory))

    return {
        "actor_id": str(actor_id or ""),
        "immersive_context": "\n".join(line for line in lines if line).strip(),
    }
