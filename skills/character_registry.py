"""Persistence helpers for important/core character declarations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import agent_run


def _to_text(value: Any) -> str:
    return "" if value is None else str(value)


def _clean_name(value: Any) -> str:
    text = _to_text(value).strip()
    text = text.strip(" \t\r\n\"'“”‘’「」『』《》（）()[]【】")
    return " ".join(text.split())[:80]


def _authoritative_setting(record: Dict[str, Any]) -> str:
    for key in (
        "text",
        "setting_text",
        "authoritative_setting",
        "description",
        "profile",
        "summary",
    ):
        text = _to_text(record.get(key)).strip()
        if text:
            return text
    return _clean_name(record.get("name"))


def _load_card_data(card_folder: Any, card_data: Any = None) -> Dict[str, Any]:
    if isinstance(card_data, dict):
        return card_data
    path = Path(card_folder) / ".card_data.json"
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _write_card_data(card_folder: Any, card_data: Dict[str, Any]) -> None:
    path = Path(card_folder) / ".card_data.json"
    path.write_text(
        json.dumps(card_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _profile_markdown(
    *,
    name: str,
    setting_text: str,
    source_input_id: str,
    round_id: str,
) -> str:
    return "\n".join(
        [
            f"# {name}",
            "",
            "## Authoritative Player Setting",
            "- source: input_analysis",
            f"- source_input_id: {source_input_id}",
            f"- round_id: {round_id}",
            "- importance: major",
            "- visibility: character_private_and_gm",
            "",
            setting_text,
            "",
        ]
    )


def _profile_json(
    *,
    name: str,
    setting_text: str,
    source_input_id: str,
    round_id: str,
    source_unit_id: str,
) -> Dict[str, Any]:
    return {
        "name": name,
        "importance": "major",
        "source": "input_analysis",
        "source_unit_id": source_unit_id,
        "source_input_id": source_input_id,
        "round_id": round_id,
        "visibility": "character_private_and_gm",
        "status": "active",
        "authoritative_setting": setting_text,
    }


def persist_important_characters(
    card_folder: Any,
    card_data: Any,
    records: Any,
    *,
    source_input_id: str = "",
    round_id: str = "",
) -> List[Dict[str, Any]]:
    """Persist input-analysis important/core character records.

    Updates `.card_data.json.character_orchestration.major` and writes each
    character's profile markdown/JSON under `memory/characters/<name>/`.
    """
    if not isinstance(records, list):
        return []

    data = _load_card_data(card_folder, card_data)
    orchestration = data.setdefault("character_orchestration", {})
    if not isinstance(orchestration, dict):
        orchestration = {}
        data["character_orchestration"] = orchestration
    major = orchestration.setdefault("major", [])
    if not isinstance(major, list):
        major = []
        orchestration["major"] = major
    orchestration.setdefault("minor_policy", "main_agent")
    orchestration.setdefault("max_parallel_subagents", 2)

    persisted: List[Dict[str, Any]] = []
    changed_card_data = False
    for record in records:
        if not isinstance(record, dict):
            continue
        name = _clean_name(record.get("name") or record.get("character_name"))
        if not name:
            continue
        setting_text = _authoritative_setting(record)
        if name not in major:
            major.append(name)
            changed_card_data = True

        safe = agent_run.safe_name(name)
        char_dir = Path(card_folder) / "memory" / "characters" / safe
        char_dir.mkdir(parents=True, exist_ok=True)
        source_unit_id = _to_text(record.get("id") or record.get("source_unit_id")).strip()
        (char_dir / "profile.md").write_text(
            _profile_markdown(
                name=name,
                setting_text=setting_text,
                source_input_id=_to_text(source_input_id),
                round_id=_to_text(round_id),
            ),
            encoding="utf-8",
        )
        profile = _profile_json(
            name=name,
            setting_text=setting_text,
            source_input_id=_to_text(source_input_id),
            round_id=_to_text(round_id),
            source_unit_id=source_unit_id,
        )
        (char_dir / "profile.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        persisted.append(
            {
                "name": name,
                "safe_name": safe,
                "profile_md": str((char_dir / "profile.md").resolve()),
                "profile_json": str((char_dir / "profile.json").resolve()),
            }
        )

    if persisted or changed_card_data:
        _write_card_data(card_folder, data)
    return persisted
