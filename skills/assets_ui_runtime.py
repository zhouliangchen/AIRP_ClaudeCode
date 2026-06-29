"""Assets-UI runtime planning helpers for deterministic job materialization."""

from __future__ import annotations

import json
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable

import agent_run
import llm_runner
import llm_settings
import model_debug
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
    runtime_settings: dict[str, Any] | None = None,
    run_command: Callable[..., Any] | None = None,
    planner: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    card = Path(card_folder)
    run_root = Path(run_dir)
    payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
    postprocess_contract_update = postprocess_outputs.apply_ui_schema_contract_update(card, payload)
    context = _planner_context(card, run_root, intent, payload, phase)
    plan = (
        _run_planner_with_debug(
            model_debug.logger_from_settings(card, run_root.name, runtime_settings),
            planner,
            context,
        )
        if planner is not None
        else _default_plan(context)
    )
    if not isinstance(plan, dict):
        plan = {}
    plan = dict(plan)
    plan["scene_jobs"] = _scene_jobs_with_payload_defaults(plan.get("scene_jobs"), payload, card, context)

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
    runtime_settings: dict[str, Any] | None = None,
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
            "characters": _as_list(scene_requirement.get("characters"))
            or _infer_current_scene_characters(card, Path(run_dir)),
            "character_appearances": _as_list(scene_requirement.get("character_appearances")),
            "reference_policy": _text(scene_requirement.get("reference_policy")),
            "art_style": _text(scene_requirement.get("art_style")),
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
        runtime_settings=runtime_settings,
        run_command=run_command,
        planner=planner,
    )


