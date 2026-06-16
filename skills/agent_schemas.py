"""Validation helpers for multi-agent round artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict


class ValidationError(ValueError):
    """Raised when an agent artifact does not match its file contract."""


FORBIDDEN_ACTOR_KEYS = {
    "gm_notes",
    "player_name",
    "world_truth",
}

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


def _reject_forbidden_keys(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = _path(path, str(key))
            if key in FORBIDDEN_ACTOR_KEYS:
                raise ValidationError(f"{child_path} is forbidden in actor output")
            _reject_forbidden_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, f"{path}[{index}]")


def validate_gm_output(payload: Any) -> Dict[str, Any]:
    """Validate and normalize `gm.output.json`."""
    data = _require_dict(payload, "gm_output")
    return {
        "agent": _require_agent(data, "gm", "gm_output"),
        "narration": _require_str(data, "narration", "gm_output"),
        "npc_events": _require_list(data, "npc_events", "gm_output"),
        "world_state_delta": _require_list(data, "world_state_delta", "gm_output"),
        "handoff": _optional_dict(data, "handoff", "gm_output"),
    }


def validate_actor_output(payload: Any) -> Dict[str, Any]:
    """Validate and normalize player/character first-person output."""
    data = _require_dict(payload, "actor_output")
    _reject_forbidden_keys(data, "actor_output")

    agent = _require_str(data, "agent", "actor_output")
    if agent not in {"player", "character"}:
        raise ValidationError("actor_output.agent must be 'player' or 'character'")

    normalized = {
        "agent": agent,
        "agent_id": _require_str(data, "agent_id", "actor_output"),
        "action": _require_str(data, "action", "actor_output"),
        "dialogue": _require_list(data, "dialogue", "actor_output"),
        "perception": _require_list(data, "perception", "actor_output"),
        "memory_delta": _require_list(data, "memory_delta", "actor_output"),
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
