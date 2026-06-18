"""Validation helpers for multi-agent round artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict


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

LEGACY_ACTOR_KEYS = {
    "action",
    "dialogue",
    "perception",
    "memory_delta",
}

ACTOR_EVENT_TYPES = {
    "perceive_request",
    "dialogue",
    "action",
    "memory_delta",
    "goal_update",
    "wait_for_gm",
    "stop_for_player_decision",
}

ACTOR_EVENT_KEYS = {"type", "target", "content", "metadata"}

CRITIC_DECISIONS = {"pass", "revise", "block"}


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


def _forbidden_actor_marker(text: str) -> str:
    raw = str(text or "")
    camel_separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", raw)
    lowered = camel_separated.lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    compact = re.sub(r"[^a-z0-9]+", "", lowered)
    for marker in FORBIDDEN_ACTOR_KEYS:
        marker_compact = re.sub(r"[^a-z0-9]+", "", marker)
        if marker in lowered or marker in normalized or marker_compact in compact:
            return marker
    return ""


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
    return {
        "type": event_type,
        "target": _optional_str(data, "target", "", path),
        "content": _require_str(data, "content", path),
        "metadata": _optional_dict(data, "metadata", path),
    }


def _normalize_actor_events(items: list[Any], path: str) -> list[Dict[str, Any]]:
    if not items:
        raise ValidationError(f"{path} must not be empty")
    return [_normalize_actor_event(item, f"{path}[{index}]") for index, item in enumerate(items)]


def _normalize_gm_scene_beat(item: Any, path: str) -> Dict[str, Any]:
    data = _require_dict(item, path)
    normalized = {"content": _require_str(data, "content", path)}
    if "metadata" in data:
        normalized["metadata"] = _optional_dict(data, "metadata", path)
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
    return normalized


def _normalize_gm_actor_call(item: Any, path: str) -> Dict[str, Any]:
    data = _require_dict(item, path)
    actor_id = _require_str(data, "actor_id", path).strip()
    if not actor_id:
        raise ValidationError(f"{_path(path, 'actor_id')} must not be blank")
    normalized = {
        "call_id": _require_str(data, "call_id", path),
        "actor_id": actor_id,
        "prompt": _require_str(data, "prompt", path),
        "reason": _require_str(data, "reason", path),
    }
    if "metadata" in data:
        normalized["metadata"] = _optional_dict(data, "metadata", path)
    return normalized


def _normalize_list_items(
    items: list[Any],
    path: str,
    normalizer: Callable[[Any, str], Dict[str, Any]],
) -> list[Dict[str, Any]]:
    return [normalizer(item, f"{path}[{index}]") for index, item in enumerate(items)]


def validate_gm_output(payload: Any) -> Dict[str, Any]:
    """Validate and normalize `gm.output.json`."""
    data = _require_dict(payload, "gm_output")
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
        "decision_point": data.get("decision_point"),
        "stop_reason": _optional_str(data, "stop_reason", "continue", "gm_output"),
    }


def validate_actor_output(payload: Any) -> Dict[str, Any]:
    """Validate and normalize player/character first-person output."""
    data = _require_dict(payload, "actor_output")
    _reject_forbidden_keys(data, "actor_output")
    _reject_legacy_actor_keys(data, "actor_output")

    agent = _require_str(data, "agent", "actor_output")
    if agent not in {"player", "character"}:
        raise ValidationError("actor_output.agent must be 'player' or 'character'")
    agent_id = _require_str(data, "agent_id", "actor_output")
    if agent == "player" and agent_id != "player":
        raise ValidationError("actor_output.agent_id must be 'player' when agent is 'player'")
    if agent == "character" and not agent_id.startswith("character:"):
        raise ValidationError("actor_output.agent_id must start with 'character:' when agent is 'character'")

    normalized = {
        "agent": agent,
        "agent_id": agent_id,
        "events": _normalize_actor_events(_require_list(data, "events", "actor_output"), "actor_output.events"),
        "stop_reason": _optional_str(data, "stop_reason", "continue", "actor_output"),
    }
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


def validate_critic_report(payload: Any) -> Dict[str, Any]:
    """Validate and normalize `critic.report.json`."""
    data = _require_dict(payload, "critic_report")
    decision = _require_str(data, "decision", "critic_report")
    if decision not in CRITIC_DECISIONS:
        raise ValidationError("critic_report.decision must be pass, revise, or block")
    return {
        "decision": decision,
        "hard_failures": _optional_list(data, "hard_failures", "critic_report"),
        "soft_issues": _optional_list(data, "soft_issues", "critic_report"),
        "repair_instruction": _optional_str(data, "repair_instruction", path="critic_report"),
        "system_iteration_suggestion": _optional_str(data, "system_iteration_suggestion", path="critic_report"),
    }


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
