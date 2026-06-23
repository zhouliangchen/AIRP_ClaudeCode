"""Declarative capability registry for agent-driven routing requests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class CapabilityRegistryError(ValueError):
    """Raised when a capability request is structurally invalid."""


RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
VALID_AUTHORIZATION_GATES = {"none", "manual_confirmation", "allowSourceCodeSelfRepair"}
VALID_SOURCE_CHANNELS = {"user_instruction", "role_input", "raw_input"}

CAPABILITIES: dict[str, dict[str, Any]] = {
    "assets.generate_image": {
        "target": "assets-ui",
        "action": "intent",
        "intent_type": "assets_task",
        "allowed_requesters": ("input_analyst", "gm", "story", "critic", "main_agent"),
        "authorization_gate": "none",
        "max_risk": "medium",
    },
    "source.change_request": {
        "target": "main-agent",
        "action": "intent",
        "intent_type": "system_request",
        "allowed_requesters": ("input_analyst", "critic", "main_agent"),
        "authorization_gate": "allowSourceCodeSelfRepair",
        "max_risk": "critical",
    },
    "retcon.consult": {
        "target": "story",
        "action": "message",
        "message_targets": ("gm", "story"),
        "visibility": "story_facing",
        "allowed_requesters": ("input_analyst", "gm", "main_agent"),
        "authorization_gate": "none",
        "max_risk": "high",
    },
    "replay.plan": {
        "target": "replay",
        "action": "intent",
        "intent_type": "replay_plan",
        "allowed_requesters": ("input_analyst", "story", "gm", "main_agent"),
        "authorization_gate": "manual_confirmation",
        "max_risk": "critical",
    },
    "card.patch_data": {
        "target": "card-data",
        "action": "audit_only",
        "allowed_requesters": ("input_analyst", "gm", "main_agent"),
        "authorization_gate": "manual_confirmation",
        "max_risk": "high",
    },
}

LEGACY_TYPE_MAP = {
    "assets_ui_task": "assets.generate_image",
    "source_feature_request": "source.change_request",
    "story_retcon_consult": "retcon.consult",
    "card_data_edit": "card.patch_data",
}


def normalize_capability_request(request: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized capability request without mutating caller data."""

    data = _require_dict(request, "capability_request")
    normalized = {
        "id": _require_nonempty_str(data, "id", "capability_request"),
        "requested_by": _require_nonempty_str(data, "requested_by", "capability_request"),
        "target": _require_nonempty_str(data, "target", "capability_request"),
        "capability": _require_nonempty_str(data, "capability", "capability_request"),
        "summary": _require_nonempty_str(data, "summary", "capability_request"),
        "reason": _require_nonempty_str(data, "reason", "capability_request"),
        "source_channel": _require_nonempty_str(data, "source_channel", "capability_request"),
        "risk": _require_nonempty_str(data, "risk", "capability_request"),
        "authorization_gate": _require_nonempty_str(data, "authorization_gate", "capability_request"),
        "payload": _optional_dict(data, "payload", "capability_request"),
        "evidence": _optional_dict(data, "evidence", "capability_request"),
    }
    _validate_request_shape(normalized)

    definition = CAPABILITIES.get(normalized["capability"])
    if definition is None:
        normalized.update(
            {
                "status": "unsupported_capability",
                "action": "audit_only",
                "registry": {},
            }
        )
        return normalized

    normalized["registry"] = deepcopy(definition)

    expected_target = str(definition.get("target") or "")
    if expected_target and normalized["target"] != expected_target:
        normalized.update(
            {
                "status": "target_mismatch",
                "action": "audit_only",
                "expected_target": expected_target,
            }
        )
        return normalized

    expected_gate = str(definition.get("authorization_gate") or "")
    if expected_gate and normalized["authorization_gate"] != expected_gate:
        normalized.update(
            {
                "status": "authorization_gate_mismatch",
                "action": "audit_only",
                "expected_authorization_gate": expected_gate,
            }
        )
        return normalized

    if normalized["requested_by"] not in definition["allowed_requesters"]:
        normalized.update({"status": "requester_not_allowed", "action": "audit_only"})
        return normalized
    if RISK_ORDER[normalized["risk"]] > RISK_ORDER[definition["max_risk"]]:
        normalized.update({"status": "risk_exceeds_capability", "action": "audit_only"})
        return normalized

    normalized["status"] = "recognized"
    normalized["action"] = definition["action"]
    if "intent_type" in definition:
        normalized["intent_type"] = definition["intent_type"]
    if "message_targets" in definition:
        normalized["message_targets"] = list(definition["message_targets"])
    if "visibility" in definition:
        normalized["visibility"] = definition["visibility"]
    return normalized


