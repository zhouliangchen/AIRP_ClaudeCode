"""Actor collaboration helpers for per-round agent runtime."""

from __future__ import annotations

from pathlib import Path

import agent_intents
import agent_messages


class AgentActorRuntimeError(RuntimeError):
    """Raised when actor collaboration artifacts cannot be recorded."""


class AgentActorProjectionError(AgentActorRuntimeError):
    """Raised when a projection request must block with a structured reason."""

    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


def record_request_actor(run_dir: Path, sender: str, actor_id: str, call: dict) -> tuple[str, str]:
    """Record a GM/subGM request for an actor-facing projection."""

    try:
        call_id = str(call.get("call_id") or "")
        request_message = _require_message_result(
            "append request_actor message",
            agent_messages.append_message(
                run_dir,
                {
                    "from": sender,
                    "to": ["projection"],
                    "type": "request_actor",
                    "visibility": "gm_only",
                    "source_call_id": call_id,
                    "payload": {
                        "actor_id": actor_id,
                        "call": call,
                    },
                },
            ),
        )
        intent = _require_intent_result(
            "create request_projection intent",
            agent_intents.create_intent(
                run_dir,
                {
                    "requested_by": sender,
                    "type": "request_projection",
                    "source_message_id": str(request_message["id"]),
                    "payload": {
                        "actor_id": actor_id,
                        "source_message_id": str(request_message["id"]),
                        "source_call_id": call_id,
                    },
                },
            ),
        )
        intent_id = str(intent["id"])
        return str(request_message["id"]), intent_id
    except AgentActorRuntimeError:
        raise
    except Exception as exc:
        raise _runtime_write_error("record request_actor intent", exc) from exc


def project_actor_request(
    run_dir: Path,
    *,
    actor_id: str,
    source_message_id: str,
    source_call_id: str,
) -> dict:
    """Project a request_actor message into the target actor inbox."""

    source_message = _find_source_message(run_dir, source_message_id)
    if source_message is None:
        raise AgentActorProjectionError(
            "projection_source_missing",
            {"source_message_id": source_message_id},
        )
    if source_message.get("type") != "request_actor" or source_message.get("status") != "delivered":
        raise AgentActorProjectionError(
            "projection_source_invalid",
            {
                "source_message_id": source_message_id,
                "source_type": source_message.get("type"),
                "source_status": source_message.get("status"),
            },
        )

    payload = source_message.get("payload")
    if not isinstance(payload, dict):
        raise AgentActorProjectionError(
            "projection_source_invalid",
            {"source_message_id": source_message_id, "error": "payload_missing"},
        )

    source_actor_id = str(payload.get("actor_id") or "")
    if source_actor_id != actor_id:
        raise AgentActorProjectionError(
            "projection_actor_mismatch",
            {
                "source_message_id": source_message_id,
                "expected_actor_id": actor_id,
                "source_actor_id": source_actor_id,
            },
        )

    call = payload.get("call")
    if not isinstance(call, dict):
        raise AgentActorProjectionError(
            "projection_source_invalid",
            {"source_message_id": source_message_id, "error": "call_missing"},
        )
    call_actor_id = str(call.get("actor_id") or actor_id)
    if call_actor_id != actor_id:
        raise AgentActorProjectionError(
            "projection_actor_mismatch",
            {
                "source_message_id": source_message_id,
                "expected_actor_id": actor_id,
                "call_actor_id": call_actor_id,
            },
        )

    resolved_call_id = _resolve_source_call_id(source_message, call)
    if source_call_id and resolved_call_id != source_call_id:
        raise AgentActorProjectionError(
            "projection_call_mismatch",
            {
                "source_message_id": source_message_id,
                "expected_source_call_id": source_call_id,
                "source_call_id": resolved_call_id,
            },
        )

    packet = _projection_packet(actor_id, payload, call)
    existing_projected_message = find_projected_message(
        run_dir,
        actor_id=actor_id,
        source_call_id=resolved_call_id,
        source_message_id=source_message_id,
        call=call,
    )
    if existing_projected_message is not None:
        return {
            "actor_id": actor_id,
            "source_message_id": source_message_id,
            "source_call_id": resolved_call_id,
            "projected_message_id": str(existing_projected_message["id"]),
            "projected_message": existing_projected_message,
            "projected_message_created": False,
        }

    projected_message = _append_projected_message(
        run_dir,
        actor_id,
        call,
        packet,
        resolved_call_id,
        source_message_id,
    )
    return {
        "actor_id": actor_id,
        "source_message_id": source_message_id,
        "source_call_id": resolved_call_id,
        "projected_message_id": str(projected_message["id"]),
        "projected_message": projected_message,
        "projected_message_created": True,
    }


def record_projected_actor_message(
    run_dir: Path,
    actor_id: str,
    call: dict,
    packet: dict,
    intent_id: str,
) -> str:
    """Record an actor-facing projected message and complete its projection intent."""

    try:
        projected_message = _require_message_result(
            "append projected_message",
            agent_messages.append_message(
                run_dir,
                {
                    "from": "projection",
                    "to": [actor_id],
                    "type": "projected_message",
                    "visibility": "actor_facing",
                    "source_call_id": str(call.get("call_id") or ""),
                    "payload": {
                        "actor_id": actor_id,
                        "packet": packet,
                        "gm_prompt": str(call.get("prompt") or ""),
                    },
                },
            ),
        )
        _require_intent_result(
            "complete request_projection intent",
            agent_intents.complete_intent(
                run_dir,
                intent_id,
                outputs={"projected_message_id": str(projected_message["id"])},
            ),
        )
        return str(projected_message["id"])
    except AgentActorRuntimeError:
        raise
    except Exception as exc:
        raise _runtime_write_error("record projected actor message", exc) from exc


