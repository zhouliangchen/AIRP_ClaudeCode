"""Assets-UI runtime planning helpers for deterministic job materialization."""

from __future__ import annotations

import json
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable

import agent_run
import llm_settings
import postprocess_outputs


DEFAULT_UI_MANIFEST = {"version": 1, "mode": "autonomous", "generated_assets": []}


class InvalidAssetPathError(ValueError):
    """Raised when a planned asset path is not card-relative and safe."""

    def __init__(self, path_text: str):
        self.path_text = path_text
        super().__init__(f"invalid asset path: {path_text}")


def process_assets_task(
    card_folder: str | Path,
    run_dir: str | Path,
    intent: dict[str, Any],
    *,
    phase: str,
    run_command: Callable[..., Any] | None = None,
    planner: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    card = Path(card_folder)
    run_root = Path(run_dir)
    payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
    postprocess_contract_update = postprocess_outputs.apply_ui_schema_contract_update(card, payload)
    context = _planner_context(card, run_root, intent, payload, phase)
    plan = planner(context) if planner is not None else _default_plan(context)
    if not isinstance(plan, dict):
        plan = {}

    asset_requirement_update = _apply_asset_requirement_update(card, payload, plan)
    settings = llm_settings.read_effective_settings()
    jobs: list[dict[str, Any]] = []
    resumed_jobs: list[dict[str, Any]] = []

    for job in _as_list(plan.get("character_reference_jobs")):
        materialized = _materialize_job(
            card,
            run_root,
            job,
            lambda current_job: _materialize_character_reference_job(
                card,
                run_root,
                settings,
                current_job,
                run_command,
            ),
            default_kind="character_reference",
        )
        jobs.append(materialized)

    for job in _as_list(plan.get("scene_jobs")):
        materialized = _materialize_job(
            card,
            run_root,
            job,
            lambda current_job: _materialize_scene_job(
                card,
                run_root,
                settings,
                current_job,
                run_command,
            ),
            default_kind="scene_illustration",
        )
        jobs.append(materialized)

    resumed_jobs = _resume_waiting_scene_jobs(card, run_root, settings, run_command)
    summary_status = _summarize_status(jobs + resumed_jobs)
    outputs = {
        "status": summary_status,
        "phase": phase,
        "postprocess_contract_update": postprocess_contract_update,
        "asset_requirement_update": asset_requirement_update,
        "jobs": jobs,
        "resumed_jobs": resumed_jobs,
        "plan": plan,
    }
    audit = {
        "schema_version": 1,
        "intent_id": _text(intent.get("id")),
        "intent_type": _text(intent.get("type")) or "assets_task",
        "phase": phase,
        "payload": payload,
        "plan": plan,
        "outputs": outputs,
    }
    audit_path = run_root / "artifacts" / "assets_ui" / f"{agent_run.safe_name(_text(intent.get('id')) or 'assets-task')}.json"
    agent_run.write_json(audit_path, audit)
    return {"status": "completed", "outputs": outputs}


def process_persistent_requirements(
    card_folder: str | Path,
    run_dir: str | Path,
    *,
    phase: str,
    run_command: Callable[..., Any] | None = None,
    planner: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    card = Path(card_folder)
    manifest = _read_json(card / "ui_manifest.json", {})
    requirements = manifest.get("asset_requirements") if isinstance(manifest, dict) else {}
    scene_requirement = (
        requirements.get("scene_illustration_each_round")
        if isinstance(requirements, dict)
        else None
    )
    if not isinstance(scene_requirement, dict) or scene_requirement.get("enabled") is not True:
        return {"status": "not_required", "jobs": []}

    intent = {
        "id": f"persistent-assets-{Path(run_dir).name}",
        "type": "assets_task",
        "payload": {
            "kind": "scene_illustration",
            "target": "scene_illustration",
            "prompt": _text(scene_requirement.get("prompt")) or _default_persistent_prompt(card, Path(run_dir)),
            "asset_requirement": {
                "scene_illustration_each_round": True,
                "reason": _text(scene_requirement.get("reason")),
            },
        },
    }
    return process_assets_task(
        card,
        run_dir,
        intent,
        phase=phase,
        run_command=run_command,
        planner=planner,
    )


def _planner_context(
    card: Path,
    run_dir: Path,
    intent: dict[str, Any],
    payload: dict[str, Any],
    phase: str,
) -> dict[str, Any]:
    return {
        "card_path": card,
        "run_dir": run_dir,
        "phase": phase,
        "intent": intent,
        "payload": payload,
        "ui_manifest": _load_ui_manifest(card),
        "card_assets": _read_json(card / ".card_assets.json", {"images": []}),
        "story_output": _load_run_json(run_dir, "story.output.json"),
        "critic_report": _load_run_json(run_dir, "critic.report.json"),
        "character_profiles": _load_character_profiles(card, payload),
    }


def _default_plan(context: dict[str, Any]) -> dict[str, Any]:
    payload = context.get("payload") if isinstance(context.get("payload"), dict) else {}
    card = Path(context["card_path"])
    run_dir = Path(context["run_dir"])
    job_id = _text(payload.get("job_id")) or f"scene-{run_dir.name}"
    characters = _safe_character_names(payload.get("characters"))
    reference_candidates = _as_string_list(payload.get("reference_candidates"))
    using_default_references = False
    if not reference_candidates and characters:
        reference_candidates = [f"characters/{name}/{name}.png" for name in characters]
        using_default_references = True
    reference_policy = _text(payload.get("reference_policy")) or "optional"
    plan: dict[str, Any] = {
        "schema_version": 1,
        "scene_jobs": [
            {
                "job_id": job_id,
                "kind": _text(payload.get("kind")) or "scene_illustration",
                "target": _text(payload.get("target")) or "scene_illustration",
                "prompt": _text(payload.get("prompt")) or _default_persistent_prompt(card, run_dir),
                "characters": characters,
                "reference_policy": reference_policy,
                "reference_candidates": reference_candidates,
            }
        ],
    }
    if reference_policy == "required" and using_default_references:
        reference_jobs = []
        profiles = context.get("character_profiles") if isinstance(context.get("character_profiles"), dict) else {}
        for name in characters:
            target_path = f"characters/{name}/{name}.png"
            if (card / Path(target_path)).exists():
                continue
            profile = _text(profiles.get(name))
            reference_jobs.append(
                {
                    "job_id": f"character-{agent_run.safe_name(name)}-reference",
                    "character_name": name,
                    "target_path": target_path,
                    "prompt": profile or f"character reference portrait for {name}",
                }
            )
        if reference_jobs:
            plan["character_reference_jobs"] = reference_jobs
    requirement = _required_scene_requirement(payload) or _required_scene_requirement({})
    if requirement:
        plan["asset_requirement_update"] = requirement
    return plan


def _materialize_character_reference_job(
    card: Path,
    run_dir: Path,
    settings: dict[str, Any],
    job: dict[str, Any],
    run_command: Callable[..., Any] | None,
) -> dict[str, Any]:
    job_id = _text(job.get("job_id")) or "character-reference"
    target_path = _normalize_relative_path(job.get("target_path"))
    payload = {
        "schema_version": 1,
        "job_id": job_id,
        "kind": "character_reference",
        "character_name": _text(job.get("character_name")),
        "target_path": target_path,
        "prompt": _text(job.get("prompt")),
        "status": "deferred",
        "reason": "asset_worker_not_configured",
    }
    if _image_settings_ready(settings) and run_command is not None:
        payload["command"] = _run_job_command(
            card,
            job_id,
            payload["prompt"],
            "character_reference",
            target_path,
            [],
            run_command,
        )
        if payload["command"]["returncode"] == 0:
            payload["status"] = "queued"
            payload.pop("reason", None)
        else:
            payload["reason"] = "asset_worker_start_failed"
    _write_job(card, run_dir, job_id, payload)
    return payload


def _materialize_scene_job(
    card: Path,
    run_dir: Path,
    settings: dict[str, Any],
    job: dict[str, Any],
    run_command: Callable[..., Any] | None,
) -> dict[str, Any]:
    job_id = _text(job.get("job_id")) or f"scene-{run_dir.name}"
    references = _normalize_reference_list(job.get("reference_candidates"))
    existing_references: list[str] = []
    missing_references: list[str] = []
    for item in references:
        if (card / Path(item)).exists():
            existing_references.append(item)
        else:
            missing_references.append(item)

    payload = {
        "schema_version": 1,
        "job_id": job_id,
        "kind": _text(job.get("kind")) or "scene_illustration",
        "target": _text(job.get("target")) or "scene_illustration",
        "prompt": _text(job.get("prompt")),
        "characters": _safe_character_names(job.get("characters")),
        "reference_policy": _text(job.get("reference_policy")) or "optional",
        "reference_candidates": references,
        "resolved_references": existing_references,
        "status": "deferred",
        "reason": "asset_worker_not_configured",
    }

    if payload["reference_policy"] == "required" and missing_references:
        payload["status"] = "waiting_on_references"
        payload["reason"] = "missing_character_reference"
        payload["missing_references"] = missing_references
    elif _image_settings_ready(settings) and run_command is not None:
        worker_references = existing_references if payload["reference_policy"] == "required" else []
        payload["command"] = _run_job_command(
            card,
            job_id,
            payload["prompt"],
            payload["kind"],
            payload["target"],
            worker_references,
            run_command,
        )
        _apply_worker_result(payload, payload["command"])
    _write_job(card, run_dir, job_id, payload)
    return payload


def _apply_worker_result(payload: dict[str, Any], command: dict[str, Any]) -> None:
    if command["returncode"] == 0:
        payload["status"] = "queued"
        payload.pop("reason", None)
        return

    child = _parse_worker_stdout(command.get("stdout"))
    child_status = _text(child.get("status"))
    child_reason = _text(child.get("reason")) or _text(child.get("error"))
    if child_status in {"deferred", "failed"}:
        payload["status"] = child_status
        payload["reason"] = child_reason or child_status
        if isinstance(child.get("references"), list):
            payload["worker_references"] = child.get("references")
        return
    payload["reason"] = "asset_worker_start_failed"


def _parse_worker_stdout(stdout: Any) -> dict[str, Any]:
    text = _text(stdout)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _run_job_command(
    card: Path,
    job_id: str,
    prompt: str,
    kind: str,
    target: str,
    references: list[str],
    run_command: Callable[..., Any],
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "image_generate.py"),
        str(card),
        "--prompt",
        prompt,
        "--kind",
        kind,
        "--target",
        target,
        "--job-id",
        job_id,
    ]
    for reference in references:
        command.extend(["--reference", reference])
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
        "returncode": getattr(result, "returncode", 1),
        "stdout": _text(getattr(result, "stdout", "")),
        "stderr": _text(getattr(result, "stderr", "")),
    }


