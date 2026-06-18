"""Structured first-person actor memory update validation and rendering."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict


ACTOR_FORBIDDEN_MARKERS = {
    "gm_only",
    "omniscient",
    "world_truth",
    "gm_notes",
    "hidden_note",
    "out_of_character",
}

FORBIDDEN_ACTOR_PROFILE_KEYS = {
    "profile",
    "background",
    "personality",
    "body_facts",
    "authoritative_setting",
    "character_sheet",
}

ALLOWED_TOP_LEVEL_KEYS = {
    "agent_id",
    "character_name",
    "source",
    "visibility",
    "long_term",
    "key_memories",
    "short_term",
    "goals",
}
LONG_TERM_KEYS = ("self_understanding", "stable_beliefs", "relationship_models")
KEY_MEMORY_KEYS = {"content", "importance", "details"}
SHORT_TERM_KEYS = {"content", "expires_after"}
GOAL_KEYS = ("active", "paused", "resolved")


class AgentMemoryModelError(ValueError):
    """Raised when an actor memory update violates the structured contract."""


def _canonical_tokens(text: str) -> list[str]:
    raw = str(text or "")
    acronym_separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", raw)
    camel_separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", acronym_separated)
    return re.findall(r"[a-z0-9]+", camel_separated.lower())


ACTOR_FORBIDDEN_MARKER_TOKENS = {
    marker: tuple(_canonical_tokens(marker))
    for marker in ACTOR_FORBIDDEN_MARKERS
}
FORBIDDEN_PROFILE_KEY_TOKENS = {
    key: tuple(_canonical_tokens(key))
    for key in FORBIDDEN_ACTOR_PROFILE_KEYS
}


def _contains_tokens(tokens: list[str], marker_tokens: tuple[str, ...]) -> bool:
    if not marker_tokens or len(marker_tokens) > len(tokens):
        return False
    for index in range(0, len(tokens) - len(marker_tokens) + 1):
        if tuple(tokens[index:index + len(marker_tokens)]) == marker_tokens:
            return True
    return False


def _contains_forbidden_marker(text: str) -> str:
    tokens = _canonical_tokens(text)
    if not tokens:
        return ""
    for marker, marker_tokens in ACTOR_FORBIDDEN_MARKER_TOKENS.items():
        if _contains_tokens(tokens, marker_tokens):
            return marker
    return ""


def _contains_forbidden_profile_key(text: str) -> str:
    tokens = _canonical_tokens(text)
    if not tokens:
        return ""
    for key, key_tokens in FORBIDDEN_PROFILE_KEY_TOKENS.items():
        if _contains_tokens(tokens, key_tokens):
            return key
    return ""


def _reject_forbidden_keys_and_markers(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            profile_key = _contains_forbidden_profile_key(key_text)
            if profile_key:
                raise AgentMemoryModelError(f"{child_path}: forbidden actor profile field {profile_key}")
            marker = _contains_forbidden_marker(key_text)
            if marker:
                raise AgentMemoryModelError(f"{child_path}: forbidden actor marker {marker}")
            _reject_forbidden_keys_and_markers(child, child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_keys_and_markers(child, f"{path}[{index}]")
        return
    if isinstance(value, str):
        marker = _contains_forbidden_marker(value)
        if marker:
            raise AgentMemoryModelError(f"{path}: forbidden actor marker {marker}")


def _require_object(value: Any, path: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise AgentMemoryModelError(f"{path}: object is required")
    return value


def _require_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentMemoryModelError(f"{path}: nonempty string is required")
    return value.strip()


def _text_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list):
        raise AgentMemoryModelError(f"{path}: list is required")
    result = []
    for index, item in enumerate(value):
        result.append(_require_text(item, f"{path}[{index}]"))
    return result


def _normalize_long_term(value: Any) -> Dict[str, list[str]]:
    data = _require_object(value, "long_term")
    extra = sorted(str(key) for key in data if key not in LONG_TERM_KEYS)
    if extra:
        raise AgentMemoryModelError(f"long_term.{extra[0]}: unsupported long-term bucket")
    return {key: _text_list(data.get(key, []), f"long_term.{key}") for key in LONG_TERM_KEYS}


def _normalize_key_memories(value: Any) -> list[Dict[str, Any]]:
    if not isinstance(value, list):
        raise AgentMemoryModelError("key_memories: list is required")
    memories = []
    for index, item in enumerate(value):
        data = _require_object(item, f"key_memories[{index}]")
        extra = sorted(str(key) for key in data if key not in KEY_MEMORY_KEYS)
        if extra:
            raise AgentMemoryModelError(f"key_memories[{index}].{extra[0]}: unsupported key memory field")
        memories.append(
            {
                "content": _require_text(data.get("content"), f"key_memories[{index}].content"),
                "importance": str(data.get("importance") or "medium").strip() or "medium",
                "details": _text_list(data.get("details", []), f"key_memories[{index}].details"),
            }
        )
    return memories


def _normalize_short_term(value: Any) -> list[Dict[str, str]]:
    if not isinstance(value, list):
        raise AgentMemoryModelError("short_term: list is required")
    memories = []
    for index, item in enumerate(value):
        data = _require_object(item, f"short_term[{index}]")
        extra = sorted(str(key) for key in data if key not in SHORT_TERM_KEYS)
        if extra:
            raise AgentMemoryModelError(f"short_term[{index}].{extra[0]}: unsupported short-term field")
        memories.append(
            {
                "content": _require_text(data.get("content"), f"short_term[{index}].content"),
                "expires_after": str(data.get("expires_after") or "scene_end").strip() or "scene_end",
            }
        )
    return memories


def _normalize_goals(value: Any) -> Dict[str, list[str]]:
    data = _require_object(value, "goals")
    extra = sorted(str(key) for key in data if key not in GOAL_KEYS)
    if extra:
        raise AgentMemoryModelError(f"goals.{extra[0]}: unsupported goals bucket")
    return {key: _text_list(data.get(key, []), f"goals.{key}") for key in GOAL_KEYS}


def validate_memory_update(payload: Any) -> Dict[str, Any]:
    """Return a normalized actor memory update or raise `AgentMemoryModelError`."""
    data = _require_object(payload, "payload")
    _reject_forbidden_keys_and_markers(data, "payload")

    extra = sorted(str(key) for key in data if key not in ALLOWED_TOP_LEVEL_KEYS)
    if extra:
        raise AgentMemoryModelError(f"{extra[0]}: unsupported memory update field")

    agent_id = _require_text(data.get("agent_id"), "agent_id")
    if agent_id != "player" and not agent_id.startswith("character:"):
        raise AgentMemoryModelError(f"agent_id: unsupported actor id {agent_id}")
    if agent_id.startswith("character:") and not agent_id.split(":", 1)[1].strip():
        raise AgentMemoryModelError("agent_id: character id suffix is required")

    source = _require_text(data.get("source"), "source").lower()
    if source != "self":
        raise AgentMemoryModelError("source: actor memory updates must use source self")
    visibility = _require_text(data.get("visibility"), "visibility").lower()
    if visibility != "actor":
        raise AgentMemoryModelError("visibility: actor memory updates must use visibility actor")

    character_name = str(data.get("character_name") or "").strip()
    if agent_id.startswith("character:") and not character_name:
        raise AgentMemoryModelError("character_name: character memory updates must declare character_name")

    return {
        "agent_id": agent_id,
        "character_name": character_name,
        "source": source,
        "visibility": visibility,
        "long_term": _normalize_long_term(data.get("long_term")),
        "key_memories": _normalize_key_memories(data.get("key_memories")),
        "short_term": _normalize_short_term(data.get("short_term")),
        "goals": _normalize_goals(data.get("goals")),
    }


def _validated(update: Any) -> Dict[str, Any]:
    return validate_memory_update(copy.deepcopy(update))


def _title(update: Dict[str, Any], suffix: str) -> str:
    return f"# {update['agent_id']} {suffix}"


def _extend_string_section(lines: list[str], heading: str, items: list[str]) -> None:
    lines.extend(["", f"## {heading}", ""])
    if not items:
        lines.append("- (none)")
        return
    lines.extend(f"- {item}" for item in items)


def render_long_term_markdown(update: Any) -> str:
    data = _validated(update)
    long_term = data["long_term"]
    lines = [_title(data, "Long-Term Memory"), "", "- source: self", "- visibility: actor"]
    _extend_string_section(lines, "Self Understanding", long_term["self_understanding"])
    _extend_string_section(lines, "Stable Beliefs", long_term["stable_beliefs"])
    _extend_string_section(lines, "Relationship Models", long_term["relationship_models"])
    return "\n".join(lines).rstrip() + "\n"


def render_key_memories_markdown(update: Any) -> str:
    data = _validated(update)
    lines = [_title(data, "Key Memories"), "", "- source: self", "- visibility: actor", ""]
    if not data["key_memories"]:
        lines.append("- (none)")
    for item in data["key_memories"]:
        lines.append(f"- [{item['importance']}] {item['content']}")
        for detail in item["details"]:
            lines.append(f"  - {detail}")
    return "\n".join(lines).rstrip() + "\n"


def render_short_term_markdown(update: Any) -> str:
    data = _validated(update)
    lines = [_title(data, "Short-Term Memory"), "", "- source: self", "- visibility: actor", ""]
    if not data["short_term"]:
        lines.append("- (none)")
    for item in data["short_term"]:
        lines.append(f"- [{item['expires_after']}] {item['content']}")
    return "\n".join(lines).rstrip() + "\n"


def render_goals_json(update: Any) -> Dict[str, Any]:
    data = _validated(update)
    return {
        "agent_id": data["agent_id"],
        "character_name": data["character_name"],
        "source": "self",
        "visibility": "actor",
        "goals": copy.deepcopy(data["goals"]),
    }


__all__ = [
    "AgentMemoryModelError",
    "ACTOR_FORBIDDEN_MARKERS",
    "FORBIDDEN_ACTOR_PROFILE_KEYS",
    "validate_memory_update",
    "render_long_term_markdown",
    "render_key_memories_markdown",
    "render_short_term_markdown",
    "render_goals_json",
]
