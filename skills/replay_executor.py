"""Deterministic replay session executor."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import agent_snapshots
import handler
import round_prepare
import rp_generate_cli


PrepareRound = Callable[[Path, Path, dict[str, Any]], dict[str, Any]]
GenerateRound = Callable[[Path, Path], dict[str, Any]]
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
DEFAULT_MUST_PRESERVE = [
    "Do not rewrite player input text.",
    "Do not expose next_input to player or character actors.",
]


def prepare_round_default(
    card_folder: str | Path,
    root_dir: str | Path,
    outline: dict[str, Any],
) -> dict[str, Any]:
    """Prepare the current replay round through the production round API."""

    del outline
    try:
        return _dict_result(round_prepare.prepare_round(Path(card_folder), Path(root_dir)))
    except Exception as exc:
        return {"ok": False, "reason": str(exc) or exc.__class__.__name__}


def generate_round_default(card_folder: str | Path, root_dir: str | Path) -> dict[str, Any]:
    """Generate and deliver the current prepared replay round."""

    try:
        return _dict_result(rp_generate_cli.run_round(Path(card_folder), Path(root_dir)))
    except Exception as exc:
        return {"ok": False, "reason": str(exc) or exc.__class__.__name__}


def execute_replay_session(
    card_folder: str | Path,
    root_dir: str | Path,
    session_id: str,
    *,
    prepare_round: PrepareRound,
    generate_round: GenerateRound,
) -> dict[str, Any]:
    """Execute a materialized replay session using injected deterministic hooks."""

    card = Path(card_folder)
    root = Path(root_dir)
    if not _is_safe_session_id(session_id):
        return {"ok": False, "reason": "unsafe_session_id"}
    try:
        session = _session_dir(card, session_id)
    except ValueError:
        return {"ok": False, "reason": "unsafe_session_id"}
    plan = _read_json(session / "plan.json", {})
    status = _read_json(session / "status.json", {})
    affected_inputs = plan.get("affected_inputs")
    if not isinstance(affected_inputs, list):
        return _block(
            session,
            status,
            active_round_index=0,
            failed_round={},
            error="invalid_replay_plan",
        )

    active_index = _active_round_index(status)
    completed_rounds = _completed_rounds(status)
    if status.get("status") == "planned":
        restored = agent_snapshots.restore_snapshot(
            card,
            str(plan.get("backup_id") or ""),
            mode="replay_execute",
        )
        if not restored.get("ok"):
            return _block(
                session,
                status,
                active_round_index=active_index,
                failed_round={},
                error=_failure_reason(restored, "restore_snapshot_failed"),
            )
        status = _status_payload(
            session_id=session_id,
            status="active",
            active_round_index=active_index,
            completed_rounds=completed_rounds,
            failed_round={},
            last_error="",
        )
        _write_json(session / "plan.json", plan)
        _write_json(session / "status.json", status)
        _write_active(session, session_id, "active")

    for index in range(active_index, len(affected_inputs)):
        current = _as_dict(affected_inputs[index])
        next_input = _as_dict(affected_inputs[index + 1]) if index + 1 < len(affected_inputs) else {}
        outline = _build_outline(plan, session_id, current, next_input)
        round_dir = session / "rounds" / str(index)
        _write_json(round_dir / "outline.json", outline)
        _write_pending(card, current)

        prepare_result = prepare_round(card, root, outline)
        if not _ok(prepare_result):
            return _block(
                session,
                status,
                active_round_index=index,
                failed_round=_failed_round(index, outline, prepare_result),
                error=_failure_reason(prepare_result, "prepare_round_failed"),
            )

        generate_result = generate_round(card, root)
        _write_json(round_dir / "result.json", generate_result)
        if not _ok(generate_result):
            return _block(
                session,
                status,
                active_round_index=index,
                failed_round=_failed_round(index, outline, generate_result),
                error=_failure_reason(generate_result, "generate_round_failed"),
            )

        completed_rounds.append(
            {
                "round_index": index,
                "round_id": outline["round_id"],
                "input_id": outline["input_id"],
            }
        )
        status = _status_payload(
            session_id=session_id,
            status="active",
            active_round_index=index + 1,
            completed_rounds=completed_rounds,
            failed_round={},
            last_error="",
        )
        _write_json(session / "status.json", status)

    complete = _status_payload(
        session_id=session_id,
        status="complete",
        active_round_index=len(affected_inputs),
        completed_rounds=completed_rounds,
        failed_round={},
        last_error="",
    )
    _write_json(session / "status.json", complete)
    _write_json(card / ".replay" / "active.json", {"session_id": "", "status": "complete"})
    return {"ok": True, "session_id": session_id, "status": "complete", "completed_rounds": completed_rounds}


def _session_dir(card: Path, session_id: str) -> Path:
    sessions_root = (card / ".replay" / "sessions").resolve()
    candidate = (sessions_root / session_id).resolve()
    if candidate == sessions_root or sessions_root not in candidate.parents:
        raise ValueError("unsafe_session_id")
    return candidate


def _is_safe_session_id(session_id: Any) -> bool:
    if not isinstance(session_id, str) or not session_id:
        return False
    if session_id in {".", ".."}:
        return False
    if "/" in session_id or "\\" in session_id:
        return False
    if Path(session_id).is_absolute():
        return False
    return bool(SESSION_ID_RE.fullmatch(session_id))


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def _active_round_index(status: dict[str, Any]) -> int:
    value = status.get("active_round_index")
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def _completed_rounds(status: dict[str, Any]) -> list[dict[str, Any]]:
    raw = status.get("completed_rounds")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _build_outline(
    plan: dict[str, Any],
    session_id: str,
    current: dict[str, Any],
    next_input: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "session_id": session_id,
        "round_id": str(current.get("round_id") or ""),
        "input_id": str(current.get("input_id") or ""),
        "current_input": _input_outline(current),
        "next_input": _input_outline(next_input) if next_input else {},
        "bridge_goal": _bridge_goal(next_input),
        "must_preserve": _must_preserve(plan),
        "must_change": _list_field(plan, "must_change"),
        "visibility": {
            "current_input": "round_runtime",
            "next_input": "gm_bridge_only" if next_input else "none",
        },
    }


def _input_outline(item: dict[str, Any]) -> dict[str, str]:
    return {
        "round_id": str(item.get("round_id") or ""),
        "input_id": str(item.get("input_id") or ""),
        "role_text": str(item.get("role_text") or ""),
        "user_instruction_text": str(item.get("user_instruction_text") or ""),
    }


def _bridge_goal(next_input: dict[str, Any]) -> str:
    if not next_input:
        return ""
    return (
        "Regenerate the current replay round so the resulting state can naturally "
        "connect to the next preserved player input."
    )


def _list_field(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    return list(value) if isinstance(value, list) else []


def _must_preserve(payload: dict[str, Any]) -> list[Any]:
    result = list(DEFAULT_MUST_PRESERVE)
    for item in _list_field(payload, "must_preserve"):
        if item not in result:
            result.append(item)
    return result


def _write_pending(card: Path, current: dict[str, Any]) -> None:
    role_text = str(current.get("role_text") or "")
    instruction_text = str(current.get("user_instruction_text") or "")
    handler.write_pending_user_turn(
        card,
        role_text,
        raw_text=_raw_text(role_text, instruction_text),
        input_id=str(current.get("input_id") or ""),
        role_text=role_text,
        user_instruction_text=instruction_text,
        input_schema="dual_channel_v1",
    )


def _raw_text(role_text: str, instruction_text: str) -> str:
    if instruction_text:
        return role_text + "\n\n[USER_INSTRUCTION]\n" + instruction_text
    return role_text


def _ok(result: Any) -> bool:
    return isinstance(result, dict) and result.get("ok") is True


def _dict_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    return {"ok": False, "reason": "invalid_round_result"}


def _failure_reason(result: Any, fallback: str) -> str:
    if isinstance(result, dict):
        reason = result.get("reason") or result.get("error") or result.get("message")
        if isinstance(reason, str) and reason:
            return reason
    return fallback


def _failed_round(index: int, outline: dict[str, Any], result: Any) -> dict[str, Any]:
    return {
        "round_index": index,
        "round_id": outline.get("round_id", ""),
        "input_id": outline.get("input_id", ""),
        "result": result if isinstance(result, dict) else {},
    }


def _block(
    session: Path,
    previous_status: dict[str, Any],
    *,
    active_round_index: int,
    failed_round: dict[str, Any],
    error: str,
) -> dict[str, Any]:
    session_id = str(previous_status.get("session_id") or session.name)
    status = _status_payload(
        session_id=session_id,
        status="blocked",
        active_round_index=active_round_index,
        completed_rounds=_completed_rounds(previous_status),
        failed_round=failed_round,
        last_error=error,
    )
    _write_json(session / "status.json", status)
    _write_active(session, session_id, "blocked")
    return {"ok": False, "session_id": session_id, "status": "blocked", "reason": error}


def _write_active(session: Path, session_id: str, status: str) -> None:
    _write_json(session.parent.parent / "active.json", {"session_id": session_id, "status": status})


def _status_payload(
    *,
    session_id: str,
    status: str,
    active_round_index: int,
    completed_rounds: list[dict[str, Any]],
    failed_round: dict[str, Any],
    last_error: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "session_id": session_id,
        "status": status,
        "active_round_index": active_round_index,
        "completed_rounds": completed_rounds,
        "failed_round": failed_round,
        "last_error": last_error,
    }
