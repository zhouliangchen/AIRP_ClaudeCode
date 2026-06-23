import hashlib
import json
import math
from pathlib import Path

import capability_registry


SCHEMA_VERSION = 1
ANALYSIS_MODES = {"ai", "fallback", "fixture"}
SEMANTIC_UNIT_TYPES = {
    "action",
    "synopsis",
    "omniscient_setting",
    "hidden_setting",
    "character_declaration",
    "edit_request",
    "system_command",
    "style_guidance",
    "unclear",
}
VISIBILITIES = {
    "gm_only",
    "public_world",
    "player_pov",
    "character_pov",
    "specific_characters",
}
WORLD_UPDATE_LIST_KEYS = (
    "hidden_facts",
    "public_facts",
    "important_characters",
    "retcon_requests",
)
WORLD_UPDATE_SAFE_STATUSES = {
    "active",
    "superseded",
    "retracted",
}
IMPORTANT_CHARACTER_VISIBILITIES = {
    "character_private_and_gm",
    "public_world",
    "character_pov",
    "specific_characters",
}
RETCON_VISIBILITIES = {
    "gm_only",
    "public_world",
}
IMPORTANT_CHARACTER_TEXT_KEYS = (
    "text",
    "setting_text",
    "authoritative_setting",
    "description",
    "profile",
    "summary",
)
NARRATIVE_DIRECTIVE_BOOL_KEYS = (
    "rewrite_previous_output",
    "expand_synopsis_before_continue",
    "continue_after_player_action",
    "must_stop_for_player_decision",
)
FALLBACK_HIGH_RISK_TYPES = {
    "hidden_setting",
    "character_declaration",
    "edit_request",
    "system_command",
}
ROUTING_REQUEST_TYPES = {
    "assets_ui_task",
    "story_retcon_consult",
    "card_data_edit",
    "source_feature_request",
}
ROUTING_REQUEST_SOURCE_CHANNELS = {
    "user_instruction",
    "role_input",
    "raw_input",
}
ROUTING_REQUEST_AUTHORIZATION_GATES = {
    "none",
    "allowSourceCodeSelfRepair",
}


class InputAnalysisError(RuntimeError):
    """Raised when an input analysis artifact fails validation."""


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json(path):
    json_path = Path(path)
    try:
        with json_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise InputAnalysisError(f"failed to load input analysis JSON: {json_path}") from exc


def validate_input_analysis(
    data,
    *,
    raw_text,
    role_text="",
    user_instruction_text="",
    explicit_payload=None,
):
    if not isinstance(data, dict):
        raise InputAnalysisError("input analysis must be a JSON object")

    if (
        type(data.get("schema_version")) is not int
        or data.get("schema_version") != SCHEMA_VERSION
    ):
        raise InputAnalysisError("schema_version must be 1")

    analysis_mode = data.get("analysis_mode")
    if analysis_mode not in ANALYSIS_MODES:
        raise InputAnalysisError("analysis_mode must be ai, fallback, or fixture")

    source_integrity = data.get("source_integrity")
    if not isinstance(source_integrity, dict):
        raise InputAnalysisError("source_integrity must be an object")
    if source_integrity.get("raw_preserved") is not True:
        raise InputAnalysisError("source_integrity.raw_preserved must be true")

    _validate_required_hash(source_integrity, "raw_text_sha256", raw_text, "raw_text")
    _validate_required_hash(source_integrity, "role_text_sha256", role_text, "role_text")
    _validate_required_hash(
        source_integrity,
        "user_instruction_text_sha256",
        user_instruction_text,
        "user_instruction_text",
    )

    semantic_units = data.get("semantic_units")
    if not isinstance(semantic_units, list):
        raise InputAnalysisError("semantic_units must be a list")
    for index, unit in enumerate(semantic_units):
        _validate_semantic_unit(unit, index)

    world_updates = data.get("world_updates")
    if not isinstance(world_updates, dict):
        raise InputAnalysisError("world_updates must be an object")
    for key in WORLD_UPDATE_LIST_KEYS:
        if not isinstance(world_updates.get(key), list):
            raise InputAnalysisError(f"world_updates.{key} must be a list")
    _validate_world_update_records(world_updates)

    _validate_narrative_directives(data.get("narrative_directives"))
    _validate_routing(data.get("routing"))
    _validate_routing_grounded(
        data.get("routing"),
        raw_text=raw_text,
        explicit_payload=explicit_payload,
    )
    _validate_routing_requests(data.get("routing_requests"))
    _validate_capability_requests(data.get("capability_requests", []))

    if analysis_mode == "fallback":
        _validate_fallback_has_no_high_risk_persistence(
            semantic_units, world_updates
        )

    return data


