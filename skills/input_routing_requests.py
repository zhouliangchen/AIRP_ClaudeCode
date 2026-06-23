"""Process validated input-analysis capability requests into safe control-plane work."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import agent_intents
import agent_messages
import agent_run
import capability_registry


REQUESTED_BY = "input_analyst"
CAPABILITY_ARTIFACT_DIR = "capability_requests"
CAPABILITY_SOURCE_NAME = "input_analysis.capability_requests"
SAFE_REQUEST_ID_PREFIX_MAX = 80


def process_capability_requests(
    run_dir: str | Path,
    capability_requests: list[dict[str, Any]],
    *,
    runtime_settings: dict[str, Any] | None = None,
    source_intent_id: str = "",
) -> dict[str, Any]:
    """Create safe intents/messages/audit artifacts for validated capability requests."""

    run_dir = Path(run_dir)
    settings = runtime_settings if isinstance(runtime_settings, dict) else {}
    created_intents: list[str] = []
    created_messages: list[str] = []
    artifacts: list[str] = []
    results: list[dict[str, Any]] = []

    for request in capability_requests or []:
        normalized = capability_registry.normalize_capability_request(request)
        context = {
            "run_dir": run_dir,
            "request": normalized,
            "request_id": normalized["id"],
            "safe_id": _safe_request_id(normalized["id"]),
            "runtime_settings": settings,
            "source_intent_id": source_intent_id,
        }
        result = _process_capability_request(context)
        artifacts.append(result["artifact"])
        created_intents.extend(result.get("created_intents", []))
        created_messages.extend(result.get("created_messages", []))
        results.append(result)

    return {
        "ok": True,
        "processed_count": len(capability_requests or []),
        "created_intents": created_intents,
        "created_messages": created_messages,
        "artifacts": artifacts,
        "created_intents_count": len(created_intents),
        "created_messages_count": len(created_messages),
        "results": results,
    }


def process_routing_requests(
    run_dir: str | Path,
    routing_requests: list[dict[str, Any]],
    *,
    runtime_settings: dict[str, Any] | None = None,
    source_intent_id: str = "",
) -> dict[str, Any]:
    """Compatibility wrapper for legacy input-analysis routing requests."""

    capability_requests = [
        capability_registry.legacy_routing_request_to_capability(item)
        for item in routing_requests or []
        if isinstance(item, dict)
    ]
    return process_capability_requests(
        run_dir,
        capability_requests,
        runtime_settings=runtime_settings,
        source_intent_id=source_intent_id,
    )


def _process_capability_request(context: dict[str, Any]) -> dict[str, Any]:
    request = context["request"]
    artifact_rel = f"artifacts/{CAPABILITY_ARTIFACT_DIR}/{context['safe_id']}.json"
    existing_audit = agent_run.read_json(context["run_dir"] / artifact_rel)
    if isinstance(existing_audit, dict):
        return _existing_audit_result(existing_audit, artifact_rel, request)

    status = str(request.get("status") or "recognized")
    created_intents: list[str] = []
    created_messages: list[str] = []

    if status != "recognized":
        authorization = {
            "allowed": False,
            "status": status,
            "authorization_gate": request.get("authorization_gate"),
        }
        message_id = _append_capability_message(context, ["main_agent"], "gm_only", status)
        created_messages.append(message_id)
    else:
        authorization = capability_registry.authorize_capability(
            request,
            runtime_settings=context["runtime_settings"],
        )
        if not authorization.get("allowed"):
            status = str(authorization.get("status") or "authorization_required")
            message_id = _append_capability_message(context, ["main_agent"], "gm_only", status)
            created_messages.append(message_id)
        elif request["action"] == "intent":
            intent = _create_capability_intent(context, request)
            created_intents.append(intent["id"])
            status = "queued"
            message_id = _append_capability_message(
                context,
                _message_targets_for_request(request),
                "gm_only",
                "capability_request",
                intent_id=intent["id"],
            )
            created_messages.append(message_id)
            _attach_source_message(context["run_dir"], intent["id"], message_id)
        elif request["action"] == "message":
            status = "deferred"
            message_id = _append_capability_message(
                context,
                request.get("message_targets", ["gm"]),
                request.get("visibility", "gm_only"),
                "capability_request",
            )
            created_messages.append(message_id)
        else:
            status = "audit_only"

    artifact = _capability_audit_artifact(
        context,
        status=status,
        authorization=authorization,
        created_intents=created_intents,
        created_messages=created_messages,
    )
    agent_run.write_json(context["run_dir"] / artifact_rel, artifact)
    return {
        "request_id": request["id"],
        "type": request.get("legacy_type") or request["capability"],
        "capability": request["capability"],
        "status": status,
        "artifact": artifact_rel,
        "created_intents": created_intents,
        "created_messages": created_messages,
    }


def _create_capability_intent(context: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    intent_type = str(request.get("intent_type") or "")
    existing = _find_existing_capability_intent(context, intent_type)
    if existing is not None:
        return existing

    created = agent_intents.create_intent(
        context["run_dir"],
        {
            "requested_by": REQUESTED_BY,
            "type": intent_type,
            "payload": _intent_payload_for_request(request),
            "policy": _policy(context),
        },
    )
    if not created.get("ok") or not isinstance(created.get("intent"), dict):
        raise RuntimeError(f"capability request intent creation failed: {created!r}")
    return created["intent"]


def _intent_payload_for_request(request: dict[str, Any]) -> dict[str, Any]:
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    capability = request["capability"]
    if capability == "assets.generate_image":
        intent_payload = {
            "kind": str(payload.get("kind") or "scene"),
            "target": str(payload.get("target") or request["id"]),
            "prompt": str(payload.get("prompt") or request.get("summary") or ""),
            "source": f"{CAPABILITY_SOURCE_NAME}.{request['id']}",
            "capability_request": {
                "id": request["id"],
                "capability": capability,
            },
        }
        for key in ("ui_schema", "postprocess_contract"):
            if isinstance(payload.get(key), dict):
                intent_payload[key] = payload[key]
        return intent_payload

    if capability == "source.change_request":
        return {
            "reason": "user_requested_source_feature",
            "authorization_gate": "allowSourceCodeSelfRepair",
            "selfRepairMode_required": False,
            "source": CAPABILITY_SOURCE_NAME,
            "capability_request_id": request["id"],
            "summary": request["summary"],
            "target": request["target"],
            "payload": payload,
            "evidence": request.get("evidence") if isinstance(request.get("evidence"), dict) else {},
        }

    return {
        "source": CAPABILITY_SOURCE_NAME,
        "capability_request_id": request["id"],
        "capability": capability,
        "summary": request.get("summary"),
        "target": request.get("target"),
        "payload": payload,
        "evidence": request.get("evidence") if isinstance(request.get("evidence"), dict) else {},
    }


def _message_targets_for_request(request: dict[str, Any]) -> list[str]:
    if isinstance(request.get("message_targets"), list) and request["message_targets"]:
        return [str(item) for item in request["message_targets"] if str(item)]
    target = str(request.get("target") or "")
    if target == "assets-ui":
        return ["assets"]
    if target == "main-agent":
        return ["main_agent"]
    if target:
        return [target]
    return ["main_agent"]


def _append_capability_message(
    context: dict[str, Any],
    targets: list[str],
    visibility: str,
    message_type: str,
    *,
    intent_id: str = "",
) -> str:
    existing_message_id = _find_existing_capability_message(
        context,
        message_type=message_type,
        intent_id=intent_id,
    )
    if existing_message_id:
        return existing_message_id

    request = context["request"]
    payload = {
        "capability_request_id": context["request_id"],
        "capability": request["capability"],
        "status": message_type if message_type != "capability_request" else request.get("status"),
        "summary": request.get("summary"),
        "target": request.get("target"),
        "payload": request.get("payload") if isinstance(request.get("payload"), dict) else {},
        "evidence": request.get("evidence") if isinstance(request.get("evidence"), dict) else {},
        "source": CAPABILITY_SOURCE_NAME,
    }
    if request.get("legacy_type"):
        payload["legacy_type"] = request["legacy_type"]
    if intent_id:
        payload["intent_id"] = intent_id

    appended = agent_messages.append_message(
        context["run_dir"],
        {
            "from": REQUESTED_BY,
            "to": targets,
            "type": message_type,
            "visibility": visibility,
            "payload": payload,
        },
    )
    if not appended.get("ok"):
        raise RuntimeError(f"capability request message append failed: {appended!r}")
    message_id = str((appended.get("message") or {}).get("id") or "")
    if not message_id:
        raise RuntimeError(f"capability request message append missing id: {appended!r}")
    return message_id


def _attach_source_message(run_dir: Path, intent_id: str, message_id: str) -> None:
    intent = _find_intent_by_id(run_dir, intent_id)
    if isinstance(intent, dict) and intent.get("source_message_id") == message_id:
        return
    attached = agent_intents.attach_source_message(run_dir, intent_id, message_id)
    if not attached.get("ok"):
        raise RuntimeError(f"capability request source message attach failed: {attached!r}")


def _find_existing_capability_intent(context: dict[str, Any], intent_type: str) -> dict[str, Any] | None:
    for state in agent_intents.VALID_STATES:
        for intent in agent_intents.list_intents(context["run_dir"], state):
            if intent.get("type") != intent_type:
                continue
            policy = intent.get("policy")
            if not isinstance(policy, dict):
                continue
            if policy.get("capability_request_id") != context["request_id"]:
                continue
            return intent
    return None


def _find_existing_capability_message(
    context: dict[str, Any],
    *,
    message_type: str,
    intent_id: str = "",
) -> str:
    for message in agent_messages.read_messages(context["run_dir"]):
        if message.get("from") != REQUESTED_BY or message.get("type") != message_type:
            continue
        payload = message.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("capability_request_id") != context["request_id"]:
            continue
        payload_intent_id = str(payload.get("intent_id") or "")
        if intent_id:
            if payload_intent_id != intent_id:
                continue
        elif payload_intent_id:
            continue
        message_id = str(message.get("id") or "")
        if message_id:
            return message_id
    return ""


def _find_intent_by_id(run_dir: Path, intent_id: str) -> dict[str, Any] | None:
    for state in agent_intents.VALID_STATES:
        for intent in agent_intents.list_intents(run_dir, state):
            if intent.get("id") == intent_id:
                return intent
    return None


def _capability_audit_artifact(
    context: dict[str, Any],
    *,
    status: str,
    authorization: dict[str, Any],
    created_intents: list[str],
    created_messages: list[str],
) -> dict[str, Any]:
    request = context["request"]
    return {
        "schema_version": 1,
        "request_id": context["request_id"],
        "capability": request["capability"],
        "target": request.get("target"),
        "action": request.get("action"),
        "intent_type": request.get("intent_type"),
        "status": status,
        "requested_by": REQUESTED_BY,
        "source_round": context["run_dir"].name,
        "authorization": authorization,
        "registry_status": request.get("status"),
        "registry": request.get("registry") if isinstance(request.get("registry"), dict) else {},
        "created_intent_ids": list(created_intents),
        "created_message_ids": list(created_messages),
        "source_intent_id": context["source_intent_id"],
        "request": request,
    }


def _existing_audit_result(
    audit: dict[str, Any],
    artifact_rel: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    created_intents = audit.get("created_intent_ids")
    if not isinstance(created_intents, list):
        created_intents = []
    created_messages = audit.get("created_message_ids")
    if not isinstance(created_messages, list):
        created_messages = []
    capability = str(audit.get("capability") or request.get("capability") or "")
    return {
        "request_id": str(audit.get("request_id") or request.get("id") or ""),
        "type": str((audit.get("request") or {}).get("legacy_type") or capability),
        "capability": capability,
        "status": str(audit.get("status") or "blocked"),
        "artifact": artifact_rel,
        "created_intents": [str(item) for item in created_intents],
        "created_messages": [str(item) for item in created_messages],
    }


def _policy(context: dict[str, Any]) -> dict[str, str]:
    return {
        "source_intent_id": context["source_intent_id"],
        "capability_request_id": context["request_id"],
    }


def _safe_request_id(request_id: str) -> str:
    raw = str(request_id or "")
    prefix = re.sub(r"[^0-9A-Za-z_-]+", "-", raw).strip("-_")
    if not prefix:
        prefix = "capability_request"
    prefix = prefix[:SAFE_REQUEST_ID_PREFIX_MAX].rstrip("-_") or "capability_request"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"
