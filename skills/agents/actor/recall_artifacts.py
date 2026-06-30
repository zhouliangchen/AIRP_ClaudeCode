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


def record_match_keys(item: Any) -> set[str]:
    if not isinstance(item, dict):
        return set()
    keys = {
        str(item.get("tag") or "").strip(),
        str(item.get("summary") or "").strip(),
        str(item.get("query") or "").strip(),
    }
    return {key for key in keys if key}


def has_matching_record(memory_item: Any, records: Any) -> bool:
    keys = record_match_keys(memory_item)
    if not keys:
        return False
    for record in normalize_items(records):
        if keys & record_match_keys(record):
            return True
    return False


def format_recalled_memory(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    query = str(item.get("query") or "").strip()
    tag = str(item.get("tag") or "").strip() or query
    summary = str(item.get("summary") or "").strip()
    detail = str(item.get("detail") or "").strip()
    if not (tag or summary or detail):
        return ""
    label = tag or "这段记忆"
    if summary and detail:
        return f'我已经回忆起"{label}"："{summary}"，详情为"{detail}"'
    if summary:
        return f'我已经回忆起"{label}"："{summary}"'
    if detail:
        return f'我已经回忆起"{label}"，详情为"{detail}"'
    return f'我已经回忆起"{label}"'


def format_recalled_memory_lines(records: Any, *, limit: int = 20) -> list[str]:
    lines: list[str] = []
    for record in normalize_items(records):
        if len(lines) >= limit:
            lines.append("- ...")
            break
        text = format_recalled_memory(record)
        if text:
            lines.append(text)
    return lines


def attach_records_to_packet(packet: dict[str, Any], records: Any) -> dict[str, Any]:
    normalized = normalize_items(records)
    if not normalized:
        return packet
    result = dict(packet)
    result["runtime_recalled_key_memories"] = normalized
    memory = result.get("memory") if isinstance(result.get("memory"), dict) else {}
    key_lines = [str(line) for line in (memory.get("key_memories") or [])]
    recalled_keys: set[str] = set()
    for record in normalized:
        recalled_keys.update(record_match_keys(record))
    if recalled_keys:
        key_lines = [
            line
            for line in key_lines
            if not (
                line.startswith("可进一步回忆")
                and any(key and key in line for key in recalled_keys)
            )
        ]
    key_lines.extend(format_recalled_memory_lines(normalized))
    updated_memory = dict(memory)
    updated_memory["key_memories"] = key_lines
    result["memory"] = updated_memory
    return result


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


def read_actor_records(run_dir: str | Path, actor_id: str, *, include_side_threads: bool = True) -> list[dict[str, str]]:
    actor_key = str(actor_id or "").strip()
    if not actor_key:
        return []
    root = Path(run_dir)
    records: list[dict[str, str]] = []
    records.extend(read_records(root).get(actor_key, []))
    if include_side_threads:
        side_root = root / "side_threads"
        for side_dir in sorted(side_root.iterdir()) if side_root.exists() else []:
            if side_dir.is_dir():
                records.extend(read_records(side_dir).get(actor_key, []))
    return records


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


def clear_round_records(run_dir: str | Path) -> None:
    root = Path(run_dir)
    paths = [artifact_path(root)]
    side_root = root / "side_threads"
    if side_root.exists():
        paths.extend(artifact_path(side_dir) for side_dir in side_root.iterdir() if side_dir.is_dir())
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
