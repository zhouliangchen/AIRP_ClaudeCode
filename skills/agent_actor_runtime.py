"""Actor collaboration helpers for per-round agent runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import agent_intents
import agent_messages
import agent_run


class AgentActorRuntimeError(RuntimeError):
    """Raised when actor collaboration artifacts cannot be recorded."""


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
                        "source_call_id": call_id,
                    },
                },
            ),
        )
        intent_id = str(intent["id"])
        _require_intent_result(
            "accept request_projection intent",
            agent_intents.accept_intent(run_dir, intent_id),
        )
        return str(request_message["id"]), intent_id
    except AgentActorRuntimeError:
        raise
    except Exception as exc:
        raise _runtime_write_error("record request_actor intent", exc) from exc


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


def read_actor_outputs_artifact(run_dir: Path) -> dict:
    """Read dispatcher-authoritative actor outputs artifact if present."""

    path = Path(run_dir) / "artifacts" / "actor.outputs.json"
    if not path.exists():
        return {}
    data = agent_run.read_json(path, {})
    if not isinstance(data, dict):
        raise AgentActorRuntimeError(f"{path}: actor outputs artifact must be a JSON object")
    return data


def append_actor_output_artifact(run_dir: Path, actor_id: str, actor_output: dict) -> dict:
    """Append one actor output to artifacts/actor.outputs.json."""

    if not isinstance(actor_id, str) or not actor_id:
        raise AgentActorRuntimeError("actor_id must be a non-empty string")
    if not isinstance(actor_output, dict):
        raise AgentActorRuntimeError("actor_output must be a JSON object")

    payload = read_actor_outputs_artifact(run_dir)
    existing = payload.get(actor_id)
    if existing is None:
        outputs: list[dict[str, Any]] = []
    elif isinstance(existing, list):
        outputs = list(existing)
    else:
        raise AgentActorRuntimeError(f"actor outputs for {actor_id} must be a list")
    outputs.append(actor_output)
    payload[actor_id] = outputs
    agent_run.write_json(Path(run_dir) / "artifacts" / "actor.outputs.json", payload)
    return payload


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
