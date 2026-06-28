"""Executors for capability-backed runtime intents."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import assets_ui_runtime
import agent_run
import actor_memory_store
import character_registry
import replay_capabilities
import replay_executor


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
    if intent_type == "replay_execute":
        return execute_replay_execute(
            card_folder,
            Path(__file__).resolve().parent.parent,
            run_dir,
            intent,
        )
    if intent_type == "system_request":
        return execute_system_request(run_dir, intent, runtime_settings=runtime_settings)
    if intent_type == "character_rename":
        return execute_character_rename(card_folder, run_dir, intent)
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
    return assets_ui_runtime.process_assets_task(
        card_folder,
        run_dir,
        intent,
        phase=phase,
        run_command=run_command,
    )


def execute_replay_plan(run_dir: str | Path, intent: dict[str, Any]) -> dict[str, Any]:
    payload = _nested_capability_payload(intent)
    materialized = replay_capabilities.materialize_replay_plan(run_dir, payload)
    return {"status": "completed", "outputs": materialized}


def execute_replay_execute(
    card_folder: str | Path,
    root_dir: str | Path,
    run_dir: str | Path,
    intent: dict[str, Any],
) -> dict[str, Any]:
    payload = _nested_capability_payload(intent)
    try:
        normalized = replay_capabilities.validate_replay_execute(payload)
    except replay_capabilities.ReplayCapabilityError as exc:
        return {
            "status": "blocked",
            "reason": "invalid_replay_execute_payload",
            "outputs": {"error": str(exc)},
        }
    result = replay_executor.execute_replay_session(
        card_folder,
        root_dir,
        normalized["plan_id"],
        prepare_round=replay_executor.prepare_round_default,
        generate_round=replay_executor.generate_round_default,
    )
    if result.get("ok") is True:
        return {"status": "completed", "outputs": result}
    return {
        "status": "blocked",
        "reason": _text(result.get("reason")) or "replay_execute_failed",
        "outputs": result,
    }


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


def execute_character_rename(
    card_folder: str | Path,
    run_dir: str | Path,
    intent: dict[str, Any],
) -> dict[str, Any]:
    card = Path(card_folder)
    payload = _payload(intent)
    to_name = _required_text(payload, "to_name")
    from_name = _text(payload.get("from_name") or payload.get("old_name"))
    actor_id = _text(payload.get("actor_id"))
    if not from_name and actor_id:
        from_name = actor_memory_store.actor_paths(card, actor_id).name
    if not from_name:
        raise CapabilityExecutorError("intent payload from_name is required")

    idempotent = _idempotent_character_rename_result(card, from_name, to_name, actor_id)
    if idempotent:
        idempotent["reason"] = _text(payload.get("reason"))
        _write_executor_artifact(run_dir, "character_renames", intent, idempotent)
        return {"status": "completed", "outputs": idempotent}

    rename_result = actor_memory_store.rename_character_identity(card, from_name, to_name)
    registry_result = character_registry.rename_registered_character(
        card,
        rename_result["from_name"],
        rename_result["to_name"],
    )
    outputs = dict(rename_result)
    outputs["registry"] = registry_result
    outputs["reason"] = _text(payload.get("reason"))
    _write_executor_artifact(run_dir, "character_renames", intent, outputs)
    return {"status": "completed", "outputs": outputs}


def _idempotent_character_rename_result(
    card: Path,
    from_name: str,
    to_name: str,
    actor_id: str,
) -> dict[str, Any]:
    if not to_name:
        return {}
    source_path = card / "characters" / from_name
    if source_path.exists():
        return {}
    target_path = card / "characters" / to_name
    if not target_path.exists():
        return {}
    current_player_name = actor_memory_store.actor_paths(card, "player").name
    source_is_player_placeholder = from_name in {"player", "未命名角色"} or actor_id == "player"
    if source_is_player_placeholder and current_player_name == to_name:
        return {
            "from_name": from_name,
            "to_name": to_name,
            "actor_id": actor_id,
            "player_mapping_updated": False,
            "idempotent": True,
            "registry": {"ok": True, "status": "not_required"},
        }
    return {}


def _payload(intent: dict[str, Any]) -> dict[str, Any]:
    payload = intent.get("payload")
    if not isinstance(payload, dict):
        raise CapabilityExecutorError("intent payload must be an object")
    return payload


def _nested_capability_payload(intent: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(intent)
    nested = payload.get("payload")
    if isinstance(nested, dict):
        return nested
    return payload


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = _text(payload.get(key))
    if not value:
        raise CapabilityExecutorError(f"intent payload {key} is required")
    return value


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


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
