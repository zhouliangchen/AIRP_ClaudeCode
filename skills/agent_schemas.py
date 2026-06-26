"""Validation helpers for multi-agent round artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict

import agent_visibility
import character_promotions


class ValidationError(ValueError):
    """Raised when an agent artifact does not match its file contract."""


FORBIDDEN_ACTOR_KEYS = {
    "gm_only",
    "gm_notes",
    "hidden_note",
    "omniscient",
    "out_of_character",
    "player_name",
    "world_truth",
}
FORBIDDEN_ACTOR_MARKERS = set(FORBIDDEN_ACTOR_KEYS) | set(agent_visibility.HIDDEN_MARKERS)

LEGACY_ACTOR_KEYS = {"action", "dialogue", "perception", "memory_delta"}
ACTOR_OUTPUT_KEYS = {"agent", "agent_id", "character_name", "context_version", "events", "natural_reply"}
ACTOR_EVENT_TYPES = {"reply"}
ACTOR_EVENT_KEYS = {"type", "target", "content", "metadata"}

GM_STOP_REASONS = {"continue", "player_decision", "word_target", "complete", "max_steps"}
CRITIC_DECISIONS = {"pass", "revise", "block"}
CRITIC_QUALITY_STATUSES = {"pass", "revise", "block", "not_checked"}
CRITIC_LENGTH_STATUSES = CRITIC_QUALITY_STATUSES | {"exempt"}

SUBGM_COMMAND_ACTIONS = {"start", "message", "accelerate", "pause", "resume", "merge", "close"}
SUBGM_OUTPUT_STATUSES = {"running", "paused", "completed", "blocked", "needs_gm"}
SUBGM_FORBIDDEN_OUTPUT_KEYS = {"character_promotions", "subgm_commands"}
SUBGM_OUTPUT_KEYS = [
    "agent",
    "thread_id",
    "status",
    "scene_beats",
    "events",
    "actor_calls",
    "messages_to_gm",
    "world_state_delta",
    "character_usage",
    "promotion_requests",
    "boundary_requests",
    "notes_for_story",
    "next_resume_point",
]


def _path(parent: str, key: str) -> str:
    return f"{parent}.{key}" if parent else key


def _require_dict(value: Any, path: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{path} must be an object")
    return dict(value)


def _require_str(payload: Dict[str, Any], key: str, path: str = "") -> str:
    full_path = _path(path, key)
    if key not in payload:
        raise ValidationError(f"{full_path} is required")
    value = payload[key]
    if not isinstance(value, str):
        raise ValidationError(f"{full_path} must be a string")
    return value


def _require_nonempty_str(payload: Dict[str, Any], key: str, path: str = "") -> str:
    full_path = _path(path, key)
    value = _require_str(payload, key, path).strip()
    if not value:
        raise ValidationError(f"{full_path} must not be blank")
    return value


def _optional_str(payload: Dict[str, Any], key: str, default: str = "", path: str = "") -> str:
    full_path = _path(path, key)
    value = payload.get(key, default)
    if not isinstance(value, str):
        raise ValidationError(f"{full_path} must be a string")
    return value


def _require_list(payload: Dict[str, Any], key: str, path: str = "") -> list[Any]:
    full_path = _path(path, key)
    if key not in payload:
        raise ValidationError(f"{full_path} is required")
    value = payload[key]
    if not isinstance(value, list):
        raise ValidationError(f"{full_path} must be a list")
    return list(value)


def _optional_list(payload: Dict[str, Any], key: str, path: str = "") -> list[Any]:
    full_path = _path(path, key)
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise ValidationError(f"{full_path} must be a list")
    return list(value)


def _optional_dict(payload: Dict[str, Any], key: str, path: str = "") -> Dict[str, Any]:
    full_path = _path(path, key)
    value = payload.get(key, {})
    if not isinstance(value, dict):
        raise ValidationError(f"{full_path} must be an object")
    return dict(value)


def _require_agent(payload: Dict[str, Any], expected: str, path: str = "") -> str:
    agent = _require_str(payload, "agent", path)
    if agent != expected:
        raise ValidationError(f"{_path(path, 'agent')} must be {expected!r}")
    return agent


def _canonical_tokens(text: str) -> list[str]:
    raw = str(text or "")
    acronym_separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", raw)
    camel_separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", acronym_separated)
    return re.findall(r"[a-z0-9]+", camel_separated.lower())


FORBIDDEN_ACTOR_KEY_TOKENS = {
    marker: tuple(_canonical_tokens(marker))
    for marker in sorted(FORBIDDEN_ACTOR_MARKERS)
}


def _forbidden_actor_marker(text: str) -> str:
    tokens = _canonical_tokens(text)
    if tokens:
        for marker, marker_tokens in FORBIDDEN_ACTOR_KEY_TOKENS.items():
            if not marker_tokens or len(marker_tokens) > len(tokens):
                continue
            for index in range(0, len(tokens) - len(marker_tokens) + 1):
                if tuple(tokens[index:index + len(marker_tokens)]) == marker_tokens:
                    return marker
    return agent_visibility.hidden_marker_name(text, FORBIDDEN_ACTOR_MARKERS)


def _validate_actor_id_marker(actor_id: str, path: str) -> str:
    actor_key = str(actor_id or "").strip()
    if not actor_key:
        raise ValidationError(f"{path} must not be blank")
    marker_text = actor_key
    if actor_key.startswith("character:"):
        suffix = actor_key.split(":", 1)[1].strip()
        if not suffix:
            raise ValidationError(f"{path} must include a character id after 'character:'")
        marker_text = suffix
    marker = _forbidden_actor_marker(marker_text)
    if marker:
        raise ValidationError(f"{path}: forbidden actor marker {marker}")
    return actor_key


def _validate_actor_agent_id(agent: str, agent_id: str, path: str) -> str:
    if agent == "player":
        if agent_id != "player":
            raise ValidationError("actor_output.agent_id must be 'player' when agent is 'player'")
        return agent_id
    if not agent_id.startswith("character:"):
        raise ValidationError("actor_output.agent_id must start with 'character:' when agent is 'character'")
    suffix = agent_id.split(":", 1)[1].strip()
    if not suffix:
        raise ValidationError(f"{path} must include a character id after 'character:'")
    marker = _forbidden_actor_marker(suffix)
    if marker:
        raise ValidationError(f"{path}: forbidden actor marker {marker}")
    return agent_id


def _reject_forbidden_keys(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = _path(path, str(key))
            marker = _forbidden_actor_marker(str(key))
            if marker:
                raise ValidationError(f"{child_path}: forbidden actor marker {marker}")
            _reject_forbidden_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, f"{path}[{index}]")
    elif isinstance(value, str):
        marker = _forbidden_actor_marker(value)
        if marker:
            raise ValidationError(f"{path}: forbidden actor marker {marker}")


def _reject_legacy_actor_keys(payload: Dict[str, Any], path: str) -> None:
    for key in sorted(LEGACY_ACTOR_KEYS):
        if key in payload:
            raise ValidationError(f"{_path(path, key)} is a legacy actor output key")


def _normalize_actor_event(item: Any, path: str) -> Dict[str, Any]:
    data = _require_dict(item, path)
    for key in sorted(data):
        if key not in ACTOR_EVENT_KEYS:
            raise ValidationError(f"{_path(path, str(key))} is not an allowed actor event field")
    event_type = _require_str(data, "type", path)
    if event_type not in ACTOR_EVENT_TYPES:
        raise ValidationError(f"{_path(path, 'type')} is not an allowed actor event type")
    metadata = _optional_dict(data, "metadata", path)
    if metadata:
        raise ValidationError(f"{_path(path, 'metadata')} must be empty for actor natural replies")
    content = _require_str(data, "content", path)
    target = _optional_str(data, "target", "", path)
    normalized = {
        "type": event_type,
        "target": target,
        "content": content,
        "metadata": metadata,
    }
    _reject_forbidden_keys(normalized, path)
    return normalized


def _normalize_actor_events(items: list[Any], path: str) -> list[Dict[str, Any]]:
    if not items:
        raise ValidationError(f"{path} must not be empty")
    return [_normalize_actor_event(item, f"{path}[{index}]") for index, item in enumerate(items)]


def natural_actor_output(actor_id: str, reply: Any, character_name: str = "") -> Dict[str, Any]:
    """Wrap one actor natural-language reply in the internal actor artifact shape."""

    actor_key = _validate_actor_agent_id(
        "player" if actor_id == "player" else "character",
        str(actor_id or "").strip(),
        "actor_output.agent_id",
    )
    content = str(reply or "").strip()
    if not content:
        raise ValidationError("actor natural reply must not be blank")
    _reject_forbidden_keys({"natural_reply": content}, "actor_output")

    agent = "player" if actor_key == "player" else "character"
    output: Dict[str, Any] = {
        "agent": agent,
        "agent_id": actor_key,
        "natural_reply": content,
        "events": [
            {
                "type": "reply",
                "target": "gm",
                "content": content,
                "metadata": {},
            }
        ],
    }
    if agent == "character":
        name = str(character_name or actor_key.split(":", 1)[-1]).strip()
        if name:
            output["character_name"] = name
    return output


def _normalize_gm_scene_beat(item: Any, path: str) -> Dict[str, Any]:
    data = _require_dict(item, path)
    normalized = {"content": _require_str(data, "content", path)}
    if "metadata" in data:
        normalized["metadata"] = _optional_dict(data, "metadata", path)
    normalized.update(_normalize_visibility_fields(data, path, require_basis=False))
    return normalized


def _normalize_gm_event(item: Any, path: str) -> Dict[str, Any]:
    data = _require_dict(item, path)
    normalized = {
        "type": _require_str(data, "type", path),
        "content": _require_str(data, "content", path),
    }
    if "target" in data:
        normalized["target"] = _optional_str(data, "target", "", path)
    if "source_call_id" in data:
        normalized["source_call_id"] = _optional_str(data, "source_call_id", "", path)
    if "metadata" in data:
        normalized["metadata"] = _optional_dict(data, "metadata", path)
    normalized.update(_normalize_visibility_fields(data, path, require_basis=False))
    return normalized


def _normalize_gm_actor_call(item: Any, path: str) -> Dict[str, Any]:
    data = _require_dict(item, path)
    actor_id = _validate_actor_id_marker(_require_str(data, "actor_id", path).strip(), _path(path, "actor_id"))
    normalized = {
        "call_id": _require_str(data, "call_id", path),
        "actor_id": actor_id,
        "prompt": _require_str(data, "prompt", path),
        "reason": _require_str(data, "reason", path),
    }
    if "metadata" in data:
        normalized["metadata"] = _optional_dict(data, "metadata", path)
    normalized.update(_normalize_visibility_fields(data, path, require_basis=True))
    return normalized


def _normalize_subgm_actor_call(item: Any, path: str) -> Dict[str, Any]:
    data = _require_dict(item, path)
    raw_actor_id = _require_str(data, "actor_id", path).strip()
    if raw_actor_id == "player":
        raise ValidationError(f"{_path(path, 'actor_id')} must not target player")
    if not raw_actor_id.startswith("character:"):
        raise ValidationError(f"{_path(path, 'actor_id')} must start with 'character:'")
    actor_id = _validate_actor_id_marker(raw_actor_id, _path(path, "actor_id"))
    normalized = {
        "call_id": _require_nonempty_str(data, "call_id", path),
        "actor_id": actor_id,
        "prompt": _require_str(data, "prompt", path),
        "reason": _require_str(data, "reason", path),
    }
    if "metadata" in data:
        normalized["metadata"] = _optional_dict(data, "metadata", path)
    normalized.update(_normalize_visibility_fields(data, path, require_basis=True))
    return normalized


def _normalize_gm_stop_reason(data: dict, path: str) -> str:
    stop_reason = _optional_str(data, "stop_reason", "continue", path).strip()
    if stop_reason not in GM_STOP_REASONS:
        allowed = ", ".join(sorted(GM_STOP_REASONS))
        raise ValidationError(f"{_path(path, 'stop_reason')} must be one of: {allowed}")
    return stop_reason


def _normalize_character_ref(item: Any, path: str) -> str:
    if not isinstance(item, str):
        raise ValidationError(f"{path} must be a string")
    actor_id = item.strip()
    if not actor_id.startswith("character:"):
        raise ValidationError(f"{path} must start with 'character:'")
    return _validate_actor_id_marker(actor_id, path)


def _normalize_forbidden_ref(item: Any, path: str) -> str:
    if not isinstance(item, str):
        raise ValidationError(f"{path} must be a string")
    actor_id = item.strip()
    if actor_id == "player":
        return actor_id
    if not actor_id.startswith("character:"):
        raise ValidationError(f"{path} must be 'player' or start with 'character:'")
    return _validate_actor_id_marker(actor_id, path)


def _normalize_dict_item(item: Any, path: str) -> Dict[str, Any]:
    return _require_dict(item, path)


def _normalize_nonempty_str_item(item: Any, path: str) -> str:
    if not isinstance(item, str):
        raise ValidationError(f"{path} must be a string")
    value = item.strip()
    if not value:
        raise ValidationError(f"{path} must not be blank")
    return value


def _normalize_visibility_fields(data: Dict[str, Any], path: str, *, require_basis: bool = False) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for field in ("scene_id", "location", "time_window", "source_actor", "target_actor"):
        if field in data:
            value = data[field]
            if not isinstance(value, str):
                value = str(value)
            normalized[field] = value
    for field in ("visible_to", "sensory_channels"):
        if field in data:
            normalized[field] = _normalize_list_items(
                _optional_list(data, field, path),
                _path(path, field),
                _normalize_nonempty_str_item,
            )
    if "visibility_basis" in data:
        raw_basis = _require_dict(data["visibility_basis"], _path(path, "visibility_basis"))
        _reject_forbidden_keys(raw_basis, _path(path, "visibility_basis"))
        basis = agent_visibility.normalize_visibility_basis(
            raw_basis,
            require_summary=require_basis,
        )
        if require_basis and not basis:
            raise ValidationError(f"{_path(path, 'visibility_basis')} must be a visibility proof object")
        if require_basis and not basis.get("mode"):
            raise ValidationError(f"{_path(path, 'visibility_basis')}.mode must be a supported visibility proof mode")
        normalized["visibility_basis"] = basis
    elif require_basis:
        raise ValidationError(f"{_path(path, 'visibility_basis')} is required")
    _reject_forbidden_keys(normalized, path)
    return normalized


def _normalize_character_promotion(item: Any, path: str) -> Dict[str, Any]:
    try:
        return character_promotions.validate_promotion(item, path)
    except character_promotions.CharacterPromotionError as exc:
        message = str(exc)
        if "subGM sources" in message:
            message = f"{message}; gm_output.character_promotions accepts applied promotion records only"
        raise ValidationError(message) from exc


def _normalize_gm_character_promotion(item: Any, path: str) -> Dict[str, Any]:
    normalized = _normalize_character_promotion(item, path)
    if normalized.get("source_agent") != "gm":
        raise ValidationError(f"{_path(path, 'source_agent')} must be 'gm' for gm_output.character_promotions")
    return normalized


def _normalize_list_items(
    items: list[Any],
    path: str,
    normalizer: Callable[[Any, str], Any],
) -> list[Any]:
    return [normalizer(item, f"{path}[{index}]") for index, item in enumerate(items)]


def _normalize_subgm_command(item: Any, path: str) -> Dict[str, Any]:
    data = _require_dict(item, path)
    action = _require_str(data, "action", path)
    if action not in SUBGM_COMMAND_ACTIONS:
        raise ValidationError(f"{_path(path, 'action')} is not an allowed subGM command action")
    thread_id = _require_nonempty_str(data, "thread_id", path)

    if action == "start":
        return {
            "action": action,
            "thread_id": thread_id,
            "title": _require_nonempty_str(data, "title", path),
            "outline": _require_nonempty_str(data, "outline", path),
            "time_window": _require_nonempty_str(data, "time_window", path),
            "location": _require_nonempty_str(data, "location", path),
            "objective": _require_nonempty_str(data, "objective", path),
            "allowed_characters": _normalize_list_items(
                _optional_list(data, "allowed_characters", path),
                _path(path, "allowed_characters"),
                _normalize_character_ref,
            ),
            "forbidden_characters": _normalize_list_items(
                _optional_list(data, "forbidden_characters", path),
                _path(path, "forbidden_characters"),
                _normalize_forbidden_ref,
            ),
            "priority": _optional_str(data, "priority", "", path),
            "message": _optional_str(data, "message", "", path),
            "metadata": _optional_dict(data, "metadata", path),
        }

    return {
        "action": action,
        "thread_id": thread_id,
        "title": _optional_str(data, "title", "", path),
        "outline": _optional_str(data, "outline", "", path),
        "time_window": _optional_str(data, "time_window", "", path),
        "location": _optional_str(data, "location", "", path),
        "objective": _optional_str(data, "objective", "", path),
        "allowed_characters": _normalize_list_items(
            _optional_list(data, "allowed_characters", path),
            _path(path, "allowed_characters"),
            _normalize_character_ref,
        ),
        "forbidden_characters": _normalize_list_items(
            _optional_list(data, "forbidden_characters", path),
            _path(path, "forbidden_characters"),
            _normalize_forbidden_ref,
        ),
        "priority": _optional_str(data, "priority", "", path),
        "message": _require_nonempty_str(data, "message", path),
        "metadata": _optional_dict(data, "metadata", path),
    }


def validate_gm_output(payload: Any) -> Dict[str, Any]:
    """Validate and normalize `gm.output.json`."""
    data = _require_dict(payload, "gm_output")
    if "perception_responses" in data:
        raise ValidationError(
            "gm_output.perception_responses is a legacy actor-facing protocol; "
            "send sensory feedback through natural-language actor_calls[].prompt"
        )
    return {
        "agent": _require_agent(data, "gm", "gm_output"),
        "scene_beats": _normalize_list_items(
            _require_list(data, "scene_beats", "gm_output"),
            "gm_output.scene_beats",
            _normalize_gm_scene_beat,
        ),
        "events": _normalize_list_items(
            _require_list(data, "events", "gm_output"),
            "gm_output.events",
            _normalize_gm_event,
        ),
        "actor_calls": _normalize_list_items(
            _require_list(data, "actor_calls", "gm_output"),
            "gm_output.actor_calls",
            _normalize_gm_actor_call,
        ),
        "parallel_groups": _optional_list(data, "parallel_groups", "gm_output"),
        "world_state_delta": _require_list(data, "world_state_delta", "gm_output"),
        "character_promotions": _normalize_list_items(
            _optional_list(data, "character_promotions", "gm_output"),
            "gm_output.character_promotions",
            _normalize_gm_character_promotion,
        ),
        "decision_point": data.get("decision_point"),
        "stop_reason": _normalize_gm_stop_reason(data, "gm_output"),
        "subgm_commands": _normalize_list_items(
            _optional_list(data, "subgm_commands", "gm_output"),
            "gm_output.subgm_commands",
            _normalize_subgm_command,
        ),
    }


def validate_subgm_output(payload: Any) -> Dict[str, Any]:
    """Validate and normalize a subGM side-thread output artifact."""
    data = _require_dict(payload, "subgm_output")
    for key in sorted(SUBGM_FORBIDDEN_OUTPUT_KEYS):
        if key in data:
            raise ValidationError(f"subgm_output must not contain {key}")

    agent = _require_agent(data, "subGM", "subgm_output")
    thread_id = _require_nonempty_str(data, "thread_id", "subgm_output")
    status = _require_str(data, "status", "subgm_output")
    if status not in SUBGM_OUTPUT_STATUSES:
        raise ValidationError(f"subgm_output.status is not an allowed subGM status")

    normalized = {
        "agent": agent,
        "thread_id": thread_id,
        "status": status,
        "scene_beats": _normalize_list_items(
            _require_list(data, "scene_beats", "subgm_output"),
            "subgm_output.scene_beats",
            _normalize_gm_scene_beat,
        ),
        "events": _normalize_list_items(
            _require_list(data, "events", "subgm_output"),
            "subgm_output.events",
            _normalize_gm_event,
        ),
        "actor_calls": _normalize_list_items(
            _require_list(data, "actor_calls", "subgm_output"),
            "subgm_output.actor_calls",
            _normalize_subgm_actor_call,
        ),
        "messages_to_gm": _normalize_list_items(
            _require_list(data, "messages_to_gm", "subgm_output"),
            "subgm_output.messages_to_gm",
            _normalize_dict_item,
        ),
        "world_state_delta": _normalize_list_items(
            _require_list(data, "world_state_delta", "subgm_output"),
            "subgm_output.world_state_delta",
            _normalize_dict_item,
        ),
        "character_usage": _normalize_list_items(
            _require_list(data, "character_usage", "subgm_output"),
            "subgm_output.character_usage",
            _normalize_character_ref,
        ),
        "promotion_requests": _normalize_list_items(
            _require_list(data, "promotion_requests", "subgm_output"),
            "subgm_output.promotion_requests",
            _normalize_dict_item,
        ),
        "boundary_requests": _normalize_list_items(
            _require_list(data, "boundary_requests", "subgm_output"),
            "subgm_output.boundary_requests",
            _normalize_dict_item,
        ),
        "notes_for_story": _normalize_list_items(
            _require_list(data, "notes_for_story", "subgm_output"),
            "subgm_output.notes_for_story",
            _normalize_nonempty_str_item,
        ),
        "next_resume_point": _optional_str(data, "next_resume_point", "", "subgm_output"),
    }
    return {key: normalized[key] for key in SUBGM_OUTPUT_KEYS}


def validate_actor_output(payload: Any) -> Dict[str, Any]:
    """Validate the internal wrapper for one actor natural-language reply."""
    data = _require_dict(payload, "actor_output")
    for key in sorted(data):
        if key not in ACTOR_OUTPUT_KEYS:
            raise ValidationError(f"actor_output.{key} is not an allowed actor output field")
    envelope = dict(data)
    envelope.pop("events", None)
    _reject_forbidden_keys(envelope, "actor_output")
    _reject_legacy_actor_keys(data, "actor_output")

    agent = _require_str(data, "agent", "actor_output")
    if agent not in {"player", "character"}:
        raise ValidationError("actor_output.agent must be 'player' or 'character'")
    agent_id = _validate_actor_agent_id(
        agent,
        _require_str(data, "agent_id", "actor_output").strip(),
        "actor_output.agent_id",
    )

    normalized = {
        "agent": agent,
        "agent_id": agent_id,
        "natural_reply": _require_nonempty_str(data, "natural_reply", "actor_output"),
        "events": _normalize_actor_events(_require_list(data, "events", "actor_output"), "actor_output.events"),
    }
    if len(normalized["events"]) != 1:
        raise ValidationError("actor_output.events must contain exactly one natural reply event")
    reply_event = normalized["events"][0]
    if reply_event["content"].strip() != normalized["natural_reply"]:
        raise ValidationError("actor_output.events[0].content must match actor_output.natural_reply")
    if agent == "character":
        normalized["character_name"] = _optional_str(data, "character_name", path="actor_output")
    return normalized


def validate_story_output(payload: Any) -> Dict[str, Any]:
    """Validate and normalize `story.output.json`."""
    data = _require_dict(payload, "story_output")
    return {
        "content": _require_str(data, "content", "story_output"),
        "character_dialogues": _optional_list(data, "character_dialogues", "story_output"),
        "metadata": _optional_dict(data, "metadata", "story_output"),
    }


def _optional_int(payload: Dict[str, Any], key: str, default: int = 0, path: str = "") -> int:
    full_path = _path(path, key)
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{full_path} must be an integer")
    return value


def _optional_bool(payload: Dict[str, Any], key: str, default: bool = False, path: str = "") -> bool:
    full_path = _path(path, key)
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValidationError(f"{full_path} must be a boolean")
    return value


def _normalize_quality_status(
    payload: Dict[str, Any],
    key: str,
    allowed: set[str],
    path: str,
    default: str = "not_checked",
) -> str:
    status = _optional_str(payload, key, default, path)
    if status not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ValidationError(f"{_path(path, key)} must be one of: {allowed_text}")
    return status


def _normalize_critic_quality_checks(payload: Dict[str, Any]) -> Dict[str, Any]:
    if "quality_checks" not in payload:
        return {
            "style_alignment": {
                "status": "not_checked",
                "expected_style": "",
                "profile_title": "",
                "notes": "",
            },
            "length": {
                "status": "not_checked",
                "target": 0,
                "minimum": 0,
                "current": 0,
                "exempted": False,
                "notes": "",
            },
        }

    checks = _optional_dict(payload, "quality_checks", "critic_report")
    if "nsfw" in checks:
        raise ValidationError("critic_report.quality_checks.nsfw is not a critic validation item")

    style = _optional_dict(checks, "style_alignment", "critic_report.quality_checks")
    length = _optional_dict(checks, "length", "critic_report.quality_checks")

    return {
        "style_alignment": {
            "status": _normalize_quality_status(
                style,
                "status",
                CRITIC_QUALITY_STATUSES,
                "critic_report.quality_checks.style_alignment",
            ),
            "expected_style": _optional_str(
                style,
                "expected_style",
                "",
                "critic_report.quality_checks.style_alignment",
            ),
            "profile_title": _optional_str(
                style,
                "profile_title",
                "",
                "critic_report.quality_checks.style_alignment",
            ),
            "notes": _optional_str(style, "notes", "", "critic_report.quality_checks.style_alignment"),
        },
        "length": {
            "status": _normalize_quality_status(
                length,
                "status",
                CRITIC_LENGTH_STATUSES,
                "critic_report.quality_checks.length",
            ),
            "target": _optional_int(length, "target", 0, "critic_report.quality_checks.length"),
            "minimum": _optional_int(length, "minimum", 0, "critic_report.quality_checks.length"),
            "current": _optional_int(length, "current", 0, "critic_report.quality_checks.length"),
            "exempted": _optional_bool(length, "exempted", False, "critic_report.quality_checks.length"),
            "notes": _optional_str(length, "notes", "", "critic_report.quality_checks.length"),
        },
    }


def validate_critic_report(payload: Any) -> Dict[str, Any]:
    """Validate and normalize `critic.report.json`."""
    data = _require_dict(payload, "critic_report")
    decision = _require_str(data, "decision", "critic_report")
    if decision not in CRITIC_DECISIONS:
        raise ValidationError("critic_report.decision must be pass, revise, or block")
    normalized = {
        "decision": decision,
        "hard_failures": _optional_list(data, "hard_failures", "critic_report"),
        "soft_issues": _optional_list(data, "soft_issues", "critic_report"),
        "repair_instruction": _optional_str(data, "repair_instruction", path="critic_report"),
        "system_iteration_suggestion": _optional_str(data, "system_iteration_suggestion", path="critic_report"),
        "quality_checks": _normalize_critic_quality_checks(data),
    }
    if "repair_routing" in data:
        normalized["repair_routing"] = _optional_dict(data, "repair_routing", "critic_report")
    return normalized


def load_json_checked(path: str | Path, validator: Callable[[Any], Dict[str, Any]]) -> Dict[str, Any]:
    """Load JSON and validate it through the supplied artifact validator."""
    artifact_path = Path(path)
    try:
        with artifact_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{artifact_path}: invalid JSON: {exc}") from exc

    try:
        return validator(payload)
    except ValidationError as exc:
        raise ValidationError(f"{artifact_path}: {exc}") from exc
