"""Strict LLM planner for assets-ui tasks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import llm_runner


VALID_QUEUE_TYPES = {
    "character_reference",
    "character_reference_candidate",
    "character_reference_selection",
    "scene_illustration",
    "ui_patch_request",
    "asset_rename",
}

VALID_CAMERA_PERSPECTIVES = {
    "protagonist_first_person",
    "other_character_first_person",
    "third_person_camera",
}

VALID_SCENE_MODES = {
    "story_scene",
    "atmosphere_only",
    "dream",
    "memory",
    "flashback",
    "transition",
}

REFERENCE_QUEUE_TYPES = {
    "character_reference",
    "character_reference_candidate",
    "character_reference_selection",
}


class AssetsUiAgentError(RuntimeError):
    def __init__(self, reason: str, message: str = ""):
        self.reason = reason
        super().__init__(reason if not message else f"{reason}: {message}")


def plan_assets_task(
    context: dict[str, Any],
    *,
    llm_run: Callable[[str, str, str | Path], str] | None = None,
) -> dict[str, Any]:
    prompt = build_assets_ui_prompt(context)
    runner = llm_run or llm_runner.run_llm_agent
    raw = runner("assets-ui", prompt, Path(context.get("run_dir") or "."))
    plan = _parse_json_object(raw)
    return validate_plan(plan)


def build_assets_ui_prompt(context: dict[str, Any]) -> str:
    payload = context.get("payload") if isinstance(context.get("payload"), dict) else {}
    story = context.get("story_output") if isinstance(context.get("story_output"), dict) else {}
    profiles = context.get("character_profiles") if isinstance(context.get("character_profiles"), dict) else {}
    return "\n".join(
        [
            "你是独立的 assets-ui agent。",
            "必须返回严格 JSON，不要 Markdown，不要解释。",
            "职责：优化绘图提示词、整理图片任务批次、整理 card_only UI patch request、提出资产重命名计划。",
            "正式人设图路径必须使用 generated/characters/<角色名>/<角色名>.png。",
            "剧情插图 display_policy 必须为 story_inline；人设图 display_policy 必须为 hidden_reference。",
            "场景 prompt 必须包含画面目标、镜头或视角、主体、动作情绪、光线氛围、参考图用途和负面约束，不得直接复制 story 正文。",
            "camera_perspective 只能是 protagonist_first_person、other_character_first_person、third_person_camera。",
            "梦境、回忆、闪回等特殊场景适合采用主角第一人称视角；这类 scene_mode 请优先考虑 protagonist_first_person，但不要机械校验。",
            "scene_mode 只能是 story_scene、atmosphere_only、dream、memory、flashback、transition。",
            "UI patch request 必须 scope: \"card_only\"，不得要求修改全局模板影响所有存档。",
            "JSON schema: {\"schema_version\":1,\"plan_id\":\"...\",\"style_state\":{\"has_style_reference\":false,\"style_reference_paths\":[],\"art_style\":\"\"},\"jobs\":[],\"ui_patch_requests\":[],\"rename_operations\":[]}",
            f"payload: {json.dumps(payload, ensure_ascii=False, default=str)}",
            f"story_output: {json.dumps(story, ensure_ascii=False, default=str)}",
            f"character_profiles: {json.dumps(profiles, ensure_ascii=False, default=str)}",
        ]
    )


def validate_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise AssetsUiAgentError("invalid_plan", "assets-ui plan must be an object")
    schema_version = plan.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1:
        raise AssetsUiAgentError("invalid_schema_version")
    if "jobs" not in plan or not isinstance(plan["jobs"], list):
        raise AssetsUiAgentError("invalid_jobs")
    if "ui_patch_requests" not in plan or not isinstance(plan["ui_patch_requests"], list):
        raise AssetsUiAgentError("invalid_ui_patch_requests")
    if "rename_operations" not in plan or not isinstance(plan["rename_operations"], list):
        raise AssetsUiAgentError("invalid_rename_operations")
    for job in plan["jobs"]:
        _validate_job(job)
    for request in plan["ui_patch_requests"]:
        if not isinstance(request, dict) or request.get("scope") != "card_only":
            raise AssetsUiAgentError("ui_patch_scope")
    return plan


def _validate_job(job: Any) -> None:
    if not isinstance(job, dict):
        raise AssetsUiAgentError("invalid_job")
    queue_type = job.get("queue_type")
    if not isinstance(queue_type, str) or queue_type not in VALID_QUEUE_TYPES:
        raise AssetsUiAgentError("invalid_queue_type")
    if queue_type == "scene_illustration":
        _require_non_empty_string(job, "job_id", "invalid_job_id")
        _require_non_empty_string(job, "prompt", "invalid_prompt")
        if job.get("display_policy") != "story_inline":
            raise AssetsUiAgentError("invalid_display_policy")
        if job.get("camera_perspective") not in VALID_CAMERA_PERSPECTIVES:
            raise AssetsUiAgentError("invalid_camera_perspective")
        if job.get("scene_mode") not in VALID_SCENE_MODES:
            raise AssetsUiAgentError("invalid_scene_mode")
        if "reference_candidates" in job and not isinstance(job["reference_candidates"], list):
            raise AssetsUiAgentError("invalid_reference_candidates")
    if queue_type in REFERENCE_QUEUE_TYPES:
        _require_non_empty_string(job, "job_id", "invalid_job_id")
        _require_non_empty_string(job, "prompt", "invalid_prompt")
        if job.get("display_policy") not in {"hidden_reference", None, ""}:
            raise AssetsUiAgentError("invalid_display_policy")
        if not _has_non_empty_string(job, "target_path") and not _has_non_empty_string(job, "final_target_path"):
            raise AssetsUiAgentError("invalid_target_path")
    if queue_type == "ui_patch_request":
        _require_non_empty_string(job, "job_id", "invalid_job_id")
        if job.get("scope") != "card_only":
            raise AssetsUiAgentError("ui_patch_scope")
    if queue_type == "asset_rename":
        _require_non_empty_string(job, "job_id", "invalid_job_id")
        if "replacements" in job and not isinstance(job["replacements"], list):
            raise AssetsUiAgentError("invalid_replacements")


def _has_non_empty_string(data: dict[str, Any], key: str) -> bool:
    value = data.get(key)
    return isinstance(value, str) and bool(value.strip())


def _require_non_empty_string(data: dict[str, Any], key: str, reason: str) -> None:
    if not _has_non_empty_string(data, key):
        raise AssetsUiAgentError(reason)


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```\s*(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssetsUiAgentError("invalid_json", str(exc)) from exc
    if not isinstance(parsed, dict):
        raise AssetsUiAgentError("invalid_json_root")
    return parsed
