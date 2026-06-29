"""Deterministic assets job queue for assets-ui plans."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import agent_run


READY_STATUSES = {"queued", "completed"}


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
    for raw_job in plan.get("jobs") or []:
        job = _normalize_job(raw_job, plan)
        job = _evaluate_and_submit(card, job, image_settings_ready=image_settings_ready, run_command=run_command)
        _write_job(card, run_root, job)
        jobs.append(job)
    return {"status": _summarize(jobs), "jobs": jobs, "plan_id": str(plan.get("plan_id") or "")}


def _normalize_job(raw_job: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    queue_type = str(raw_job.get("queue_type") or raw_job.get("kind") or "")
    job_id = str(raw_job.get("job_id") or queue_type or "asset-job")
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


def _evaluate_and_submit(
    card: Path,
    job: dict[str, Any],
    *,
    image_settings_ready: bool,
    run_command: Callable[..., Any] | None,
) -> dict[str, Any]:
    missing = _missing_dependencies(card, job)
    if missing:
        job["status"] = "waiting_on_references"
        job["reason"] = "missing_character_reference"
        job["missing_references"] = missing
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
        job["status"] = "deferred"
        job["reason"] = "asset_worker_start_failed"
    return job


def _missing_dependencies(card: Path, job: dict[str, Any]) -> list[str]:
    explicit = [str(item) for item in job.get("dependencies") or [] if str(item)]
    important = [str(item) for item in job.get("important_characters") or [] if str(item)]
    for name in important:
        explicit.append(f"generated/characters/{name}/{name}.png")
    missing = []
    for rel in explicit:
        if rel and not (card / Path(rel)).exists():
            missing.append(rel.replace("\\", "/"))
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
    for ref in job.get("resolved_references") or []:
        command.extend(["--reference", str(ref)])
    command.append("--async")
    result = run_command(command, cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, text=True, encoding="utf-8")
    return {
        "command": command,
        "returncode": int(getattr(result, "returncode", 1)),
        "stdout": str(getattr(result, "stdout", "") or ""),
        "stderr": str(getattr(result, "stderr", "") or ""),
    }


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
