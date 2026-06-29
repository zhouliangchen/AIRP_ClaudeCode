"""Deterministic assets job queue for assets-ui plans."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable

import agent_run


SUPPORTED_QUEUE_TYPES = {
    "asset_rename",
    "character_reference",
    "character_reference_candidate",
    "character_reference_selection",
    "scene_illustration",
    "ui_patch_request",
}

NON_IMAGE_QUEUE_WAIT_STATES = {
    "character_reference_selection": ("waiting_on_critic", "critic_vision_not_available"),
    "ui_patch_request": ("deferred", "ui_patch_requires_claude_code"),
}

CHILD_WRITTEN_JOB_STATUSES = {"completed", "deferred", "failed"}


def _job_filename_slug(text: str) -> str:
    keep = []
    for ch in text.lower():
        if ch.isalnum():
            keep.append(ch)
        elif ch in "-_ ":
            keep.append("-")
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:48] or "image"


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
    raw_jobs = _expand_character_reference_candidate_batch(_plan_jobs(plan), plan)
    for index, raw_job in enumerate(raw_jobs, start=1):
        job = _normalize_job(raw_job, plan, index)
        _dedupe_job_id(job, used_safe_ids)
        job = _evaluate_and_submit(
            card,
            run_root,
            job,
            image_settings_ready=image_settings_ready,
            run_command=run_command,
        )
        job = _write_job(card, run_root, job)
        jobs.append(job)
    return {"status": _summarize(jobs), "jobs": jobs, "plan_id": str(plan.get("plan_id") or "")}


def resume_waiting_jobs(
    card_folder: str | Path,
    run_dir: str | Path,
    critic_runner: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    card = Path(card_folder)
    run_root = Path(run_dir)
    jobs = []
    for batch_id, candidates in _completed_candidate_batches(card).items():
        if len(candidates) < 3:
            continue
        existing_selection = _read_selection_job(card, batch_id)
        if existing_selection and existing_selection.get("status") == "completed":
            jobs.append(_write_job(card, run_root, existing_selection))
            continue
        selection = _selection_job_for_candidates(batch_id, candidates)
        if critic_runner is None:
            selection["status"] = "waiting_on_critic"
            selection["reason"] = "critic_vision_not_available"
        else:
            selection = _run_character_reference_selection(card, selection, critic_runner)
        jobs.append(_write_job(card, run_root, selection))
    return {"status": _summarize(jobs), "jobs": jobs}


def _read_selection_job(card: Path, batch_id: str) -> dict[str, Any] | None:
    safe_id = _job_filename_slug(f"{batch_id}-selection")
    path = card / "generated" / "jobs" / f"{safe_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _completed_candidate_batches(card: Path) -> dict[str, list[dict[str, Any]]]:
    batches: dict[str, list[dict[str, Any]]] = {}
    jobs_dir = card / "generated" / "jobs"
    for path in sorted(jobs_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("queue_type") != "character_reference_candidate":
            continue
        if data.get("status") != "completed":
            continue
        batch_id = str(data.get("batch_id") or "").strip()
        if not batch_id:
            continue
        batches.setdefault(batch_id, []).append(data)
    for candidates in batches.values():
        candidates.sort(key=lambda item: str(item.get("job_id") or ""))
    return batches


def _selection_job_for_candidates(batch_id: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    first = candidates[0]
    final_target = str(first.get("final_target_path") or "").strip()
    if not final_target:
        final_target = _character_target_path(first)
    return {
        "schema_version": 1,
        "queue_type": "character_reference_selection",
        "job_id": f"{batch_id}-selection",
        "batch_id": batch_id,
        "character_name": str(first.get("character_name") or first.get("name") or ""),
        "status": "waiting_on_critic",
        "reason": "critic_vision_not_available",
        "final_target_path": final_target,
        "candidates": [_candidate_selection_payload(candidate) for candidate in candidates],
    }


def _candidate_selection_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "job_id": str(candidate.get("job_id") or ""),
        "batch_id": str(candidate.get("batch_id") or ""),
        "character_name": str(candidate.get("character_name") or candidate.get("name") or ""),
    }
    for key in ("target_path", "output_path", "final_target_path", "prompt"):
        if candidate.get(key):
            payload[key] = str(candidate[key])
    return payload


def _run_character_reference_selection(
    card: Path,
    selection: dict[str, Any],
    critic_runner: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "batch_id": selection["batch_id"],
        "candidates": selection["candidates"],
    }
    try:
        critic_report = critic_runner(payload)
    except Exception as exc:
        selection["status"] = "waiting_on_critic"
        selection["reason"] = "critic_runner_failed"
        selection["error"] = str(exc)
        selection["exception_type"] = type(exc).__name__
        return selection
    if not isinstance(critic_report, dict):
        critic_report = {"raw_report": critic_report}
    selection["critic_report"] = critic_report
    winner_id = str(critic_report.get("winner_candidate_id") or "").strip()
    candidates = selection.get("candidates") or []
    winner = next((candidate for candidate in candidates if candidate.get("job_id") == winner_id), None)
    if winner is None:
        selection["status"] = "waiting_on_critic"
        selection["reason"] = "invalid_winner_candidate"
        selection["winner_candidate_id"] = winner_id
        return selection

    final_target = str(critic_report.get("final_target_path") or selection.get("final_target_path") or "").strip()
    if not final_target:
        final_target = _character_target_path(winner)
    normalized_final = _normalize_asset_path(final_target)
    if normalized_final is None:
        selection["status"] = "waiting_on_critic"
        selection["reason"] = "invalid_final_target_path"
        selection["invalid_path"] = final_target
        selection["winner_candidate_id"] = winner_id
        return selection

    final_abs = card / Path(normalized_final)
    winner_source = _candidate_output_path(winner)
    if winner_source is None:
        selection["status"] = "waiting_on_critic"
        selection["reason"] = "invalid_winner_candidate_path"
        selection["winner_candidate_id"] = winner_id
        return selection
    winner_source_abs = card / Path(winner_source)
    winner_already_moved = final_abs.is_file()
    if not winner_source_abs.is_file() and not winner_already_moved:
        selection["status"] = "waiting_on_critic"
        selection["reason"] = "winner_candidate_file_missing"
        selection["winner_candidate_id"] = winner_id
        selection["missing_path"] = winner_source
        return selection

    rejected_moves = []
    for candidate in candidates:
        if candidate.get("job_id") == winner_id:
            continue
        source = _candidate_output_path(candidate)
        if source is None:
            selection["status"] = "waiting_on_critic"
            selection["reason"] = "invalid_rejected_candidate_path"
            selection["winner_candidate_id"] = winner_id
            selection["invalid_candidate_id"] = candidate.get("job_id")
            return selection
        rejected = _rejected_candidate_path(selection["batch_id"], source)
        if rejected is None:
            selection["status"] = "waiting_on_critic"
            selection["reason"] = "invalid_rejected_candidate_path"
            selection["winner_candidate_id"] = winner_id
            selection["invalid_candidate_id"] = candidate.get("job_id")
            return selection
        rejected_moves.append((source, rejected))

    missing_rejected_paths = []
    try:
        final_abs.parent.mkdir(parents=True, exist_ok=True)
        if not winner_already_moved:
            shutil.move(str(winner_source_abs), str(final_abs))
        for source, rejected in rejected_moves:
            source_abs = card / Path(source)
            rejected_abs = card / Path(rejected)
            if not source_abs.exists():
                if not rejected_abs.exists():
                    missing_rejected_paths.append(source)
                continue
            rejected_abs.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_abs), str(rejected_abs))
    except Exception as exc:
        selection["status"] = "waiting_on_critic"
        selection["reason"] = "candidate_file_move_failed"
        selection["error"] = str(exc)
        selection["exception_type"] = type(exc).__name__
        selection["winner_candidate_id"] = winner_id
        return selection

    selection["status"] = "completed"
    selection.pop("reason", None)
    selection["winner_candidate_id"] = winner_id
    selection["final_target_path"] = normalized_final
    selection["rejected_paths"] = [rejected for _, rejected in rejected_moves]
    if missing_rejected_paths:
        selection["missing_rejected_paths"] = missing_rejected_paths
    return selection


def _candidate_output_path(candidate: dict[str, Any]) -> str | None:
    normalized = _normalize_asset_path(candidate.get("output_path") or candidate.get("target_path") or "")
    return normalized if normalized else None


def _rejected_candidate_path(batch_id: str, source_path: str) -> str | None:
    source_name = Path(source_path).name
    if not source_name:
        return None
    return _normalize_asset_path(f"generated/tmp/{batch_id}/rejected/{source_name}")


def _plan_jobs(plan: dict[str, Any]) -> list[Any]:
    raw_jobs = plan.get("jobs")
    if raw_jobs is None:
        return []
    if isinstance(raw_jobs, list):
        return raw_jobs
    return [
        {
            "queue_type": "invalid_jobs_shape",
            "job_id": f"{str(plan.get('plan_id') or 'asset-plan')}-invalid_jobs_shape-1",
            "status": "failed",
            "reason": "invalid_jobs_shape",
            "raw_jobs_type": type(raw_jobs).__name__,
        }
    ]


def _expand_character_reference_candidate_batch(raw_jobs: list[Any], plan: dict[str, Any]) -> list[Any]:
    if not _needs_style_reference_candidate_batch(plan):
        return raw_jobs

    expanded = []
    first_character_reference_seen = False
    for raw_job in raw_jobs:
        if not isinstance(raw_job, dict) or _raw_queue_type(raw_job) != "character_reference":
            expanded.append(raw_job)
            continue

        if _is_waiting_status(raw_job.get("status")):
            expanded.append(raw_job)
            continue

        if not first_character_reference_seen:
            first_character_reference_seen = True
            expanded.extend(_candidate_jobs_for_first_character_reference(raw_job))
            continue

        blocked = dict(raw_job)
        blocked["status"] = "waiting_on_style_reference"
        blocked["reason"] = "style_reference_not_selected"
        expanded.append(blocked)

    return expanded


def _needs_style_reference_candidate_batch(plan: dict[str, Any]) -> bool:
    style_state = plan.get("style_state")
    if not isinstance(style_state, dict):
        return False
    if style_state.get("has_style_reference") is not False:
        return False
    return not _style_reference_paths(plan)


def _style_reference_paths(plan: dict[str, Any]) -> list[str]:
    paths = []
    style_state = plan.get("style_state")
    if isinstance(style_state, dict):
        paths.extend(style_state.get("style_reference_paths") or [])
    paths.extend(plan.get("style_reference_paths") or [])
    return [str(path) for path in paths if str(path or "").strip()]


def _candidate_jobs_for_first_character_reference(raw_job: dict[str, Any]) -> list[dict[str, Any]]:
    first_job_id = str(raw_job.get("job_id") or raw_job.get("id") or "character-reference")
    batch_id = f"{_path_component_slug(first_job_id)}-candidates"
    candidates = []
    for index in range(1, 4):
        candidate = dict(raw_job)
        candidate["queue_type"] = "character_reference_candidate"
        candidate.pop("kind", None)
        candidate["job_id"] = f"{first_job_id}-candidate-{index}"
        candidate["batch_id"] = batch_id
        candidate["target_path"] = f"generated/tmp/{batch_id}/candidate-{index}.png"
        candidate["display_policy"] = "hidden_reference"
        candidates.append(candidate)
    return candidates


def _path_component_slug(text: str) -> str:
    keep = []
    for ch in text.lower():
        if ch.isalnum():
            keep.append(ch)
        else:
            keep.append("-")
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:48] or "image"


def _raw_queue_type(raw_job: dict[str, Any]) -> str:
    return str(raw_job.get("queue_type") or raw_job.get("kind") or "")


def _is_waiting_status(status: Any) -> bool:
    return str(status or "").startswith("waiting_on_")


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
    if str(job.get("status") or "") == "failed":
        return job
    if not queue_type:
        job["status"] = "failed"
        job["reason"] = "invalid_queue_type"
        return job
    if queue_type not in SUPPORTED_QUEUE_TYPES:
        job["status"] = "failed"
        job["reason"] = "unsupported_queue_type"
        return job
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
    while _job_filename_slug(candidate) in used_safe_ids:
        direct_candidate = f"{original}-{suffix}"
        candidate = direct_candidate
        if _job_filename_slug(candidate) in used_safe_ids:
            suffix_text = f"-{suffix}"
            base_slug = _job_filename_slug(original)
            prefix_limit = max(1, 48 - len(suffix_text))
            candidate = f"{base_slug[:prefix_limit].rstrip('-') or 'image'}{suffix_text}"
        suffix += 1
    if candidate != original:
        job["source_job_id"] = original
        job["job_id"] = candidate
    used_safe_ids.add(_job_filename_slug(candidate))


def _evaluate_and_submit(
    card: Path,
    run_dir: Path,
    job: dict[str, Any],
    *,
    image_settings_ready: bool,
    run_command: Callable[..., Any] | None,
) -> dict[str, Any]:
    if job.get("status") == "failed":
        return job
    waiting = _is_waiting_status(job.get("status"))
    invalid = _prepare_asset_paths(card, job)
    if invalid:
        job["status"] = "failed"
        job["reason"] = "invalid_asset_path"
        job["invalid_path"] = invalid
        return job
    if job.get("queue_type") == "asset_rename":
        return _apply_asset_rename(card, run_dir, job)
    if waiting:
        job.pop("_required_missing_references", None)
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
    non_image_wait = NON_IMAGE_QUEUE_WAIT_STATES.get(str(job.get("queue_type") or ""))
    if non_image_wait:
        job["status"], job["reason"] = non_image_wait
        return job
    if not image_settings_ready or run_command is None:
        job["status"] = "deferred"
        job["reason"] = "asset_worker_not_configured"
        return job
    try:
        job["command"] = _run_image_job(card, job, run_command)
    except Exception as exc:
        job["status"] = "deferred"
        job["reason"] = "asset_worker_start_failed"
        job["error"] = str(exc)
        job["exception_type"] = type(exc).__name__
        return job
    if job["command"]["returncode"] == 0:
        job["status"] = "queued"
        job.pop("reason", None)
    else:
        _apply_worker_failure(job)
    return job


def _apply_asset_rename(card: Path, run_dir: Path, job: dict[str, Any]) -> dict[str, Any]:
    rename = _normalized_asset_rename(job)
    if rename.get("error") == "invalid_asset_path":
        job["status"] = "failed"
        job["reason"] = "invalid_asset_path"
        job["invalid_path"] = rename["invalid_path"]
        return job

    from_path = str(rename["from_path"])
    to_path = str(rename["to_path"])
    source = card / Path(from_path)
    target = card / Path(to_path)
    if not source.exists():
        job["status"] = "failed"
        job["reason"] = "source_asset_missing"
        job["missing_path"] = from_path
        return job

    replacements = rename["replacements"]
    conflict = _preflight_asset_rename(card, from_path, to_path, replacements)
    if conflict:
        job["status"] = "failed"
        job["reason"] = conflict["reason"]
        if conflict.get("conflict_path"):
            job["conflict_path"] = conflict["conflict_path"]
        return job

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.move(str(source), str(target))
        _apply_internal_renamed_files(card, from_path, to_path, replacements)
        _rewrite_asset_reference_files(card, run_dir, replacements)
    except Exception as exc:
        job["status"] = "failed"
        job["reason"] = "asset_rename_failed"
        job["error"] = str(exc)
        job["exception_type"] = type(exc).__name__
        return job

    job["from_path"] = from_path
    job["to_path"] = to_path
    job["applied_replacements"] = replacements
    job["status"] = "completed"
    job.pop("reason", None)
    return job


def _preflight_asset_rename(
    card: Path,
    from_path: str,
    to_path: str,
    replacements: list[dict[str, str]],
) -> dict[str, str]:
    source = card / Path(from_path)
    target = card / Path(to_path)
    if target.exists() and source.resolve() != target.resolve():
        return {"reason": "target_asset_exists", "conflict_path": to_path}
    for replacement in replacements:
        old = replacement["from"]
        new = replacement["to"]
        if old == from_path or new == to_path:
            continue
        moved_old = _path_after_parent_rename(old, from_path, to_path)
        if moved_old is None or moved_old == new:
            continue
        old_abs = card / Path(old)
        new_abs = card / Path(new)
        if old_abs.exists() and new_abs.exists():
            return {"reason": "target_asset_exists", "conflict_path": new}
    return {}


def _normalized_asset_rename(job: dict[str, Any]) -> dict[str, Any]:
    raw_from = job.get("from_path") or job.get("source_path") or ""
    raw_to = job.get("to_path") or job.get("target_path") or ""
    from_path = _normalize_asset_path(raw_from)
    if from_path is None:
        return {"error": "invalid_asset_path", "invalid_path": str(raw_from)}
    if not from_path:
        return {"error": "invalid_asset_path", "invalid_path": str(raw_from)}
    to_path = _normalize_asset_path(raw_to)
    if to_path is None:
        return {"error": "invalid_asset_path", "invalid_path": str(raw_to)}
    if not to_path:
        return {"error": "invalid_asset_path", "invalid_path": str(raw_to)}

    replacements = []
    for item in job.get("replacements") or []:
        if not isinstance(item, dict):
            return {"error": "invalid_asset_path", "invalid_path": str(item)}
        raw_old = item.get("from")
        raw_new = item.get("to")
        old = _normalize_asset_path(raw_old)
        if old is None:
            return {"error": "invalid_asset_path", "invalid_path": str(raw_old)}
        if not old:
            return {"error": "invalid_asset_path", "invalid_path": str(raw_old)}
        new = _normalize_asset_path(raw_new)
        if new is None:
            return {"error": "invalid_asset_path", "invalid_path": str(raw_new)}
        if not new:
            return {"error": "invalid_asset_path", "invalid_path": str(raw_new)}
        replacements.append({"from": old, "to": new})
    if not replacements and from_path and to_path:
        replacements.append({"from": from_path, "to": to_path})
    return {"from_path": from_path, "to_path": to_path, "replacements": replacements}


def _apply_internal_renamed_files(
    card: Path,
    from_path: str,
    to_path: str,
    replacements: list[dict[str, str]],
) -> None:
    for replacement in replacements:
        old = replacement["from"]
        new = replacement["to"]
        if old == from_path or new == to_path:
            continue
        moved_old = _path_after_parent_rename(old, from_path, to_path)
        if moved_old is None or moved_old == new:
            continue
        moved_old_abs = card / Path(moved_old)
        new_abs = card / Path(new)
        if not moved_old_abs.exists() or new_abs.exists():
            continue
        new_abs.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(moved_old_abs), str(new_abs))


def _path_after_parent_rename(path: str, old_parent: str, new_parent: str) -> str | None:
    if path == old_parent:
        return new_parent
    prefix = f"{old_parent}/"
    if not path.startswith(prefix):
        return None
    return f"{new_parent}/{path[len(prefix):]}"


def _rewrite_asset_reference_files(card: Path, run_dir: Path, replacements: list[dict[str, str]]) -> None:
    files = [
        card / ".card_assets.json",
        card / "ui_manifest.json",
    ]
    files.extend(sorted((card / "generated" / "jobs").glob("*.json")))
    files.extend(sorted((run_dir / "artifacts" / "assets_ui" / "jobs").glob("*.json")))
    seen: set[Path] = set()
    for path in files:
        if path in seen:
            continue
        seen.add(path)
        _rewrite_json_file_paths(path, replacements)


def _rewrite_json_file_paths(path: Path, replacements: list[dict[str, str]]) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return
    rewritten = _replace_asset_path_strings(data, replacements)
    agent_run.write_json(path, rewritten)


def _replace_asset_path_strings(value: Any, replacements: list[dict[str, str]]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_asset_path_strings(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_asset_path_strings(item, replacements) for item in value]
    if isinstance(value, str):
        for replacement in replacements:
            if value == replacement["from"]:
                return replacement["to"]
        return value
    return value


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
    if job.get("reference_policy") == "required":
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
    if job.get("round_id"):
        command.extend(["--round-id", str(job["round_id"])])
    for character in _worker_characters(job):
        command.extend(["--character", character])
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


def _worker_characters(job: dict[str, Any]) -> list[str]:
    queue_type = str(job.get("queue_type") or "")
    if queue_type == "scene_illustration":
        return [str(name) for name in job.get("characters") or [] if str(name)]
    if queue_type in {"character_reference", "character_reference_candidate"}:
        name = str(job.get("character_name") or job.get("name") or "")
        return [name] if name else []
    return []


def _apply_worker_failure(job: dict[str, Any]) -> None:
    command = job.get("command") or {}
    details = _parse_worker_stdout(command.get("stdout"))
    reason = str(details.get("reason") or "")
    status = str(details.get("status") or "")
    error = details.get("error")
    semantic_reason = reason or str(error or "")
    if status in {"deferred", "failed"}:
        job["status"] = "failed" if semantic_reason == "invalid_asset_path" else "deferred"
        if semantic_reason:
            job["reason"] = semantic_reason
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


def _write_job(card: Path, run_dir: Path, job: dict[str, Any]) -> dict[str, Any]:
    safe_id = _job_filename_slug(str(job.get("job_id") or ""))
    job_path = card / "generated" / "jobs" / f"{safe_id}.json"
    final_job = _merge_child_written_job(job_path, job)
    agent_run.write_json(job_path, final_job)
    agent_run.write_json(run_dir / "artifacts" / "assets_ui" / "jobs" / f"{safe_id}.json", final_job)
    return final_job


def _merge_child_written_job(job_path: Path, job: dict[str, Any]) -> dict[str, Any]:
    if str(job.get("status") or "") != "queued":
        return job
    try:
        existing = json.loads(job_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return job
    if not isinstance(existing, dict):
        return job
    if str(existing.get("status") or "") not in CHILD_WRITTEN_JOB_STATUSES:
        return job
    merged = dict(job)
    merged.update(existing)
    return merged


def _summarize(jobs: list[dict[str, Any]]) -> str:
    statuses = {str(job.get("status") or "") for job in jobs}
    if "failed" in statuses:
        return "failed"
    if "waiting_on_references" in statuses:
        return "waiting_on_references"
    if "waiting_on_critic" in statuses:
        return "waiting_on_critic"
    if any(status.startswith("waiting_on") for status in statuses):
        return sorted(statuses)[0]
    if "queued" in statuses:
        return "queued"
    if "deferred" in statuses:
        return "deferred"
    return "completed" if jobs else "not_required"
