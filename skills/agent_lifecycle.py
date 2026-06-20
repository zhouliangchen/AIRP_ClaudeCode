"""File-level lifecycle cleanup for per-round agent activity."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import agent_run
import subgm_threads


CST = timezone(timedelta(hours=8))
ACTIVE_SIDE_THREAD_STATUSES = {"running", "merging", "needs_gm", "blocked", "max_steps"}
TERMINAL_SIDE_THREAD_STATUSES = {"completed", "closed"}
PAUSED_SIDE_THREAD_STATUS = "paused"
DEFAULT_NEXT_RESUME_POINT = "resume when the main GM schedules this side thread in a later round"


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"{path}: invalid JSON") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{path}:{index}: invalid JSONL") from exc
        if not isinstance(record, dict):
            raise RuntimeError(f"{path}:{index}: JSONL record must be an object")
        records.append(record)
    return records


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(payload)
    record["sequence"] = len(_read_jsonl_records(path))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _thread_dirs(run_dir: Path) -> list[Path]:
    root = subgm_threads.side_threads_root(run_dir)
    if not root.exists():
        return []
    return sorted([child for child in root.iterdir() if child.is_dir()], key=lambda item: item.name)


def _pause_side_thread(run_dir: Path, side_dir: Path, reason: str, updated_at: str) -> None:
    state_path = side_dir / "state.json"
    state = _read_json(state_path)
    if not isinstance(state, dict):
        raise RuntimeError(f"{state_path}: state must be an object")
    _read_jsonl_records(side_dir / "messages.jsonl")

    state["status"] = PAUSED_SIDE_THREAD_STATUS
    if not str(state.get("next_resume_point") or "").strip():
        state["next_resume_point"] = DEFAULT_NEXT_RESUME_POINT
    state["updated_at"] = updated_at

    history = state.setdefault("history", [])
    if not isinstance(history, list):
        history = []
        state["history"] = history
    history.append(
        {
            "action": "lifecycle_cleanup",
            "message": DEFAULT_NEXT_RESUME_POINT,
            "created_at": updated_at,
            "metadata": {"reason": reason},
        }
    )

    _write_json(state_path, state)
    thread_id = str(state.get("thread_id") or side_dir.name)
    _append_jsonl(
        side_dir / "messages.jsonl",
        {
            "from": "gm",
            "to": f"subGM:{thread_id}",
            "thread_id": thread_id,
            "action": "lifecycle_cleanup",
            "content": DEFAULT_NEXT_RESUME_POINT,
            "created_at": updated_at,
            "metadata": {"reason": reason},
        },
    )


def _append_manifest_cleanup(run_dir: Path, result: dict[str, Any]) -> None:
    manifest_path = run_dir / "manifest.json"
    manifest = _read_json(manifest_path, {}) or {}
    if not isinstance(manifest, dict):
        manifest = {}
    original_stage = manifest.get("stage")
    manifest["agent_lifecycle_cleanup"] = result

    status = manifest.setdefault("status", [])
    if not isinstance(status, list):
        status = []
        manifest["status"] = status
    entry = {
        "stage": "agent_lifecycle.cleanup",
        "message": "Round agent lifecycle cleanup complete."
        if result.get("ok")
        else "Round agent lifecycle cleanup completed with errors.",
        "timestamp": result["updated_at"],
        "status": result["status"],
    }
    status.append(entry)
    manifest["progress"] = entry
    manifest["progress_message"] = entry["message"]
    if original_stage is not None:
        manifest["stage"] = original_stage
    elif "stage" in manifest:
        manifest.pop("stage", None)

    agent_run.write_json(manifest_path, manifest)


def _manifest_failure_result(reason: str, updated_at: str, error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "degraded",
        "reason": str(reason or ""),
        "paused_side_threads": [],
        "already_terminal": [],
        "already_paused": [],
        "closed_invocations": [],
        "failed": [{"scope": "manifest", "error": error}],
        "updated_at": updated_at,
    }


def cleanup_round_agents(card_folder: str | Path, run_dir: str | Path, *, reason: str = "delivered") -> dict[str, Any]:
    """Pause active side threads and record lifecycle cleanup in the run manifest.

    This is intentionally file-level only: it updates side-thread state and
    mailbox logs, but does not kill OS processes or delete generated artifacts.
    """
    del card_folder
    root = Path(run_dir)
    updated_at = _now()
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        try:
            _read_json(manifest_path)
        except Exception as exc:
            return _manifest_failure_result(str(reason or ""), updated_at, str(exc))

    result: dict[str, Any] = {
        "ok": True,
        "status": "complete",
        "reason": str(reason or ""),
        "paused_side_threads": [],
        "already_terminal": [],
        "already_paused": [],
        "closed_invocations": [],
        "failed": [],
        "updated_at": updated_at,
    }

    for side_dir in _thread_dirs(root):
        thread_id = side_dir.name
        try:
            state = _read_json(side_dir / "state.json")
            if not isinstance(state, dict):
                raise RuntimeError("state.json must contain an object")
            status = str(state.get("status") or "")
            if status in ACTIVE_SIDE_THREAD_STATUSES:
                _pause_side_thread(root, side_dir, result["reason"], updated_at)
                result["paused_side_threads"].append(thread_id)
            elif status in TERMINAL_SIDE_THREAD_STATUSES:
                result["already_terminal"].append(thread_id)
            elif status == PAUSED_SIDE_THREAD_STATUS:
                result["already_paused"].append(thread_id)
            else:
                result["failed"].append(
                    {"thread_id": thread_id, "status": status, "error": "unknown side-thread status"}
                )
        except Exception as exc:
            result["failed"].append({"thread_id": thread_id, "error": str(exc)})

    if result["failed"]:
        result["ok"] = False
        result["status"] = "degraded"

    _append_manifest_cleanup(root, result)
    return result


__all__ = [
    "ACTIVE_SIDE_THREAD_STATUSES",
    "TERMINAL_SIDE_THREAD_STATUSES",
    "cleanup_round_agents",
]
