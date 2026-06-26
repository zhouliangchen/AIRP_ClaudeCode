"""Projection agent contract helpers.

This module is intentionally pure: it shapes natural-language projection review
material and validates returned projection envelopes.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import actor_memory_store


ALLOWED_DECISIONS = {"pass", "edited", "needs_rewrite", "blocked"}
STRUCTURED_REVIEW_KEYS = {
    "self_knowledge",
    "memory",
    "visible_events",
    "gm_visibility_basis",
    "actor_visible_events",
    "gm_only_hidden_settings",
    "packet",
    "call",
    "visibility_basis",
    "hidden_facts",
    "private_events",
}
OBJECTIVE_REFERENCE_KEYS = {
    "review_reference",
    "objective_reference",
    "objective_actor_reference",
    "facts",
    "objective_facts",
    "public_facts",
}


class ProjectionValidationError(ValueError):
    """Raised when projection output violates the control-plane contract."""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _optional_text_field(data: dict[str, Any], field: str) -> str:
    if field not in data or data[field] is None:
        return ""
    if not isinstance(data[field], str):
        raise ProjectionValidationError(f"{field} must be a string when present")
    return data[field].strip()


def _skip_key(key: Any, skip_keys: set[str] | frozenset[str]) -> bool:
    return str(key or "").strip().lower() in skip_keys


def _natural_text(value: Any, *, include_keys: bool = True, skip_keys: set[str] | frozenset[str] = frozenset()) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts: list[str] = []
        for key, child in value.items():
            if _skip_key(key, skip_keys):
                continue
            child_text = _natural_text(child, include_keys=include_keys, skip_keys=skip_keys)
            if child_text:
                prefix = f"{key}: " if include_keys else ""
                parts.append(f"{prefix}{child_text}")
        return "\n".join(parts).strip()
    if isinstance(value, (list, tuple, set)):
        parts = [_natural_text(item, include_keys=include_keys, skip_keys=skip_keys) for item in value]
        return "\n".join(part for part in parts if part).strip()
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value).strip()


def _section(title: str, value: Any) -> str:
    text = _natural_text(value)
    return f"{title}：\n{text}" if text else ""


def _card_folder(actor: dict[str, Any], explicit_card_folder: str | None) -> str:
    return _text(explicit_card_folder or actor.get("card_folder") or actor.get("card"))


def _read_actor_memory(card_folder: str, actor_id: str) -> dict[str, Any]:
    if not card_folder:
        return {}
    return actor_memory_store.read_actor_memory(card_folder, actor_id)


def _key_memory_cues(items: Any) -> str:
    lines: list[str] = []
    for item in _as_list(items):
        if not isinstance(item, dict):
            continue
        tag = _text(item.get("tag"))
        summary = _text(item.get("summary"))
        text = " - ".join(part for part in (tag, summary) if part)
        if text:
            lines.append(text)
    return "\n".join(lines)


def _stored_actor_context_sections(memory: dict[str, Any]) -> list[str]:
    return [
        _section("Target actor profile", memory.get("profile")),
        _section("Target actor long-term recollections", memory.get("long_term")),
        _section("Target actor key recollection cues", _key_memory_cues(memory.get("key_memories"))),
        _section("Target actor recent recollections", memory.get("short_term")),
    ]


def _stored_objective_reference_sections(memory: dict[str, Any]) -> list[str]:
    return [
        _section("Target actor objective profile", memory.get("objective_profile")),
        _section("Target actor objective background", memory.get("background")),
    ]


def _natural_reference(value: Any) -> str:
    return _natural_text(
        copy.deepcopy(value),
        include_keys=False,
        skip_keys=STRUCTURED_REVIEW_KEYS,
    )


def _objective_reference_text(objective_context: dict[str, Any] | None) -> str:
    context = _as_dict(objective_context)
    parts = [
        _natural_reference(value)
        for key, value in context.items()
        if str(key or "").strip().lower() in OBJECTIVE_REFERENCE_KEYS
    ]
    return "\n".join(part for part in parts if part).strip()


def _actor_review_context(actor_id: str, actor: dict[str, Any], card_folder: str) -> str:
    stored_memory = _read_actor_memory(card_folder, actor_id)
    sections = [_section("Target actor immersive context", actor.get("immersive_context"))]
    sections.extend(_stored_actor_context_sections(stored_memory))
    if not any(sections):
        fallback = actor.get("actor_context") or actor.get("context")
        fallback_text = _natural_reference(fallback)
        if fallback_text:
            sections.append(_section("Target actor context", fallback_text))
    return "\n\n".join(section for section in sections if section).strip()


def _review_reference(
    actor_id: str,
    actor: dict[str, Any],
    objective_context: dict[str, Any] | None,
    card_folder: str,
) -> str:
    stored_memory = _read_actor_memory(card_folder, actor_id)
    sections = _stored_objective_reference_sections(stored_memory)
    reference_text = _objective_reference_text(objective_context)
    if reference_text:
        sections.append(_section("Objective review reference", reference_text))
    return "\n\n".join(section for section in sections if section).strip()


def build_review_packet(
    *,
    actor_id: str,
    source_call_id: str,
    source_message_id: str,
    requested_actor_message: str,
    actor_packet: dict[str, Any] | None,
    objective_context: dict[str, Any] | None,
    card_folder: str | None = None,
) -> dict[str, Any]:
    """Build the isolated natural-language packet a projection model reviews."""
    actor = _as_dict(actor_packet)
    folder = _card_folder(actor, card_folder)
    target_actor_id = actor_memory_store.canonical_actor_id(actor_id)
    actor_context = _actor_review_context(target_actor_id, actor, folder)
    return {
        "target_actor_id": target_actor_id,
        "source_call_id": _text(source_call_id),
        "source_message_id": _text(source_message_id),
        "requested_actor_message": _text(requested_actor_message),
        "actor_context": actor_context,
        "review_reference": _review_reference(target_actor_id, actor, objective_context, folder),
        "instruction": (
            "Review the requested actor message. Return one decision: pass, edited, "
            "needs_rewrite, or blocked. Keep the actor immersed. Do not reveal "
            "whether subjective beliefs are false. If the message only needs a "
            "small local wording change, return edited with a natural-language "
            "final_actor_message. If safe wording cannot be negotiated locally, "
            "return needs_rewrite with concise feedback for GM/subGM."
        ),
    }


def validate_projection_output(
    payload: Any,
    *,
    actor_id: str,
    source_call_id: str,
) -> dict[str, str]:
    """Validate and normalize one projection agent response envelope."""
    data = _as_dict(payload)
    decision = _text(data.get("decision"))
    if decision not in ALLOWED_DECISIONS:
        raise ProjectionValidationError("decision must be pass, edited, needs_rewrite, or blocked")

    final_actor_message = _optional_text_field(data, "final_actor_message")
    feedback = _optional_text_field(data, "feedback")
    projection_feedback = _optional_text_field(data, "projection_feedback")
    if not feedback:
        feedback = projection_feedback

    if decision in {"pass", "edited"} and not final_actor_message:
        raise ProjectionValidationError("final_actor_message is required for pass or edited")
    if decision in {"needs_rewrite", "blocked"} and not feedback:
        raise ProjectionValidationError("feedback is required for needs_rewrite or blocked")

    expected_actor_id = _text(actor_id)
    output_actor_id = _text(data.get("target_actor_id")) if "target_actor_id" in data else expected_actor_id
    if output_actor_id != expected_actor_id:
        raise ProjectionValidationError("target_actor_id does not match projection request")

    expected_call_id = _text(source_call_id)
    output_call_id = _text(data.get("source_call_id")) if "source_call_id" in data else expected_call_id
    if output_call_id != expected_call_id:
        raise ProjectionValidationError("source_call_id does not match projection request")

    return {
        "decision": decision,
        "target_actor_id": expected_actor_id,
        "source_call_id": expected_call_id,
        "final_actor_message": final_actor_message,
        "feedback": feedback,
    }


__all__ = [
    "ALLOWED_DECISIONS",
    "ProjectionValidationError",
    "build_review_packet",
    "validate_projection_output",
]
