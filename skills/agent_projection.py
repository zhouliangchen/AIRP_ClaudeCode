"""Actor perspective projection for interactive RP agent calls."""

from __future__ import annotations

import copy
import math
import re
from typing import Any, Dict

import agent_visibility


ADDRESS_MODE = "second_person_gm_narration"
SEGMENT_RE = re.compile(r"[^.!?;。！？；\r\n]+[.!?;。！？；]?")

PROJECTION_FORBIDDEN_WORLD_KEYS = {
    "gm_only_hidden_settings",
    "gm_notes",
    "recent_chat",
    "private_events",
    "hidden_identity_facts",
}

FORBIDDEN_WORLD_KEYS = set(agent_visibility.HIDDEN_MARKERS) | PROJECTION_FORBIDDEN_WORLD_KEYS

PROJECTION_FORBIDDEN_NESTED_KEYS = {
    "hidden_identity",
}

FORBIDDEN_NESTED_KEYS = FORBIDDEN_WORLD_KEYS | PROJECTION_FORBIDDEN_NESTED_KEYS


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _is_forbidden_key(key: Any) -> bool:
    return bool(agent_visibility.hidden_marker_name(key, FORBIDDEN_NESTED_KEYS))


def _contains_forbidden_text(value: Any) -> bool:
    return bool(agent_visibility.hidden_marker_name(value, FORBIDDEN_NESTED_KEYS))


def _safe_text(value: Any) -> str:
    text = _text(value)
    return "" if _contains_forbidden_text(text) else text


def _sanitize_prompt(value: Any) -> str:
    text = _text(value).strip()
    if not text:
        return ""

    kept = []
    for match in SEGMENT_RE.finditer(text):
        segment = match.group(0).strip()
        if not segment:
            continue
        if _contains_forbidden_text(segment):
            break
        kept.append(segment)
    return " ".join(kept)


def _json_safe(value: Any) -> Any:
    """Return a copied JSON-serializable value with forbidden keys removed."""
    if isinstance(value, dict):
        return {
            str(key): _json_safe(child)
            for key, child in value.items()
            if not _is_forbidden_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return [_json_safe(item) for item in sorted(value, key=lambda item: str(item))]
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (int, bool)):
        return copy.deepcopy(value)
    return str(value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return [_json_safe(item) for item in sorted(value, key=lambda item: str(item))]
    return [_json_safe(value)]


def _first_present(payload: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _actor_visibility(actor_id: str) -> str:
    return "first_person_player" if actor_id == "player" else "first_person_character"


def _agent_type(actor_id: str) -> str:
    return "player" if actor_id == "player" else "character"


def _self_knowledge(actor: Dict[str, Any]) -> Dict[str, Any]:
    nested = _as_dict(actor.get("self_knowledge"))
    identity = _first_present(nested, "identity", "position")
    if identity is None:
        identity = _first_present(actor, "identity", "position")
    role = _first_present(nested, "role")
    if role is None:
        role = actor.get("role")
    return {
        "name": _safe_text(
            _first_present(nested, "name", "character_name")
            or _first_present(actor, "name", "character_name")
        ),
        "identity": _safe_text(identity),
        "role": _safe_text(role),
        "body_state": _json_safe(_first_present(nested, "body_state") or actor.get("body_state") or {}),
        "relationships": _json_safe(_first_present(nested, "relationships") or actor.get("relationships") or {}),
    }


def _memory(actor: Dict[str, Any]) -> Dict[str, Any]:
    raw_memory = actor.get("memory")
    source = raw_memory if isinstance(raw_memory, dict) else actor
    long_term = source.get("long_term")
    key_memories = source.get("key_memories")
    short_term = source.get("short_term")
    goals = source.get("goals")

    return {
        "long_term": _as_list(long_term),
        "key_memories": _as_list(key_memories),
        "short_term": _as_list(short_term),
        "goals": _as_list(goals),
    }


def _sensory_context(world: Dict[str, Any], actor: Dict[str, Any], actor_id: str) -> Any:
    if "sensory_context" in actor:
        return _json_safe(actor.get("sensory_context"))
    actor_sensory = _as_dict(world.get("actor_sensory_context"))
    if actor_id in actor_sensory:
        return _json_safe(actor_sensory.get(actor_id))
    if "sensory_context" in world:
        return _json_safe(world.get("sensory_context"))
    return {}


def _collect_events(
    value: Any,
    actor_id: str,
    actor: Dict[str, Any],
    *,
    source_bucket_actor_id: str = "",
) -> list[Any]:
    return [
        _json_safe(item)
        for item in agent_visibility.filter_visible_events(
            value,
            actor_id,
            actor,
            source_bucket_actor_id=source_bucket_actor_id,
        )
    ]


def _actor_specific_events(world: Dict[str, Any], actor_id: str, actor: Dict[str, Any]) -> list[Any]:
    actor_visible = _as_dict(world.get("actor_visible_events"))
    events = []
    checked_keys = []
    for key in (actor_id, _agent_type(actor_id), "all", "public"):
        if key not in checked_keys:
            checked_keys.append(key)
    for key in checked_keys:
        if key in actor_visible:
            events.extend(
                _collect_events(
                    actor_visible.get(key),
                    actor_id,
                    actor,
                    source_bucket_actor_id=key,
                )
            )
    return events


def _visible_events(world: Dict[str, Any], actor_id: str, actor: Dict[str, Any]) -> list[Any]:
    events = []
    for key in ("visible_events", "world_visible_events", "public_events"):
        if key in world:
            events.extend(_collect_events(world.get(key), actor_id, actor))
    if "events" in world:
        events.extend(_collect_events(world.get("events"), actor_id, actor))
    events.extend(_actor_specific_events(world, actor_id, actor))
    return events


def project_actor_context(
    actor_id: str,
    world_state: dict | None,
    actor_state: dict | None,
    gm_prompt: str,
    gm_visibility_basis: dict | None = None,
) -> dict:
    """Return the only compact context an actor agent may see for one GM call."""
    actor_key = _text(actor_id)
    world = _as_dict(world_state)
    actor = _as_dict(actor_state)

    return {
        "actor_id": actor_key,
        "agent": _agent_type(actor_key),
        "visibility": _actor_visibility(actor_key),
        "gm_prompt": _sanitize_prompt(gm_prompt),
        "gm_visibility_basis": _json_safe(
            agent_visibility.normalize_visibility_basis(gm_visibility_basis or {})
        ),
        "address_mode": ADDRESS_MODE,
        "self_knowledge": _self_knowledge(actor),
        "memory": _memory(actor),
        "sensory_context": _sensory_context(world, actor, actor_key),
        "visible_events": _visible_events(world, actor_key, actor),
        "misconceptions": _as_list(actor.get("misconceptions")),
        "role_channel_anchor": _text(world.get("role_channel")) if actor_key == "player" else "",
    }


__all__ = ["FORBIDDEN_WORLD_KEYS", "project_actor_context"]
