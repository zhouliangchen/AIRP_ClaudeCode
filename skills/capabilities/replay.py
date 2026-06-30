"""Bounded replay capability planning helpers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any

from runtime import agent_run as agent_run_io
class ReplayCapabilityError(ValueError):
    """Raised when a replay plan is unsafe or structurally invalid."""


PLAN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
BACKUP_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$")
ROUND_ID_RE = re.compile(r"^round-[0-9]{6}$")
OPTIONAL_STRING_FIELDS = ("reason", "requested_by", "source_capability_request_id")
SESSION_ROOT = ".replay"


def validate_replay_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a replay plan without mutating caller data."""

    data = _require_dict(plan, "replay_plan")
    if data.get("schema_version") != 1:
        raise ReplayCapabilityError("replay_plan.schema_version must be 1")

    scope = _coalesce_nonempty_str(data, ("scope", "mode"), "replay_plan")
    if scope not in {"single_round", "multi_round"}:
        raise ReplayCapabilityError("replay_plan.scope must be single_round or multi_round")

    plan_id = _require_safe_id(data, "plan_id", "replay_plan")
    backup_id = _require_backup_id(data, "backup_id" if "backup_id" in data else "snapshot_id", "replay_plan")
    affected_inputs = _require_affected_inputs(data)

    normalized = {
        "schema_version": 1,
        "scope": scope,
        "plan_id": plan_id,
        "backup_id": backup_id,
        "affected_inputs": affected_inputs,
        "requires_manual_confirmation": bool(data.get("requires_manual_confirmation", False)),
    }
    for key in OPTIONAL_STRING_FIELDS:
        if key in data:
            normalized[key] = _require_nonempty_str(data, key, "replay_plan")
    return normalized


def validate_replay_execute(payload: dict[str, Any]) -> dict[str, Any]:
    data = _require_dict(payload, "replay_execute")
    if data.get("schema_version") != 1:
        raise ReplayCapabilityError("replay_execute.schema_version must be 1")
    return {
        "schema_version": 1,
        "plan_id": _require_safe_id(data, "plan_id", "replay_execute"),
        "resume": bool(data.get("resume", True)),
    }