def analysis_to_routed_input(data, explicit_payload=None):
    routing = data.get("routing") if isinstance(data, dict) else None
    if not isinstance(routing, dict):
        routing = {}

    role_channel = _to_text(routing.get("role_channel", ""))
    user_instruction_channel = _to_text(routing.get("user_instruction_channel", ""))

    if (
        isinstance(explicit_payload, dict)
        and explicit_payload.get("input_schema") == "dual_channel_v1"
    ):
        role_channel = _to_text(explicit_payload.get("role_text"))
        user_instruction_channel = _to_text(
            explicit_payload.get("user_instruction_text")
        )

    characters = routing.get("characters", [])
    if not isinstance(characters, list):
        characters = []

    components = []
    if role_channel:
        components.append({"channel": "role", "text": role_channel})
    if user_instruction_channel:
        components.append(
            {"channel": "user_instruction", "text": user_instruction_channel}
        )

    return {
        "input_schema": "analysis_v1",
        "analysis_mode": (
            data.get("analysis_mode", "") if isinstance(data, dict) else ""
        ),
        "role_channel": role_channel,
        "user_instruction_channel": user_instruction_channel,
        "gm": bool(routing.get("gm", bool(user_instruction_channel))),
        "player": bool(routing.get("player", bool(role_channel))),
        "characters": characters,
        "components": components,
    }


def build_fallback_analysis(
    *, raw_text, role_text="", user_instruction_text="", round_id=""
):
    semantic_units = []
    if role_text:
        semantic_units.append(
            {
                "id": "fallback-role-1",
                "source_channel": "role_input",
                "type": "action",
                "raw_excerpt": role_text,
                "derived_summary": "Fallback preserved role input without semantic interpretation.",
                "confidence": 0.0,
                "visibility": "player_pov",
                "persist": False,
            }
        )
    if user_instruction_text:
        semantic_units.append(
            {
                "id": "fallback-user-instruction-1",
                "source_channel": "user_instruction",
                "type": "unclear",
                "raw_excerpt": user_instruction_text,
                "derived_summary": "Fallback preserved user instruction without persistence.",
                "confidence": 0.0,
                "visibility": "gm_only",
                "persist": False,
            }
        )

    analysis = {
        "schema_version": SCHEMA_VERSION,
        "round_id": round_id,
        "analysis_mode": "fallback",
        "source_integrity": {
            "raw_text_sha256": sha256_text(raw_text),
            "role_text_sha256": sha256_text(role_text),
            "user_instruction_text_sha256": sha256_text(user_instruction_text),
            "raw_preserved": True,
        },
        "semantic_units": semantic_units,
        "world_updates": {
            "hidden_facts": [],
            "public_facts": [],
            "important_characters": [],
            "retcon_requests": [],
        },
        "narrative_directives": {
            "rewrite_previous_output": False,
            "expand_synopsis_before_continue": False,
            "continue_after_player_action": bool(role_text),
            "must_stop_for_player_decision": False,
        },
        "routing": {
            "role_channel": role_text,
            "user_instruction_channel": user_instruction_text,
            "gm": bool(user_instruction_text),
            "player": bool(role_text),
            "characters": [],
        },
        "routing_requests": [],
        "capability_requests": [],
        "risks": [
            "fallback: semantic persistence blocked; raw input preserved for downstream handling"
        ],
    }
    return validate_input_analysis(
        analysis,
        raw_text=raw_text,
        role_text=role_text,
        user_instruction_text=user_instruction_text,
    )


def _to_text(value):
    if value is None:
        return ""
    return str(value)


def _validate_required_hash(source_integrity, key, text, label):
    if key not in source_integrity:
        raise InputAnalysisError(f"source_integrity.{key} is required")
    if source_integrity[key] != sha256_text(text):
        raise InputAnalysisError(f"source_integrity.{key} does not match {label}")


def _validate_semantic_unit(unit, index):
    if not isinstance(unit, dict):
        raise InputAnalysisError(f"semantic_units[{index}] must be an object")

    unit_id = unit.get("id")
    if not isinstance(unit_id, str) or not unit_id.strip():
        raise InputAnalysisError(f"semantic_units[{index}].id is required")

    source_channel = unit.get("source_channel")
    if not isinstance(source_channel, str) or not source_channel.strip():
        raise InputAnalysisError(
            f"semantic_units[{index}].source_channel is required"
        )

    unit_type = unit.get("type")
    if unit_type not in SEMANTIC_UNIT_TYPES:
        raise InputAnalysisError(f"semantic_units[{index}].type is invalid")

    visibility = unit.get("visibility")
    if visibility not in VISIBILITIES:
        raise InputAnalysisError(f"semantic_units[{index}].visibility is invalid")

    confidence = unit.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or confidence < 0
        or confidence > 1
    ):
        raise InputAnalysisError(f"semantic_units[{index}].confidence must be 0..1")

    raw_excerpt = unit.get("raw_excerpt")
    if not isinstance(raw_excerpt, str) or not raw_excerpt.strip():
        raise InputAnalysisError(f"semantic_units[{index}].raw_excerpt is required")

    derived_summary = unit.get("derived_summary")
    if not isinstance(derived_summary, str):
        raise InputAnalysisError(
            f"semantic_units[{index}].derived_summary must be a string"
        )

    if not isinstance(unit.get("persist"), bool):
        raise InputAnalysisError(f"semantic_units[{index}].persist must be a bool")


