"""Immersive actor context rendering for player and character packets."""

from __future__ import annotations

import re
from typing import Any

import actor_memory_store
import agent_visibility


PROJECTION_CONTROL_MARKERS = {
    "misconceptions",
    "objective_truth",
    "projection_control",
    "control_note",
    "projection_review",
    "belief_is_false",
    "visibility_basis",
    "audit_trail",
    "projection_audit",
    "audit_log",
    "audit_note",
    "audit_trace",
}

ACTOR_FACING_FORBIDDEN_MARKERS = set(agent_visibility.HIDDEN_MARKERS) | PROJECTION_CONTROL_MARKERS
LONG_TERM_MEMORY_LIMIT = 1000
KEY_MEMORY_CUE_LIMIT = 18
STRUCTURED_KEY_MEMORY_CUE_LIMIT = 80


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


def actor_facing_marker_name(value: Any) -> str:
    return agent_visibility.hidden_marker_name(value) or _control_marker_name(value)


def contains_actor_facing_marker(value: Any) -> bool:
    return bool(actor_facing_marker_name(value))


def _clean_text(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return "" if contains_actor_facing_marker(text) else text


def _is_forbidden_key(value: Any) -> bool:
    return contains_actor_facing_marker(value)


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


def _display_key(value: Any) -> str:
    return str(value).strip().replace("_", " ")


def _render_value(value: Any) -> str:
    if isinstance(value, dict):
        parts = []
        for key, child in sorted(value.items(), key=lambda item: str(item[0])):
            rendered = _render_value(child)
            if rendered:
                parts.append(f"{_display_key(key)}: {rendered}")
        return "; ".join(parts)
    if isinstance(value, (list, tuple)):
        parts = [_render_value(item) for item in value]
        return "; ".join(part for part in parts if part)
    text = _clean_text(value)
    return text


def _append_line(lines: list[str], prefix: str, value: Any) -> None:
    cleaned = _clean_value(value)
    if isinstance(cleaned, dict) and "content" in cleaned:
        cleaned = cleaned.get("content")
    text = _render_value(cleaned)
    if text:
        lines.append(f"{prefix}{text}")


def _plain_memory_text(value: Any) -> str:
    cleaned = _clean_value(value)
    if isinstance(cleaned, dict) and "content" in cleaned:
        cleaned = cleaned.get("content")
    text = _render_value(cleaned).strip()
    if not text:
        return ""
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        line = re.sub(r"^[-*]\s*", "", line)
        line = re.sub(r"^\[[^\]]+\]\s*", "", line)
        lower_line = line.lower()
        if lower_line in {"source: self", "visibility: actor"}:
            continue
        if lower_line.startswith((
            "source:",
            "source_agent:",
            "player_authoritative:",
            "source_input_id:",
            "round_id:",
            "importance:",
            "visibility:",
        )):
            continue
        if line:
            lines.append(line)
    return " ".join(lines).strip()


def _append_capped_text(items: list[str], text: str, remaining: int) -> int:
    if remaining <= 0 or not text:
        return remaining
    clipped = text[:remaining].rstrip()
    if clipped:
        items.append(clipped)
    return remaining - len(clipped)


def _clip_recall_cue(text: str, limit: int) -> str:
    cue = text[:limit].rstrip(" ,，.。;；:")
    if len(text) > len(cue):
        cue += "..."
    return cue


def _recall_cue(value: Any) -> str:
    if isinstance(value, dict):
        cue_parts = [
            _plain_memory_text(value.get(key))
            for key in ("tag", "summary")
            if _plain_memory_text(value.get(key))
        ]
        if cue_parts:
            text = "；".join(cue_parts)
            limit = STRUCTURED_KEY_MEMORY_CUE_LIMIT
        else:
            topic = value.get("topic") or value.get("title")
            text = _plain_memory_text(topic if topic else value.get("content"))
            limit = KEY_MEMORY_CUE_LIMIT
    else:
        text = _plain_memory_text(value)
        limit = KEY_MEMORY_CUE_LIMIT
    if not text:
        return ""
    return f"我想回忆：{_clip_recall_cue(text, limit)}"


def _append_unique_memory_value(memory: dict[str, list[Any]], key: str, value: Any) -> None:
    if key not in memory:
        return
    for item in _as_list(value):
        if item not in memory[key]:
            memory[key].append(item)


def _stored_actor_memory(actor_id: str, actor: dict[str, Any]) -> dict[str, Any]:
    card_folder = actor.get("card_folder")
    if not card_folder:
        return {}
    stored = actor_memory_store.read_actor_memory(card_folder, actor_id)
    memory: dict[str, Any] = {
        "profile": _clean_text(stored.get("profile")),
        "long_term": [],
        "key_memories": [],
        "short_term": [],
        "goals": [],
    }
    _append_unique_memory_value(memory, "long_term", stored.get("long_term"))
    _append_unique_memory_value(
        memory,
        "key_memories",
        [
            {"tag": item.get("tag", ""), "summary": item.get("summary", "")}
            for item in _as_list(stored.get("key_memories"))
            if isinstance(item, dict)
        ],
    )
    _append_unique_memory_value(memory, "short_term", stored.get("short_term"))
    return memory


def _merged_actor_memory(actor_id: str, actor: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "profile": "",
        "long_term": [],
        "key_memories": [],
        "short_term": [],
        "goals": [],
    }
    stored = _stored_actor_memory(actor_id, actor)
    merged["profile"] = _clean_text(stored.get("profile"))
    for key in ("long_term", "key_memories", "short_term", "goals"):
        _append_unique_memory_value(merged, key, stored.get(key))

    inline = _as_dict(actor.get("memory"))
    if not merged["profile"]:
        inline_profile = inline.get("profile")
        if isinstance(inline_profile, str):
            merged["profile"] = _clean_text(inline_profile)
    for key in ("long_term", "key_memories", "short_term", "goals"):
        _append_unique_memory_value(merged, key, inline.get(key))
    return merged


def project_actor_memory(memory: dict[str, Any] | None) -> dict[str, list[str]]:
    """Return the actor-facing memory projection used in prompts and packets."""

    source = _as_dict(memory)
    projected = {
        "long_term": [],
        "key_memories": [],
        "short_term": [],
        "goals": [],
    }
    remaining = LONG_TERM_MEMORY_LIMIT
    for value in _as_list(source.get("long_term")):
        remaining = _append_capped_text(projected["long_term"], _plain_memory_text(value), remaining)
        if remaining <= 0:
            break
    for value in _as_list(source.get("key_memories")):
        cue = _recall_cue(value)
        if cue:
            projected["key_memories"].append(cue)
    for value in _as_list(source.get("short_term")):
        text = _plain_memory_text(value)
        if text:
            projected["short_term"].append(text)
    for value in _as_list(source.get("goals")):
        text = _plain_memory_text(value)
        if text:
            projected["goals"].append(text)
    return projected


def _memory_lines(memory: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    projected = project_actor_memory(memory)
    for value in projected["long_term"]:
        _append_line(lines, "我记得：", value)
    if projected["key_memories"]:
        lines.append(
            "有些记忆一时间有些模糊，只记得大概。"
            "如果觉得现在需要想起来，可以想想："
            + "；".join(projected["key_memories"])
            + "。"
        )
    for value in projected["short_term"]:
        _append_line(lines, "刚才我记得：", value)
    for value in projected["goals"]:
        _append_line(lines, "我现在想要：", value)
    return lines


def render_actor_context(actor_id: str, actor_state: dict[str, Any] | None, world_state: dict[str, Any] | None) -> dict[str, Any]:
    actor = _as_dict(actor_state)
    world = _as_dict(world_state)
    memory = _merged_actor_memory(actor_id, actor)
    lines: list[str] = []

    if actor_id == "player":
        lines.append("我是当前扮演的角色。")
    else:
        name = _clean_text(actor.get("name") or actor.get("character_name") or actor_id.split(":", 1)[-1])
        role = _clean_text(actor.get("role") or actor.get("identity"))
        lines.append(f"我是 {name}。" if name else "我是这个角色。")
        if role:
            lines.append(f"我的身份是：{role}。")

    profile = _plain_memory_text(memory.get("profile"))
    if profile:
        lines.append(profile)

    body_state = _as_dict(actor.get("body_state"))
    for key, value in sorted(body_state.items()):
        if _is_forbidden_key(key):
            continue
        _append_line(lines, f"我的 {key}：", value)

    relationships = _as_dict(actor.get("relationships"))
    for key, value in sorted(relationships.items()):
        if _is_forbidden_key(key):
            continue
        _append_line(lines, f"我和 {key} 的关系：", value)

    sensory = _as_dict(actor.get("sensory_context") or world.get("sensory_context"))
    for key, value in sorted(sensory.items()):
        if _is_forbidden_key(key):
            continue
        _append_line(lines, f"我能通过 {key} 感到：", value)

    lines.extend(_memory_lines(memory))

    return {
        "actor_id": str(actor_id or ""),
        "immersive_context": "\n".join(line for line in lines if line).strip(),
    }
