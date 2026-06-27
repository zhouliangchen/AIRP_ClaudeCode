#!/usr/bin/env python3
"""Apply the current run's validated input analysis to control-plane state."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict

import agent_packets
import agent_run
import actor_memory_store
import capability_registry
import character_registry
import hidden_settings
import input_analysis


_ANALYSIS_APPLY_ALLOWED_STAGES = {
    "",
    "prepared",
    "prompts_ready",
    "awaiting_agent_outputs",
    "analysis_applied",
}

_PROFILE_SAFE_IMPORTANT_CHARACTER_VISIBILITIES = {
    "character_private_and_gm",
    "public_world",
    "character_pov",
    "specific_characters",
}


def _read_json_required(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"required JSON file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON file: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"required JSON object not found: {path}")
    return data


def _read_card_data(card_folder: Any) -> Dict[str, Any]:
    path = Path(card_folder) / ".card_data.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise input_analysis.InputAnalysisError(
            f".card_data.json must be a valid JSON object: {path}"
        ) from exc
    if not isinstance(data, dict):
        raise input_analysis.InputAnalysisError(
            f".card_data.json must be a valid JSON object: {path}"
        )
    return data


def _read_manifest(run_dir: Path) -> Dict[str, Any]:
    path = run_dir / "manifest.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise input_analysis.InputAnalysisError(
            f"manifest.json must be a valid JSON object before applying input analysis: {path}"
        ) from exc
    if not isinstance(data, dict):
        raise input_analysis.InputAnalysisError(
            f"manifest.json must be a valid JSON object before applying input analysis: {path}"
        )
    return data


def _assert_manifest_stage_allows_apply(run_dir: Path) -> Dict[str, Any]:
    manifest = _read_manifest(run_dir)
    stage = manifest.get("stage")
    stage_text = "" if stage is None else str(stage)
    if stage_text not in _ANALYSIS_APPLY_ALLOWED_STAGES:
        raise input_analysis.InputAnalysisError(
            f"cannot apply input analysis after manifest stage: {stage_text}"
        )
    return manifest


def _validate_important_characters_for_profile(world_updates: Any) -> None:
    if not isinstance(world_updates, dict):
        return
    important_records = world_updates.get("important_characters", [])
    if not isinstance(important_records, list):
        return

    for index, record in enumerate(important_records):
        if not isinstance(record, dict):
            raise input_analysis.InputAnalysisError(
                f"world_updates.important_characters[{index}] must be an object"
            )

        name = str(record.get("name") or record.get("character_name") or "").strip()
        if not name:
            raise input_analysis.InputAnalysisError(
                f"world_updates.important_characters[{index}].name is required"
            )

        visibility = str(record.get("visibility") or "").strip()
        if visibility not in _PROFILE_SAFE_IMPORTANT_CHARACTER_VISIBILITIES:
            raise input_analysis.InputAnalysisError(
                "world_updates.important_characters"
                f"[{index}].visibility cannot be written to a character profile: {visibility}"
            )


def _important_character_profile_text(record: Dict[str, Any]) -> str:
    for key in (
        "authoritative_setting",
        "setting_text",
        "description",
        "profile",
        "summary",
        "text",
    ):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (dict, list)) and value:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return ""


_ACTOR_UNAWARE_PROFILE_PATTERNS = (
    re.compile(r"(?:本人|角色本人|当事人|角色|玩家|主角).{0,16}(?:不知|不清楚|不知道|未察觉|尚未察觉|尚不知)"),
    re.compile(r"(?:不知情|尚不知情|并不知情|毫不知情|尚未知情)"),
    re.compile(r"\b(?:unknown|not known|unaware|does not know|do not know)\b", re.IGNORECASE),
)


def _profile_declares_actor_unaware(text: str) -> bool:
    value = str(text or "").strip()
    return bool(value) and any(pattern.search(value) for pattern in _ACTOR_UNAWARE_PROFILE_PATTERNS)


def _filter_actor_unaware_important_characters(world_updates: Any) -> tuple[Dict[str, Any], list[str]]:
    if not isinstance(world_updates, dict):
        return {}, []
    important_records = world_updates.get("important_characters", [])
    if not isinstance(important_records, list):
        return dict(world_updates), []

    kept = []
    skipped = []
    for record in important_records:
        if not isinstance(record, dict):
            kept.append(record)
            continue
        visibility = str(record.get("visibility") or "").strip()
        profile_text = _important_character_profile_text(record)
        if visibility == "character_private_and_gm" and _profile_declares_actor_unaware(profile_text):
            name = str(record.get("name") or record.get("character_name") or "").strip()
            if name:
                skipped.append(name)
            continue
        kept.append(record)

    if len(kept) == len(important_records):
        return dict(world_updates), []
    filtered = dict(world_updates)
    filtered["important_characters"] = kept
    return filtered, skipped


def _card_looks_blank_bootstrap(card_data: Dict[str, Any]) -> bool:
    if not isinstance(card_data, dict):
        return False
    data = card_data.get("data") if isinstance(card_data.get("data"), dict) else {}
    name = str(card_data.get("name") or data.get("name") or "").strip()
    return (
        str(card_data.get("mode") or "").strip() == "blank_bootstrap"
        or str(card_data.get("source_type") or "").strip() == "blank"
        or name in {"", "未命名角色", "player"}
    )


def _player_mapping_is_unset_or_default(card_folder: Any) -> bool:
    path = Path(card_folder) / "characters" / actor_memory_store.PLAYER_MAPPING_FILE
    if not path.exists():
        return True
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip().casefold()] = value.strip()
    return values.get("name") in {"", "player"} and values.get("path") in {
        "",
        "characters/player",
    }


def _analysis_declares_player_character(analysis: Dict[str, Any]) -> bool:
    for unit in analysis.get("semantic_units", []) if isinstance(analysis, dict) else []:
        if not isinstance(unit, dict):
            continue
        if (
            str(unit.get("type") or "").strip() == "character_declaration"
            and str(unit.get("source_channel") or "").strip() == "role_input"
        ):
            return True
    return False


def _routing_requests_player(routing: Any) -> bool:
    return isinstance(routing, dict) and routing.get("player") is True


def _maybe_write_initial_player_mapping(
    card_folder: Any,
    card_data: Dict[str, Any],
    analysis: Dict[str, Any],
    character_records: list[Dict[str, Any]],
) -> dict[str, str]:
    if (
        not _card_looks_blank_bootstrap(card_data)
        or not _player_mapping_is_unset_or_default(card_folder)
    ):
        return {}
    has_player_declaration = _analysis_declares_player_character(analysis)
    has_player_routing = _routing_requests_player(analysis.get("routing"))
    if not has_player_declaration and not has_player_routing:
        return {}
    usable_records = [
        record
        for record in character_records
        if isinstance(record, dict)
        and str(record.get("name") or "").strip()
    ]
    names = {str(record.get("name") or "").strip() for record in usable_records}
    if len(names) != 1:
        return {}
    record = usable_records[0]
    name = str(record.get("name") or "").strip()
    safe_name = str(record.get("safe_name") or name).strip()
    if not name or not safe_name:
        return {}
    actor_memory_store.ensure_actor_files(card_folder, "player")
    return actor_memory_store.migrate_default_player_memory(
        card_folder,
        name,
        f"characters/{safe_name}",
    )


def _source_input_id(raw_request: Dict[str, Any]) -> str:
    explicit_payload = raw_request.get("explicit_payload")
    if isinstance(explicit_payload, dict):
        value = explicit_payload.get("id")
        if value:
            return str(value)
    value = raw_request.get("source_input_id") or raw_request.get("id")
    return "" if value is None else str(value)


def _clean_routed_character_names(routed_input: Dict[str, Any]) -> list[str]:
    names = []
    for value in routed_input.get("characters", []) if isinstance(routed_input, dict) else []:
        if not isinstance(value, str):
            continue
        name = value.strip()
        if name and name not in names:
            names.append(name)
    return names


def _semantic_unit_source_channel(unit: Dict[str, Any], raw_request: Dict[str, Any]) -> str:
    existing = unit.get("source_channel")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()

    role_text = str(raw_request.get("role_text") or "")
    instruction_text = str(raw_request.get("user_instruction_text") or "")
    unit_type = str(unit.get("type") or "")
    visibility = str(unit.get("visibility") or "")
    instruction_first_types = {
        "character_declaration",
        "hidden_setting",
        "omniscient_setting",
        "style_guidance",
        "system_command",
    }
    if (visibility == "gm_only" or unit_type in instruction_first_types) and instruction_text:
        return "user_instruction"
    if role_text:
        return "role_input"
    if instruction_text:
        return "user_instruction"
    return "raw_input"


def _semantic_unit_raw_excerpt(source_channel: str, raw_request: Dict[str, Any]) -> str:
    if source_channel == "role_input":
        text = str(raw_request.get("role_text") or "")
        if text.strip():
            return text
    if source_channel == "user_instruction":
        text = str(raw_request.get("user_instruction_text") or "")
        if text.strip():
            return text
    return str(raw_request.get("raw_text") or "")


def _contains_text(haystack: str, needle: str) -> bool:
    return bool(needle.strip()) and needle.strip() in haystack


def _semantic_unit_text(unit: Dict[str, Any]) -> str:
    for key in ("text", "raw_excerpt", "derived_summary", "content"):
        value = unit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _semantic_units_claim_instruction_as_style(
    semantic_units: Any,
    raw_request: Dict[str, Any],
) -> bool:
    instruction_text = str(raw_request.get("user_instruction_text") or "").strip()
    if not instruction_text or not isinstance(semantic_units, list):
        return False
    for unit in semantic_units:
        if not isinstance(unit, dict):
            continue
        if str(unit.get("type") or "").strip() != "style_guidance":
            continue
        evidence = str(unit.get("raw_excerpt") or unit.get("text") or "").strip()
        if evidence == instruction_text:
            return True
    return False


def _normalize_semantic_unit_provenance(
    unit: Dict[str, Any],
    raw_request: Dict[str, Any],
    *,
    instruction_claimed_as_style: bool,
) -> tuple[Dict[str, Any], bool]:
    normalized = dict(unit)
    role_text = str(raw_request.get("role_text") or "")
    instruction_text = str(raw_request.get("user_instruction_text") or "")
    source_channel = str(normalized.get("source_channel") or "").strip()
    raw_excerpt = str(normalized.get("raw_excerpt") or "").strip()
    unit_text = _semantic_unit_text(normalized)

    excerpt_in_role = _contains_text(role_text, raw_excerpt)
    excerpt_in_instruction = _contains_text(instruction_text, raw_excerpt)
    text_in_role = _contains_text(role_text, unit_text)
    text_in_instruction = _contains_text(instruction_text, unit_text)

    target_channel = ""
    target_excerpt = ""
    if (
        instruction_claimed_as_style
        and source_channel == "user_instruction"
        and raw_excerpt
        and raw_excerpt == instruction_text.strip()
        and str(normalized.get("type") or "").strip() != "style_guidance"
        and role_text.strip()
        and not text_in_instruction
    ):
        target_channel = "role_input"
        target_excerpt = unit_text if text_in_role else role_text
    elif excerpt_in_role and not excerpt_in_instruction:
        target_channel = "role_input"
        target_excerpt = raw_excerpt
    elif excerpt_in_instruction and not excerpt_in_role:
        target_channel = "user_instruction"
        target_excerpt = raw_excerpt
    elif text_in_role and not text_in_instruction:
        target_channel = "role_input"
        target_excerpt = unit_text
    elif text_in_instruction and not text_in_role:
        target_channel = "user_instruction"
        target_excerpt = unit_text
    if not target_channel:
        return normalized, False
    if (
        normalized.get("source_channel") == target_channel
        and normalized.get("raw_excerpt") == target_excerpt
    ):
        return normalized, False
    normalized["source_channel"] = target_channel
    normalized["raw_excerpt"] = target_excerpt
    return normalized, True


def _normalize_legacy_semantic_units(
    analysis: Dict[str, Any], raw_request: Dict[str, Any]
) -> tuple[Dict[str, Any], bool]:
    semantic_units = analysis.get("semantic_units")
    if not isinstance(semantic_units, list):
        return analysis, False

    changed = False
    normalized_units = []
    used_ids = {
        str(unit.get("id")).strip()
        for unit in semantic_units
        if isinstance(unit, dict) and str(unit.get("id") or "").strip()
    }
    instruction_claimed_as_style = _semantic_units_claim_instruction_as_style(
        semantic_units,
        raw_request,
    )
    next_index = 1
    for unit in semantic_units:
        if not isinstance(unit, dict):
            normalized_units.append(unit)
            continue

        normalized = dict(unit)
        if not isinstance(normalized.get("id"), str) or not normalized["id"].strip():
            while True:
                candidate = f"unit-{next_index:03d}"
                next_index += 1
                if candidate not in used_ids:
                    break
            normalized["id"] = candidate
            used_ids.add(candidate)
            changed = True

        if not isinstance(normalized.get("source_channel"), str) or not normalized["source_channel"].strip():
            normalized["source_channel"] = _semantic_unit_source_channel(
                normalized,
                raw_request,
            )
            changed = True

        if not isinstance(normalized.get("raw_excerpt"), str) or not normalized["raw_excerpt"].strip():
            normalized["raw_excerpt"] = _semantic_unit_raw_excerpt(
                normalized["source_channel"],
                raw_request,
            )
            changed = True

        normalized, provenance_changed = _normalize_semantic_unit_provenance(
            normalized,
            raw_request,
            instruction_claimed_as_style=instruction_claimed_as_style,
        )
        changed = changed or provenance_changed

        if not isinstance(normalized.get("derived_summary"), str):
            content = normalized.get("content")
            normalized["derived_summary"] = (
                str(content) if content not in (None, "") else normalized["raw_excerpt"]
            )
            changed = True

        confidence = normalized.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
        ):
            normalized["confidence"] = 0.5
            changed = True

        if not isinstance(normalized.get("persist"), bool):
            normalized["persist"] = False
            changed = True

        normalized_units.append(normalized)

    if not changed:
        return analysis, False
    normalized_analysis = dict(analysis)
    normalized_analysis["semantic_units"] = normalized_units
    return normalized_analysis, True


def _normalize_legacy_routing_requests(analysis: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    normalized = dict(analysis)
    changed = False

    if "routing_requests" not in normalized:
        normalized["routing_requests"] = []
        changed = True

    if "capability_requests" not in normalized:
        routing_requests = normalized.get("routing_requests")
        capability_requests = []
        if isinstance(routing_requests, list):
            for index, request in enumerate(routing_requests):
                try:
                    capability_requests.append(
                        capability_registry.legacy_routing_request_to_capability(request)
                    )
                except capability_registry.CapabilityRegistryError as exc:
                    message = str(exc).replace(
                        "routing_request",
                        f"routing_requests[{index}]",
                        1,
                    )
                    raise input_analysis.InputAnalysisError(message) from exc
        normalized["capability_requests"] = capability_requests
        changed = True

    if not changed:
        return analysis, False
    return normalized, True


def _normalize_routing_channel_aliases(analysis: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    routing = analysis.get("routing")
    if not isinstance(routing, dict):
        return analysis, False
    if isinstance(routing.get("user_instruction_channel"), str):
        return analysis, False
    alias = routing.get("user_instruction_text")
    if not isinstance(alias, str):
        return analysis, False

    normalized_routing = dict(routing)
    normalized_routing["user_instruction_channel"] = alias
    normalized_routing.pop("user_instruction_text", None)
    normalized = dict(analysis)
    normalized["routing"] = normalized_routing
    return normalized, True


def _known_card_character_names(card_data: Dict[str, Any]) -> set[str]:
    names: set[str] = set()
    if not isinstance(card_data, dict):
        return names
    orchestration = card_data.get("character_orchestration")
    if isinstance(orchestration, dict):
        for name in orchestration.get("major", []) or []:
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
    for container in (card_data.get("characters"), (card_data.get("data") or {}).get("characters")):
        if isinstance(container, dict):
            for name in container.keys():
                if isinstance(name, str) and name.strip():
                    names.add(name.strip())
        elif isinstance(container, list):
            for item in container:
                if isinstance(item, str) and item.strip():
                    names.add(item.strip())
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("character_name")
                    if isinstance(name, str) and name.strip():
                        names.add(name.strip())
    return names


def _character_memory_exists(card_folder: Any, name: str) -> bool:
    paths = actor_memory_store.actor_paths(card_folder, f"character:{name}")
    for path in (
        paths.objective_profile,
        paths.background,
        paths.profile,
    ):
        try:
            if path.exists() and path.read_text(encoding="utf-8").strip():
                return True
        except OSError:
            continue
    return False


def _with_existing_routed_characters(card_folder: Any, card_data: Dict[str, Any], routed_input: Dict[str, Any]) -> Dict[str, Any]:
    routed_names = _clean_routed_character_names(routed_input)
    if not routed_names:
        return card_data

    known_names = _known_card_character_names(card_data)
    selected = [
        name
        for name in routed_names
        if name in known_names or _character_memory_exists(card_folder, name)
    ]
    if not selected:
        return card_data

    data = dict(card_data)
    original_orchestration = card_data.get("character_orchestration")
    orchestration = dict(original_orchestration) if isinstance(original_orchestration, dict) else {}
    major = [
        name
        for name in orchestration.get("major", []) or []
        if isinstance(name, str) and name.strip()
    ]
    for name in selected:
        if name not in major:
            major.append(name)
    orchestration["major"] = major
    data["character_orchestration"] = orchestration
    return data


def _load_existing_run_routed_character_contexts(run_dir: Path, routed_input: Dict[str, Any]) -> list[Dict[str, Any]]:
    contexts: list[Dict[str, Any]] = []
    for name in _clean_routed_character_names(routed_input):
        path = run_dir / "characters" / f"{agent_run.safe_name(name)}.context.json"
        try:
            packet = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(packet, dict):
            continue
        context = _character_context_from_packet(packet, name)
        context_name = context.get("name")
        if not isinstance(context_name, str) or not context_name.strip():
            continue
        context["name"] = context_name.strip()
        contexts.append(context)
    return contexts


def _projected_packet_name(packet: Dict[str, Any], fallback_name: str) -> str:
    self_knowledge = packet.get("self_knowledge")
    if isinstance(self_knowledge, dict):
        name = self_knowledge.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    name = packet.get("character_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    actor_id = packet.get("actor_id")
    if isinstance(actor_id, str) and actor_id.startswith("character:"):
        name = actor_id.split(":", 1)[1].strip()
        if name:
            return name
    return fallback_name


def _character_context_from_packet(packet: Dict[str, Any], fallback_name: str) -> Dict[str, Any]:
    character = packet.get("character")
    context = dict(character) if isinstance(character, dict) else {}

    self_knowledge = packet.get("self_knowledge")
    if isinstance(self_knowledge, dict):
        for source_key, target_key in (
            ("identity", "identity"),
            ("role", "role"),
            ("body_state", "body_state"),
            ("relationships", "relationships"),
        ):
            value = self_knowledge.get(source_key)
            if value not in (None, "", {}, []):
                context.setdefault(target_key, value)

    memory = packet.get("memory")
    if isinstance(memory, dict):
        context.setdefault("memory", memory)

    for key in ("sensory_context", "misconceptions"):
        value = packet.get(key)
        if value not in (None, "", {}, []):
            context.setdefault(key, value)

    context["name"] = context.get("name") or _projected_packet_name(packet, fallback_name)
    return context


def _merge_character_contexts(character_contexts: Dict[str, Any], extra_contexts: list[Dict[str, Any]]) -> Dict[str, Any]:
    if not extra_contexts:
        return character_contexts
    merged = dict(character_contexts) if isinstance(character_contexts, dict) else {}
    characters = list(merged.get("characters") or [])
    existing_names = {
        item.get("name")
        for item in characters
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    for context in extra_contexts:
        name = context.get("name")
        if isinstance(name, str) and name not in existing_names:
            characters.append(context)
            existing_names.add(name)
    merged["characters"] = characters
    return merged


def apply_current_run(card_folder, root_dir=None):
    """Validate and apply `input_analysis.output.json` for the current run."""
    run_dir = agent_run.current_run_dir(card_folder)
    if run_dir is None:
        raise FileNotFoundError(f"no current agent run for card folder: {card_folder}")

    run_dir = Path(run_dir)
    _assert_manifest_stage_allows_apply(run_dir)
    raw_request = _read_json_required(run_dir / "input.raw.json")
    analysis = input_analysis.load_json(run_dir / "input_analysis.output.json")
    analysis, normalized = _normalize_legacy_semantic_units(analysis, raw_request)
    analysis, normalized_channel_aliases = _normalize_routing_channel_aliases(analysis)
    analysis, normalized_routing_requests = _normalize_legacy_routing_requests(analysis)
    normalized = normalized or normalized_channel_aliases or normalized_routing_requests
    input_analysis.validate_input_analysis(
        analysis,
        raw_text=str(raw_request.get("raw_text") or ""),
        role_text=str(raw_request.get("role_text") or ""),
        user_instruction_text=str(raw_request.get("user_instruction_text") or ""),
        explicit_payload=raw_request.get("explicit_payload"),
    )
    if normalized:
        (run_dir / "input_analysis.output.json").write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    routed_input = input_analysis.analysis_to_routed_input(
        analysis,
        explicit_payload=raw_request.get("explicit_payload"),
    )
    existing_run_routed_contexts = _load_existing_run_routed_character_contexts(
        run_dir,
        routed_input,
    )

    source_input_id = _source_input_id(raw_request)
    world_updates = analysis.get("world_updates", {})
    world_updates, skipped_important_characters = _filter_actor_unaware_important_characters(world_updates)
    if skipped_important_characters:
        analysis = dict(analysis)
        analysis["world_updates"] = world_updates
        (run_dir / "input_analysis.output.json").write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    _validate_important_characters_for_profile(world_updates)
    card_data = _read_card_data(card_folder)

    hidden_records = []
    for record in world_updates.get("hidden_facts", []) if isinstance(world_updates, dict) else []:
        persisted = hidden_settings.persist_hidden_setting_record(
            card_folder,
            record,
            source_input_id=source_input_id,
            round_id=run_dir.name,
        )
        if persisted:
            hidden_records.append(persisted)

    important_records = (
        world_updates.get("important_characters", [])
        if isinstance(world_updates, dict)
        else []
    )
    character_records = character_registry.persist_important_characters(
        card_folder,
        card_data,
        important_records,
        source_input_id=source_input_id,
        round_id=run_dir.name,
        source_agent="preprocess",
    )
    _maybe_write_initial_player_mapping(card_folder, card_data, analysis, character_records)

    previous_input = agent_run.read_json(run_dir / "input.json", {}) or {}
    chat_log = previous_input.get("recent_chat", [])
    if not isinstance(chat_log, list):
        chat_log = []
    hidden_setting_records = hidden_settings.load_hidden_settings(card_folder)
    context_card_data = _with_existing_routed_characters(
        card_folder,
        card_data,
        routed_input,
    )
    character_contexts = agent_packets.build_character_contexts_from_card(
        card_folder,
        context_card_data,
        chat_log,
        raw_request.get("raw_text", ""),
    )
    character_contexts = _merge_character_contexts(
        character_contexts,
        existing_run_routed_contexts,
    )
    rebuilt = agent_packets.rebuild_agent_run_from_analysis(
        card_folder,
        run_dir,
        analysis,
        routed_input,
        raw_request,
        chat_log=chat_log,
        card_data=card_data,
        character_contexts=character_contexts,
        hidden_setting_records=hidden_setting_records,
    )
    manifest = rebuilt.get("manifest", {})
    return {
        "ok": True,
        "stage": manifest.get("stage", "analysis_applied"),
        "run_dir": str(run_dir.resolve()),
        "root_dir": str(Path(root_dir).resolve()) if root_dir else "",
        "hidden_facts_persisted": len(hidden_records),
        "important_characters_persisted": [
            item.get("name") for item in character_records if isinstance(item, dict)
        ],
        "important_characters_skipped": skipped_important_characters,
        "routed_input": routed_input,
        "routing_requests": analysis.get("routing_requests", []),
        "capability_requests": analysis.get("capability_requests", []),
        "manifest": manifest,
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("Usage: python input_analysis_apply.py <card_folder> [ROOT]", file=sys.stderr)
        return 2
    result = apply_current_run(argv[0], root_dir=argv[1] if len(argv) > 1 else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
