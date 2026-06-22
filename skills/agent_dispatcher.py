"""Intent dispatcher for the per-round agent runtime."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

import agent_intents
import agent_messages
import agent_run
import input_analysis_apply


SUPPORTED_INTENT_TYPES = {
    "analyze_input",
    "run_gm_turn",
    "request_projection",
    "run_actor",
    "run_subgm_thread",
    "compose_story",
    "review_critic",
    "repair_request",
    "rollback_request",
    "deliver_round",
}


class AgentDispatcherError(RuntimeError):
    """Raised when dispatcher execution cannot continue safely."""


def dispatch_next(
    run_dir: str | Path,
    card_folder: str | Path,
    root_dir: str | Path,
    *,
    run_claude: Callable[[str, str, str | Path], str] | None = None,
    run_command: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Execute one pending intent or report a terminal dispatcher state."""

    root = Path(run_dir)
    manifest = _load_manifest(root)
    if manifest.get("stage") == "delivered":
        return _result(True, "delivered", reason="", artifacts=[], created_intents=[], created_messages=[])
    if manifest.get("stage") == "blocked":
        return _blocked_terminal_result(manifest)

    pending = agent_intents.list_intents(root, "pending")
    if not pending:
        return _block_stalled(root, manifest)

    intent = pending[0]
    intent_type = str(intent.get("type") or "")
    intent_id = str(intent.get("id") or "")
    if intent_type not in SUPPORTED_INTENT_TYPES:
        blocked = agent_intents.block_intent(
            root,
            intent_id,
            "unsupported_intent_type",
            outputs={"intent_type": intent_type},
        )
        _mark_blocked(root, "unsupported_intent_type", {"intent_id": intent_id, "intent_type": intent_type})
        return _result(
            False,
            "blocked",
            intent_id=intent_id,
            intent_type=intent_type,
            reason="unsupported_intent_type",
            created_intents=[],
            created_messages=[],
            artifacts=[],
            detail=blocked.get("result", {}),
        )

    return _execute_supported_intent(root, Path(card_folder), Path(root_dir), intent, run_claude, run_command)


def artifact_path(run_dir: str | Path, relative_path: str) -> Path:
    """Return the authoritative artifact path for a run-relative artifact."""

    relative = Path(relative_path)
    if relative.is_absolute():
        raise AgentDispatcherError(f"artifact path must be relative: {relative_path}")

    artifacts_root = (Path(run_dir) / "artifacts").resolve()
    candidate = (artifacts_root / relative).resolve()
    if candidate != artifacts_root and artifacts_root not in candidate.parents:
        raise AgentDispatcherError(f"artifact path escapes artifacts directory: {relative_path}")
    return candidate


def write_artifact(run_dir: str | Path, relative_path: str, payload: dict[str, Any]) -> Path:
    path = artifact_path(run_dir, relative_path)
    agent_run.write_json(path, payload)
    return path


def read_artifact(run_dir: str | Path, relative_path: str) -> dict[str, Any]:
    path = artifact_path(run_dir, relative_path)
    data = agent_run.read_json(path)
    if not isinstance(data, dict):
        raise AgentDispatcherError(f"{path}: artifact JSON object is missing or invalid")
    return data


def _execute_supported_intent(
    run_dir: Path,
    card_folder: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str] | None,
    run_command: Callable[..., Any] | None,
) -> dict[str, Any]:
    intent_type = str(intent.get("type") or "")
    if intent_type == "analyze_input":
        return _execute_analyze_input(run_dir, card_folder, root_dir, intent)
    if intent_type == "assets_task":
        raise AgentDispatcherError("assets_task is not included in SUPPORTED_INTENT_TYPES")
    blocked = agent_intents.block_intent(
        run_dir,
        str(intent.get("id") or ""),
        "executor_not_wired",
        outputs={"intent_type": intent_type},
    )
    _mark_blocked(run_dir, "executor_not_wired", {"intent_id": intent.get("id"), "intent_type": intent_type})
    return _result(
        False,
        "blocked",
        intent_id=str(intent.get("id") or ""),
        intent_type=intent_type,
        reason="executor_not_wired",
        created_intents=[],
        created_messages=[],
        artifacts=[],
        detail=blocked.get("result", {}),
    )


