"""Intent dispatcher for the per-round agent runtime."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable

import agent_outputs
import agent_snapshots
import agent_intents
import agent_messages
import agent_run
import input_analysis_apply
import rp_generate_cli
import self_repair


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
    if intent_type == "run_gm_turn":
        return _execute_run_gm_turn(run_dir, root_dir, intent, run_claude)
    if intent_type == "compose_story":
        return _execute_compose_story(run_dir, root_dir, intent, run_claude)
    if intent_type == "review_critic":
        return _execute_review_critic(run_dir, root_dir, intent, run_claude)
    if intent_type == "repair_request":
        return _execute_repair_request(run_dir, intent)
    if intent_type == "rollback_request":
        return _execute_rollback_request(run_dir, card_folder, intent)
    if intent_type == "deliver_round":
        return _execute_deliver_round(run_dir, card_folder, root_dir, intent, run_command)
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


def _read_prompt(run_dir: Path, key: str) -> str:
    manifest = _load_manifest(run_dir)
    prompts = manifest.get("prompts")
    relative_path = ""
    if isinstance(prompts, dict):
        value = prompts.get(key)
        if isinstance(value, str):
            relative_path = value
    if not relative_path:
        relative_path = f"prompts/{key}.prompt.md"

    relative = Path(relative_path)
    if relative.is_absolute():
        raise AgentDispatcherError(f"prompt path must be relative: {relative_path}")
    prompt_path = (run_dir / relative).resolve()
    root = run_dir.resolve()
    if prompt_path != root and root not in prompt_path.parents:
        raise AgentDispatcherError(f"prompt path escapes run directory: {relative_path}")
    try:
        return prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentDispatcherError(f"{prompt_path}: prompt is missing") from exc


def _dispatch_agent_payload(
    agent_key: str,
    run_dir: Path,
    root_dir: Path,
    run_claude: Callable[[str, str, str | Path], str] | None,
    extra_context: dict[str, Any],
) -> dict[str, Any]:
    if run_claude is None:
        raise AgentDispatcherError(f"run_claude is required for {agent_key}")
    prompt = _read_prompt(run_dir, agent_key)
    return rp_generate_cli._dispatch_agent_payload(
        agent_key,
        prompt,
        root_dir,
        run_claude,
        extra_context=extra_context,
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


def _execute_compose_story(
    run_dir: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str] | None,
) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "compose_story"})
    artifacts: list[str] = []

    try:
        story_input = agent_outputs.build_story_input(run_dir)
        artifacts.append("artifacts/story.input.json")
        story_output = _dispatch_agent_payload(
            "story",
            run_dir,
            root_dir,
            run_claude,
            {"story_input": story_input},
        )
        story_output = rp_generate_cli._normalize_story_output(story_output, story_input)
        write_artifact(run_dir, "story.output.json", story_output)
        artifacts.append("artifacts/story.output.json")
        follow_up = _ensure_follow_up_intent(
            run_dir,
            intent_id,
            {
                "requested_by": "story",
                "type": "review_critic",
                "payload": {
                    "story_input_path": "artifacts/story.input.json",
                    "story_output_path": "artifacts/story.output.json",
                },
                "policy": {"source_intent_id": intent_id},
            },
        )
    except Exception as exc:
        return _block_executor_failure(run_dir, intent_id, "compose_story", "compose_story_failed", exc, artifacts)

    follow_up_id = str(follow_up.get("id") or "")
    created_intents = [follow_up_id] if follow_up.get("created") else []
    agent_intents.complete_intent(
        run_dir,
        intent_id,
        outputs={
            "executor": "compose_story",
            "follow_up_intent_id": follow_up_id,
            "artifacts": artifacts,
        },
    )
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="compose_story",
        reason="",
        created_intents=created_intents,
        created_messages=[],
        artifacts=artifacts,
        detail={"follow_up_intent_id": follow_up_id},
    )


def _execute_review_critic(
    run_dir: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str] | None,
) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "review_critic"})
    artifacts: list[str] = []

    try:
        story_input = read_artifact(run_dir, "story.input.json")
        story_output = read_artifact(run_dir, "story.output.json")
        critic_report = _dispatch_agent_payload(
            "critic",
            run_dir,
            root_dir,
            run_claude,
            {"story_input": story_input, "story_output": story_output},
        )
        critic_report = rp_generate_cli._normalize_critic_report_for_story(critic_report, story_output)
        write_artifact(run_dir, "critic.report.json", critic_report)
        artifacts.append("artifacts/critic.report.json")
        decision = str(critic_report.get("decision") or "")
        if decision == "pass":
            follow_up_payload = {
                "requested_by": "critic",
                "type": "deliver_round",
                "payload": {"critic_report_path": "artifacts/critic.report.json", "decision": decision},
                "policy": {"source_intent_id": intent_id},
            }
            follow_up = _ensure_follow_up_intent(run_dir, intent_id, follow_up_payload)
        else:
            follow_up = agent_outputs.record_critic_repair_request(
                run_dir.parents[1],
                run_dir,
                critic_report,
            )
    except Exception as exc:
        return _block_executor_failure(run_dir, intent_id, "review_critic", "review_critic_failed", exc, artifacts)

    follow_up_id = str(follow_up.get("id") or "")
    created_intents = [follow_up_id] if follow_up.get("created") else []
    agent_intents.complete_intent(
        run_dir,
        intent_id,
        outputs={
            "executor": "review_critic",
            "decision": decision,
            "follow_up_intent_id": follow_up_id,
            "artifacts": artifacts,
        },
    )
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="review_critic",
        reason="",
        created_intents=created_intents,
        created_messages=[],
        artifacts=artifacts,
        detail={"decision": decision, "follow_up_intent_id": follow_up_id},
    )


def _execute_repair_request(run_dir: Path, intent: dict[str, Any]) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "repair_request"})

    try:
        payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
        report_path = str(payload.get("critic_report_path") or "artifacts/critic.report.json")
        critic_report = _read_critic_report(run_dir, report_path)
        routing_source = critic_report.get("repair_routing") if "repair_routing" in critic_report else payload.get("repair_routing")
        routing = self_repair.normalize_repair_routing(routing_source)
        repair_instruction = str(critic_report.get("repair_instruction") or payload.get("repair_instruction") or "")
        decision = str(critic_report.get("decision") or payload.get("decision") or "")

        if routing.get("rollback") == "round_progression":
            follow_up_payload = {
                "requested_by": "repair",
                "type": "rollback_request",
                "payload": {
                    "mode": "round_progression",
                    "reason": "critic_repair",
                    "critic_report_path": report_path,
                },
                "policy": {"source_intent_id": intent_id},
            }
            snapshot_id = _first_text(payload.get("snapshot_id"), critic_report.get("snapshot_id"))
            if snapshot_id:
                follow_up_payload["payload"]["snapshot_id"] = snapshot_id
        else:
            repair_payload: dict[str, Any] = {
                "repair_routing": routing,
                "critic_report_path": report_path,
            }
            if repair_instruction:
                repair_payload["repair_instruction"] = repair_instruction
            if decision:
                repair_payload["decision"] = decision
            follow_up_payload = {
                "requested_by": "repair",
                "type": "compose_story",
                "payload": repair_payload,
                "policy": {"source_intent_id": intent_id},
            }
        follow_up = _ensure_follow_up_intent(run_dir, intent_id, follow_up_payload)
    except Exception as exc:
        return _block_executor_failure(run_dir, intent_id, "repair_request", "repair_request_failed", exc, [])

    follow_up_id = str(follow_up.get("id") or "")
    created_intents = [follow_up_id] if follow_up.get("created") else []
    agent_intents.complete_intent(
        run_dir,
        intent_id,
        outputs={
            "executor": "repair_request",
            "decision": decision,
            "repair_routing": routing,
            "follow_up_intent_id": follow_up_id,
            "follow_up_type": follow_up_payload.get("type"),
        },
    )
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="repair_request",
        reason="",
        created_intents=created_intents,
        created_messages=[],
        artifacts=[],
        detail={
            "decision": decision,
            "repair_routing": routing,
            "follow_up_intent_id": follow_up_id,
            "follow_up_type": follow_up_payload.get("type"),
        },
    )


def _execute_rollback_request(run_dir: Path, card_folder: Path, intent: dict[str, Any]) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "rollback_request"})

    try:
        payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
        snapshot_id = str(payload.get("snapshot_id") or "")
        mode = str(payload.get("mode") or "round_progression")
        restore = agent_snapshots.restore_snapshot(card_folder, snapshot_id, mode=mode)
        if not restore.get("ok"):
            blocked = agent_intents.block_intent(
                run_dir,
                intent_id,
                "rollback_failed",
                outputs={"executor": "rollback_request", "restore": restore},
            )
            _mark_blocked(run_dir, "rollback_failed", {"intent_id": intent_id, "restore": restore})
            return _result(
                False,
                "blocked",
                intent_id=intent_id,
                intent_type="rollback_request",
                reason="rollback_failed",
                created_intents=[],
                created_messages=[],
                artifacts=[],
                detail=blocked.get("result", {}),
            )

        follow_up_type = "compose_story" if mode == "story_only" else "run_gm_turn"
        follow_up_run_dir = _restored_current_run_dir(card_folder)
        follow_up = _ensure_follow_up_intent(
            follow_up_run_dir,
            intent_id,
            {
                "requested_by": "rollback",
                "type": follow_up_type,
                "payload": {"rollback": restore},
                "policy": {"source_intent_id": intent_id},
            },
        )
    except Exception as exc:
        return _block_executor_failure(run_dir, intent_id, "rollback_request", "rollback_failed", exc, [])

    follow_up_id = str(follow_up.get("id") or "")
    created_intents = [follow_up_id] if follow_up.get("created") else []
    completion = _complete_rollback_intent_if_safe(
        run_dir,
        intent_id,
        {
            "executor": "rollback_request",
            "restore": restore,
            "follow_up_run_dir": str(follow_up_run_dir),
            "follow_up_intent_id": follow_up_id,
            "follow_up_type": follow_up_type,
        },
    )
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="rollback_request",
        reason="",
        created_intents=created_intents,
        created_messages=[],
        artifacts=[],
        detail={
            "restore": restore,
            "follow_up_run_dir": str(follow_up_run_dir),
            "follow_up_intent_id": follow_up_id,
            "follow_up_type": follow_up_type,
            "completion": completion,
        },
    )


def _execute_deliver_round(
    run_dir: Path,
    card_folder: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_command: Callable[..., Any] | None,
) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "deliver_round"})

    try:
        if run_command is None:
            raise AgentDispatcherError("run_command is required for deliver_round")
        delivery = rp_generate_cli._run_delivery(card_folder, root_dir, run_command)
        if not _delivery_succeeded(delivery):
            blocked = agent_intents.block_intent(
                run_dir,
                intent_id,
                "delivery_failed",
                outputs={"executor": "deliver_round", "delivery": delivery},
            )
            _mark_blocked(run_dir, "delivery_failed", {"intent_id": intent_id, "delivery": delivery})
            return _result(
                False,
                "blocked",
                intent_id=intent_id,
                intent_type="deliver_round",
                reason="delivery_failed",
                created_intents=[],
                created_messages=[],
                artifacts=[],
                detail=blocked.get("result", {}),
            )
        _mark_delivered(run_dir, {"intent_id": intent_id, "delivery": delivery})
        agent_intents.complete_intent(
            run_dir,
            intent_id,
            outputs={"executor": "deliver_round", "delivery": delivery},
        )
    except Exception as exc:
        return _block_executor_failure(run_dir, intent_id, "deliver_round", "delivery_failed", exc, [])

    return _result(
        True,
        "delivered",
        intent_id=intent_id,
        intent_type="deliver_round",
        reason="",
        created_intents=[],
        created_messages=[],
        artifacts=[],
        detail={"delivery": delivery},
    )


def _read_critic_report(run_dir: Path, report_path: str) -> dict[str, Any]:
    relative = Path(report_path)
    if relative.is_absolute():
        raise AgentDispatcherError(f"critic report path must be relative: {report_path}")
    if relative.parts and relative.parts[0] == "artifacts":
        return read_artifact(run_dir, str(Path(*relative.parts[1:])))

    run_root = run_dir.resolve()
    artifacts_root = (run_root / "artifacts").resolve()
    candidate = (run_root / relative).resolve()
    if candidate == artifacts_root or artifacts_root not in candidate.parents:
        raise AgentDispatcherError(f"critic report path must stay under artifacts: {report_path}")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise AgentDispatcherError(f"{candidate}: critic report is invalid JSON") from exc
    except OSError as exc:
        raise AgentDispatcherError(f"{candidate}: critic report is missing") from exc
    if not isinstance(payload, dict):
        raise AgentDispatcherError(f"{candidate}: critic report must be a JSON object")
    return payload


def _restored_current_run_dir(card_folder: Path) -> Path:
    current = agent_run.current_run_dir(card_folder)
    if current is None:
        raise AgentDispatcherError(f"{Path(card_folder) / '.agent_runs' / 'current'} is missing after rollback restore")
    return current.resolve()


def _complete_rollback_intent_if_safe(run_dir: Path, intent_id: str, outputs: dict[str, Any]) -> dict[str, Any]:
    if not _intent_in_state(run_dir, intent_id, "accepted"):
        return {
            "status": "skipped",
            "reason": "original_rollback_intent_not_found_after_restore",
            "run_dir": str(run_dir),
        }
    completed = agent_intents.complete_intent(run_dir, intent_id, outputs=outputs)
    return {
        "status": "completed" if completed.get("ok") else "skipped",
        "reason": completed.get("reason") or "",
        "run_dir": str(run_dir),
        "result": completed.get("result", {}),
    }


def _intent_in_state(run_dir: Path, intent_id: str, state: str) -> bool:
    try:
        return any(item.get("id") == intent_id for item in agent_intents.list_intents(run_dir, state))
    except Exception:
        return False


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _delivery_succeeded(delivery: dict[str, Any]) -> bool:
    return bool(rp_generate_cli._delivery_complete(delivery))


def _block_executor_failure(
    run_dir: Path,
    intent_id: str,
    intent_type: str,
    reason: str,
    exc: Exception,
    artifacts: list[str],
) -> dict[str, Any]:
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
        outputs={"executor": intent_type, **detail, "artifacts": artifacts},
    )
    _mark_blocked(run_dir, reason, detail)
    return _result(
        False,
        "blocked",
        intent_id=intent_id,
        intent_type=intent_type,
        reason=reason,
        created_intents=[],
        created_messages=[],
        artifacts=artifacts,
        detail=blocked.get("result", {}),
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


def _execute_run_gm_turn(
    run_dir: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str] | None,
) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "run_gm_turn"})
    artifacts: list[str] = []

    try:
        if run_claude is None:
            raise AgentDispatcherError("run_claude is required for run_gm_turn")
        manifest = _load_manifest(run_dir)
        loop_result = rp_generate_cli._run_interactive_agent_loop(run_dir, manifest, root_dir, run_claude)
        artifacts = [
            _refresh_root_artifact_to_authority(run_dir, "gm.output.json"),
            _refresh_root_artifact_to_authority(run_dir, "actor.outputs.json"),
        ]
        follow_up = _ensure_follow_up_intent(
            run_dir,
            intent_id,
            {
                "requested_by": "gm",
                "type": "compose_story",
                "payload": {"loop_result": loop_result},
                "policy": {"source_intent_id": intent_id},
            },
        )
    except Exception as exc:
        return _block_executor_failure(run_dir, intent_id, "run_gm_turn", "run_gm_turn_failed", exc, artifacts)

    follow_up_id = str(follow_up.get("id") or "")
    created_intents = [follow_up_id] if follow_up.get("created") else []
    agent_intents.complete_intent(
        run_dir,
        intent_id,
        outputs={
            "executor": "run_gm_turn",
            "loop_result": loop_result,
            "follow_up_intent_id": follow_up_id,
            "artifacts": artifacts,
        },
    )
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="run_gm_turn",
        reason="",
        created_intents=created_intents,
        created_messages=[],
        artifacts=artifacts,
        detail={"loop_result": loop_result, "follow_up_intent_id": follow_up_id},
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


def _refresh_root_artifact_to_authority(run_dir: Path, relative_path: str) -> str:
    relative = Path(relative_path)
    if relative.is_absolute():
        raise AgentDispatcherError(f"executor artifact path must be relative: {relative_path}")

    run_root = run_dir.resolve()
    source = (run_root / relative).resolve()
    if source != run_root and run_root not in source.parents:
        raise AgentDispatcherError(f"executor artifact path escapes run directory: {relative_path}")
    if not source.exists():
        raise AgentDispatcherError(f"{source}: expected current executor artifact is missing")

    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise AgentDispatcherError(f"{source}: current executor artifact is invalid JSON") from exc
    except OSError as exc:
        raise AgentDispatcherError(f"{source}: current executor artifact cannot be read") from exc
    if not isinstance(payload, dict):
        raise AgentDispatcherError(f"{source}: current executor artifact must be a JSON object")

    authoritative = artifact_path(run_dir, relative_path)
    agent_run.write_json(authoritative, payload)
    return f"artifacts/{relative_path}"


def _mark_blocked(run_dir: Path, reason: str, detail: dict[str, Any]) -> None:
    manifest = _load_manifest(run_dir)
    manifest["stage"] = "blocked"
    manifest["dispatcher"] = {"status": "blocked", "reason": reason, "detail": detail}
    history = manifest.setdefault("stage_history", [])
    if isinstance(history, list):
        history.append({"stage": "blocked", "reason": reason})
    agent_run.write_json(run_dir / "manifest.json", manifest)


def _mark_delivered(run_dir: Path, detail: dict[str, Any]) -> None:
    manifest = _load_manifest(run_dir)
    manifest["stage"] = "delivered"
    manifest["dispatcher"] = {"status": "delivered", "detail": detail}
    history = manifest.setdefault("stage_history", [])
    if isinstance(history, list):
        history.append({"stage": "delivered", "reason": "dispatcher_delivered"})
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