def _resume_waiting_scene_jobs(
    card: Path,
    run_dir: Path,
    settings: dict[str, Any],
    run_command: Callable[..., Any] | None,
) -> list[dict[str, Any]]:
    jobs_dir = card / "generated" / "jobs"
    if not jobs_dir.exists():
        return []
    resumed: list[dict[str, Any]] = []
    for job_path in sorted(jobs_dir.glob("*.json")):
        job = _read_json(job_path, {})
        if not isinstance(job, dict) or job.get("status") != "waiting_on_references":
            continue
        try:
            missing = _normalize_reference_list(job.get("missing_references"))
            references = _normalize_reference_list(job.get("reference_candidates"))
        except InvalidAssetPathError as exc:
            failed = dict(job)
            failed["status"] = "failed"
            failed["reason"] = "invalid_asset_path"
            failed["invalid_path"] = exc.path_text
            _write_job(card, run_dir, _text(failed.get("job_id")) or job_path.stem, failed)
            resumed.append(failed)
            continue
        if not missing or any(not (card / Path(item)).exists() for item in missing):
            continue
        resume_job = dict(job)
        resume_job["reference_candidates"] = references or missing
        resume_job.pop("missing_references", None)
        resume_job.pop("reason", None)
        resumed.append(_materialize_scene_job(card, run_dir, settings, resume_job, run_command))
    return resumed


