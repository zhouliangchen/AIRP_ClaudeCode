"""Shared evidence rules for GM-declared player decisions."""

from __future__ import annotations

from typing import Any


DECISION_LABEL_FIELDS = ("required_label", "content", "summary", "reason")


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _gm_output_calls_player(gm_output: dict[str, Any]) -> bool:
    actor_calls = gm_output.get("actor_calls")
    if not isinstance(actor_calls, list):
        return False
    return any(
        _clean_text(call.get("actor_id")) == "player"
        for call in actor_calls
        if isinstance(call, dict)
    )


def _player_call_ids(gm_output: dict[str, Any]) -> list[str]:
    actor_calls = gm_output.get("actor_calls")
    if not isinstance(actor_calls, list):
        return []
    call_ids: list[str] = []
    for call in actor_calls:
        if not isinstance(call, dict):
            continue
        if _clean_text(call.get("actor_id")) != "player":
            continue
        call_ids.append(_clean_text(call.get("call_id")))
    return call_ids


def _decision_label(decision_point: dict[str, Any]) -> str:
    for field in DECISION_LABEL_FIELDS:
        label = _clean_text(decision_point.get(field))
        if label:
            return label
    return ""


def valid_gm_player_decision(
    gm_output: Any,
    *,
    player_participated_before_gm: bool,
) -> dict[str, Any]:
    """Validate whether one GM output can stop for a player decision."""
    if not isinstance(gm_output, dict):
        return {"valid": False, "reason": "invalid_gm_output"}

    if _clean_text(gm_output.get("stop_reason")) != "player_decision":
        return {"valid": False, "reason": "stop_reason_not_player_decision"}

    if _gm_output_calls_player(gm_output):
        return {"valid": False, "reason": "same_output_calls_player"}

    decision_point = gm_output.get("decision_point")
    if not isinstance(decision_point, dict):
        return {"valid": False, "reason": "missing_decision_point"}

    label = _decision_label(decision_point)
    if not label:
        return {"valid": False, "reason": "missing_decision_label"}

    if not player_participated_before_gm:
        return {"valid": False, "reason": "missing_prior_player_reply"}

    return {
        "valid": True,
        "reason": "valid",
        "label": label,
        "decision_point": decision_point,
    }


def _reply_from_player_output(output: dict[str, Any]) -> tuple[str, str]:
    natural = _clean_text(output.get("natural_reply"))
    source_call_id = _clean_text(output.get("source_call_id"))
    if natural:
        return natural, source_call_id

    events = output.get("events")
    if not isinstance(events, list):
        return "", source_call_id
    for event in events:
        if not isinstance(event, dict):
            continue
        if _clean_text(event.get("type")) != "reply":
            continue
        content = _clean_text(event.get("content"))
        if content:
            return content, source_call_id or _clean_text(event.get("source_call_id"))
    return "", source_call_id


def _player_actor_reply_records(story_input: Any) -> list[dict[str, str]]:
    if not isinstance(story_input, dict):
        return []
    loop_outputs = story_input.get("loop_outputs")
    if not isinstance(loop_outputs, dict):
        return []
    actors = loop_outputs.get("actors")
    if not isinstance(actors, dict):
        return []
    player_outputs = actors.get("player")
    if not isinstance(player_outputs, list):
        return []

    replies: list[dict[str, str]] = []
    for output in player_outputs:
        if not isinstance(output, dict):
            continue
        reply, source_call_id = _reply_from_player_output(output)
        if reply:
            replies.append({"reply": reply, "source_call_id": source_call_id})
    return replies


def player_actor_replies(story_input: Any) -> list[str]:
    """Return real player actor replies, preferring natural_reply over reply events."""
    return [record["reply"] for record in _player_actor_reply_records(story_input)]


def role_action_reply(story_input: Any) -> str:
    if not isinstance(story_input, dict):
        return ""
    player_inputs = story_input.get("player_inputs")
    if isinstance(player_inputs, dict):
        routed = player_inputs.get("routed_input")
        if isinstance(routed, dict):
            action = _clean_text(routed.get("role_action_channel"))
            if action:
                return action
        action = _clean_text(player_inputs.get("role_action_channel"))
        if action:
            return action
    return _clean_text(story_input.get("role_action_channel"))


def gm_outputs_from_story_input(story_input: Any) -> list[dict[str, Any]]:
    if not isinstance(story_input, dict):
        return []

    gm_outputs = story_input.get("gm")
    if not isinstance(gm_outputs, list):
        loop_outputs = story_input.get("loop_outputs")
        if isinstance(loop_outputs, dict):
            gm_branch = loop_outputs.get("gm")
            if isinstance(gm_branch, dict):
                gm_outputs = gm_branch.get("outputs")

    if not isinstance(gm_outputs, list):
        return []
    return [output for output in gm_outputs if isinstance(output, dict)]


def extract_player_critical_action_evidence(story_input: Any) -> list[dict[str, Any]]:
    """Extract postprocess-compatible evidence for a GM-validated player decision."""
    role_action = role_action_reply(story_input)
    player_replies_by_call_id: dict[str, list[str]] = {}
    player_replies_without_call_id: list[str] = []
    for record in _player_actor_reply_records(story_input):
        reply = record["reply"]
        source_call_id = record["source_call_id"]
        if source_call_id:
            player_replies_by_call_id.setdefault(source_call_id, []).append(reply)
        else:
            player_replies_without_call_id.append(reply)

    latest_player_reply = role_action
    evidence: list[dict[str, Any]] = []
    for index, output in enumerate(gm_outputs_from_story_input(story_input)):
        validation = valid_gm_player_decision(
            output,
            player_participated_before_gm=bool(latest_player_reply),
        )
        if validation.get("valid"):
            decision_point = validation["decision_point"]
            evidence_id = _clean_text(decision_point.get("id")) or f"player-decision-{index + 1}"
            evidence.append(
                {
                    "id": evidence_id,
                    "required_label": latest_player_reply,
                    "risk_level": "gm_decision",
                }
            )

        for call_id in _player_call_ids(output):
            matched_replies = player_replies_by_call_id.get(call_id) if call_id else None
            if matched_replies:
                latest_player_reply = matched_replies.pop(0)
            elif player_replies_without_call_id:
                latest_player_reply = player_replies_without_call_id.pop(0)
    return evidence


def has_valid_player_decision(story_input: Any) -> bool:
    return bool(extract_player_critical_action_evidence(story_input))


__all__ = [
    "DECISION_LABEL_FIELDS",
    "valid_gm_player_decision",
    "player_actor_replies",
    "role_action_reply",
    "gm_outputs_from_story_input",
    "extract_player_critical_action_evidence",
    "has_valid_player_decision",
]
