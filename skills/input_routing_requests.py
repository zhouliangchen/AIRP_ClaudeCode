"""Process validated input-analysis routing requests into safe control-plane work."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import agent_intents
import agent_messages
import agent_run


REQUESTED_BY = "input_analyst"
ARTIFACT_DIR = "input_routing_requests"
SOURCE_NAME = "input_analysis.routing_requests"


def process_routing_requests(
    run_dir: str | Path,
    routing_requests: list[dict[str, Any]],
    *,
    runtime_settings: dict[str, Any] | None = None,
    source_intent_id: str = "",
) -> dict[str, Any]:
    """Create safe intents/messages/audit artifacts for validated routing requests."""

    run_dir = Path(run_dir)
    settings = runtime_settings if isinstance(runtime_settings, dict) else {}
    created_intents: list[str] = []
    created_messages: list[str] = []
    artifacts: list[str] = []
    results: list[dict[str, Any]] = []

    for request in routing_requests or []:
        request_id = _request_id(request)
        request_type = str(request.get("type") or "")
        safe_id = _safe_request_id(request_id)
        artifact_rel = f"artifacts/{ARTIFACT_DIR}/{safe_id}.json"
        context = {
            "run_dir": run_dir,
            "request": request,
            "request_id": request_id,
            "request_type": request_type,
            "safe_id": safe_id,
            "runtime_settings": settings,
            "source_intent_id": source_intent_id,
        }

        result: dict[str, Any]
        if request_type == "assets_ui_task":
            result = _process_assets_ui_task(context)
        elif request_type == "source_feature_request":
            result = _process_source_feature_request(context)
        elif request_type == "story_retcon_consult":
            result = _process_story_retcon_consult(context)
        elif request_type == "card_data_edit":
            result = _process_card_data_edit(context)
        else:
            result = {"status": "blocked", "created_intents": [], "created_messages": []}

        artifact = _audit_artifact(
            context,
            status=result["status"],
            created_intents=result.get("created_intents", []),
            created_messages=result.get("created_messages", []),
        )
        agent_run.write_json(run_dir / artifact_rel, artifact)
        artifacts.append(artifact_rel)

        created_intents.extend(result.get("created_intents", []))
        created_messages.extend(result.get("created_messages", []))
        results.append(
            {
                "request_id": request_id,
                "type": request_type,
                "status": result["status"],
                "artifact": artifact_rel,
                "created_intents": result.get("created_intents", []),
                "created_messages": result.get("created_messages", []),
            }
        )

    return {
        "ok": True,
        "processed_count": len(routing_requests or []),
        "created_intents": created_intents,
        "created_messages": created_messages,
        "artifacts": artifacts,
        "created_intents_count": len(created_intents),
        "created_messages_count": len(created_messages),
        "results": results,
    }


def _process_assets_ui_task(context: dict[str, Any]) -> dict[str, Any]:
    request = context["request"]
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    request_id = context["request_id"]
    intent_payload = {
        "kind": str(payload.get("kind") or "scene"),
        "target": str(payload.get("target") or request_id),
        "prompt": str(payload.get("prompt") or request.get("summary") or ""),
        "source": f"{SOURCE_NAME}.{request_id}",
        "routing_request": _routing_request_reference(request),
    }
    intent = _create_intent(
        context,
        {
            "requested_by": REQUESTED_BY,
            "type": "assets_task",
            "payload": intent_payload,
            "policy": _policy(context),
        },
    )
    message_id = _append_routing_message(context, ["assets"], "gm_only", intent_id=intent["id"])
    _attach_source_message(context["run_dir"], intent["id"], message_id)
    return {"status": "queued", "created_intents": [intent["id"]], "created_messages": [message_id]}


def _process_source_feature_request(context: dict[str, Any]) -> dict[str, Any]:
    settings = context["runtime_settings"]
    allow_source = settings.get("allowSourceCodeSelfRepair") is True

    if not allow_source:
        message_id = _append_routing_message(context, ["main_agent"], "gm_only")
        return {
            "status": "authorization_required",
            "created_intents": [],
            "created_messages": [message_id],
        }

    request = context["request"]
    system_payload = {
        "reason": "user_requested_source_feature",
        "authorization_gate": "allowSourceCodeSelfRepair",
        "selfRepairMode_required": False,
        "source": SOURCE_NAME,
        "routing_request_id": context["request_id"],
        "summary": request.get("summary"),
        "target": request.get("target"),
        "payload": request.get("payload") if isinstance(request.get("payload"), dict) else {},
        "evidence": request.get("evidence") if isinstance(request.get("evidence"), dict) else {},
    }
    intent = _create_intent(
        context,
        {
            "requested_by": REQUESTED_BY,
            "type": "system_request",
            "payload": system_payload,
            "policy": _policy(context),
        },
    )
    message_id = _append_routing_message(context, ["main_agent"], "gm_only", intent_id=intent["id"])
    _attach_source_message(context["run_dir"], intent["id"], message_id)
    return {"status": "queued", "created_intents": [intent["id"]], "created_messages": [message_id]}


def _process_story_retcon_consult(context: dict[str, Any]) -> dict[str, Any]:
    message_id = _append_routing_message(context, ["gm", "story"], "story_facing")
    return {"status": "deferred", "created_intents": [], "created_messages": [message_id]}


def _process_card_data_edit(context: dict[str, Any]) -> dict[str, Any]:
    message_id = _append_routing_message(context, ["gm", "main_agent"], "gm_only")
    return {"status": "audit_only", "created_intents": [], "created_messages": [message_id]}


def _create_intent(context: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    created = agent_intents.create_intent(context["run_dir"], payload)
    if not created.get("ok") or not isinstance(created.get("intent"), dict):
        raise RuntimeError(f"routing request intent creation failed: {created!r}")
    return created["intent"]


def _append_routing_message(
    context: dict[str, Any],
    targets: list[str],
    visibility: str,
    *,
    intent_id: str = "",
) -> str:
    payload = {
        "routing_request_id": context["request_id"],
        "routing_request_type": context["request_type"],
        "summary": context["request"].get("summary"),
        "target": context["request"].get("target"),
        "payload": context["request"].get("payload") if isinstance(context["request"].get("payload"), dict) else {},
        "evidence": context["request"].get("evidence") if isinstance(context["request"].get("evidence"), dict) else {},
        "source": SOURCE_NAME,
    }
    if intent_id:
        payload["intent_id"] = intent_id
    appended = agent_messages.append_message(
        context["run_dir"],
        {
            "from": REQUESTED_BY,
            "to": targets,
            "type": "routing_request",
            "visibility": visibility,
            "payload": payload,
        },
    )
    if not appended.get("ok"):
        raise RuntimeError(f"routing request message append failed: {appended!r}")
    message_id = str((appended.get("message") or {}).get("id") or "")
    if not message_id:
        raise RuntimeError(f"routing request message append missing id: {appended!r}")
    return message_id


def _attach_source_message(run_dir: Path, intent_id: str, message_id: str) -> None:
    attached = agent_intents.attach_source_message(run_dir, intent_id, message_id)
    if not attached.get("ok"):
        raise RuntimeError(f"routing request source message attach failed: {attached!r}")


def _audit_artifact(
    context: dict[str, Any],
    *,
    status: str,
    created_intents: list[str],
    created_messages: list[str],
) -> dict[str, Any]:
    settings = context["runtime_settings"]
    return {
        "schema_version": 1,
        "request_id": context["request_id"],
        "request_type": context["request_type"],
        "status": status,
        "requested_by": REQUESTED_BY,
        "source_round": context["run_dir"].name,
        "authorization": {
            "authorization_gate": context["request"].get("authorization_gate"),
            "requires_authorization": context["request"].get("requires_authorization"),
            "allowSourceCodeSelfRepair": settings.get("allowSourceCodeSelfRepair") is True,
            "selfRepairMode": settings.get("selfRepairMode"),
        },
        "created_intent_ids": list(created_intents),
        "created_message_ids": list(created_messages),
        "source_intent_id": context["source_intent_id"],
        "request": context["request"],
    }


def _policy(context: dict[str, Any]) -> dict[str, str]:
    return {
        "source_intent_id": context["source_intent_id"],
        "routing_request_id": context["request_id"],
    }


def _routing_request_reference(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _request_id(request),
        "type": request.get("type"),
        "summary": request.get("summary"),
        "target": request.get("target"),
        "evidence": request.get("evidence") if isinstance(request.get("evidence"), dict) else {},
    }


def _request_id(request: dict[str, Any]) -> str:
    request_id = request.get("id") if isinstance(request, dict) else ""
    return str(request_id or "routing_request")


def _safe_request_id(request_id: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", str(request_id or "routing_request"))
    safe = safe.strip("._")
    return safe or "routing_request"
