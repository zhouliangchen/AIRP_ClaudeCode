"""Actor collaboration helpers for per-round agent runtime."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import agent_intents
import agent_interactions
import agent_messages
import agent_run


class AgentActorRuntimeError(RuntimeError):
    """Raised when actor collaboration artifacts cannot be recorded."""


class AgentActorProjectionError(AgentActorRuntimeError):
    """Raised when a projection request must block with a structured reason."""

    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


class AgentActorDispatchError(AgentActorRuntimeError):
    """Raised when a run_actor request must block with a structured reason."""

    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


def record_request_actor(
    run_dir: Path,
    sender: str,
    actor_id: str,
    call: dict,
    *,
    packet: dict | None = None,
    source_intent_id: str = "",
    fanout: dict | None = None,
) -> tuple[str, str]:
    """Record a GM/subGM request for an actor-facing projection."""

    try:
        call_id = str(call.get("call_id") or "")
        request_payload = {
            "actor_id": actor_id,
            "call": copy.deepcopy(call),
        }
        context_packet = _validated_actor_context_packet(actor_id, packet)
        if context_packet is None:
            context_packet = load_actor_context_packet(run_dir, actor_id)
        if context_packet is not None:
            request_payload["packet"] = context_packet
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
                    "payload": request_payload,
                },
            ),
        )
        intent_payload = {
            "requested_by": sender,
            "type": "request_projection",
            "source_message_id": str(request_message["id"]),
            "payload": {
                "actor_id": actor_id,
                "source_message_id": str(request_message["id"]),
                "source_call_id": call_id,
            },
        }
        if isinstance(fanout, dict) and fanout:
            intent_payload["payload"]["fanout"] = fanout
        if source_intent_id:
            intent_payload["policy"] = {"source_intent_id": source_intent_id}
            if isinstance(fanout, dict) and fanout.get("batch_id"):
                intent_payload["policy"]["fanout_batch_id"] = str(fanout.get("batch_id"))
        intent = _require_intent_result(
            "create request_projection intent",
            agent_intents.create_intent(run_dir, intent_payload),
        )
        intent_id = str(intent["id"])
        return str(request_message["id"]), intent_id
    except AgentActorRuntimeError:
        raise
    except Exception as exc:
        raise _runtime_write_error("record request_actor intent", exc) from exc


def load_actor_context_packet(run_dir: str | Path, actor_id: str) -> dict | None:
    """Load the current round actor packet if it has already been rendered."""

    path = _actor_context_packet_path(Path(run_dir), actor_id)
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AgentActorProjectionError(
            "actor_context_packet_invalid",
            {"actor_id": actor_id, "path": str(path), "error": str(exc)},
        ) from exc
    return _validated_actor_context_packet(actor_id, payload)


def require_actor_context_packet(run_dir: str | Path, actor_id: str) -> dict:
    """Load the current actor packet or block projection before context is lost."""

    packet = load_actor_context_packet(run_dir, actor_id)
    if packet is None:
        path = _actor_context_packet_path(Path(run_dir), actor_id)
        raise AgentActorProjectionError(
            "actor_context_packet_missing",
            {"actor_id": actor_id, "path": str(path) if path is not None else ""},
        )
    return packet


def project_actor_request(
    run_dir: Path,
    *,
    actor_id: str,
    source_message_id: str,
    source_call_id: str,
    projection_result: dict | None = None,
) -> dict:
    """Project a request_actor message into the target actor inbox."""

    request = inspect_projection_request(
        run_dir,
        actor_id=actor_id,
        source_message_id=source_message_id,
        source_call_id=source_call_id,
    )
    call = request["call"]
    resolved_call_id = request["source_call_id"]
    packet = _projection_packet(actor_id, request["payload"], call, projection_result=projection_result)
    existing_projected_message = request["existing_projected_message"]
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
        projection_result=projection_result,
    )
    return {
        "actor_id": actor_id,
        "source_message_id": source_message_id,
        "source_call_id": resolved_call_id,
        "projected_message_id": str(projected_message["id"]),
        "projected_message": projected_message,
        "projected_message_created": True,
    }


def inspect_projection_request(
    run_dir: Path,
    *,
    actor_id: str,
    source_message_id: str,
    source_call_id: str,
) -> dict:
    """Validate and describe a request_actor projection without appending messages."""

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
    return {
        "actor_id": actor_id,
        "source_message_id": source_message_id,
        "source_call_id": resolved_call_id,
        "source_message": source_message,
        "payload": payload,
        "call": call,
        "packet": packet,
        "existing_projected_message": existing_projected_message,
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
        projected_message = _append_projected_message(
            run_dir,
            actor_id,
            call,
            packet,
            str(call.get("call_id") or ""),
            "",
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


def read_projected_actor_packet(
    run_dir: Path,
    *,
    actor_id: str,
    projected_message_id: str,
    source_call_id: str,
) -> dict:
    """Read the actor packet from a delivered actor-facing projected message."""

    missing_fields = [
        field
        for field, value in (
            ("actor_id", actor_id),
            ("projected_message_id", projected_message_id),
            ("source_call_id", source_call_id),
        )
        if not isinstance(value, str) or not value
    ]
    if missing_fields:
        raise AgentActorDispatchError(
            "run_actor_payload_invalid",
            {"missing_fields": missing_fields},
        )

    message = _find_source_message(run_dir, projected_message_id)
    if message is None:
        raise AgentActorDispatchError(
            "projected_message_missing",
            {"projected_message_id": projected_message_id},
        )
    if message.get("type") != "projected_message":
        raise AgentActorDispatchError(
            "projected_message_missing",
            {
                "projected_message_id": projected_message_id,
                "message_type": message.get("type"),
            },
        )
    if message.get("status") != "delivered" or message.get("visibility") != "actor_facing":
        raise AgentActorDispatchError(
            "projected_message_invalid",
            {
                "projected_message_id": projected_message_id,
                "message_status": message.get("status"),
                "message_visibility": message.get("visibility"),
            },
        )

    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise AgentActorDispatchError(
            "actor_dispatch_failed",
            {"projected_message_id": projected_message_id, "error": "projected_payload_missing"},
        )

    targets = message.get("to")
    if not isinstance(targets, list) or actor_id not in targets:
        raise AgentActorDispatchError(
            "projected_message_actor_mismatch",
            {
                "projected_message_id": projected_message_id,
                "expected_actor_id": actor_id,
                "message_targets": targets,
            },
        )
    projected_actor_id = str(payload.get("actor_id") or "")
    if projected_actor_id != actor_id:
        raise AgentActorDispatchError(
            "projected_message_actor_mismatch",
            {
                "projected_message_id": projected_message_id,
                "expected_actor_id": actor_id,
                "projected_actor_id": projected_actor_id,
            },
        )
    projected_source_call_id = str(message.get("source_call_id") or payload.get("source_call_id") or "")
    if projected_source_call_id != source_call_id:
        raise AgentActorDispatchError(
            "projected_message_actor_mismatch",
            {
                "projected_message_id": projected_message_id,
                "expected_source_call_id": source_call_id,
                "projected_source_call_id": projected_source_call_id,
            },
        )

    natural_message = str(payload.get("natural_message") or "").strip()
    if not natural_message:
        raise AgentActorDispatchError(
            "actor_dispatch_failed",
            {"projected_message_id": projected_message_id, "error": "projected_natural_message_missing"},
        )
    packet = load_actor_context_packet(run_dir, actor_id)
    if packet is None:
        packet = {"actor_id": actor_id}
    packet = copy.deepcopy(packet)
    packet["actor_id"] = actor_id
    packet["gm_prompt"] = natural_message
    packet_actor_id = str(packet.get("actor_id") or actor_id)
    if packet_actor_id != actor_id:
        raise AgentActorDispatchError(
            "projected_message_actor_mismatch",
            {
                "projected_message_id": projected_message_id,
                "expected_actor_id": actor_id,
                "packet_actor_id": packet_actor_id,
            },
        )

    call = {
        "call_id": projected_source_call_id,
        "actor_id": actor_id,
        "prompt": natural_message,
    }
    packet["call"] = dict(call)
    return {
        "message": message,
        "packet": packet,
        "call": call,
        "source_call_id": projected_source_call_id,
    }


def append_actor_output(run_dir: Path, actor_id: str, actor_output: dict) -> dict:
    """Append a validated actor output to root and authoritative artifact files."""

    try:
        root_path = Path(run_dir) / "actor.outputs.json"
        current = agent_run.read_json(root_path, {}) if root_path.exists() else {}
        if current is None:
            current = {}
        if not isinstance(current, dict):
            raise AgentActorRuntimeError("actor.outputs.json must be a JSON object")
        outputs = dict(current)
        actor_items = outputs.get(actor_id)
        if actor_items is None:
            actor_items = []
        if not isinstance(actor_items, list):
            raise AgentActorRuntimeError(f"actor.outputs.json.{actor_id} must be a list")
        actor_items = list(actor_items)
        actor_items.append(actor_output)
        outputs[actor_id] = actor_items
        agent_run.write_json(root_path, outputs)
        agent_run.write_json(Path(run_dir) / "artifacts" / "actor.outputs.json", outputs)
        return outputs
    except AgentActorRuntimeError:
        raise
    except Exception as exc:
        raise _runtime_write_error("append actor output", exc) from exc


def _event_content(event: dict) -> str:
    return str(event.get("content") or "")


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def record_actor_event(run_dir: Path, actor_id: str, event: dict, source_call_id: str) -> None:
    """Record one actor output event in the authoritative interaction trace."""

    event_type = str(event.get("type") or "")
    visibility = "world_visible" if event_type == "reply" else "actor_visible"
    agent_interactions.append_event(
        run_dir,
        actor=actor_id,
        visibility=visibility,
        event_type=event_type,
        content=_event_content(event),
        target=str(event.get("target") or ""),
        source_call_id=source_call_id,
    )


def record_actor_events(run_dir: Path, actor_id: str, actor_output: dict, source_call_id: str) -> None:
    """Record all events from a validated actor output in the interaction trace."""

    for event in actor_output.get("events", []):
        if isinstance(event, dict):
            record_actor_event(run_dir, actor_id, event, source_call_id)


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


def _projection_packet(
    actor_id: str,
    payload: dict,
    call: dict,
    *,
    projection_result: dict | None = None,
) -> dict:
    packet = payload.get("packet")
    if not isinstance(packet, dict):
        packet = call.get("packet")
    if isinstance(packet, dict):
        projected = copy.deepcopy(packet)
        projected.setdefault("actor_id", actor_id)
    else:
        projected = {"actor_id": actor_id, "call": copy.deepcopy(call)}
    final_actor_message = _projection_final_actor_message(call, projection_result)
    if final_actor_message:
        projected["gm_prompt"] = final_actor_message
    projected_call = copy.deepcopy(call)
    if final_actor_message:
        projected_call["prompt"] = final_actor_message
    projected["call"] = projected_call
    visibility_basis = call.get("visibility_basis")
    if isinstance(visibility_basis, dict):
        projected["gm_visibility_basis"] = copy.deepcopy(visibility_basis)
    return projected


def _actor_context_packet_path(run_dir: Path, actor_id: str) -> Path | None:
    if actor_id == "player":
        return run_dir / "player.context.json"
    if actor_id.startswith("character:"):
        safe = agent_run.safe_name(actor_id.split(":", 1)[1] or "_unknown")
        return run_dir / "characters" / f"{safe}.context.json"
    return None


def _validated_actor_context_packet(actor_id: str, packet: dict | None) -> dict | None:
    if not isinstance(packet, dict):
        return None
    projected = copy.deepcopy(packet)
    packet_actor_id = str(projected.get("actor_id") or actor_id)
    if packet_actor_id != actor_id:
        raise AgentActorProjectionError(
            "actor_context_actor_mismatch",
            {"expected_actor_id": actor_id, "packet_actor_id": packet_actor_id},
        )
    projected["actor_id"] = actor_id
    return projected


def _projection_final_actor_message(call: dict, projection_result: dict | None) -> str:
    if isinstance(projection_result, dict):
        final_actor_message = projection_result.get("final_actor_message")
        if isinstance(final_actor_message, str) and final_actor_message.strip():
            return final_actor_message.strip()
    return str(call.get("prompt") or "")


def _append_projected_message(
    run_dir: Path,
    actor_id: str,
    call: dict,
    packet: dict,
    source_call_id: str,
    source_message_id: str,
    *,
    projection_result: dict | None = None,
) -> dict:
    final_actor_message = _projection_final_actor_message(call, projection_result)
    payload = {
        "actor_id": actor_id,
        "natural_message": final_actor_message,
    }
    if source_message_id:
        payload["source_message_id"] = source_message_id
    result = agent_messages.append_message(
        run_dir,
        {
            "from": "projection",
            "to": [actor_id],
            "type": "projected_message",
            "visibility": "actor_facing",
            "source_call_id": source_call_id,
            "payload": payload,
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
