"""Projection agent contract helpers.

This module is intentionally pure: it only shapes projection review packets and
validates returned projection envelopes.
"""

from __future__ import annotations

import copy
from typing import Any


ALLOWED_DECISIONS = {"pass", "edited", "needs_rewrite", "blocked"}


class ProjectionValidationError(ValueError):
    """Raised when projection output violates the control-plane contract."""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _optional_text_field(data: dict[str, Any], field: str) -> str:
    if field not in data or data[field] is None:
        return ""
    if not isinstance(data[field], str):
        raise ProjectionValidationError(f"{field} must be a string when present")
    return data[field].strip()


def build_review_packet(
    *,
    actor_id: str,
    source_call_id: str,
    source_message_id: str,
    requested_actor_message: str,
    actor_packet: dict[str, Any] | None,
    objective_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the isolated packet a future projection model should review."""
    actor = _as_dict(actor_packet)
    return {
        "target_actor_id": _text(actor_id),
        "source_call_id": _text(source_call_id),
        "source_message_id": _text(source_message_id),
        "requested_actor_message": _text(requested_actor_message),
        "actor_context": _text(actor.get("immersive_context")),
        "actor_visible_events": copy.deepcopy(actor.get("visible_events") or []),
        "objective_context": copy.deepcopy(_as_dict(objective_context)),
        "instruction": (
            "Review the requested actor message. Return one decision: pass, edited, "
            "needs_rewrite, or blocked. Keep the actor immersed. Do not reveal "
            "whether subjective beliefs are false."
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
