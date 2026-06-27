"""Retcon rollback and replay helpers for prepared RP rounds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import agent_run
import agent_snapshots
import handler


STATE_FILE = ".retcon_replay.json"


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _player_inputs(card: Path) -> list[dict[str, Any]]:
    records = []
    path = card / ".player_inputs.jsonl"
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _record_id(record: dict[str, Any]) -> str:
    return str(record.get("id") or "").strip()


def _current_input_id(run_dir: Path) -> str:
    raw = _read_json(run_dir / "input.raw.json", {}) or {}
    explicit = raw.get("explicit_payload") if isinstance(raw, dict) else {}
    if isinstance(explicit, dict):
        value = str(explicit.get("id") or "").strip()
        if value:
            return value
    pending = handler.read_pending_user_turn(run_dir.parent.parent)
    if isinstance(pending, dict):
        return str(pending.get("id") or "").strip()
    return ""


def _current_record(records: list[dict[str, Any]], input_id: str) -> dict[str, Any] | None:
    if input_id:
        for record in reversed(records):
            if _record_id(record) == input_id:
                return record
    return records[-1] if records else None


def _snapshot_metadata(card: Path) -> list[dict[str, Any]]:
    root = card / "backup"
    results = []
    for path in sorted(root.glob("*/backup.json")) if root.exists() else []:
        payload = _read_json(path, {}) or {}
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["_snapshot_dir"] = str(path.parent)
            results.append(payload)
    return results


def _latest_snapshot(card: Path, round_id: str, reason: str) -> dict[str, Any] | None:
    matches = [
        item
        for item in _snapshot_metadata(card)
        if item.get("round_id") == round_id and item.get("reason") == reason
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda item: str(item.get("created_at") or ""))[-1]


def _needs_replay(analysis: dict[str, Any]) -> bool:
    directives = analysis.get("narrative_directives")
    if isinstance(directives, dict) and directives.get("rewrite_previous_output") is True:
        return True
    requests = analysis.get("capability_requests")
    if isinstance(requests, list):
        return any(
            isinstance(item, dict) and str(item.get("capability") or "") == "replay.plan"
            for item in requests
        )
    return False


def _rollback_turn_index(run_dir: Path) -> int:
    current_input = _read_json(run_dir / "input.json", {}) or {}
    recent_chat = current_input.get("recent_chat") if isinstance(current_input, dict) else []
    if not isinstance(recent_chat, list):
        recent_chat = []
    return max(0, len(recent_chat) - 1)


def _pending_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _record_id(record),
        "display_text": str(record.get("display_text") or record.get("role_text") or record.get("raw_text") or ""),
        "raw_text": str(record.get("raw_text") or record.get("display_text") or ""),
        "role_text": str(record.get("role_text") or record.get("display_text") or ""),
        "user_instruction_text": str(record.get("user_instruction_text") or ""),
        "input_schema": str(record.get("input_schema") or "dual_channel_v1"),
    }


def _set_pending(card: Path, record: dict[str, Any]) -> dict[str, Any]:
    payload = _pending_payload(record)
    return handler.write_pending_user_turn(
        card,
        payload["display_text"],
        raw_text=payload["raw_text"],
        input_id=payload["id"],
        role_text=payload["role_text"],
        user_instruction_text=payload["user_instruction_text"],
        input_schema=payload["input_schema"],
    )


def _sanitize_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _record_id(record),
        "display_text": str(record.get("display_text") or ""),
        "raw_text": str(record.get("raw_text") or ""),
        "role_text": str(record.get("role_text") or ""),
        "user_instruction_text": str(record.get("user_instruction_text") or ""),
        "input_schema": str(record.get("input_schema") or ""),
        "created_at": str(record.get("created_at") or ""),
    }


def _replay_records(records: list[dict[str, Any]], rollback_index: int, current: dict[str, Any]) -> list[dict[str, Any]]:
    prefix = records[rollback_index : rollback_index + 1]
    if not prefix:
        prefix = [current]
    if _record_id(prefix[-1]) != _record_id(current):
        prefix.append(current)
    return prefix


def prepare_replay_from_current_run(card_folder: str | Path, run_dir: str | Path | None = None) -> dict[str, Any]:
    card = Path(card_folder)
    root = Path(run_dir) if run_dir is not None else agent_run.current_run_dir(card)
    if root is None:
        return {"ok": False, "action": "blocked", "reason": "missing_current_run"}
    root = Path(root)
    active_state = load_state(card)
    if active_state.get("status") == "active":
        return {
            "ok": True,
            "action": "replay_already_active",
            "active_input_index": int(active_state.get("active_input_index") or 0),
            "input_ids": active_state.get("input_ids") if isinstance(active_state.get("input_ids"), list) else [],
        }
    analysis = _read_json(root / "input_analysis.output.json", {}) or {}
    if not isinstance(analysis, dict) or not _needs_replay(analysis):
        return {"ok": True, "action": "not_required"}

    records = _player_inputs(card)
    current = _current_record(records, _current_input_id(root))
    if current is None:
        return {"ok": False, "action": "blocked", "reason": "missing_current_input_record"}

    rollback_index = _rollback_turn_index(root)
    rollback_round_id = f"round-{rollback_index + 1:06d}"
    snapshot = _latest_snapshot(card, rollback_round_id, "before_round_prepare")
    if snapshot is None:
        return {
            "ok": False,
            "action": "blocked",
            "reason": "missing_rollback_snapshot",
            "rollback_turn_index": rollback_index,
            "rollback_round_id": rollback_round_id,
        }

    restored = agent_snapshots.restore_snapshot(
        card,
        str(snapshot.get("snapshot_id") or ""),
        mode="retcon_replay",
    )
    if not restored.get("ok"):
        return {
            "ok": False,
            "action": "blocked",
            "reason": "restore_snapshot_failed",
            "snapshot": restored,
        }

    replay_records = _replay_records(records, rollback_index, current)
    state = {
        "schema_version": 1,
        "status": "active",
        "source_run_dir": str(root),
        "rollback_turn_index": rollback_index,
        "rollback_round_id": rollback_round_id,
        "snapshot_id": str(snapshot.get("snapshot_id") or ""),
        "active_input_index": 0,
        "input_ids": [_record_id(record) for record in replay_records],
        "records": [_sanitize_record(record) for record in replay_records],
    }
    _write_json(card / STATE_FILE, state)
    _set_pending(card, replay_records[0])
    return {
        "ok": True,
        "action": "retcon_replay_prepared",
        "rollback_turn_index": rollback_index,
        "rollback_round_id": rollback_round_id,
        "snapshot": snapshot,
        "restore": restored,
        "input_ids": state["input_ids"],
    }


def load_state(card_folder: str | Path) -> dict[str, Any]:
    state = _read_json(Path(card_folder) / STATE_FILE, {}) or {}
    return state if isinstance(state, dict) else {}


def active_constraint_for_pending(card_folder: str | Path, pending: dict[str, Any] | None) -> dict[str, Any]:
    state = load_state(card_folder)
    if state.get("status") != "active" or not isinstance(pending, dict):
        return {}
    records = state.get("records")
    index = int(state.get("active_input_index") or 0)
    if not isinstance(records, list) or index < 0 or index >= len(records):
        return {}
    current = records[index] if isinstance(records[index], dict) else {}
    if _record_id(current) != str(pending.get("id") or "").strip():
        return {}
    next_record = records[index + 1] if index + 1 < len(records) and isinstance(records[index + 1], dict) else {}
    return {
        "status": "active",
        "rollback_turn_index": state.get("rollback_turn_index"),
        "active_input_index": index,
        "current_input_id": _record_id(current),
        "next_input": _sanitize_record(next_record) if next_record else {},
        "instruction": (
            "This is a retcon replay round. Start from the current player input, "
            "regenerate this turn from the rollback point, and if next_input is present "
            "end in a state that can naturally connect to that next player input. "
            "Do not expose next_input to player or character actors."
        ),
    }


def advance_after_delivery(card_folder: str | Path) -> dict[str, Any]:
    card = Path(card_folder)
    state = load_state(card)
    if state.get("status") != "active":
        return {"ok": True, "action": "not_required"}
    records = state.get("records")
    if not isinstance(records, list) or not records:
        state["status"] = "complete"
        _write_json(card / STATE_FILE, state)
        return {"ok": True, "action": "complete"}
    next_index = int(state.get("active_input_index") or 0) + 1
    if next_index >= len(records):
        state["status"] = "complete"
        _write_json(card / STATE_FILE, state)
        return {"ok": True, "action": "complete"}
    state["active_input_index"] = next_index
    _write_json(card / STATE_FILE, state)
    _set_pending(card, records[next_index])
    return {
        "ok": True,
        "action": "queued_next_input",
        "active_input_index": next_index,
        "input_id": _record_id(records[next_index]),
    }
