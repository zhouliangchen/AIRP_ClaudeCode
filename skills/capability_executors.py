"""Executors for capability-backed runtime intents."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

import agent_run
import postprocess_outputs
import replay_capabilities


class CapabilityExecutorError(RuntimeError):
    """Raised when an intent executor cannot produce a structured result."""


def execute_intent(
    card_folder: str | Path,
    run_dir: str | Path,
    intent: dict[str, Any],
    *,
    phase: str,
    runtime_settings: dict[str, Any] | None = None,
    run_command: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    intent_type = str(intent.get("type") or "").strip()
    if intent_type == "assets_task":
        return execute_assets_task(
            card_folder,
            run_dir,
            intent,
            phase=phase,
            run_command=run_command,
        )
    if intent_type == "replay_plan":
        return execute_replay_plan(run_dir, intent)
    if intent_type == "system_request":
        return execute_system_request(run_dir, intent, runtime_settings=runtime_settings)
    return {
        "status": "blocked",
        "reason": "executor_not_wired",
        "outputs": {"intent_type": intent_type},
    }


def execute_assets_task(
    card_folder: str | Path,
    run_dir: str | Path,
    intent: dict[str, Any],
    *,
    phase: str,
    run_command: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    card = Path(card_folder)
    payload = _payload(intent)
    prompt = _required_text(payload, "prompt")
    kind = _text(payload.get("kind")) or "scene"
    target = _text(payload.get("target")) or _text(intent.get("id")) or "asset"

    contract_update = postprocess_outputs.apply_ui_schema_contract_update(card, payload)
    outputs: dict[str, Any] = {
        "status": "deferred",
        "reason": "asset_worker_not_configured",
        "kind": kind,
        "target": target,
        "prompt": prompt,
        "phase": phase,
        "postprocess_contract_update": contract_update,
    }
    if _can_start_image_job(card) and run_command is not None:
        command_result = run_command(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "image_generate.py"),
                str(card),
                "--prompt",
                prompt,
                "--kind",
                kind,
                "--target",
                target,
                "--async",
            ],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        returncode = getattr(command_result, "returncode", 1)
        outputs["image_job"] = {
            "returncode": returncode,
            "stdout": _text(getattr(command_result, "stdout", "")),
            "stderr": _text(getattr(command_result, "stderr", "")),
        }
        if returncode == 0:
            outputs["status"] = "queued"
            outputs.pop("reason", None)
        else:
            outputs["status"] = "deferred"
            outputs["reason"] = "asset_worker_start_failed"

    _write_executor_artifact(run_dir, "assets_tasks", intent, outputs)
    return {"status": "completed", "outputs": outputs}


def execute_replay_plan(run_dir: str | Path, intent: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(intent)
    if payload.get("confirmed") is not True:
        return {
            "status": "blocked",
            "reason": "manual_confirmation_required",
            "outputs": {"requires_manual_confirmation": True},
        }
    materialized = replay_capabilities.materialize_replay_plan(run_dir, payload)
    return {"status": "completed", "outputs": materialized}


def execute_system_request(
    run_dir: str | Path,
    intent: dict[str, Any],
    *,
    runtime_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = runtime_settings if isinstance(runtime_settings, dict) else {}
    if settings.get("allowSourceCodeSelfRepair") is not True:
        return {
            "status": "blocked",
            "reason": "source_code_self_repair_not_authorized",
            "outputs": {"requires_source_repair_authorization": True},
        }
    payload = _payload(intent)
    outputs = {
        "status": "queued_for_main_agent",
        "summary": _text(payload.get("summary")),
        "payload": payload,
    }
    _write_executor_artifact(run_dir, "system_requests", intent, outputs)
    return {"status": "completed", "outputs": outputs}


def _payload(intent: dict[str, Any]) -> dict[str, Any]:
    payload = intent.get("payload")
    if not isinstance(payload, dict):
        raise CapabilityExecutorError("intent payload must be an object")
    return payload


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = _text(payload.get(key))
    if not value:
        raise CapabilityExecutorError(f"intent payload {key} is required")
    return value


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _can_start_image_job(card: Path) -> bool:
    if os.environ.get("OPENAI_API_KEY"):
        return True
    here = Path(__file__).resolve().parent
    root = here.parent
    for path in (
        card / "image_config.local.json",
        here / "image_config.local.json",
        root / "image_config.local.json",
        root / ".image_api.json",
    ):
        if path.is_file():
            return True
    return False


def _write_executor_artifact(
    run_dir: str | Path,
    kind: str,
    intent: dict[str, Any],
    outputs: dict[str, Any],
) -> None:
    intent_id = _text(intent.get("id")) or "intent"
    safe_id = agent_run.safe_name(intent_id)
    agent_run.write_json(
        Path(run_dir) / "artifacts" / "runtime_pump" / kind / f"{safe_id}.json",
        {
            "intent_id": intent_id,
            "intent_type": _text(intent.get("type")),
            "outputs": outputs,
        },
    )