def _run_planner_with_debug(
    logger: model_debug.ModelDebugLogger | None,
    planner: Callable[[dict[str, Any]], dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, Any]:
    if logger is None:
        return planner(context)

    started = model_debug.utc_now()
    prompt = _debug_json(
        {
            "agent": "assets-ui",
            "task": "plan image asset jobs from runtime context",
            "context": context,
        }
    )
    stdout = ""
    error = ""
    exception_type = ""
    returncode: int | None = None
    try:
        plan = planner(context)
        stdout = _debug_json(plan)
        returncode = 0
        return plan
    except Exception as exc:
        error = str(exc)
        exception_type = exc.__class__.__name__
        raise
    finally:
        ended = model_debug.utc_now()
        try:
            logger.write_call(
                agent_key="assets-ui",
                cwd=str(context.get("run_dir") or ""),
                prompt=prompt,
                stdout=stdout,
                stderr="",
                returncode=returncode,
                started_at=model_debug.isoformat(started),
                ended_at=model_debug.isoformat(ended),
                duration_ms=model_debug.duration_ms(started, ended),
                error=error,
                exception_type=exception_type,
                api_metadata=_planner_api_metadata(stdout),
            )
        except Exception:
            if not exception_type:
                raise


def _planner_api_metadata(stdout: str) -> dict[str, Any]:
    try:
        last_result = llm_runner.get_last_result()
    except Exception:
        last_result = None
    if not isinstance(last_result, dict):
        return {}
    metadata = {
        key: last_result[key]
        for key in ("provider", "model", "status", "usage", "raw_response")
        if key in last_result
    }
    preview = str(last_result.get("text") or stdout or "").strip()
    if preview:
        metadata["response_preview"] = preview[:500]
    return metadata


def _debug_json(value: Any) -> str:
    return json.dumps(_debug_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True)


def _debug_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _debug_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_debug_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_debug_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted((_debug_jsonable(item) for item in value), key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


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
    action = _text(payload.get("action") or payload.get("operation")).lower()
    if action in {"modify", "delete"}:
        update = dict(payload)
        update["action"] = action
        update.setdefault("asset_requirement_key", "scene_illustration_each_round")
        return {
            "schema_version": 1,
            "asset_requirement_update": update,
            "scene_jobs": [],
            "character_reference_jobs": [],
        }
    card = Path(context["card_path"])
    run_dir = Path(context["run_dir"])
    job_id = _text(payload.get("job_id")) or f"scene-{run_dir.name}"
    requested_characters = _safe_character_names(payload.get("characters"))
    appearance_specs = _character_appearance_specs(payload, requested_characters)
    characters = _appearance_character_names(appearance_specs)
    reference_candidates = _as_string_list(payload.get("reference_candidates"))
    using_default_references = False
    if not reference_candidates and appearance_specs:
        reference_candidates = [spec["reference_path"] for spec in appearance_specs]
        using_default_references = True
    reference_candidates = _append_reference_candidates(
        reference_candidates,
        _recent_scene_reference_candidates(card, context),
    )
    reference_policy = _default_reference_policy(payload, characters)
    prompt = _scene_illustration_prompt(context, payload, card, run_dir, characters, reference_candidates)
    plan: dict[str, Any] = {
        "schema_version": 1,
        "scene_jobs": [
            {
                "job_id": job_id,
                "kind": _text(payload.get("kind")) or "scene_illustration",
                "target": _text(payload.get("target")) or "scene_illustration",
                "prompt": prompt,
                "characters": characters,
                "art_style": _text(payload.get("art_style")),
                "reference_policy": reference_policy,
                "reference_candidates": reference_candidates,
                "character_appearances": appearance_specs,
            }
        ],
    }
    if reference_policy == "required" and using_default_references:
        reference_jobs = []
        profiles = context.get("character_profiles") if isinstance(context.get("character_profiles"), dict) else {}
        for spec in appearance_specs:
            name = spec["name"]
            target_path = spec["reference_path"]
            if (card / Path(target_path)).exists():
                continue
            profile = _text(profiles.get(name))
            reference_jobs.append(
                {
                    "job_id": _character_reference_job_id(spec),
                    "character_name": name,
                    "appearance_state": spec.get("appearance_state", ""),
                    "appearance_description": spec.get("description", ""),
                    "target_path": target_path,
                    "prompt": _character_reference_prompt(spec, profile),
                }
            )
        if reference_jobs:
            plan["character_reference_jobs"] = reference_jobs
    requirement = _required_scene_requirement(payload) or _required_scene_requirement({})
    if requirement:
        plan["asset_requirement_update"] = requirement
    return plan


def _scene_jobs_with_payload_defaults(
    value: Any,
    payload: dict[str, Any],
    card: Path,
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    payload_characters = _safe_character_names(payload.get("characters"))
    payload_appearances = _character_appearance_specs(payload, payload_characters)
    payload_reference_candidates = _as_string_list(payload.get("reference_candidates"))
    if not payload_reference_candidates and payload_appearances:
        payload_reference_candidates = [spec["reference_path"] for spec in payload_appearances]
    payload_reference_candidates = _append_reference_candidates(
        payload_reference_candidates,
        _recent_scene_reference_candidates(card, context),
    )
    payload_policy = _default_reference_policy(payload, _appearance_character_names(payload_appearances))
    payload_art_style = _text(payload.get("art_style"))

    for item in _as_list(value):
        job = dict(item) if isinstance(item, dict) else {}
        characters = _safe_character_names(job.get("characters"))
        appearances = _normalize_job_appearances(job.get("character_appearances"))
        reference_candidates = _as_string_list(job.get("reference_candidates"))

        if not characters and payload_appearances:
            job["characters"] = _appearance_character_names(payload_appearances)
        if not appearances and payload_appearances:
            job["character_appearances"] = payload_appearances
        if not reference_candidates and payload_reference_candidates:
            job["reference_candidates"] = payload_reference_candidates
        if not _text(job.get("reference_policy")) and payload_policy:
            job["reference_policy"] = payload_policy
        if not _text(job.get("art_style")) and payload_art_style:
            job["art_style"] = payload_art_style
        jobs.append(job)
    return jobs


def _append_reference_candidates(base: list[str], extra: list[str]) -> list[str]:
    result = list(base)
    for item in extra:
        if item and item not in result:
            result.append(item)
    return result


def _recent_scene_reference_candidates(card: Path, context: dict[str, Any]) -> list[str]:
    assets = context.get("card_assets") if isinstance(context.get("card_assets"), dict) else {}
    images = _as_list(assets.get("images") if isinstance(assets, dict) else None)
    for item in reversed(images):
        if not isinstance(item, dict):
            continue
        kind = _text(item.get("kind"))
        if kind not in {"scene", "scene_illustration"}:
            continue
        status = _text(item.get("status"))
        if status and status != "completed":
            continue
        try:
            path = _normalize_relative_path(item.get("path"))
        except InvalidAssetPathError:
            continue
        if path and (card / Path(path)).exists():
            return [path]
    return []


def _default_reference_policy(payload: dict[str, Any], characters: list[str]) -> str:
    policy = _text(payload.get("reference_policy"))
    if policy == "optional":
        return "optional"
    if policy in {"required", "reuse"}:
        return "required"
    if characters:
        return "required"
    return "optional"


def _scene_illustration_prompt(
    context: dict[str, Any],
    payload: dict[str, Any],
    card: Path,
    run_dir: Path,
    characters: list[str],
    reference_candidates: list[str],
) -> str:
    parts: list[str] = []
    parts.append("画面目标：生成一张有镜头感的剧情插图，选择本轮剧情中最有张力的一瞬间；不要把正文逐句画成流水账。")
    hints = _text(payload.get("planner_hints"))
    if hints:
        parts.append(f"画面重点：{hints}")
    art_style = _text(payload.get("art_style"))
    if art_style:
        parts.append(f"用户指定画风：{art_style}")
    elif not _has_available_reference_images(card, context, reference_candidates):
        parts.append(
            "画风策略：当前存档没有可用参考图片；请根据剧情题材、时代、情绪、场景和角色状态智能匹配画风，"
            "并在后续同一存档中保持一致。"
    )
    story = context.get("story_output") if isinstance(context.get("story_output"), dict) else {}
    story_text = _text(story.get("content")) or _default_persistent_prompt(card, run_dir)
    summary = _text(payload.get("summary")) or _text(payload.get("prompt"))
    if summary:
        parts.append(f"需求来源：{summary}")
    profiles = context.get("character_profiles") if isinstance(context.get("character_profiles"), dict) else {}
    profile_lines = []
    for name in characters:
        profile = _trim_text(_text(profiles.get(name)), 180)
        if profile:
            profile_lines.append(f"{name}：{profile}")
    if profile_lines:
        parts.append("角色形象参考：" + "；".join(profile_lines))
    if characters:
        parts.append("画面必须包含角色：" + "、".join(characters))
    reference_lines = _reference_usage_lines(reference_candidates)
    if reference_lines:
        parts.append("参考图用途：\n" + "\n".join(reference_lines))
    visual_source = _trim_text(story_text, 520)
    if visual_source:
        parts.append(f"剧情素材摘要（仅用于提炼画面，不要逐句复述）：{visual_source}")
    if characters:
        parts.append(
            "构图与主体：以主要角色的姿态、视线和相互距离表达关系；让关键道具或异常现象成为视觉焦点，"
            "避免把所有剧情元素平均铺满画面。"
        )
    else:
        parts.append(
            "构图与主体：选择最能概括本轮剧情的主体、道具或环境变化作为视觉焦点，避免信息堆叠。"
        )
    parts.append("镜头设计：使用小说插画式单镜头构图，可采用中景、近景或斜向视角；明确前景、中景、背景层次。")
    parts.append("动作与情绪：突出正在发生的动作、微表情和紧张/暧昧/悬疑等情绪，不要画成静态人物站桩。")
    parts.append("光线与氛围：根据场景时间、地点和情绪设计光源、色温、阴影和空气感，使画面具有电影感。")
    parts.append("禁止：不要文字、对白气泡、UI、截图边框、水印、签名；不要把剧情文本或说明文字画进画面。")
    return "\n".join(part for part in parts if part).strip()


def _reference_usage_lines(reference_candidates: list[str]) -> list[str]:
    lines: list[str] = []
    for reference in reference_candidates:
        rel = _normalize_reference_display_path(reference)
        if not rel:
            continue
        filename = Path(rel).name
        purpose = _reference_purpose(rel)
        lines.append(f"- {rel}（文件名：{filename}）：{purpose}")
    return lines


def _normalize_reference_display_path(reference: str) -> str:
    raw = _text(reference).replace("\\", "/").strip("/")
    if not raw or raw.startswith("../") or "/../" in raw:
        return ""
    return raw


def _reference_purpose(reference: str) -> str:
    parts = [part for part in reference.replace("\\", "/").split("/") if part]
    if len(parts) >= 3 and parts[0] == "characters":
        character_name = parts[1]
        stem = Path(parts[-1]).stem
        prefix = character_name + "-"
        if stem.startswith(prefix):
            state = stem[len(prefix) :]
            return f"角色人设参考：{character_name}（当前外观状态：{state}）"
        return f"角色人设参考：{character_name}"
    lowered = reference.lower()
    if any(marker in lowered for marker in ("style", "art_style", "palette", "mood", "画风")):
        return "画风参考：沿用整体画风、色彩倾向、笔触和质感"
    if "generated/images/" in lowered or "scene" in lowered:
        return "画风参考：沿用既有插图的整体画风、镜头氛围和场景质感"
    return "其他参考：用于补充画面设定、道具、场景或氛围"


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
        "appearance_state": _text(job.get("appearance_state")),
        "appearance_description": _text(job.get("appearance_description")),
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
            output_path=target_path,
            characters=[payload["character_name"]] if payload["character_name"] else [],
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
        "art_style": _text(job.get("art_style")),
        "character_appearances": _normalize_job_appearances(job.get("character_appearances")),
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
            characters=payload["characters"],
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
        payload["status"] = "failed" if child_reason == "invalid_asset_path" else "deferred"
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
    *,
    output_path: str = "",
    characters: list[str] | None = None,
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
    if output_path:
        command.extend(["--output-path", output_path])
    for reference in references:
        command.extend(["--reference", reference])
    for character in characters or []:
        command.extend(["--character", character])
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
    payload_action = _text(payload.get("action") or payload.get("operation")).lower()
    if not requested and payload_action in {"modify", "delete"}:
        requested = {"action": payload_action}
    if payload_action and "action" not in requested:
        requested = dict(requested)
        requested["action"] = payload_action
    if "asset_requirement_key" in payload and "asset_requirement_key" not in requested:
        requested = dict(requested)
        requested["asset_requirement_key"] = payload.get("asset_requirement_key")
    if requested.get("scene_illustration_each_round") is not True:
        payload_requirement = payload.get("asset_requirement")
        if isinstance(payload_requirement, dict) and payload_requirement.get("scene_illustration_each_round") is True:
            requested = dict(payload_requirement)
    action = _text(requested.get("action")).lower() or "create"
    key = _text(requested.get("asset_requirement_key") or "scene_illustration_each_round")
    if action in {"delete", "modify"}:
        manifest = _load_ui_manifest(card)
        requirements = manifest.setdefault("asset_requirements", {})
        if not isinstance(requirements, dict):
            requirements = {}
            manifest["asset_requirements"] = requirements
        if action == "delete":
            existed = key in requirements
            requirements.pop(key, None)
            agent_run.write_json(card / "ui_manifest.json", manifest)
            return {"applied": existed, "action": "delete", "asset_requirement_key": key}
        existing = requirements.get(key) if isinstance(requirements.get(key), dict) else {}
        updated = dict(existing)
        updated["enabled"] = requested.get("enabled") is not False
        for field in ("reason", "prompt"):
            if field in requested or field in payload:
                updated[field] = _text(requested.get(field) or payload.get(field))
        characters = _safe_character_names(payload.get("characters"))
        if characters:
            updated["characters"] = characters
            updated["character_appearances"] = _character_appearance_specs(payload, characters)
            updated["reference_policy"] = _default_reference_policy(payload, characters)
        if "art_style" in payload:
            updated["art_style"] = _text(payload.get("art_style"))
        requirements[key] = updated
        agent_run.write_json(card / "ui_manifest.json", manifest)
        return {"applied": True, "action": "modify", "asset_requirement_key": key}
    if requested.get("scene_illustration_each_round") is not True:
        return {"applied": False}

    manifest = _load_ui_manifest(card)
    requirements = manifest.setdefault("asset_requirements", {})
    requirements["scene_illustration_each_round"] = {
        "enabled": True,
        "reason": _text(requested.get("reason")),
        "prompt": _text(requested.get("prompt")),
        "characters": _safe_character_names(payload.get("characters")),
        "character_appearances": _character_appearance_specs(
            payload,
            _safe_character_names(payload.get("characters")),
        ),
        "art_style": _text(payload.get("art_style")),
        "reference_policy": _default_reference_policy(payload, _safe_character_names(payload.get("characters"))),
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
    names = _payload_character_names(payload)
    profiles: dict[str, str] = {}
    for name in names:
        profile_path = card / "memory" / "characters" / name / "profile.md"
        if profile_path.exists():
            profiles[name] = profile_path.read_text(encoding="utf-8")
    return profiles


def _has_available_reference_images(
    card: Path,
    context: dict[str, Any],
    reference_candidates: list[str],
) -> bool:
    for item in reference_candidates:
        try:
            path = _normalize_relative_path(item)
        except InvalidAssetPathError:
            continue
        if path and (card / Path(path)).exists():
            return True

    assets = context.get("card_assets") if isinstance(context.get("card_assets"), dict) else {}
    for item in _as_list(assets.get("images") if isinstance(assets, dict) else None):
        if not isinstance(item, dict):
            continue
        status = _text(item.get("status"))
        if status and status != "completed":
            continue
        try:
            path = _normalize_relative_path(item.get("path"))
        except InvalidAssetPathError:
            continue
        if path and (card / Path(path)).exists():
            return True
    return False


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


def _infer_current_scene_characters(card: Path, run_dir: Path) -> list[str]:
    names: list[str] = []

    player_context = _load_run_json(run_dir, "player.context.json")
    self_knowledge = player_context.get("self_knowledge") if isinstance(player_context, dict) else {}
    if isinstance(self_knowledge, dict):
        _append_unique_name(names, self_knowledge.get("name"))

    actor_outputs = _load_run_json(run_dir, "actor.outputs.json")
    if isinstance(actor_outputs, dict):
        for actor_id, outputs in actor_outputs.items():
            if isinstance(actor_id, str) and actor_id.startswith("character:"):
                _append_unique_name(names, actor_id.removeprefix("character:"))
            for item in _as_list(outputs):
                if isinstance(item, dict):
                    _append_unique_name(names, item.get("character_name"))
                    item_agent_id = _text(item.get("agent_id"))
                    if item_agent_id.startswith("character:"):
                        _append_unique_name(names, item_agent_id.removeprefix("character:"))

    story = _load_run_json(run_dir, "story.output.json")
    content = _text(story.get("content")) if isinstance(story, dict) else ""
    for item in _extract_character_dialogues(content):
        if isinstance(item, dict):
            _append_unique_name(names, item.get("character_name"))

    profiles = _load_character_profiles(card, {"characters": names})
    return [name for name in names if name in profiles or name]


def _extract_character_dialogues(content: str) -> list[Any]:
    start_tag = "<character_dialogues>"
    end_tag = "</character_dialogues>"
    start = content.find(start_tag)
    end = content.find(end_tag, start + len(start_tag))
    if start < 0 or end < 0:
        return []
    raw = content[start + len(start_tag) : end].strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _append_unique_name(names: list[str], value: Any) -> None:
    name = _text(value)
    if name and name not in names:
        names.append(name)


def _payload_character_names(payload: dict[str, Any]) -> list[str]:
    names = _safe_character_names(payload.get("characters"))
    for spec in _character_appearance_specs(payload, []):
        _append_unique_name(names, spec.get("name"))
    return names


def _character_appearance_specs(payload: dict[str, Any], fallback_characters: list[str]) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in _as_list(payload.get("character_appearances")):
        if not isinstance(item, dict):
            continue
        name = _safe_character_name(
            item.get("name")
            or item.get("character_name")
            or item.get("character")
        )
        if not name:
            continue
        appearance_state = _text(
            item.get("appearance_state")
            or item.get("state")
            or item.get("form")
        )
        description = _text(
            item.get("description")
            or item.get("appearance_description")
            or item.get("prompt")
        )
        reference_path = _text(item.get("reference_path") or item.get("target_path"))
        if not reference_path:
            reference_path = _default_character_reference_path(name, appearance_state)
        key = (name, appearance_state, reference_path)
        if key in seen:
            continue
        seen.add(key)
        specs.append(
            {
                "name": name,
                "appearance_state": appearance_state,
                "description": description,
                "reference_path": reference_path,
            }
        )

    for name in fallback_characters:
        if any(spec["name"] == name for spec in specs):
            continue
        reference_path = _default_character_reference_path(name, "")
        specs.append(
            {
                "name": name,
                "appearance_state": "",
                "description": "",
                "reference_path": reference_path,
            }
        )
    return specs


def _appearance_character_names(specs: list[dict[str, str]]) -> list[str]:
    names: list[str] = []
    for spec in specs:
        _append_unique_name(names, spec.get("name"))
    return names


def _default_character_reference_path(name: str, appearance_state: str) -> str:
    if appearance_state:
        suffix = agent_run.safe_name(appearance_state)
        return f"characters/{name}/{name}-{suffix}.png"
    return f"characters/{name}/{name}.png"


def _character_reference_job_id(spec: dict[str, str]) -> str:
    name = agent_run.safe_name(spec.get("name"))
    state = agent_run.safe_name(spec.get("appearance_state")) if spec.get("appearance_state") else ""
    if state:
        return f"character-{name}-{state}-reference"
    return f"character-{name}-reference"


def _character_reference_prompt(spec: dict[str, str], profile: str) -> str:
    parts = [f"为角色{spec['name']}生成专业人设图。"]
    appearance_state = _text(spec.get("appearance_state"))
    if appearance_state:
        parts.append(f"外观状态：{appearance_state}。")
    description = _text(spec.get("description"))
    if description:
        parts.append(f"本状态外观：{description}。")
    if profile:
        parts.append(f"角色档案：{_trim_text(profile, 500)}")
    parts.append("包含正面、侧面、背面、表情和关键动作参考；干净背景；不要文字、水印或对白气泡。")
    return "\n".join(parts)


def _normalize_job_appearances(value: Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        name = _safe_character_name(item.get("name"))
        if not name:
            continue
        normalized.append(
            {
                "name": name,
                "appearance_state": _text(item.get("appearance_state")),
                "description": _text(item.get("description")),
                "reference_path": _text(item.get("reference_path")),
            }
        )
    return normalized


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
    if payload.get("scene_illustration_each_round") is True:
        return {
            "scene_illustration_each_round": True,
            "reason": _text(payload.get("reason")) or _text(payload.get("summary")),
            "prompt": _text(payload.get("prompt")) or _text(payload.get("summary")),
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


def _trim_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


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