def legacy_routing_request_to_capability(route: dict[str, Any]) -> dict[str, Any]:
    """Map a legacy routing request into the current capability request shape."""

    data = _require_dict(route, "routing_request")
    legacy_type = _require_nonempty_str(data, "type", "routing_request")
    capability = LEGACY_TYPE_MAP.get(legacy_type, f"legacy.{legacy_type}")
    definition = CAPABILITIES.get(capability, {})
    return {
        "id": _require_nonempty_str(data, "id", "routing_request"),
        "requested_by": "input_analyst",
        "target": str(definition.get("target") or data.get("target") or "legacy"),
        "capability": capability,
        "summary": _require_nonempty_str(data, "summary", "routing_request"),
        "reason": str(data.get("reason") or data.get("summary") or "").strip(),
        "source_channel": _require_nonempty_str(data, "source_channel", "routing_request"),
        "risk": str(data.get("risk") or _default_risk(capability)).strip(),
        "authorization_gate": str(definition.get("authorization_gate") or data.get("authorization_gate") or "none").strip(),
        "payload": _optional_dict(data, "payload", "routing_request"),
        "evidence": _optional_dict(data, "evidence", "routing_request"),
        "legacy_type": legacy_type,
    }


def authorize_capability(
    normalized: dict[str, Any],
    runtime_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Authorize a normalized request against runtime settings."""

    data = _require_dict(normalized, "normalized_capability")
    gate = _require_nonempty_str(data, "authorization_gate", "normalized_capability")
    status = str(data.get("status") or "")
    if status and status != "recognized":
        return {"allowed": False, "status": status, "authorization_gate": gate}
    if gate == "none":
        return {"allowed": True, "status": "authorized", "authorization_gate": gate}
    if gate == "manual_confirmation":
        return {
            "allowed": False,
            "status": "authorization_required",
            "authorization_gate": gate,
            "manual_confirmation_required": True,
        }

    settings = runtime_settings if isinstance(runtime_settings, dict) else {}
    allowed = settings.get(gate) is True
    result = {
        "allowed": allowed,
        "status": "authorized" if allowed else "authorization_required",
        "authorization_gate": gate,
    }
    if gate == "allowSourceCodeSelfRepair":
        result["allowSourceCodeSelfRepair"] = allowed
    return result


def _default_risk(capability: str) -> str:
    if capability == "source.change_request":
        return "high"
    if capability.startswith("legacy."):
        return "low"
    return "medium"


def _validate_request_shape(normalized: dict[str, Any]) -> None:
    if normalized["source_channel"] not in VALID_SOURCE_CHANNELS:
        raise CapabilityRegistryError("capability_request.source_channel is invalid")
    if normalized["risk"] not in RISK_ORDER:
        raise CapabilityRegistryError("capability_request.risk is invalid")
    if normalized["authorization_gate"] not in VALID_AUTHORIZATION_GATES:
        raise CapabilityRegistryError("capability_request.authorization_gate is invalid")
    raw_excerpt = normalized["evidence"].get("raw_excerpt")
    if not isinstance(raw_excerpt, str) or not raw_excerpt.strip():
        raise CapabilityRegistryError("capability_request.evidence.raw_excerpt is required")


def _require_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CapabilityRegistryError(f"{path} must be an object")
    return deepcopy(value)


def _optional_dict(payload: dict[str, Any], key: str, path: str) -> dict[str, Any]:
    value = payload.get(key, {})
    if not isinstance(value, dict):
        raise CapabilityRegistryError(f"{path}.{key} must be an object")
    return deepcopy(value)


def _require_nonempty_str(payload: dict[str, Any], key: str, path: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CapabilityRegistryError(f"{path}.{key} must be a non-empty string")
    return value.strip()
