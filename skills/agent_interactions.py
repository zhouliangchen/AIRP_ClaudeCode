"""Structured interaction trace for Claude Code RP subagents."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

import agent_visibility

CST = timezone(timedelta(hours=8))
SAFE_ID_PATTERNS = (
    re.compile(r"^player$"),
    re.compile(r"^character:[A-Za-z][A-Za-z0-9_]*$"),
    re.compile(r"^event-[0-9]+$"),
    re.compile(r"^call-player-[0-9]+$"),
    re.compile(r"^call-character-[A-Za-z][A-Za-z0-9_]*-[0-9]+$"),
    re.compile(r"^group-[a-z0-9]+(?:-[a-z0-9]+)*$"),
    re.compile(r"^batch-[a-z0-9]+(?:-[a-z0-9]+)*$"),
)


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def _path(run_dir: str | Path) -> Path:
    return Path(run_dir) / "interaction.trace.json"


def _read(run_dir: str | Path) -> Dict[str, Any] | None:
    path = _path(run_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "invalid", "events": [], "_invalid_trace": True}
    return data if isinstance(data, dict) else None


def _write(run_dir: str | Path, trace: Dict[str, Any]) -> Dict[str, Any]:
    path = _path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    return trace


def _schema_version(value: Any) -> int:
    if isinstance(value, bool):
        return 2
    try:
        version = int(value)
    except (TypeError, ValueError):
        return 2
    return version if version > 0 else 2


def _safe_id(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    if agent_visibility.contains_hidden_marker_text(text):
        return ""
    if any(pattern.fullmatch(text) for pattern in SAFE_ID_PATTERNS):
        return text
    return ""


def _safe_id_list(items: Iterable[str] | None) -> list[str]:
    if items is None or isinstance(items, (str, bytes, dict)):
        return []
    if isinstance(items, set):
        values = sorted(items, key=str)
    else:
        try:
            values = list(items)
        except TypeError:
            return []
    return [safe for safe in (_safe_id(item) for item in values) if safe]


def _parallel_groups(trace: Dict[str, Any]) -> list[Dict[str, Any]]:
    groups = trace.get("parallel_groups", [])
    if not isinstance(groups, list):
        return []
    normalized = []
    for item in groups:
        if not isinstance(item, dict):
            continue
        group_id = _safe_id(item.get("group_id", ""))
        if not group_id:
            continue
        normalized.append({
            "group_id": group_id,
            "actors": _safe_id_list(item.get("actors", [])),
        })
    return normalized


def _safe_warning_code(value: Any) -> str:
    text = str(value or "").strip()
    if agent_visibility.contains_hidden_marker_text(text):
        return ""
    if re.fullmatch(r"[a-z][a-z0-9_]*", text):
        return text
    return ""


def _safe_warning_message(value: Any) -> str:
    text = str(value or "")
    if agent_visibility.contains_hidden_marker_text(text):
        return "[redacted]"
    return text[:500]


def _safe_public_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    if agent_visibility.contains_hidden_marker_text(text):
        return "[redacted]"
    return text


def _safe_public_options(value: Any) -> list[str]:
    if value is None or isinstance(value, (str, bytes, dict)):
        return []
    if isinstance(value, set):
        values = sorted(value, key=str)
    else:
        try:
            values = list(value)
        except TypeError:
            return []
    options = []
    for item in values:
        text = str(item)
        if agent_visibility.contains_hidden_marker_text(text):
            continue
        options.append(text)
    return options


def _actor_batches(trace: Dict[str, Any]) -> list[Dict[str, Any]]:
    batches = trace.get("actor_batches", [])
    if not isinstance(batches, list):
        return []
    normalized = []
    for item in batches:
        if not isinstance(item, dict):
            continue
        batch_id = _safe_id(item.get("batch_id", ""))
        if not batch_id:
            continue
        kind = str(item.get("kind") or "")
        if kind not in {"serial", "parallel"}:
            kind = "serial"
        normalized.append({
            "batch_id": batch_id,
            "kind": kind,
            "group_id": _safe_id(item.get("group_id", "")),
            "actors": _safe_id_list(item.get("actors", [])),
            "call_ids": _safe_id_list(item.get("call_ids", [])),
        })
    return normalized


def _routing_warnings(trace: Dict[str, Any]) -> list[Dict[str, Any]]:
    warnings = trace.get("routing_warnings", [])
    if not isinstance(warnings, list):
        return []
    normalized = []
    for item in warnings:
        if not isinstance(item, dict):
            continue
        code = _safe_warning_code(item.get("code", ""))
        if not code:
            continue
        normalized.append({
            "code": code,
            "message": _safe_warning_message(item.get("message", "")),
            "group_id": _safe_id(item.get("group_id", "")),
            "actors": _safe_id_list(item.get("actors", [])),
            "call_ids": _safe_id_list(item.get("call_ids", [])),
        })
    return normalized


def init_trace(
    run_dir: str | Path,
    participants: Iterable[str] | None = None,
    chapter_target_words: int = 0,
) -> Dict[str, Any]:
    now = _now()
    trace = {
        "schema_version": 2,
        "round_id": Path(run_dir).name,
        "created_at": now,
        "updated_at": now,
        "status": "interacting",
        "participants": list(participants or []),
        "chapter_target_words": int(chapter_target_words or 0),
        "events": [],
        "parallel_groups": [],
        "actor_batches": [],
        "routing_warnings": [],
        "decision_point": None,
        "stop_reason": "",
    }
    return _write(run_dir, trace)


def append_event(
    run_dir: str | Path,
    actor: str,
    visibility: str,
    event_type: str,
    content: str,
    target: str = "",
    source_call_id: str = "",
    causal_links: Iterable[str] | None = None,
    visibility_metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    trace = _read(run_dir) or init_trace(run_dir)
    trace["schema_version"] = 2
    if not isinstance(trace.get("parallel_groups"), list):
        trace["parallel_groups"] = []
    if not isinstance(trace.get("actor_batches"), list):
        trace["actor_batches"] = []
    if not isinstance(trace.get("routing_warnings"), list):
        trace["routing_warnings"] = []
    events = trace.get("events")
    if not isinstance(events, list):
        events = []
        trace["events"] = events
    event = {
        "index": len(events),
        "created_at": _now(),
        "actor": str(actor),
        "visibility": str(visibility),
        "type": str(event_type),
        "content": str(content),
        "target": _safe_id(target),
        "source_call_id": _safe_id(source_call_id),
        "causal_links": _safe_id_list(causal_links),
    }
    metadata = agent_visibility.visibility_fields_from_event(visibility_metadata or {})
    event.update(metadata)
    events.append(event)
    trace["updated_at"] = _now()
    return _write(run_dir, trace)


def record_parallel_group(
    run_dir: str | Path,
    group_id: str,
    actors: Iterable[str],
) -> Dict[str, Any]:
    trace = _read(run_dir) or init_trace(run_dir)
    trace["schema_version"] = 2
    groups = trace.get("parallel_groups")
    if not isinstance(groups, list):
        groups = []
        trace["parallel_groups"] = groups
    safe_group_id = _safe_id(group_id)
    if safe_group_id:
        groups.append({
            "group_id": safe_group_id,
            "actors": _safe_id_list(actors),
        })
    trace["updated_at"] = _now()
    return _write(run_dir, trace)


def record_actor_batch(
    run_dir: str | Path,
    batch_id: str,
    kind: str,
    actors: Iterable[str],
    call_ids: Iterable[str],
    group_id: str = "",
) -> Dict[str, Any]:
    trace = _read(run_dir) or init_trace(run_dir)
    trace["schema_version"] = 2
    batches = trace.get("actor_batches")
    if not isinstance(batches, list):
        batches = []
        trace["actor_batches"] = batches
    safe_batch_id = _safe_id(batch_id)
    if safe_batch_id:
        batch_kind = str(kind or "")
        if batch_kind not in {"serial", "parallel"}:
            batch_kind = "serial"
        batches.append({
            "batch_id": safe_batch_id,
            "kind": batch_kind,
            "group_id": _safe_id(group_id),
            "actors": _safe_id_list(actors),
            "call_ids": _safe_id_list(call_ids),
        })
    trace["updated_at"] = _now()
    return _write(run_dir, trace)


def record_routing_warning(
    run_dir: str | Path,
    code: str,
    message: str,
    group_id: str = "",
    actors: Iterable[str] | None = None,
    call_ids: Iterable[str] | None = None,
) -> Dict[str, Any]:
    trace = _read(run_dir) or init_trace(run_dir)
    trace["schema_version"] = 2
    warnings = trace.get("routing_warnings")
    if not isinstance(warnings, list):
        warnings = []
        trace["routing_warnings"] = warnings
    safe_code = _safe_warning_code(code)
    if safe_code:
        warnings.append({
            "code": safe_code,
            "message": _safe_warning_message(message),
            "group_id": _safe_id(group_id),
            "actors": _safe_id_list(actors or []),
            "call_ids": _safe_id_list(call_ids or []),
        })
    trace["updated_at"] = _now()
    return _write(run_dir, trace)


def mark_decision_point(
    run_dir: str | Path,
    reason: str,
    options: Iterable[str] | None = None,
) -> Dict[str, Any]:
    trace = _read(run_dir) or init_trace(run_dir)
    trace["schema_version"] = 2
    if not isinstance(trace.get("parallel_groups"), list):
        trace["parallel_groups"] = []
    if not isinstance(trace.get("actor_batches"), list):
        trace["actor_batches"] = []
    if not isinstance(trace.get("routing_warnings"), list):
        trace["routing_warnings"] = []
    trace["status"] = "decision_point"
    trace["decision_point"] = {
        "reason": str(reason),
        "public_reason": _safe_public_text(reason),
        "options": list(options or []),
        "public_options": _safe_public_options(options),
        "created_at": _now(),
    }
    trace["stop_reason"] = str(reason)
    trace["public_stop_reason"] = _safe_public_text(reason)
    trace["updated_at"] = _now()
    return _write(run_dir, trace)


def summarize_for_story_input(run_dir: str | Path) -> Dict[str, Any]:
    trace = _read(run_dir)
    if not trace:
        return {
            "schema_version": 2,
            "round_id": Path(run_dir).name,
            "status": "missing",
            "visible_events": [],
            "private_event_count": 0,
            "parallel_groups": [],
            "actor_batches": [],
            "routing_warnings": [],
            "decision_point": None,
            "stop_reason": "",
            "chapter_target_words": 0,
        }
    if trace.get("_invalid_trace"):
        return {
            "schema_version": 2,
            "round_id": Path(run_dir).name,
            "status": "invalid",
            "visible_events": [],
            "private_event_count": 0,
            "parallel_groups": [],
            "actor_batches": [],
            "routing_warnings": [],
            "decision_point": None,
            "stop_reason": "",
            "chapter_target_words": 0,
        }

    events = trace.get("events", [])
    if not isinstance(events, list):
        events = []
    visible = []
    for item in events:
        if not isinstance(item, dict) or item.get("visibility") != "world_visible":
            continue
        visible_item = {
            "actor": str(item.get("actor", "")),
            "type": str(item.get("type", "")),
            "content": str(item.get("content", "")),
            "target": _safe_id(item.get("target", "")),
            "source_call_id": _safe_id(item.get("source_call_id", "")),
            "causal_links": _safe_id_list(item.get("causal_links", [])),
        }
        raw_visibility_metadata = {
            field: item[field]
            for field in agent_visibility.VISIBILITY_FIELDS
            if field in item
        }
        visibility_metadata = agent_visibility.visibility_fields_from_event(raw_visibility_metadata)
        for field in agent_visibility.VISIBILITY_FIELDS:
            if field in visibility_metadata:
                visible_item[field] = visibility_metadata[field]
        visible.append(visible_item)
    private_count = len([
        item for item in events
        if isinstance(item, dict) and item.get("visibility") != "world_visible"
    ])
    decision_point = trace.get("decision_point")
    if isinstance(decision_point, dict):
        public_reason = decision_point.get("public_reason")
        public_options = decision_point.get("public_options")
        if public_reason is not None or public_options is not None:
            decision_point = {
                "reason": "" if public_reason is None else _safe_public_text(public_reason),
                "options": _safe_public_options(public_options),
            }
        else:
            decision_point = None
    else:
        decision_point = None
    return {
        "schema_version": _schema_version(trace.get("schema_version", 2) or 2),
        "round_id": trace.get("round_id", Path(run_dir).name),
        "status": trace.get("status", ""),
        "visible_events": visible,
        "private_event_count": private_count,
        "parallel_groups": _parallel_groups(trace),
        "actor_batches": _actor_batches(trace),
        "routing_warnings": _routing_warnings(trace),
        "decision_point": decision_point,
        "stop_reason": _safe_public_text(trace.get("public_stop_reason", "")),
        "chapter_target_words": trace.get("chapter_target_words", 0),
    }
