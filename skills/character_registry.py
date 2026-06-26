"""Persistence helpers for important/core character declarations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import actor_memory_store


def _to_text(value: Any) -> str:
    return "" if value is None else str(value)


def _clean_name(value: Any) -> str:
    text = _to_text(value).strip()
    text = text.strip(" \t\r\n\"'“”‘’「」『』《》（）()[]【】")
    return " ".join(text.split())[:80]


def _authoritative_setting(record: Dict[str, Any]) -> str:
    for key in (
        "profile_seed",
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
    source_agent: str,
) -> str:
    if source_agent == "preprocess":
        heading = "Authoritative Player Setting"
        source = "input_analysis"
        player_authoritative = "true"
    else:
        heading = "GM-Originated Promotion Seed"
        source = "character_promotion"
        player_authoritative = "false"
    return "\n".join(
        [
            f"# {name}",
            "",
            f"## {heading}",
            f"- source: {source}",
            f"- source_agent: {source_agent}",
            f"- player_authoritative: {player_authoritative}",
            f"- source_input_id: {source_input_id}",
            f"- round_id: {round_id}",
            "- importance: major",
            "- visibility: character_private_and_gm",
            "",
            setting_text,
            "",
        ]
    )


def _actor_profile_markdown(*, name: str, setting_text: str) -> str:
    lines = [
        f"# {name}",
        "",
        f"我是{name}。",
    ]
    text = _to_text(setting_text).strip()
    if text:
        lines.extend(["", f"我的情况：{text}"])
    lines.append("")
    return "\n".join(lines)


def _profile_source_agent(markdown: str) -> str:
    for line in str(markdown or "").splitlines():
        if line.strip().startswith("- source_agent:"):
            return line.split(":", 1)[1].strip()
        if line.strip().startswith("- source: input_analysis"):
            return "preprocess"
        if line.strip().startswith("- source: character_promotion"):
            return "gm"
        if line.strip().startswith("- source: player"):
            return "player"
    return ""


def _read_nonempty_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text


def _first_existing_profile_text(*paths: Path) -> str:
    for path in paths:
        text = _read_nonempty_text(path)
        if text:
            return text
    return ""


def persist_important_characters(
    card_folder: Any,
    card_data: Any,
    records: Any,
    *,
    source_input_id: str = "",
    round_id: str = "",
    source_agent: str = "preprocess",
) -> List[Dict[str, Any]]:
    """Persist input-analysis important/core character records.

    Updates `.card_data.json.character_orchestration.major` and writes each
    character's objective and actor-facing markdown stores.
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

    source_agent = _to_text(source_agent).strip() or "preprocess"
    if source_agent == "input_analysis":
        source_agent = "preprocess"
    allow_profile_overwrite = source_agent == "preprocess"

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

        paths = actor_memory_store.ensure_actor_files(card_folder, f"character:{name}")
        safe = paths.name
        profile_md_path = paths.objective_profile
        actor_profile_path = paths.profile
        background_md_path = paths.background
        existing_profile = _first_existing_profile_text(
            profile_md_path,
            actor_profile_path,
            background_md_path,
        )

        if existing_profile and not allow_profile_overwrite:
            persisted.append(
                {
                    "name": name,
                    "safe_name": safe,
                    "profile_md": str(profile_md_path.resolve()),
                    "actor_profile_md": str(actor_profile_path.resolve()),
                    "background_md": str(background_md_path.resolve()),
                    "profile_text": existing_profile,
                    "authoritative_setting": "",
                    "profile_preserved": True,
                    "existing_source_agent": _profile_source_agent(existing_profile) or "player",
                }
            )
            continue

        profile_text = _profile_markdown(
            name=name,
            setting_text=setting_text,
            source_input_id=_to_text(source_input_id),
            round_id=_to_text(round_id),
            source_agent=source_agent,
        )
        profile_md_path.write_text(
            profile_text,
            encoding="utf-8",
        )
        actor_profile_path.write_text(
            _actor_profile_markdown(name=name, setting_text=setting_text),
            encoding="utf-8",
        )
        background_md_path.write_text(setting_text.rstrip() + ("\n" if setting_text else ""), encoding="utf-8")
        persisted.append(
            {
                "name": name,
                "safe_name": safe,
                "profile_md": str(profile_md_path.resolve()),
                "actor_profile_md": str(actor_profile_path.resolve()),
                "background_md": str(background_md_path.resolve()),
                "profile_text": profile_text,
                "authoritative_setting": setting_text,
                "source_agent": source_agent,
                "profile_preserved": False,
            }
        )

    if persisted or changed_card_data:
        _write_card_data(card_folder, data)
    return persisted
