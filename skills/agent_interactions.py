"""Structured interaction trace for Claude Code RP subagents."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

CST = timezone(timedelta(hours=8))


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


def init_trace(
    run_dir: str | Path,
    participants: Iterable[str] | None = None,
    chapter_target_words: int = 0,
) -> Dict[str, Any]:
    now = _now()
    trace = {
        "round_id": Path(run_dir).name,
        "created_at": now,
        "updated_at": now,
        "status": "interacting",
        "participants": list(participants or []),
        "chapter_target_words": int(chapter_target_words or 0),
        "events": [],
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
) -> Dict[str, Any]:
    trace = _read(run_dir) or init_trace(run_dir)
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
    }
    events.append(event)
    trace["updated_at"] = _now()
    return _write(run_dir, trace)


def mark_decision_point(
    run_dir: str | Path,
    reason: str,
    options: Iterable[str] | None = None,
) -> Dict[str, Any]:
    trace = _read(run_dir) or init_trace(run_dir)
    trace["status"] = "decision_point"
    trace["decision_point"] = {
        "reason": str(reason),
        "public_reason": str(reason),
        "options": list(options or []),
        "public_options": list(options or []),
        "created_at": _now(),
    }
    trace["stop_reason"] = str(reason)
    trace["public_stop_reason"] = str(reason)
    trace["updated_at"] = _now()
    return _write(run_dir, trace)


def summarize_for_story_input(run_dir: str | Path) -> Dict[str, Any]:
    trace = _read(run_dir)
    if not trace:
        return {
            "round_id": Path(run_dir).name,
            "status": "missing",
            "visible_events": [],
            "private_event_count": 0,
            "decision_point": None,
            "stop_reason": "",
            "chapter_target_words": 0,
        }
    if trace.get("_invalid_trace"):
        return {
            "round_id": Path(run_dir).name,
            "status": "invalid",
            "visible_events": [],
            "private_event_count": 0,
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
        visible.append({
            "actor": str(item.get("actor", "")),
            "type": str(item.get("type", "")),
            "content": str(item.get("content", "")),
        })
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
                "reason": "" if public_reason is None else str(public_reason),
                "options": list(public_options or []),
            }
        else:
            decision_point = None
    else:
        decision_point = None
    return {
        "round_id": trace.get("round_id", Path(run_dir).name),
        "status": trace.get("status", ""),
        "visible_events": visible,
        "private_event_count": private_count,
        "decision_point": decision_point,
        "stop_reason": trace.get("public_stop_reason", ""),
        "chapter_target_words": trace.get("chapter_target_words", 0),
    }
