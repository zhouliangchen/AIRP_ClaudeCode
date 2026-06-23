"""Bounded replay capability planning helpers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any

import agent_run as agent_run_io


class ReplayCapabilityError(ValueError):
    """Raised when a replay plan is unsafe or structurally invalid."""


PLAN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
ROUND_ID_RE = re.compile(r"^round-[0-9]{6}$")
OPTIONAL_STRING_FIELDS = ("reason", "requested_by", "source_capability_request_id")


def validate_replay_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a replay plan without mutating caller data."""

    data = _require_dict(plan, "replay_plan")
    mode = _require_nonempty_str(data, "mode", "replay_plan")
    if mode == "multi_round":
        raise ReplayCapabilityError("multi_round replay is plan-only in this implementation phase")
    if mode != "single_round":
        raise ReplayCapabilityError("replay_plan.mode must be single_round")
    if data.get("requires_manual_confirmation") is not True:
        raise ReplayCapabilityError("replay_plan.requires_manual_confirmation must be true")

    plan_id = _require_safe_id(data, "plan_id", "replay_plan")
    snapshot_id = _require_safe_id(data, "snapshot_id", "replay_plan")
    affected_rounds = _require_round_list(data, "affected_rounds", "replay_plan")
    preserved_inputs = _require_preserved_inputs(data, "preserved_player_inputs", "replay_plan")
    discard_artifacts = _require_artifact_list(data, "discard_artifacts", "replay_plan")

    normalized = {
        "plan_id": plan_id,
        "mode": mode,
        "snapshot_id": snapshot_id,
        "affected_rounds": affected_rounds,
        "preserved_player_inputs": preserved_inputs,
        "discard_artifacts": discard_artifacts,
        "requires_manual_confirmation": True,
    }
    for key in OPTIONAL_STRING_FIELDS:
        if key in data:
            normalized[key] = _require_nonempty_str(data, key, "replay_plan")
    return normalized


def materialize_replay_plan(agent_run: str | Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Write a validated replay plan under the run artifact root."""

    normalized = validate_replay_plan(plan)
    artifact_path = f"replay_plans/{normalized['plan_id']}.json"
    path = _artifact_path(agent_run, artifact_path)
    agent_run_io.write_json(path, normalized)
    return {"artifact_path": f"artifacts/{artifact_path}", "plan": normalized}


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


def _require_safe_id(payload: dict[str, Any], key: str, path: str) -> str:
    value = _require_nonempty_str(payload, key, path)
    if not PLAN_ID_RE.fullmatch(value):
        raise ReplayCapabilityError(f"{path}.{key} contains unsafe characters")
    return value


def _require_round_list(payload: dict[str, Any], key: str, path: str) -> list[str]:
    values = _require_nonempty_list(payload, key, path)
    normalized = []
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ReplayCapabilityError(f"{path}.{key} must contain non-empty strings")
        round_id = item.strip()
        if not ROUND_ID_RE.fullmatch(round_id):
            raise ReplayCapabilityError(f"{path}.{key} contains invalid round id: {round_id}")
        normalized.append(round_id)
    return normalized


def _require_preserved_inputs(payload: dict[str, Any], key: str, path: str) -> list[Any]:
    values = _require_nonempty_list(payload, key, path)
    normalized = []
    for item in values:
        if isinstance(item, str):
            if not item.strip():
                raise ReplayCapabilityError(f"{path}.{key} must contain non-empty strings or objects")
            normalized.append(item.strip())
            continue
        if not isinstance(item, dict):
            raise ReplayCapabilityError(f"{path}.{key} must contain non-empty strings or objects")
        entry = deepcopy(item)
        round_id = _require_nonempty_str(entry, "round_id", f"{path}.{key}[]")
        if not ROUND_ID_RE.fullmatch(round_id):
            raise ReplayCapabilityError(f"{path}.{key}[].round_id contains invalid round id: {round_id}")
        input_id = _require_nonempty_str(entry, "input_id", f"{path}.{key}[]")
        raw_text = _require_nonempty_str(entry, "raw_text", f"{path}.{key}[]")
        normalized.append({"round_id": round_id, "input_id": input_id, "raw_text": raw_text})
    return normalized


def _require_artifact_list(payload: dict[str, Any], key: str, path: str) -> list[str]:
    values = _require_nonempty_list(payload, key, path)
    normalized = []
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ReplayCapabilityError(f"{path}.{key} must contain non-empty strings")
        artifact = item.strip().replace("\\", "/")
        relative = Path(artifact)
        if relative.is_absolute() or ".." in relative.parts:
            raise ReplayCapabilityError(f"{path}.{key} contains unsafe artifact path: {item}")
        normalized.append(artifact)
    return normalized


def _require_nonempty_list(payload: dict[str, Any], key: str, path: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ReplayCapabilityError(f"{path}.{key} must be a non-empty array")
    return deepcopy(value)
