"""GM-owned objective world archive helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

OBJECTIVE_WORLD_REL_PATH = "memory/objective_world.json"

EMPTY_OBJECTIVE_WORLD = {"facts": [], "sources": []}


def _archive_path(card_folder: str | Path) -> Path:
    return Path(card_folder) / OBJECTIVE_WORLD_REL_PATH


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _empty_with_diagnostic(reason: str, message: str = "") -> Dict[str, Any]:
    diagnostic = {
        "type": "diagnostic",
        "reason": reason,
        "path": OBJECTIVE_WORLD_REL_PATH,
    }
    if message:
        diagnostic["message"] = message
    return {"facts": [], "sources": [diagnostic]}


def normalize_objective_world(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return _empty_with_diagnostic("non_object")
    return {
        "facts": _as_list(payload.get("facts")),
        "sources": _as_list(payload.get("sources")),
    }


def read_objective_world(card_folder: str | Path) -> Dict[str, Any]:
    path = _archive_path(card_folder)
    if not path.exists():
        return {"facts": [], "sources": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return _empty_with_diagnostic("invalid_json", str(exc))
    except OSError as exc:
        return _empty_with_diagnostic("read_error", str(exc))
    return normalize_objective_world(payload)


def write_objective_world(card_folder: str | Path, payload: Any) -> Dict[str, Any]:
    normalized = normalize_objective_world(payload)
    path = _archive_path(card_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return normalized


def append_fact(card_folder: str | Path, *, scope: Any, fact: Any, source: Any) -> Dict[str, Any]:
    text = "" if fact is None else str(fact).strip()
    payload = read_objective_world(card_folder)
    if not text:
        return payload

    record = {
        "scope": "" if scope is None else str(scope).strip(),
        "fact": text,
        "source": "" if source is None else str(source).strip(),
    }
    payload["facts"].append(record)
    return write_objective_world(card_folder, payload)
