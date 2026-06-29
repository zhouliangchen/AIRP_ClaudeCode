"""Deterministic assets job queue for assets-ui plans."""

from __future__ import annotations

import json
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable

import agent_run


def apply_plan(
    card_folder: str | Path,
    run_dir: str | Path,
    plan: dict[str, Any],
    *,
    image_settings_ready: bool,
    run_command: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    card = Path(card_folder)
    run_root = Path(run_dir)
    jobs = []
    used_safe_ids: set[str] = set()
    for index, raw_job in enumerate(plan.get("jobs") or [], start=1):
        job = _normalize_job(raw_job, plan, index)
        _dedupe_job_id(job, used_safe_ids)
        job = _evaluate_and_submit(card, job, image_settings_ready=image_settings_ready, run_command=run_command)
        _write_job(card, run_root, job)
        jobs.append(job)
    return {"status": _summarize(jobs), "jobs": jobs, "plan_id": str(plan.get("plan_id") or "")}


def _normalize_job(raw_job: Any, plan: dict[str, Any], index: int) -> dict[str, Any]:
    plan_id = str(plan.get("plan_id") or "asset-plan")
    if not isinstance(raw_job, dict):
        return {
            "schema_version": 1,
            "queue_type": "invalid_job",
            "job_id": f"{plan_id}-invalid_job-{index}",
            "agent_plan_id": str(plan.get("plan_id") or ""),
            "status": "failed",
            "reason": "invalid_job",
            "raw_job": raw_job,
        }

    queue_type = str(raw_job.get("queue_type") or raw_job.get("kind") or "")
    job_id = str(raw_job.get("job_id") or f"{plan_id}-{queue_type or 'asset-job'}-{index}")
    job = dict(raw_job)
    job["schema_version"] = 1
    job["queue_type"] = queue_type
    job["job_id"] = job_id
    job["agent_plan_id"] = str(plan.get("plan_id") or "")
    job.setdefault("dependencies", [])
    job.setdefault("batch_id", str(raw_job.get("batch_id") or ""))
    job.setdefault("round_id", str(raw_job.get("round_id") or ""))
    if queue_type in {"character_reference", "character_reference_candidate"}:
        job.setdefault("display_policy", "hidden_reference")
        job.setdefault("target_path", _character_target_path(job))
    elif queue_type == "scene_illustration":
        job.setdefault("display_policy", "story_inline")
    return job


def _dedupe_job_id(job: dict[str, Any], used_safe_ids: set[str]) -> None:
    original = str(job.get("job_id") or "asset-job")
    candidate = original
    suffix = 2
    while agent_run.safe_name(candidate) in used_safe_ids:
        candidate = f"{original}-{suffix}"
        suffix += 1
    if candidate != original:
        job["source_job_id"] = original
        job["job_id"] = candidate
    used_safe_ids.add(agent_run.safe_name(candidate))


def _evaluate_and_submit(
    card: Path,
    job: dict[str, Any],
    *,
    image_settings_ready: bool,
    run_command: Callable[..., Any] | None,
) -> dict[str, Any]:
    if job.get("status") == "failed":
        return job
    invalid = _prepare_asset_paths(card, job)
    if invalid:
        job["status"] = "failed"
        job["reason"] = "invalid_asset_path"
        job["invalid_path"] = invalid
        return job
    missing = _missing_dependencies(card, job)
    required_missing = job.pop("_required_missing_references", [])
    if required_missing:
        missing.extend(required_missing)
    if missing:
        job["status"] = "waiting_on_references"
        job["reason"] = "missing_character_reference"
        job["missing_references"] = _unique_paths(missing)
        return job
    if not image_settings_ready or run_command is None:
        job["status"] = "deferred"
        job["reason"] = "asset_worker_not_configured"
        return job
    job["command"] = _run_image_job(card, job, run_command)
    if job["command"]["returncode"] == 0:
        job["status"] = "queued"
        job.pop("reason", None)
    else:
        _apply_worker_failure(job)
    return job


def _prepare_asset_paths(card: Path, job: dict[str, Any]) -> str:
    required = job.get("queue_type") == "scene_illustration" and job.get("reference_policy") == "required"
    required_missing = []
    if job.get("target_path"):
        normalized = _normalize_asset_path(job["target_path"])
        if normalized is None:
            return str(job["target_path"])
        job["target_path"] = normalized

    dependencies = []
    for item in job.get("dependencies") or []:
        normalized = _normalize_asset_path(item)
        if normalized is None:
            return str(item)
        if normalized:
            dependencies.append(normalized)
    job["dependencies"] = dependencies

    resolved = []
    for item in job.get("resolved_references") or []:
        normalized = _normalize_asset_path(item)
        if normalized is None:
            return str(item)
        if normalized:
            if required and not (card / Path(normalized)).is_file():
                required_missing.append(normalized)
            else:
                resolved.append(normalized)

    reference_candidates = []
    if job.get("queue_type") == "scene_illustration":
        for item in _reference_candidate_paths(job):
            normalized = _normalize_asset_path(item)
            if normalized is None:
                return str(item)
            if normalized:
                reference_candidates.append(normalized)

    if required:
        for normalized in reference_candidates:
            if (card / Path(normalized)).is_file():
                resolved.append(normalized)
            else:
                required_missing.append(normalized)
        if required_missing:
            job["_required_missing_references"] = required_missing

    job["resolved_references"] = _unique_paths(resolved)
    return ""


def _normalize_asset_path(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return ""
    windows_path = PureWindowsPath(raw)
    posix_path = PurePosixPath(raw)
    if windows_path.drive or windows_path.root or posix_path.is_absolute():
        return None
    parts = [part for part in windows_path.parts if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _reference_candidate_paths(job: dict[str, Any]) -> list[str]:
    paths = []
    for candidate in job.get("reference_candidates") or []:
        if isinstance(candidate, dict):
            value = candidate.get("path")
        else:
            value = candidate
        if value:
            paths.append(str(value))
    return paths


def _missing_dependencies(card: Path, job: dict[str, Any]) -> list[str]:
    explicit = [str(item) for item in job.get("dependencies") or [] if str(item)]
    important = [str(item) for item in job.get("important_characters") or [] if str(item)]
    for name in important:
        path = _normalize_asset_path(f"generated/characters/{name}/{name}.png")
        if path is None:
            return [f"generated/characters/{name}/{name}.png"]
        explicit.append(path)
    missing = []
    for rel in explicit:
        if rel and not (card / Path(rel)).is_file():
            missing.append(rel)
    return missing


def _run_image_job(card: Path, job: dict[str, Any], run_command: Callable[..., Any]) -> dict[str, Any]:
    queue_type = job["queue_type"]
    kind = "character_reference" if queue_type.startswith("character_reference") else "scene_illustration"
    target = str(job.get("target_path") or job.get("target") or "scene_illustration")
    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "image_generate.py"),
        str(card),
        "--prompt",
        str(job.get("prompt") or ""),
        "--kind",
        kind,
        "--target",
        target,
        "--job-id",
        str(job.get("job_id") or ""),
    ]
    if job.get("target_path"):
        command.extend(["--output-path", str(job["target_path"])])
    for ref in _worker_references(job):
        command.extend(["--reference", str(ref)])
    command.append("--async")
    result = run_command(
        command,
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {
        "command": command,
        "returncode": int(getattr(result, "returncode", 1)),
        "stdout": str(getattr(result, "stdout", "") or ""),
        "stderr": str(getattr(result, "stderr", "") or ""),
    }


def _worker_references(job: dict[str, Any]) -> list[str]:
    if job.get("reference_policy") != "required":
        return []
    return [str(ref) for ref in job.get("resolved_references") or [] if str(ref)]


def _apply_worker_failure(job: dict[str, Any]) -> None:
    command = job.get("command") or {}
    details = _parse_worker_stdout(command.get("stdout"))
    reason = str(details.get("reason") or "")
    status = str(details.get("status") or "")
    error = details.get("error")
    if status in {"deferred", "failed"}:
        job["status"] = "failed" if status == "failed" or reason == "invalid_asset_path" else "deferred"
        if reason:
            job["reason"] = reason
        else:
            job["reason"] = "asset_worker_start_failed"
        if error:
            job["error"] = str(error)
        if isinstance(details.get("references"), list):
            job["worker_references"] = [str(item) for item in details["references"]]
        return
    job["status"] = "deferred"
    job["reason"] = "asset_worker_start_failed"


def _parse_worker_stdout(stdout: Any) -> dict[str, Any]:
    try:
        data = json.loads(str(stdout or "").strip())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _unique_paths(paths: list[str]) -> list[str]:
    seen = set()
    unique = []
    for path in paths:
        normalized = str(path).replace("\\", "/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique


def _character_target_path(job: dict[str, Any]) -> str:
    name = str(job.get("character_name") or job.get("name") or "角色")
    state = str(job.get("appearance_state") or "").strip()
    if state:
        return f"generated/characters/{name}/{name}-{agent_run.safe_name(state)}.png"
    return f"generated/characters/{name}/{name}.png"


def _write_job(card: Path, run_dir: Path, job: dict[str, Any]) -> None:
    safe_id = agent_run.safe_name(str(job.get("job_id") or "asset-job"))
    agent_run.write_json(card / "generated" / "jobs" / f"{safe_id}.json", job)
    agent_run.write_json(run_dir / "artifacts" / "assets_ui" / "jobs" / f"{safe_id}.json", job)


def _summarize(jobs: list[dict[str, Any]]) -> str:
    statuses = {str(job.get("status") or "") for job in jobs}
    if "failed" in statuses:
        return "failed"
    if any(status.startswith("waiting_on") for status in statuses):
        return "waiting_on_references"
    if "queued" in statuses:
        return "queued"
    if "deferred" in statuses:
        return "deferred"
    return "completed" if jobs else "not_required"