def _validate_narrative_directives(narrative_directives):
    if not isinstance(narrative_directives, dict):
        raise InputAnalysisError("narrative_directives must be an object")

    for key in NARRATIVE_DIRECTIVE_BOOL_KEYS:
        if not isinstance(narrative_directives.get(key), bool):
            raise InputAnalysisError(f"narrative_directives.{key} must be a bool")


def _validate_routing(routing):
    if not isinstance(routing, dict):
        raise InputAnalysisError("routing must be an object")

    for key in ("role_channel", "user_instruction_channel"):
        if not isinstance(routing.get(key), str):
            raise InputAnalysisError(f"routing.{key} must be a string")

    for key in ("gm", "player"):
        if not isinstance(routing.get(key), bool):
            raise InputAnalysisError(f"routing.{key} must be a bool")

    if not isinstance(routing.get("characters"), list):
        raise InputAnalysisError("routing.characters must be a list")


def _validate_routing_requests(routing_requests):
    if routing_requests is None:
        raise InputAnalysisError("routing_requests must be a list")
    if not isinstance(routing_requests, list):
        raise InputAnalysisError("routing_requests must be a list")

    seen_ids = set()
    for index, request in enumerate(routing_requests):
        path = f"routing_requests[{index}]"
        if not isinstance(request, dict):
            raise InputAnalysisError(f"{path} must be an object")

        request_id = _nonblank(request, "id", path)
        if request_id in seen_ids:
            raise InputAnalysisError(f"{path}.id must be unique")
        seen_ids.add(request_id)

        request_type = _nonblank(request, "type", path)
        _nonblank(request, "source_channel", path)
        _nonblank(request, "summary", path)
        _nonblank(request, "target", path)

        if request_type not in ROUTING_REQUEST_TYPES:
            raise InputAnalysisError(f"{path}.type is invalid")

        source_channel = request.get("source_channel").strip()
        if source_channel not in ROUTING_REQUEST_SOURCE_CHANNELS:
            raise InputAnalysisError(f"{path}.source_channel is invalid")

        if not isinstance(request.get("payload"), dict):
            raise InputAnalysisError(f"{path}.payload must be an object")

        requires_authorization = request.get("requires_authorization")
        if not isinstance(requires_authorization, bool):
            raise InputAnalysisError(
                f"{path}.requires_authorization must be a bool"
            )

        authorization_gate = request.get("authorization_gate")
        if authorization_gate not in ROUTING_REQUEST_AUTHORIZATION_GATES:
            raise InputAnalysisError(f"{path}.authorization_gate is invalid")

        _validate_routing_request_evidence(request.get("evidence"), path)

        if request_type == "source_feature_request":
            if (
                not requires_authorization
                or authorization_gate != "allowSourceCodeSelfRepair"
            ):
                raise InputAnalysisError(
                    f"{path}.source_feature_request requires "
                    "allowSourceCodeSelfRepair authorization"
                )
        elif authorization_gate != "none":
            raise InputAnalysisError(f"{path}.authorization_gate must be none")


def _validate_capability_requests(capability_requests):
    if not isinstance(capability_requests, list):
        raise InputAnalysisError("capability_requests must be a list")

    seen_ids = set()
    for index, request in enumerate(capability_requests):
        path = f"capability_requests[{index}]"
        if not isinstance(request, dict):
            raise InputAnalysisError(f"{path} must be an object")

        request_id = request.get("id")
        if not isinstance(request_id, str) or not request_id.strip():
            raise InputAnalysisError(f"{path}.id must be a non-empty string")
        request_id = request_id.strip()
        if request_id in seen_ids:
            raise InputAnalysisError(f"{path}.id must be unique")
        seen_ids.add(request_id)

        try:
            capability_registry.normalize_capability_request(request)
        except capability_registry.CapabilityRegistryError as exc:
            message = str(exc).replace("capability_request", path, 1)
            raise InputAnalysisError(message) from exc


