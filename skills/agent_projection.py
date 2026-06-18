"""Actor perspective projection for interactive RP agent calls."""

from __future__ import annotations

import copy
from typing import Any, Dict


ADDRESS_MODE = "second_person_gm_narration"

FORBIDDEN_WORLD_KEYS = {
    "user_instruction_channel",
    "gm_only_hidden_settings",
    "hidden_facts",
    "world_truth",
    "gm_notes",
    "recent_chat",
    "private_events",
    "omniscient",
    "out_of_character",
    "hidden_note",
    "gm_only",
    "hidden_identity_facts",
}

FORBIDDEN_NESTED_KEYS = FORBIDDEN_WORLD_KEYS | {
    "hidden_identity",
    "private_memory",
    "internal_state",
    "internal_thoughts",
}

PRIVATE_EVENT_TYPES = {
    "gm_only",
    "hidden",
    "internal",
    "memory_delta",
    "out_of_character",
    "private",
    "thought",
}

PRIVATE_EVENT_VISIBILITIES = {
    "gm_only",
    "hidden",
    "internal",
    "omniscient",
    "out_of_character",
    "private",
}

PUBLIC_VISIBLE_MARKERS = {
    "all",
    "actor",
    "actors",
    "everyone",
    "public",
    "visible",
    "world",
    "world_visible",
}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _json_safe(value: Any) -> Any:
    """Return a copied JSON-serializable value with forbidden keys removed."""
    if isinstance(value, dict):
        return {
            str(key): _json_safe(child)
            for key, child in value.items()
            if str(key) not in FORBIDDEN_NESTED_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return [_json_safe(item) for item in sorted(value, key=lambda item: str(item))]
    if value is None or isinstance(value, (str, int, float, bool)):
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
        "name": _text(_first_present(nested, "name", "character_name") or _first_present(actor, "name", "character_name")),
        "identity": _text(identity),
        "role": _text(role),
        "body_state": _json_safe(_first_present(nested, "body_state") or actor.get("body_state") or {}),
        "relationships": _json_safe(_first_present(nested, "relationships") or actor.get("relationships") or {}),
    }


def _memory(actor: Dict[str, Any]) -> Dict[str, Any]:
    memory = actor.get("memory")
    memory_dict = _as_dict(memory)
    if memory_dict:
        long_term = _first_present(memory_dict, "long_term", "long_term_memory", "memories", "memory")
        recent = _first_present(memory_dict, "recent", "recent_memory")
        goals = _first_present(memory_dict, "goals", "current_goals")
    else:
        long_term = _first_present(actor, "long_term", "long_term_memory", "memory", "memories")
        recent = _first_present(actor, "recent", "recent_memory")
        goals = _first_present(actor, "goals", "current_goals")

    if goals is None:
        goals = actor.get("goals")

    return {
        "long_term": _as_list(long_term),
        "recent": _as_list(recent),
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


def _has_forbidden_marker(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) in FORBIDDEN_NESTED_KEYS:
                return True
            if _has_forbidden_marker(child):
                return True
    elif isinstance(value, (list, tuple, set)):
        return any(_has_forbidden_marker(item) for item in value)
    return False


def _normalize_marker_list(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value}
    return {str(value)}


def _event_visible_to_actor(event: Any, actor_id: str) -> bool:
    if not isinstance(event, dict):
        return True
    if _has_forbidden_marker(event):
        return False

    event_type = str(event.get("type", "")).lower()
    visibility = str(event.get("visibility", "")).lower()
    if event_type in PRIVATE_EVENT_TYPES or visibility in PRIVATE_EVENT_VISIBILITIES:
        return False

    visible_to = _normalize_marker_list(event.get("visible_to") or event.get("recipients"))
    if visible_to and not (
        actor_id in visible_to
        or _agent_type(actor_id) in visible_to
        or visible_to.intersection(PUBLIC_VISIBLE_MARKERS)
    ):
        return False
    return True


def _collect_events(value: Any, actor_id: str) -> list[Any]:
    events = []
    for item in _as_list(value):
        if _event_visible_to_actor(item, actor_id):
            events.append(_json_safe(item))
    return events


def _actor_specific_events(world: Dict[str, Any], actor_id: str) -> list[Any]:
    actor_visible = _as_dict(world.get("actor_visible_events"))
    events = []
    for key in (actor_id, _agent_type(actor_id), "all", "public"):
        if key in actor_visible:
            events.extend(_collect_events(actor_visible.get(key), actor_id))
    return events


def _visible_events(world: Dict[str, Any], actor_id: str) -> list[Any]:
    events = []
    for key in ("visible_events", "world_visible_events", "public_events"):
        if key in world:
            events.extend(_collect_events(world.get(key), actor_id))
    if "events" in world:
        for item in _as_list(world.get("events")):
            if not isinstance(item, dict):
                continue
            visibility = str(item.get("visibility", "")).lower()
            if visibility in PUBLIC_VISIBLE_MARKERS and _event_visible_to_actor(item, actor_id):
                events.append(_json_safe(item))
    events.extend(_actor_specific_events(world, actor_id))
    return events


def project_actor_context(
    actor_id: str,
    world_state: dict | None,
    actor_state: dict | None,
    gm_prompt: str,
) -> dict:
    """Return the only compact context an actor agent may see for one GM call."""
    actor_key = _text(actor_id)
    world = _as_dict(world_state)
    actor = _as_dict(actor_state)
    forbidden_removed = sorted(key for key in FORBIDDEN_WORLD_KEYS if key in world)

    return {
        "actor_id": actor_key,
        "agent": _agent_type(actor_key),
        "visibility": _actor_visibility(actor_key),
        "gm_prompt": _text(gm_prompt),
        "address_mode": ADDRESS_MODE,
        "self_knowledge": _self_knowledge(actor),
        "memory": _memory(actor),
        "sensory_context": _sensory_context(world, actor, actor_key),
        "visible_events": _visible_events(world, actor_key),
        "misconceptions": _as_list(actor.get("misconceptions")),
        "role_channel_anchor": _text(world.get("role_channel")) if actor_key == "player" else "",
        "forbidden_removed": forbidden_removed,
    }


__all__ = ["FORBIDDEN_WORLD_KEYS", "project_actor_context"]