def materialize_replay_plan(agent_run: str | Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Write a validated replay plan as a replay session and run artifact."""

    normalized = validate_replay_plan(plan)
    _require_existing_backup(agent_run, normalized["backup_id"])
    card = _card_from_run_dir(agent_run)
    session = _session_dir(card, normalized["plan_id"])
    if session.exists():
        raise ReplayCapabilityError("replay session already exists")
    try:
        session.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ReplayCapabilityError("replay session already exists") from exc
    replay_index = _next_replay_index(card)
    normalized["replay_index"] = replay_index
    _write_json(session / "plan.json", normalized)
    status = {
        "schema_version": 1,
        "session_id": normalized["plan_id"],
        "status": "planned",
        "active_round_index": 0,
        "completed_rounds": [],
        "failed_round": {},
        "last_error": "",
    }
    _write_json(session / "status.json", status)
    _write_json(card / SESSION_ROOT / "replay_counter.json", {"last_replay_index": replay_index})
    _write_json(card / SESSION_ROOT / "active.json", {"session_id": normalized["plan_id"]})
    artifact_path = f"replay_plans/{normalized['plan_id']}.json"
    _write_artifact(Path(agent_run), artifact_path, normalized)
    return {
        "ok": True,
        "session_id": normalized["plan_id"],
        "session_dir": str(session),
        "artifact_path": f"artifacts/{artifact_path}",
        "plan": normalized,
    }


def _next_replay_index(card: Path) -> int:
    counter = agent_run_io.read_json(card / SESSION_ROOT / "replay_counter.json", {})
    value = counter.get("last_replay_index") if isinstance(counter, dict) else None
    if isinstance(value, int) and value >= 0:
        return value + 1
    sessions_root = card / SESSION_ROOT / "sessions"
    highest = 0
    if sessions_root.exists():
        for plan_path in sessions_root.glob("*/plan.json"):
            payload = agent_run_io.read_json(plan_path, {})
            index = payload.get("replay_index") if isinstance(payload, dict) else None
            if isinstance(index, int) and index > highest:
                highest = index
    return highest + 1


def _card_from_run_dir(run_dir: str | Path) -> Path:
    return Path(run_dir).parent.parent


def _session_dir(card: Path, plan_id: str) -> Path:
    sessions_root = (card / SESSION_ROOT / "sessions").resolve()
    candidate = (sessions_root / plan_id).resolve()
    if candidate == sessions_root or sessions_root not in candidate.parents:
        raise ReplayCapabilityError("replay session path escapes sessions directory")
    return candidate


def _write_json(path: str | Path, payload: Any) -> None:
    agent_run_io.write_json(path, payload)


def _write_artifact(run_dir: Path, relative_path: str, payload: Any) -> None:
    _write_json(_artifact_path(run_dir, relative_path), payload)


def _artifact_path(run_dir: str | Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute():
        raise ReplayCapabilityError(f"artifact path must be relative: {relative_path}")

    artifacts_root = (Path(run_dir) / "artifacts").resolve()
    candidate = (artifacts_root / relative).resolve()
    if candidate != artifacts_root and artifacts_root not in candidate.parents:
        raise ReplayCapabilityError(f"artifact path escapes artifacts directory: {relative_path}")
    return candidate


def _require_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReplayCapabilityError(f"{path} must be an object")
    return deepcopy(value)


def _require_nonempty_str(payload: dict[str, Any], key: str, path: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReplayCapabilityError(f"{path}.{key} must be a non-empty string")
    return value.strip()


def _coalesce_nonempty_str(payload: dict[str, Any], keys: tuple[str, ...], path: str) -> str:
    seen = [_require_nonempty_str(payload, key, path) for key in keys if key in payload]
    if not seen:
        raise ReplayCapabilityError(f"{path}.{keys[0]} must be a non-empty string")
    if len(set(seen)) > 1:
        raise ReplayCapabilityError(f"{path}.{keys[0]} conflicts with compatibility alias")
    return seen[0]


def _require_safe_id(payload: dict[str, Any], key: str, path: str) -> str:
    value = _require_nonempty_str(payload, key, path)
    if value in {".", ".."}:
        raise ReplayCapabilityError(f"{path}.{key} contains reserved path segment")
    if not PLAN_ID_RE.fullmatch(value):
        raise ReplayCapabilityError(f"{path}.{key} contains unsafe characters")
    return value


def _require_backup_id(payload: dict[str, Any], key: str, path: str) -> str:
    value = _require_nonempty_str(payload, key, path)
    if not BACKUP_ID_RE.fullmatch(value):
        id_name = "snapshot id" if key == "snapshot_id" else "backup id"
        raise ReplayCapabilityError(f"{path}.{key} contains invalid {id_name}")
    return value


def _require_existing_backup(run_dir: str | Path, backup_id: str) -> Path:
    backups_root = (Path(run_dir).parent.parent / "backup").resolve()
    backup_dir = (backups_root / backup_id).resolve()
    if backup_dir == backups_root or backups_root not in backup_dir.parents:
        raise ReplayCapabilityError("replay_plan.backup_id escapes backup directory")
    backup_file = backup_dir / "backup.json"
    if not backup_file.is_file():
        raise ReplayCapabilityError("replay_plan.backup_id does not exist")
    return backup_dir


def _require_affected_inputs(data: dict[str, Any]) -> list[dict[str, str]]:
    raw = data.get("affected_inputs")
    affected_rounds = None
    if "affected_rounds" in data:
        affected_rounds = data["affected_rounds"]
        if not isinstance(affected_rounds, list) or not affected_rounds:
            raise ReplayCapabilityError("replay_plan.affected_rounds must be a non-empty list")
    if raw is None and affected_rounds is not None:
        raw = [
            {"round_id": item, "input_id": item, "role_text": "", "user_instruction_text": ""}
            for item in affected_rounds
        ]
    if not isinstance(raw, list) or not raw:
        raise ReplayCapabilityError("replay_plan.affected_inputs must be a non-empty list")
    result = []
    for index, item in enumerate(raw):
        entry = _require_dict(item, f"replay_plan.affected_inputs[{index}]")
        result.append(
            {
                "round_id": _require_round_id(
                    entry.get("round_id"),
                    f"replay_plan.affected_inputs[{index}].round_id",
                ),
                "input_id": _require_nonempty_str(entry, "input_id", f"replay_plan.affected_inputs[{index}]"),
                "role_text": str(entry.get("role_text") or ""),
                "user_instruction_text": str(entry.get("user_instruction_text") or ""),
            }
        )
    return result


def _require_round_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReplayCapabilityError(f"{path} must be a non-empty string")
    round_id = value.strip()
    if not ROUND_ID_RE.fullmatch(round_id):
        raise ReplayCapabilityError(f"{path} contains invalid round id: {round_id}")
    return round_id