def record_actor_response(run_dir: Path, actor_id: str, call: dict, actor_output: dict) -> str:
    """Record an actor response message for GM consumption."""

    try:
        response_message = _require_message_result(
            "append actor_response",
            agent_messages.append_message(
                run_dir,
                {
                    "from": actor_id,
                    "to": ["gm"],
                    "type": "actor_response",
                    "visibility": "gm_only",
                    "source_call_id": str(call.get("call_id") or ""),
                    "payload": {
                        "actor_id": actor_id,
                        "output": actor_output,
                    },
                },
            ),
        )
        return str(response_message["id"])
    except AgentActorRuntimeError:
        raise
    except Exception as exc:
        raise _runtime_write_error("record actor_response message", exc) from exc


def _find_source_message(run_dir: Path, source_message_id: str) -> dict | None:
    if not isinstance(source_message_id, str) or not source_message_id:
        return None
    for message in agent_messages.read_messages(run_dir):
        if message.get("id") == source_message_id:
            return message
    return None


def find_projected_message(
    run_dir: Path,
    *,
    actor_id: str,
    source_call_id: str,
    source_message_id: str = "",
    call: dict | None = None,
) -> dict | None:
    """Return an already-delivered projection matching the request, if present."""

    if not actor_id or not source_call_id:
        return None
    for message in reversed(agent_messages.read_messages(run_dir)):
        if message.get("type") != "projected_message":
            continue
        if message.get("status") != "delivered":
            continue
        if message.get("source_call_id") != source_call_id:
            continue
        payload = message.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("actor_id") != actor_id:
            continue
        if not _projection_source_matches(payload, source_message_id, call):
            continue
        return message
    return None


def _projection_source_matches(payload: dict, source_message_id: str, call: dict | None) -> bool:
    existing_source_message_id = payload.get("source_message_id")
    if existing_source_message_id and source_message_id and existing_source_message_id != source_message_id:
        return False

    expected_call_id = ""
    if isinstance(call, dict):
        expected_call_id = str(call.get("call_id") or "")
    for candidate in (payload.get("call"), _packet_call(payload)):
        if not isinstance(candidate, dict):
            continue
        candidate_call_id = str(candidate.get("call_id") or "")
        if candidate_call_id and expected_call_id and candidate_call_id != expected_call_id:
            return False
    return True


def _packet_call(payload: dict) -> dict | None:
    packet = payload.get("packet")
    if not isinstance(packet, dict):
        return None
    call = packet.get("call")
    if isinstance(call, dict):
        return call
    return None


def _resolve_source_call_id(source_message: dict, call: dict) -> str:
    return str(source_message.get("source_call_id") or call.get("call_id") or "")


def _projection_packet(actor_id: str, payload: dict, call: dict) -> dict:
    packet = payload.get("packet")
    if not isinstance(packet, dict):
        packet = call.get("packet")
    if isinstance(packet, dict):
        projected = dict(packet)
        projected.setdefault("actor_id", actor_id)
        return projected
    return {"actor_id": actor_id, "call": call}


def _append_projected_message(
    run_dir: Path,
    actor_id: str,
    call: dict,
    packet: dict,
    source_call_id: str,
    source_message_id: str,
) -> dict:
    result = agent_messages.append_message(
        run_dir,
        {
            "from": "projection",
            "to": [actor_id],
            "type": "projected_message",
            "visibility": "actor_facing",
            "source_call_id": source_call_id,
            "payload": {
                "actor_id": actor_id,
                "source_message_id": source_message_id,
                "packet": packet,
                "gm_prompt": str(call.get("prompt") or ""),
            },
        },
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise AgentActorProjectionError(
            "projection_append_rejected",
            {"message_result": result},
        )
    message = result.get("message")
    if not isinstance(message, dict) or not message.get("id"):
        raise AgentActorProjectionError(
            "projection_append_missing_id",
            {"message_result": result},
        )
    return message


def _require_message_result(action: str, result: dict) -> dict:
    if not isinstance(result, dict) or not result.get("ok"):
        raise AgentActorRuntimeError(f"{action} failed: {result}")
    message = result.get("message")
    if not isinstance(message, dict) or not message.get("id"):
        raise AgentActorRuntimeError(f"{action} failed: missing message id")
    return message


def _require_intent_result(action: str, result: dict) -> dict:
    if not isinstance(result, dict) or not result.get("ok"):
        raise AgentActorRuntimeError(f"{action} failed: {result}")
    intent = result.get("intent")
    if not isinstance(intent, dict) or not intent.get("id"):
        raise AgentActorRuntimeError(f"{action} failed: missing intent id")
    return intent


def _runtime_write_error(action: str, exc: Exception) -> AgentActorRuntimeError:
    return AgentActorRuntimeError(f"{action} failed: {exc}")
