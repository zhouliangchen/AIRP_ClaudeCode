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
SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$")
ROUND_ID_RE = re.compile(r"^round-[0-9]{6}$")
OPTIONAL_STRING_FIELDS = ("reason", "requested_by", "source_capability_request_id")
REPLAY_DISCARD_ALLOWLIST = {
    "gm.output.json",
    "actor.outputs.json",
    "interaction.trace.json",
    "story.input.json",
    "story.output.json",
    "critic.report.json",
    "postprocess.output.json",
}


def validate_replay_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a replay plan without mutating caller data."""

    data = _require_dict(plan, "replay_plan")
    if data.get("schema_version") != 1:
        raise ReplayCapabilityError("replay_plan.schema_version must be 1")

    scope = _coalesce_nonempty_str(data, ("scope", "mode"), "replay_plan")
    if scope == "multi_round":
        raise ReplayCapabilityError("multi_round replay is plan-only in this implementation phase")
    if scope != "single_round":
        raise ReplayCapabilityError("replay_plan.scope must be single_round")
    if data.get("requires_manual_confirmation") is not True:
        raise ReplayCapabilityError("replay_plan.requires_manual_confirmation must be true")

    plan_id = _require_safe_id(data, "plan_id", "replay_plan")
    snapshot_id = _require_snapshot_id(data, "snapshot_id", "replay_plan")
    affected_rounds = _require_round_list(data, "affected_rounds", "replay_plan")
    preserved_inputs = _require_preserved_inputs(
        data,
        ("preserved_player_input_ids", "preserved_player_inputs"),
        "replay_plan",
    )
    discard_artifacts = _require_artifact_list(
        data,
        ("discard_ai_artifacts", "discard_artifacts"),
        "replay_plan",
    )

    normalized = {
        "schema_version": 1,
        "plan_id": plan_id,
        "scope": scope,
        "snapshot_id": snapshot_id,
        "affected_rounds": affected_rounds,
        "preserved_player_input_ids": preserved_inputs,
        "discard_ai_artifacts": discard_artifacts,
        "requires_manual_confirmation": True,
    }
    for key in OPTIONAL_STRING_FIELDS:
        if key in data:
            normalized[key] = _require_nonempty_str(data, key, "replay_plan")
    return normalized


def materialize_replay_plan(agent_run: str | Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Write a validated replay plan under the run artifact root."""

    normalized = validate_replay_plan(plan)
    _require_existing_snapshot(agent_run, normalized["snapshot_id"])
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


def _coalesce_nonempty_str(payload: dict[str, Any], keys: tuple[str, ...], path: str) -> str:
    seen = [_require_nonempty_str(payload, key, path) for key in keys if key in payload]
    if not seen:
        raise ReplayCapabilityError(f"{path}.{keys[0]} must be a non-empty string")
    if len(set(seen)) > 1:
        raise ReplayCapabilityError(f"{path}.{keys[0]} conflicts with compatibility alias")
    return seen[0]


def _require_safe_id(payload: dict[str, Any], key: str, path: str) -> str:
    value = _require_nonempty_str(payload, key, path)
    if not PLAN_ID_RE.fullmatch(value):
        raise ReplayCapabilityError(f"{path}.{key} contains unsafe characters")
    return value


def _require_snapshot_id(payload: dict[str, Any], key: str, path: str) -> str:
    value = _require_nonempty_str(payload, key, path)
    if not SNAPSHOT_ID_RE.fullmatch(value):
        raise ReplayCapabilityError(f"{path}.{key} contains invalid snapshot id")
    return value


def _require_existing_snapshot(run_dir: str | Path, snapshot_id: str) -> Path:
    snapshots_root = (Path(run_dir).parent.parent / "backup").resolve()
    snapshot_dir = (snapshots_root / snapshot_id).resolve()
    if snapshot_dir == snapshots_root or snapshots_root not in snapshot_dir.parents:
        raise ReplayCapabilityError("replay_plan.snapshot_id escapes backup directory")
    if not snapshot_dir.is_dir():
        raise ReplayCapabilityError("replay_plan.snapshot_id does not exist")
    return snapshot_dir


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


def _coalesce_nonempty_list(payload: dict[str, Any], keys: tuple[str, ...], path: str) -> list[Any]:
    present = [key for key in keys if key in payload]
    if not present:
        raise ReplayCapabilityError(f"{path}.{keys[0]} must be a non-empty array")
    if len(present) > 1 and payload[present[0]] != payload[present[1]]:
        raise ReplayCapabilityError(f"{path}.{keys[0]} conflicts with compatibility alias")
    return _require_nonempty_list(payload, present[0], path)


def _require_preserved_inputs(payload: dict[str, Any], keys: tuple[str, ...], path: str) -> list[Any]:
    values = _coalesce_nonempty_list(payload, keys, path)
    normalized = []
    for item in values:
        if isinstance(item, str):
            if not item.strip():
                raise ReplayCapabilityError(f"{path}.{keys[0]} must contain non-empty strings or objects")
            normalized.append(item.strip())
            continue
        if not isinstance(item, dict):
            raise ReplayCapabilityError(f"{path}.{keys[0]} must contain non-empty strings or objects")
        entry = deepcopy(item)
        round_id = _require_nonempty_str(entry, "round_id", f"{path}.{keys[0]}[]")
        if not ROUND_ID_RE.fullmatch(round_id):
            raise ReplayCapabilityError(f"{path}.{keys[0]}[].round_id contains invalid round id: {round_id}")
        input_id = _require_nonempty_str(entry, "input_id", f"{path}.{keys[0]}[]")
        raw_text = _require_nonempty_raw_text(entry, "raw_text", f"{path}.{keys[0]}[]")
        normalized.append({"round_id": round_id, "input_id": input_id, "raw_text": raw_text})
    return normalized


def _require_nonempty_raw_text(payload: dict[str, Any], key: str, path: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReplayCapabilityError(f"{path}.{key} must be a non-empty string")
    return value


def _require_artifact_list(payload: dict[str, Any], keys: tuple[str, ...], path: str) -> list[str]:
    values = _coalesce_nonempty_list(payload, keys, path)
    normalized = []
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ReplayCapabilityError(f"{path}.{keys[0]} must contain non-empty strings")
        artifact = item.strip().replace("\\", "/")
        relative = Path(artifact)
        if relative.is_absolute() or ".." in relative.parts:
            raise ReplayCapabilityError(f"{path}.{keys[0]} contains unsafe artifact path: {item}")
        if artifact not in REPLAY_DISCARD_ALLOWLIST:
            raise ReplayCapabilityError(f"{path}.{keys[0]} contains unsupported artifact: {artifact}")
        normalized.append(artifact)
    return normalized


def _require_nonempty_list(payload: dict[str, Any], key: str, path: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ReplayCapabilityError(f"{path}.{key} must be a non-empty array")
    return deepcopy(value)
