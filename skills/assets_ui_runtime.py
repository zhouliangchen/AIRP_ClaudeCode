"""Assets-UI runtime planning helpers for deterministic job materialization."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import agent_run
import llm_settings
import postprocess_outputs


DEFAULT_UI_MANIFEST = {"version": 1, "mode": "autonomous", "generated_assets": []}


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

    for job in _as_list(plan.get("character_reference_jobs")):
        materialized = _materialize_character_reference_job(card, run_root, settings, job, run_command)
        jobs.append(materialized)

    for job in _as_list(plan.get("scene_jobs")):
        materialized = _materialize_scene_job(card, run_root, settings, job, run_command)
        jobs.append(materialized)

    summary_status = _summarize_status(jobs)
    outputs = {
        "status": summary_status,
        "phase": phase,
        "postprocess_contract_update": postprocess_contract_update,
        "asset_requirement_update": asset_requirement_update,
        "jobs": jobs,
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
    run_dir = Path(context["run_dir"])
    job_id = _text(payload.get("job_id")) or f"scene-{run_dir.name}"
    plan: dict[str, Any] = {
        "schema_version": 1,
        "scene_jobs": [
            {
                "job_id": job_id,
                "kind": _text(payload.get("kind")) or "scene_illustration",
                "target": _text(payload.get("target")) or "scene_illustration",
                "prompt": _text(payload.get("prompt")) or _default_persistent_prompt(Path(context["card_path"]), run_dir),
                "characters": _as_string_list(payload.get("characters")),
                "reference_policy": _text(payload.get("reference_policy")) or "optional",
                "reference_candidates": _normalize_reference_list(payload.get("reference_candidates")),
            }
        ],
    }
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
        "characters": _as_string_list(job.get("characters")),
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
        payload["command"] = _run_job_command(
            card,
            job_id,
            payload["prompt"],
            payload["kind"],
            payload["target"],
            existing_references,
            run_command,
        )
        if payload["command"]["returncode"] == 0:
            payload["status"] = "queued"
            payload.pop("reason", None)
        else:
            payload["reason"] = "asset_worker_start_failed"
    _write_job(card, run_dir, job_id, payload)
    return payload


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
    names = _as_string_list(payload.get("characters"))
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
    text = _text(value).replace("\\", "/")
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute():
        raise ValueError(f"absolute paths are not allowed: {text}")
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError(f"parent traversal is not allowed: {text}")
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