def _execute_analyze_input(
    run_dir: Path,
    card_folder: Path,
    root_dir: Path,
    intent: dict[str, Any],
) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "analyze_input"})

    try:
        applied = input_analysis_apply.apply_current_run(card_folder, root_dir)
        artifacts = []
        source_path = run_dir / "input_analysis.output.json"
        if source_path.exists():
            destination = artifact_path(run_dir, "input_analysis.output.json")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            artifacts.append("artifacts/input_analysis.output.json")

        message = _find_analysis_applied_message(run_dir)
        if message is None:
            message_result = agent_messages.append_message(
                run_dir,
                {
                    "from": "input_analyst",
                    "to": ["gm", "main_agent"],
                    "type": "analysis_applied",
                    "visibility": "gm_only",
                    "payload": {"applied": applied},
                },
            )
            if not message_result.get("ok"):
                reason = "analysis_message_failed"
                blocked = agent_intents.block_intent(
                    run_dir,
                    intent_id,
                    reason,
                    outputs={"executor": "analyze_input", "message_result": message_result},
                )
                _mark_blocked(
                    run_dir,
                    reason,
                    {"intent_id": intent_id, "message_result": message_result},
                )
                return _result(
                    False,
                    "blocked",
                    intent_id=intent_id,
                    intent_type="analyze_input",
                    reason=reason,
                    created_intents=[],
                    created_messages=[],
                    artifacts=artifacts,
                    detail=blocked.get("result", {}),
                )
            message = message_result.get("message", {})

        message_id = str(message.get("id") or "")
        follow_up = _ensure_follow_up_intent(
            run_dir,
            intent_id,
            {
                "requested_by": "input_analyst",
                "type": "run_gm_turn",
                "payload": {"reason": "input_analysis_applied"},
                "policy": {"source_intent_id": intent_id},
            },
        )
    except Exception as exc:
        return _block_analyze_input_failure(run_dir, intent_id, exc)

    follow_up_id = str(follow_up.get("id") or "")
    created_intents = [follow_up_id] if follow_up.get("created") else []
    agent_intents.complete_intent(
        run_dir,
        intent_id,
        outputs={
            "executor": "analyze_input",
            "applied": applied,
            "follow_up_intent_id": follow_up_id,
            "artifacts": artifacts,
            "message_id": message_id,
        },
    )
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="analyze_input",
        reason="",
        created_intents=created_intents,
        created_messages=[message_id] if message_id else [],
        artifacts=artifacts,
        detail={"applied": applied, "follow_up_intent_id": follow_up_id},
    )


def _find_analysis_applied_message(run_dir: Path) -> dict[str, Any] | None:
    for message in reversed(agent_messages.read_messages(run_dir)):
        if message.get("status") != "delivered":
            continue
        if message.get("from") != "input_analyst":
            continue
        if message.get("type") != "analysis_applied":
            continue
        if message.get("visibility") != "gm_only":
            continue
        targets = message.get("to")
        if not isinstance(targets, list) or "gm" not in targets:
            continue
        payload = message.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("input_path") != "input.json":
            continue
        if payload.get("analysis_path") != "input_analysis.output.json":
            continue
        if not isinstance(payload.get("routed_characters"), list):
            continue
        return message
    return None


def _block_analyze_input_failure(run_dir: Path, intent_id: str, exc: Exception) -> dict[str, Any]:
    reason = "analyze_input_failed"
    error = f"{type(exc).__name__}: {exc}"
    detail = {
        "intent_id": intent_id,
        "error": error,
        "exception_type": type(exc).__name__,
    }
    blocked = agent_intents.block_intent(
        run_dir,
        intent_id,
        reason,
        outputs={"executor": "analyze_input", **detail},
    )
    _mark_blocked(run_dir, reason, detail)
    return _result(
        False,
        "blocked",
        intent_id=intent_id,
        intent_type="analyze_input",
        reason=reason,
        created_intents=[],
        created_messages=[],
        artifacts=[],
        detail=blocked.get("result", {}),
    )


def _ensure_follow_up_intent(
    run_dir: Path,
    source_intent_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    for state in agent_intents.VALID_STATES:
        for existing in agent_intents.list_intents(run_dir, state):
            policy = existing.get("policy")
            if not isinstance(policy, dict):
                continue
            if policy.get("source_intent_id") != source_intent_id:
                continue
            if existing.get("type") != payload.get("type"):
                continue
            return {"id": existing.get("id"), "created": False, "intent": existing}

    created = agent_intents.create_intent(run_dir, payload)["intent"]
    return {"id": created.get("id"), "created": True, "intent": created}


def _block_stalled(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    _mark_blocked(run_dir, "dispatcher_stalled", {"pending_intents": 0})
    return _result(False, "stalled", reason="dispatcher_stalled", artifacts=[], created_intents=[], created_messages=[])


def _blocked_terminal_result(manifest: dict[str, Any]) -> dict[str, Any]:
    dispatcher = manifest.get("dispatcher")
    if not isinstance(dispatcher, dict):
        dispatcher = {}
    reason = str(dispatcher.get("reason") or "dispatcher_blocked")
    return _result(
        False,
        "blocked",
        reason=reason,
        artifacts=[],
        created_intents=[],
        created_messages=[],
        detail=dispatcher,
    )


def _mark_blocked(run_dir: Path, reason: str, detail: dict[str, Any]) -> None:
    manifest = _load_manifest(run_dir)
    manifest["stage"] = "blocked"
    manifest["dispatcher"] = {"status": "blocked", "reason": reason, "detail": detail}
    history = manifest.setdefault("stage_history", [])
    if isinstance(history, list):
        history.append({"stage": "blocked", "reason": reason})
    agent_run.write_json(run_dir / "manifest.json", manifest)


def _load_manifest(run_dir: Path) -> dict[str, Any]:
    manifest = agent_run.read_json(run_dir / "manifest.json", {}) or {}
    if not isinstance(manifest, dict):
        raise AgentDispatcherError(f"{run_dir / 'manifest.json'}: manifest must be a JSON object")
    return manifest


def _result(
    ok: bool,
    status: str,
    *,
    intent_id: str = "",
    intent_type: str = "",
    reason: str = "",
    created_intents: list[str] | None = None,
    created_messages: list[str] | None = None,
    artifacts: list[str] | None = None,
    detail: Any = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": ok,
        "status": status,
        "intent_id": intent_id,
        "intent_type": intent_type,
        "reason": reason,
        "created_intents": list(created_intents or []),
        "created_messages": list(created_messages or []),
        "artifacts": list(artifacts or []),
    }
    if detail is not None:
        result["detail"] = detail
    return result
