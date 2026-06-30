"""Runtime sidecar for actor key-memory recall records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime import agent_run as agent_run
RUNTIME_FIELD = "_runtime_recalled_key_memories"
ARTIFACT_NAME = "actor.recalled_key_memories.json"


def normalize_items(items: Any, *, actor_id: str = "", source_call_id: str = "") -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    source = items if isinstance(items, list) else []
    for item in source:
        if not isinstance(item, dict):
            continue
        query = str(item.get("query") or "").strip()
        tag = str(item.get("tag") or "").strip()
        summary = str(item.get("summary") or "").strip()
        detail = str(item.get("detail") or "").strip()
        if not (tag or summary or detail):
            continue
        record = {
            "query": query,
            "tag": tag,
            "summary": summary,
            "detail": detail,
        }
        if actor_id:
            record["actor_id"] = actor_id
        elif str(item.get("actor_id") or "").strip():
            record["actor_id"] = str(item.get("actor_id") or "").strip()
        if source_call_id:
            record["source_call_id"] = source_call_id
        elif str(item.get("source_call_id") or "").strip():
            record["source_call_id"] = str(item.get("source_call_id") or "").strip()
        normalized.append(record)
    return normalized


def attach_runtime_items(payload: dict[str, Any], items: Any) -> dict[str, Any]:
    normalized = normalize_items(items)
    if not normalized:
        return payload
    result = dict(payload)
    result[RUNTIME_FIELD] = normalized
    return result


def runtime_items(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        return []
    return normalize_items(payload.get(RUNTIME_FIELD))


def strip_runtime_fields(payload: dict[str, Any]) -> dict[str, Any]:
    if RUNTIME_FIELD not in payload:
        return payload
    result = dict(payload)
    result.pop(RUNTIME_FIELD, None)
    return result


def artifact_path(run_dir: str | Path) -> Path:
    return Path(run_dir) / "artifacts" / ARTIFACT_NAME


def read_records(run_dir: str | Path) -> dict[str, list[dict[str, str]]]:
    path = artifact_path(run_dir)
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, list[dict[str, str]]] = {}
    for actor_id, items in data.items():
        actor_key = str(actor_id or "").strip()
        if not actor_key:
            continue
        normalized = normalize_items(items, actor_id=actor_key)
        if normalized:
            result[actor_key] = normalized
    return result


def append_records(run_dir: str | Path, actor_id: str, source_call_id: str, items: Any) -> None:
    actor_key = str(actor_id or "").strip()
    if not actor_key:
        return
    normalized = normalize_items(items, actor_id=actor_key, source_call_id=str(source_call_id or "").strip())
    if not normalized:
        return
    root = Path(run_dir)
    records = read_records(root)
    records[actor_key] = records.get(actor_key, []) + normalized
    agent_run.write_json(artifact_path(root), records)
