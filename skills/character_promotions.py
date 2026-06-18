"""Validated important-character promotion helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import character_registry


class CharacterPromotionError(RuntimeError):
    """Raised when a character promotion record is unsafe or malformed."""


ALLOWED_SOURCE_AGENTS = {"preprocess", "gm"}
ALLOWED_VISIBILITIES = {"character_private_and_gm"}
ALLOWED_ACTIVATIONS = {"current_turn", "future_turn"}
SUBGM_PROMOTION_ERROR = (
    "subGM sources cannot be applied as character_promotions; "
    "subGM agents may only emit promotion_request records for the main GM to consider"
)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _require_text(payload: Dict[str, Any], key: str, path: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CharacterPromotionError(f"{path}.{key} is required")
    return value.strip()


def validate_promotion(record: Any, path: str) -> dict:
    """Validate and normalize one important-character promotion record."""
    if not isinstance(record, dict):
        raise CharacterPromotionError(f"{path} must be an object")

    source_agent = _text(record.get("source_agent")).strip()
    if not source_agent:
        raise CharacterPromotionError(f"{path}.source_agent is required")
    if source_agent.startswith("subGM") or source_agent.startswith("gm_assistant"):
        raise CharacterPromotionError(
            f"{path}.source_agent {source_agent!r} is not allowed: {SUBGM_PROMOTION_ERROR}"
        )
    if source_agent not in ALLOWED_SOURCE_AGENTS:
        raise CharacterPromotionError(f"{path}.source_agent is not allowed: {source_agent}")

    name = _require_text(record, "name", path)
    reason = _require_text(record, "reason", path)
    profile_seed = _require_text(record, "profile_seed", path)
    visibility = _text(record.get("visibility")).strip()
    if visibility not in ALLOWED_VISIBILITIES:
        raise CharacterPromotionError(f"{path}.visibility is not allowed: {visibility}")
    activation = _text(record.get("activation") or "current_turn").strip()
    if activation not in ALLOWED_ACTIVATIONS:
        raise CharacterPromotionError(f"{path}.activation is not allowed: {activation}")

    return {
        "name": name,
        "source_agent": source_agent,
        "reason": reason,
        "profile_seed": profile_seed,
        "visibility": visibility,
        "activation": activation,
    }


def _read_json_object(path: str | Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_text(path: str | Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _context_from_persisted_item(item: dict) -> dict:
    profile = _read_json_object(str(item.get("profile_json") or ""))
    name = str(profile.get("name") or item.get("name") or "").strip()
    authoritative = str(profile.get("authoritative_setting") or "").strip()
    if not authoritative:
        authoritative = _read_text(str(item.get("profile_md") or ""))
    context = {
        "name": name,
        "profile_summary": authoritative,
        "memory": {
            "long_term": [authoritative] if authoritative else [],
            "recent": [],
            "goals": [],
        },
        "source_agent": str(profile.get("source_agent") or item.get("existing_source_agent") or ""),
        "profile_preserved": bool(item.get("profile_preserved")),
    }
    return context


def apply_promotions(card_folder: str | Path, records: Any, *, round_id: str) -> dict:
    """Persist validated promotions and update important-character registration."""
    if records in (None, []):
        return {"promoted": [], "registered": [], "skipped": [], "records": [], "contexts": []}
    if not isinstance(records, list):
        raise CharacterPromotionError("character_promotions must be a list")

    normalized = [
        validate_promotion(record, f"character_promotions[{index}]")
        for index, record in enumerate(records)
    ]

    promoted: list[str] = []
    registered: list[str] = []
    skipped: list[dict] = []
    contexts: list[dict] = []
    for record in normalized:
        persisted = character_registry.persist_important_characters(
            card_folder,
            None,
            [
                {
                    "name": record["name"],
                    "profile_seed": record["profile_seed"],
                    "reason": record["reason"],
                    "visibility": record["visibility"],
                    "status": "active",
                    "source_unit_id": f"{record['source_agent']}:{round_id}",
                }
            ],
            source_input_id=f"{record['source_agent']}:{round_id}",
            round_id=round_id,
            source_agent=record["source_agent"],
        )
        if not persisted:
            continue
        item = persisted[0]
        name = str(item.get("name") or record["name"])
        registered.append(name)
        context = _context_from_persisted_item(item)
        if context.get("name"):
            contexts.append(context)
        if item.get("profile_preserved"):
            skipped.append({"name": name, "reason": "existing_profile_preserved"})
        else:
            promoted.append(name)

    return {
        "promoted": promoted,
        "registered": registered,
        "skipped": skipped,
        "records": normalized,
        "contexts": contexts,
    }


__all__ = ["CharacterPromotionError", "SUBGM_PROMOTION_ERROR", "validate_promotion", "apply_promotions"]
