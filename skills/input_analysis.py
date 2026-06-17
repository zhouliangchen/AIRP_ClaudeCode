import hashlib
import json
import math
from pathlib import Path


SCHEMA_VERSION = 1
ANALYSIS_MODES = {"ai", "fallback", "fixture"}
SEMANTIC_UNIT_TYPES = {
    "action",
    "dialogue",
    "thought",
    "question",
    "intent",
    "hidden_setting",
    "character_declaration",
    "edit_request",
    "system_command",
    "world_fact",
    "public_fact",
    "user_instruction",
    "ooc_note",
    "other",
}
VISIBILITIES = {
    "player_pov",
    "gm_only",
    "public",
    "character_private",
    "system_only",
}
WORLD_UPDATE_LIST_KEYS = (
    "hidden_facts",
    "public_facts",
    "important_characters",
    "retcon_requests",
)
FALLBACK_HIGH_RISK_TYPES = {
    "hidden_setting",
    "character_declaration",
    "edit_request",
    "system_command",
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


def validate_input_analysis(data, *, raw_text, role_text="", user_instruction_text=""):
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

    _validate_present_hash(
        source_integrity, "raw_text_sha256", raw_text, "raw_text"
    )
    _validate_present_hash(
        source_integrity, "role_text_sha256", role_text, "role_text"
    )
    _validate_present_hash(
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

    if analysis_mode == "fallback":
        _validate_fallback_has_no_high_risk_persistence(
            semantic_units, world_updates
        )

    return data


def analysis_to_routed_input(data, explicit_payload=None):
    routing = data.get("routing") if isinstance(data, dict) else None
    if not isinstance(routing, dict):
        routing = {}

    role_channel = routing.get("role_channel", "")
    user_instruction_channel = routing.get("user_instruction_channel", "")

    if (
        isinstance(explicit_payload, dict)
        and explicit_payload.get("input_schema") == "dual_channel_v1"
    ):
        role_channel = explicit_payload.get("role_text", "")
        user_instruction_channel = explicit_payload.get("user_instruction_text", "")

    characters = routing.get("characters", [])
    if not isinstance(characters, list):
        characters = []

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
                "type": "user_instruction",
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


def _validate_present_hash(source_integrity, key, text, label):
    if key not in source_integrity:
        return
    if source_integrity[key] != sha256_text(text):
        raise InputAnalysisError(f"source_integrity.{key} does not match {label}")


def _validate_semantic_unit(unit, index):
    if not isinstance(unit, dict):
        raise InputAnalysisError(f"semantic_units[{index}] must be an object")

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
