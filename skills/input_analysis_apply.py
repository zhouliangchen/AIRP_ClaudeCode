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
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _source_input_id(raw_request: Dict[str, Any]) -> str:
    explicit_payload = raw_request.get("explicit_payload")
    if isinstance(explicit_payload, dict):
        value = explicit_payload.get("id")
        if value:
            return str(value)
    value = raw_request.get("source_input_id") or raw_request.get("id")
    return "" if value is None else str(value)


def apply_current_run(card_folder, root_dir=None):
    """Validate and apply `input_analysis.output.json` for the current run."""
    run_dir = agent_run.current_run_dir(card_folder)
    if run_dir is None:
        raise FileNotFoundError(f"no current agent run for card folder: {card_folder}")

    run_dir = Path(run_dir)
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

    source_input_id = _source_input_id(raw_request)
    world_updates = analysis.get("world_updates", {})
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

    card_data = _read_card_data(card_folder)
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
    card_data = _read_card_data(card_folder)

    previous_input = agent_run.read_json(run_dir / "input.json", {}) or {}
    chat_log = previous_input.get("recent_chat", [])
    if not isinstance(chat_log, list):
        chat_log = []
    hidden_setting_records = hidden_settings.load_hidden_settings(card_folder)
    character_contexts = agent_packets.build_character_contexts_from_card(
        card_folder,
        card_data,
        chat_log,
        raw_request.get("raw_text", ""),
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
