"""Schema-v2 round progress state records and manifest sync helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, NamedTuple

from agent_run import read_json, write_json


class RoundStateError(ValueError):
    """Raised when a round progress state transition cannot be recorded."""


class StateSpec(NamedTuple):
    label: str
    percent: int | None = None
    terminal: bool = False


STATES: Dict[str, StateSpec] = {
    "idle": StateSpec("空闲", 0),
    "input.received": StateSpec("已接收输入", 10),
    "round.preparing": StateSpec("整理上下文", 18),
    "input_analysis.awaiting": StateSpec("等待输入分析", 24),
    "input_analysis.running": StateSpec("输入分析中", 28),
    "input_analysis.applying": StateSpec("应用输入分析", 32),
    "input_analysis.applied": StateSpec("输入分析已应用", 36),
    "gm_loop.starting": StateSpec("推演准备中", 40),
    "gm_loop.gm_dispatch": StateSpec("GM行动中", 44),
    "gm_loop.subgm_dispatch": StateSpec("支线GM行动中", 46),
    "gm_loop.actor_batch": StateSpec("角色批次调度中", 47),
    "gm_loop.actor_dispatch": StateSpec("角色行动中", 48),
    "gm_loop.waiting_player_decision": StateSpec("等待玩家决策", 52),
    "gm_loop.completed": StateSpec("推演已完成", 58),
    "gm_loop.retrying": StateSpec("推演重试中", 42),
    "story.running": StateSpec("正文生成中", 64),
    "story.preflight_repair": StateSpec("正文预检修复中", 68),
    "critic.running": StateSpec("质量检查中", 72),
    "critic.revise": StateSpec("按评审意见修订", 74),
    "critic.blocked": StateSpec("评审阻塞", 74),
    "delivery.validating": StateSpec("交付校验中", 82),
    "delivery.retrying": StateSpec("交付重试中", 84),
    "delivery.delivering": StateSpec("交付到前端", 88),
    "delivery.failed": StateSpec("交付失败", 88),
    "memory.finalizing": StateSpec("记忆整理中", 94),
    "memory.post_round_scheduling": StateSpec("安排回合后记忆", 96),
    "agent_lifecycle.cleanup": StateSpec("清理agent运行状态", 98),
    "complete": StateSpec("完成", 100, True),
    "blocked": StateSpec("已阻塞", 100, True),
    "error": StateSpec("发生错误", 100, True),
}


LEGACY_STAGE_MAP: Dict[str, str] = {
    "idle": "idle",
    "received": "input.received",
    "preparing": "round.preparing",
    "generating": "story.running",
    "delivering": "delivery.delivering",
    "finalizing": "memory.finalizing",
    "retry": "delivery.retrying",
    "blocked": "blocked",
    "error": "error",
    "done": "complete",
    "complete": "complete",
}


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_state(state: str) -> str:
    """Return the declared schema-v2 state for a current or legacy state name."""
    key = str(state or "").strip()
    if key in STATES:
        return key
    if key in LEGACY_STAGE_MAP:
        return LEGACY_STAGE_MAP[key]
    raise RoundStateError(f"unknown progress state: {state}")


def _phase_for(state: str) -> str:
    if "." in state:
        return state.split(".", 1)[0]
    return state


def build_progress_record(
    state: str,
    *,
    label: str | None = None,
    percent: int | float | None = None,
    detail: Any = None,
    run_id: str | None = None,
) -> Dict[str, Any]:
    """Build a schema-v2 progress record with legacy ``stage`` compatibility."""
    normalized = normalize_state(state)
    spec = STATES[normalized]
    resolved_percent = spec.percent if percent is None else max(0, min(100, int(percent)))
    return {
        "schema_version": 2,
        "state": normalized,
        "phase": _phase_for(normalized),
        "label": spec.label if label is None else label,
        "percent": resolved_percent,
        "run_id": run_id,
        "terminal": bool(spec.terminal),
        "detail": {} if detail is None else detail,
        "updated_at": _utc_timestamp(),
        "stage": normalized,
    }


def _read_manifest(run_dir: str | Path) -> Dict[str, Any]:
    manifest = read_json(Path(run_dir) / "manifest.json", {}) or {}
    return manifest if isinstance(manifest, dict) else {}


def _write_manifest(run_dir: str | Path, manifest: Dict[str, Any]) -> None:
    write_json(Path(run_dir) / "manifest.json", manifest)


def _append_progress_entry(manifest: Dict[str, Any], state: str, message: str) -> None:
    status = manifest.setdefault("status", [])
    if not isinstance(status, list):
        status = []
        manifest["status"] = status
    entry = {
        "stage": state,
        "message": message,
        "timestamp": _utc_timestamp(),
    }
    status.append(entry)
    manifest["progress"] = entry
    manifest["progress_message"] = message


def _ensure_complete_allowed(state: str, run_dir: str | Path | None) -> None:
    if state != "complete" or run_dir is None:
        return
    manifest = _read_manifest(run_dir)
    if manifest.get("stage") != "delivered":
        raise RoundStateError("complete requires delivered manifest")


def write_progress_state(
    styles_dir: str | Path,
    state: str,
    *,
    label: str | None = None,
    percent: int | float | None = None,
    detail: Any = None,
    run_id: str | None = None,
    run_dir: str | Path | None = None,
    manifest_message: str | None = None,
) -> Dict[str, Any]:
    """Write ``progress.json`` and optionally sync progress state to a manifest."""
    normalized = normalize_state(state)
    _ensure_complete_allowed(normalized, run_dir)
    record = build_progress_record(
        normalized,
        label=label,
        percent=percent,
        detail=detail,
        run_id=run_id,
    )

    styles_path = Path(styles_dir)
    write_json(styles_path / "progress.json", record)

    if run_dir is not None:
        manifest = _read_manifest(run_dir)
        manifest["progress_state"] = normalized
        if manifest_message is not None:
            _append_progress_entry(manifest, normalized, manifest_message)
        _write_manifest(run_dir, manifest)

    return record


def legacy_progress_record(
    stage: str,
    label: str,
    percent: int | float | None = None,
    detail: Any = None,
) -> Dict[str, Any]:
    """Return schema-v2 progress for known legacy stages, otherwise old shape."""
    key = str(stage or "").strip()
    if key in LEGACY_STAGE_MAP or key in STATES:
        return build_progress_record(key, label=label, percent=percent, detail=detail)

    data = {
        "stage": stage,
        "label": label,
        "percent": percent,
        "detail": detail or "",
        "updated_at": _utc_timestamp(),
    }
    if isinstance(percent, (int, float)):
        data["percent"] = max(0, min(100, int(percent)))
    return data