def _validate_routing_request_evidence(evidence, path):
    evidence_path = f"{path}.evidence"
    if not isinstance(evidence, dict):
        raise InputAnalysisError(f"{evidence_path} must be an object")

    raw_excerpt = evidence.get("raw_excerpt")
    if not isinstance(raw_excerpt, str) or not raw_excerpt.strip():
        raise InputAnalysisError(f"{evidence_path}.raw_excerpt is required")

    if "semantic_unit_ids" in evidence:
        semantic_unit_ids = evidence.get("semantic_unit_ids")
        if not isinstance(semantic_unit_ids, list) or not all(
            isinstance(unit_id, str) for unit_id in semantic_unit_ids
        ):
            raise InputAnalysisError(
                f"{evidence_path}.semantic_unit_ids must be a list of strings"
            )


def _explicit_dual_channel_payload(explicit_payload):
    return (
        isinstance(explicit_payload, dict)
        and explicit_payload.get("input_schema") == "dual_channel_v1"
    )


def _validate_routing_grounded(routing, *, raw_text, explicit_payload=None):
    if _explicit_dual_channel_payload(explicit_payload):
        return
    if not isinstance(routing, dict):
        return
    raw = _to_text(raw_text)
    for key in ("role_channel", "user_instruction_channel"):
        text = _to_text(routing.get(key)).strip()
        if text and text not in raw:
            raise InputAnalysisError(f"routing.{key} must be present in raw_text")


def _text(value):
    if value is None:
        return ""
    return str(value)


def _nonblank(record, key, path):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InputAnalysisError(f"{path}.{key} is required")
    return value.strip()


def _validate_status(record, path):
    if "status" not in record:
        raise InputAnalysisError(f"{path}.status is required")
    status_value = record.get("status")
    if not isinstance(status_value, str) or not status_value.strip():
        raise InputAnalysisError(f"{path}.status is required")
    status = status_value.strip()
    if status not in WORLD_UPDATE_SAFE_STATUSES:
        raise InputAnalysisError(f"{path}.status is invalid")


def _validate_fixed_visibility(record, expected, path):
    visibility = record.get("visibility")
    if not isinstance(visibility, str) or visibility.strip() != expected:
        raise InputAnalysisError(f"{path}.visibility must be {expected}")


def _validate_record_object(record, path):
    if not isinstance(record, dict):
        raise InputAnalysisError(f"{path} must be an object")


def _important_character_text(record):
    for key in IMPORTANT_CHARACTER_TEXT_KEYS:
        text = _text(record.get(key)).strip()
        if text:
            return text
    return ""


def _validate_world_update_records(world_updates):
    for index, record in enumerate(world_updates.get("hidden_facts", [])):
        path = f"world_updates.hidden_facts[{index}]"
        _validate_record_object(record, path)
        _nonblank(record, "id", path)
        _nonblank(record, "text", path)
        _validate_fixed_visibility(record, "gm_only", path)
        _validate_status(record, path)

    for index, record in enumerate(world_updates.get("public_facts", [])):
        path = f"world_updates.public_facts[{index}]"
        _validate_record_object(record, path)
        _nonblank(record, "id", path)
        _nonblank(record, "text", path)
        _validate_fixed_visibility(record, "public_world", path)
        _validate_status(record, path)

    for index, record in enumerate(world_updates.get("important_characters", [])):
        path = f"world_updates.important_characters[{index}]"
        _validate_record_object(record, path)
        _nonblank(record, "name", path)
        if not _important_character_text(record):
            raise InputAnalysisError(f"{path}.text is required")
        visibility = record.get("visibility")
        if (
            not isinstance(visibility, str)
            or visibility.strip() not in IMPORTANT_CHARACTER_VISIBILITIES
        ):
            raise InputAnalysisError(f"{path}.visibility is invalid: {_text(visibility).strip()}")
        _validate_status(record, path)
        if record.get("status").strip() != "active":
            raise InputAnalysisError(f"{path}.status must be active")

    for index, record in enumerate(world_updates.get("retcon_requests", [])):
        path = f"world_updates.retcon_requests[{index}]"
        _validate_record_object(record, path)
        _nonblank(record, "id", path)
        _nonblank(record, "text", path)
        if "visibility" in record:
            visibility = record.get("visibility")
            if (
                not isinstance(visibility, str)
                or visibility.strip() not in RETCON_VISIBILITIES
            ):
                raise InputAnalysisError(f"{path}.visibility is invalid")
        _validate_status(record, path)


def _validate_fallback_has_no_high_risk_persistence(
    semantic_units, world_updates
):
    for key in ("hidden_facts", "important_characters", "retcon_requests"):
        if world_updates.get(key):
            raise InputAnalysisError(
                f"fallback analysis cannot persist world_updates.{key}"
            )

    for index, unit in enumerate(semantic_units):
        if unit.get("persist") and unit.get("type") in FALLBACK_HIGH_RISK_TYPES:
            raise InputAnalysisError(
                f"fallback analysis cannot persist semantic_units[{index}]"
            )
