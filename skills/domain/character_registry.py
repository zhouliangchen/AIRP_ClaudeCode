"""Persistence helpers for important/core character declarations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from agents.actor import memory_store as actor_memory_store

ALLOWED_CHARACTER_MEMORY_POLICIES = {
    "shared",
    "independent_persistent",
    "temporary_proxy",
}


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


def _clean_metadata_text(value: Any, *, limit: int = 240) -> str:
    text = _to_text(value).strip()
    return " ".join(text.split())[:limit]


def _require_metadata_object(value: Any, path: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _require_metadata_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be a list")
    return value


def _normalize_memory_policy(value: Any, path: str, *, default: str = "shared") -> str:
    policy = _clean_metadata_text(value or default, limit=80)
    if policy not in ALLOWED_CHARACTER_MEMORY_POLICIES:
        raise ValueError(f"{path} must be one of {sorted(ALLOWED_CHARACTER_MEMORY_POLICIES)}")
    return policy


def _merge_unique_texts(existing: Any, incoming: list[str], *, canonical_name: str = "") -> list[str]:
    values: list[str] = []
    seen = set()
    canonical_folded = canonical_name.casefold()
    for item in existing if isinstance(existing, list) else []:
        text = _clean_metadata_text(item, limit=80)
        if not text or text.casefold() == canonical_folded or text.casefold() in seen:
            continue
        values.append(text)
        seen.add(text.casefold())
    for item in incoming:
        text = _clean_metadata_text(item, limit=80)
        if not text or text.casefold() == canonical_folded or text.casefold() in seen:
            continue
        values.append(text)
        seen.add(text.casefold())
    return values


def _normalize_aliases(record: Dict[str, Any], path: str, canonical_name: str) -> list[str]:
    if "aliases" not in record:
        return []
    raw_aliases = _require_metadata_list(record.get("aliases"), f"{path}.aliases")
    aliases: list[str] = []
    seen = set()
    canonical_folded = canonical_name.casefold()
    for index, item in enumerate(raw_aliases):
        if not isinstance(item, str):
            raise ValueError(f"{path}.aliases[{index}] must be a string")
        alias = _clean_metadata_text(item, limit=80)
        folded = alias.casefold()
        if not alias or folded == canonical_folded or folded in seen:
            continue
        aliases.append(alias)
        seen.add(folded)
    return aliases


def _normalize_form(record: Any, path: str) -> Dict[str, Any]:
    item = _require_metadata_object(record, path)
    form_name = _clean_metadata_text(
        item.get("form_name") or item.get("name") or item.get("display_name"),
        limit=80,
    )
    appearance_state = _clean_metadata_text(item.get("appearance_state"), limit=80)
    if not form_name and not appearance_state:
        raise ValueError(f"{path}.form_name or {path}.appearance_state is required")
    normalized = {
        "form_name": form_name or appearance_state,
        "appearance_state": appearance_state,
        "memory_policy": _normalize_memory_policy(
            item.get("memory_policy"),
            f"{path}.memory_policy",
            default="shared",
        ),
    }
    description = _clean_metadata_text(item.get("description") or item.get("summary"), limit=400)
    if description:
        normalized["description"] = description
    related = _clean_metadata_text(
        item.get("related_character") or item.get("independent_character_name"),
        limit=80,
    )
    if related:
        normalized["related_character"] = related
    return normalized


def _normalize_related_character(record: Any, path: str) -> Dict[str, Any]:
    item = _require_metadata_object(record, path)
    name = _clean_name(item.get("name") or item.get("character_name"))
    if not name:
        raise ValueError(f"{path}.name is required")
    normalized = {
        "name": name,
        "memory_policy": _normalize_memory_policy(
            item.get("memory_policy"),
            f"{path}.memory_policy",
            default="independent_persistent",
        ),
    }
    relation = _clean_metadata_text(item.get("relation") or item.get("description"), limit=400)
    if relation:
        normalized["relation"] = relation
    return normalized


def normalize_character_metadata(record: Dict[str, Any], *, path: str = "record") -> Dict[str, Any]:
    """Validate and normalize optional important-character identity metadata."""
    if not isinstance(record, dict):
        raise ValueError(f"{path} must be an object")
    canonical_name = _clean_name(record.get("name") or record.get("character_name"))
    metadata: Dict[str, Any] = {}

    aliases = _normalize_aliases(record, path, canonical_name)
    if aliases:
        metadata["aliases"] = aliases

    if "forms" in record:
        forms = [
            _normalize_form(item, f"{path}.forms[{index}]")
            for index, item in enumerate(_require_metadata_list(record.get("forms"), f"{path}.forms"))
        ]
        if forms:
            metadata["forms"] = _dedupe_metadata_records(
                forms,
                key_fields=("form_name", "appearance_state", "memory_policy"),
            )

    if "related_characters" in record:
        related = [
            _normalize_related_character(item, f"{path}.related_characters[{index}]")
            for index, item in enumerate(
                _require_metadata_list(record.get("related_characters"), f"{path}.related_characters")
            )
        ]
        if related:
            metadata["related_characters"] = _dedupe_metadata_records(
                related,
                key_fields=("name", "memory_policy"),
            )

    return metadata


def _dedupe_metadata_records(records: list[Dict[str, Any]], *, key_fields: tuple[str, ...]) -> list[Dict[str, Any]]:
    merged: list[Dict[str, Any]] = []
    indexes: dict[tuple[str, ...], int] = {}
    for record in records:
        key = tuple(_clean_metadata_text(record.get(field), limit=120).casefold() for field in key_fields)
        if key in indexes:
            merged[indexes[key]].update(record)
            continue
        indexes[key] = len(merged)
        merged.append(dict(record))
    return merged


def _merge_metadata_records(
    existing: Any,
    incoming: list[Dict[str, Any]],
    *,
    key_fields: tuple[str, ...],
) -> list[Dict[str, Any]]:
    existing_records = [dict(item) for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
    return _dedupe_metadata_records(existing_records + incoming, key_fields=key_fields)


def _merge_character_registry_metadata(
    orchestration: Dict[str, Any],
    name: str,
    record: Dict[str, Any],
) -> Dict[str, Any]:
    registry = orchestration.setdefault("registry", {})
    if not isinstance(registry, dict):
        registry = {}
        orchestration["registry"] = registry

    existing = registry.get(name)
    if not isinstance(existing, dict):
        existing = {}
    entry: Dict[str, Any] = dict(existing)
    entry["canonical_name"] = name

    metadata = normalize_character_metadata(record, path=f"character_orchestration.registry.{name}")
    entry["aliases"] = _merge_unique_texts(
        entry.get("aliases"),
        metadata.get("aliases", []),
        canonical_name=name,
    )
    entry["forms"] = _merge_metadata_records(
        entry.get("forms"),
        metadata.get("forms", []),
        key_fields=("form_name", "appearance_state", "memory_policy"),
    )
    entry["related_characters"] = _merge_metadata_records(
        entry.get("related_characters"),
        metadata.get("related_characters", []),
        key_fields=("name", "memory_policy"),
    )
    registry[name] = entry
    return entry


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


def rename_registered_character(card_folder: Any, from_name: Any, to_name: Any) -> Dict[str, Any]:
    data = _load_card_data(card_folder)
    orchestration = data.get("character_orchestration")
    if not isinstance(orchestration, dict):
        return {"updated": False, "major": []}
    major = orchestration.get("major")
    if not isinstance(major, list):
        return {"updated": False, "major": []}
    old_name = _clean_name(from_name)
    new_name = _clean_name(to_name)
    if not old_name or not new_name:
        return {"updated": False, "major": major}

    changed = False
    renamed: List[Any] = []
    for item in major:
        if isinstance(item, str) and item == old_name:
            if new_name not in renamed:
                renamed.append(new_name)
            changed = True
        elif item not in renamed:
            renamed.append(item)
    if changed:
        orchestration["major"] = renamed
    registry = orchestration.get("registry")
    if isinstance(registry, dict) and old_name in registry:
        old_entry = registry.pop(old_name)
        if not isinstance(old_entry, dict):
            old_entry = {}
        new_entry = registry.get(new_name)
        if not isinstance(new_entry, dict):
            new_entry = {}
        merged = dict(new_entry)
        merged.update(old_entry)
        merged["canonical_name"] = new_name
        merged["aliases"] = _merge_unique_texts(
            merged.get("aliases"),
            [old_name],
            canonical_name=new_name,
        )
        registry[new_name] = merged
        changed = True
    if changed:
        _write_card_data(card_folder, data)
    return {"updated": changed, "major": renamed if changed else major}


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
        metadata_entry = _merge_character_registry_metadata(orchestration, name, record)
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
                    "aliases": metadata_entry.get("aliases", []),
                    "forms": metadata_entry.get("forms", []),
                    "related_characters": metadata_entry.get("related_characters", []),
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
                "aliases": metadata_entry.get("aliases", []),
                "forms": metadata_entry.get("forms", []),
                "related_characters": metadata_entry.get("related_characters", []),
            }
        )

    if persisted or changed_card_data:
        _write_card_data(card_folder, data)
    return persisted