def _apply_asset_requirement_update(card: Path, payload: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    requested = (
        plan.get("asset_requirement_update")
        if isinstance(plan.get("asset_requirement_update"), dict)
        else {}
    )
    if requested.get("scene_illustration_each_round") is not True:
        payload_requirement = payload.get("asset_requirement")
        if isinstance(payload_requirement, dict) and payload_requirement.get("scene_illustration_each_round") is True:
            requested = dict(payload_requirement)
    if requested.get("scene_illustration_each_round") is not True:
        return {"applied": False}

    manifest = _load_ui_manifest(card)
    requirements = manifest.setdefault("asset_requirements", {})
    requirements["scene_illustration_each_round"] = {
        "enabled": True,
        "reason": _text(requested.get("reason")),
        "prompt": _text(requested.get("prompt")),
    }
    agent_run.write_json(card / "ui_manifest.json", manifest)
    return {"applied": True, "scene_illustration_each_round": True, "reason": _text(requested.get("reason"))}


def _write_job(card: Path, run_dir: Path, job_id: str, payload: dict[str, Any]) -> None:
    safe_job_id = agent_run.safe_name(job_id)
    agent_run.write_json(card / "generated" / "jobs" / f"{safe_job_id}.json", payload)
    agent_run.write_json(run_dir / "artifacts" / "assets_ui" / "jobs" / f"{safe_job_id}.json", payload)


def _load_ui_manifest(card: Path) -> dict[str, Any]:
    manifest = _read_json(card / "ui_manifest.json", {})
    if not isinstance(manifest, dict) or not manifest:
        return json.loads(json.dumps(DEFAULT_UI_MANIFEST))
    return manifest


def _load_run_json(run_dir: Path, name: str) -> dict[str, Any]:
    for candidate in (run_dir / "artifacts" / name, run_dir / name):
        data = _read_json(candidate, {})
        if isinstance(data, dict) and data:
            return data
    return {}


def _load_character_profiles(card: Path, payload: dict[str, Any]) -> dict[str, str]:
    names = _safe_character_names(payload.get("characters"))
    profiles: dict[str, str] = {}
    for name in names:
        profile_path = card / "memory" / "characters" / name / "profile.md"
        if profile_path.exists():
            profiles[name] = profile_path.read_text(encoding="utf-8")
    return profiles


def _normalize_reference_list(value: Any) -> list[str]:
    normalized: list[str] = []
    for item in _as_list(value):
        path = _normalize_relative_path(item)
        if path:
            normalized.append(path)
    return normalized


def _normalize_relative_path(value: Any) -> str:
    raw_text = _text(value)
    if not raw_text:
        return ""
    text = raw_text.replace("\\", "/")
    windows_path = PureWindowsPath(raw_text)
    posix_path = PurePosixPath(text)
    if (
        text.startswith("/")
        or raw_text.startswith("\\")
        or windows_path.drive
        or windows_path.root
        or windows_path.anchor
        or posix_path.root
        or posix_path.anchor
    ):
        raise InvalidAssetPathError(raw_text)
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise InvalidAssetPathError(raw_text)
    return "/".join(parts)


def _image_settings_ready(settings: dict[str, Any]) -> bool:
    image = settings.get("image_generation") if isinstance(settings, dict) else {}
    if not isinstance(image, dict):
        return False
    return all(_text(image.get(key)) for key in ("base_url", "api_key", "model"))


def _default_persistent_prompt(card: Path, run_dir: Path) -> str:
    story = _load_run_json(run_dir, "story.output.json")
    content = _text(story.get("content"))
    if content:
        return content
    critic = _load_run_json(run_dir, "critic.report.json")
    summary = _text(critic.get("summary"))
    if summary:
        return summary
    return f"{card.name} current round scene illustration"


def _summarize_status(jobs: list[dict[str, Any]]) -> str:
    statuses = [_text(item.get("status")) for item in jobs if isinstance(item, dict)]
    if not statuses:
        return "not_required"
    if "waiting_on_references" in statuses:
        return "waiting_on_references"
    if "failed" in statuses:
        return "failed"
    if "queued" in statuses:
        return "queued"
    if "deferred" in statuses:
        return "deferred"
    return statuses[0] or "completed"


def _required_scene_requirement(payload: dict[str, Any]) -> dict[str, Any]:
    requirement = payload.get("asset_requirement")
    if isinstance(requirement, dict) and requirement.get("scene_illustration_each_round") is True:
        return {
            "scene_illustration_each_round": True,
            "reason": _text(requirement.get("reason")),
            "prompt": _text(requirement.get("prompt")),
        }
    return {}


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_string_list(value: Any) -> list[str]:
    items: list[str] = []
    for item in _as_list(value):
        text = _text(item)
        if text:
            items.append(text)
    return items


def _safe_character_names(value: Any) -> list[str]:
    names: list[str] = []
    for item in _as_list(value):
        name = _safe_character_name(item)
        if name and name not in names:
            names.append(name)
    return names


def _safe_character_name(value: Any) -> str:
    name = _text(value)
    if not name:
        return ""
    normalized = name.replace("\\", "/")
    windows_path = PureWindowsPath(name)
    posix_path = PurePosixPath(normalized)
    if (
        "/" in normalized
        or name.startswith("\\")
        or normalized.startswith("/")
        or ":" in name
        or windows_path.drive
        or windows_path.root
        or windows_path.anchor
        or posix_path.root
        or posix_path.anchor
    ):
        return ""
    if name in {".", ".."}:
        return ""
    return name


def _materialize_job(
    card: Path,
    run_dir: Path,
    job: Any,
    materialize: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    default_kind: str,
) -> dict[str, Any]:
    current_job = job if isinstance(job, dict) else {}
    try:
        return materialize(current_job)
    except InvalidAssetPathError as exc:
        failed = {
            "schema_version": 1,
            "job_id": _text(current_job.get("job_id")) or default_kind,
            "kind": _text(current_job.get("kind")) or default_kind,
            "target": _text(current_job.get("target")),
            "prompt": _text(current_job.get("prompt")),
            "status": "failed",
            "reason": "invalid_asset_path",
            "invalid_path": exc.path_text,
        }
        _write_job(card, run_dir, failed["job_id"], failed)
        return failed
