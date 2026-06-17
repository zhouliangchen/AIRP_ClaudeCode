#!/usr/bin/env python3
"""Apply the current run's validated input analysis to control-plane state."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

import agent_packets
import agent_run
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
    char_dir = Path(card_folder) / "memory" / "characters" / agent_run.safe_name(name)
    if not char_dir.exists() or not char_dir.is_dir():
        return False
    for filename in ("profile.md", "profile.json", "recent.md", "goals.md", "state.json"):
        if (char_dir / filename).exists():
            return True
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
        character = packet.get("character")
        if isinstance(character, dict):
            context = dict(character)
        else:
            context = {}
        context_name = context.get("name") or packet.get("character_name") or name
        if not isinstance(context_name, str) or not context_name.strip():
            continue
        context["name"] = context_name.strip()
        contexts.append(context)
    return contexts


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
    input_analysis.validate_input_analysis(
        analysis,
        raw_text=str(raw_request.get("raw_text") or ""),
        role_text=str(raw_request.get("role_text") or ""),
        user_instruction_text=str(raw_request.get("user_instruction_text") or ""),
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
    )

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
        "routed_input": routed_input,
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
