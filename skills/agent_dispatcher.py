"""Intent dispatcher for the per-round agent runtime."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable

import agent_prompts
import agent_outputs
import agent_snapshots
import agent_actor_runtime
import agent_interactions
import agent_intents
import agent_messages
import agent_run
import agent_turn_loop
import input_analysis_apply
import input_routing_requests
import postprocess_outputs
import projection_agent
import rp_generate_cli
import self_repair
import subgm_threads
import subgm_turn_loop


SUPPORTED_INTENT_TYPES = {
    "analyze_input",
    "run_gm_turn",
    "request_projection",
    "run_actor",
    "run_subgm_thread",
    "compose_story",
    "review_critic",
    "run_postprocess",
    "repair_request",
    "rollback_request",
    "system_request",
    "deliver_round",
    "assets_task",
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
    if intent_type == "request_projection":
        return _execute_request_projection(run_dir, root_dir, intent, run_claude)
    if intent_type == "run_actor":
        return _execute_run_actor(run_dir, root_dir, intent, run_claude)
    if intent_type == "run_subgm_thread":
        return _execute_run_subgm_thread(run_dir, root_dir, intent, run_claude)
    if intent_type == "compose_story":
        return _execute_compose_story(run_dir, root_dir, intent, run_claude)
    if intent_type == "review_critic":
        return _execute_review_critic(run_dir, root_dir, intent, run_claude)
    if intent_type == "run_postprocess":
        return _execute_run_postprocess(run_dir, card_folder, root_dir, intent, run_claude)
    if intent_type == "repair_request":
        return _execute_repair_request(run_dir, root_dir, intent)
    if intent_type == "rollback_request":
        return _execute_rollback_request(run_dir, card_folder, intent)
    if intent_type == "system_request":
        return _execute_system_request(run_dir, intent)
    if intent_type == "deliver_round":
        return _execute_deliver_round(run_dir, card_folder, root_dir, intent, run_command)
    if intent_type == "assets_task":
        return _execute_assets_task(run_dir, intent)
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


def _is_loop_prompt_missing(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "missing prompt path" in text or "prompt is missing" in text


def _dispatch_agent_payload(
    agent_key: str,
    run_dir: Path,
    root_dir: Path,
    run_claude: Callable[[str, str, str | Path], str] | None,
    extra_context: dict[str, Any],
) -> dict[str, Any]:
    if run_claude is None:
        raise AgentDispatcherError(f"run_claude is required for {agent_key}")
    packet = None
    if isinstance(extra_context, dict):
        candidate = extra_context.get("packet")
        if isinstance(candidate, dict):
            packet = candidate
    if agent_key.startswith("subGM:") or agent_key.startswith("character:") or agent_key == "player":
        try:
            prompt = rp_generate_cli._read_loop_prompt(run_dir, _load_manifest(run_dir), agent_key, packet)
        except rp_generate_cli.AgentExecutionError as exc:
            if not _is_loop_prompt_missing(exc):
                raise
            prompt = _read_prompt(run_dir, agent_key)
    elif agent_key == "projection":
        projection_packet = extra_context.get("projection_packet") if isinstance(extra_context, dict) else None
        prompt = agent_prompts.projection_prompt_text(projection_packet if isinstance(projection_packet, dict) else {})
    else:
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
        applied_manifest = applied.get("manifest") if isinstance(applied.get("manifest"), dict) else {}
        runtime_settings = applied_manifest.get("runtime_settings")
        if not isinstance(runtime_settings, dict):
            loaded_runtime_settings = _load_manifest(run_dir).get("runtime_settings")
            runtime_settings = loaded_runtime_settings if isinstance(loaded_runtime_settings, dict) else {}
        routing_result = input_routing_requests.process_routing_requests(
            run_dir,
            applied.get("routing_requests", []) if isinstance(applied.get("routing_requests"), list) else [],
            runtime_settings=runtime_settings,
            source_intent_id=intent_id,
        )
        artifacts.extend(routing_result.get("artifacts", []))
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
    created_intents = list(routing_result.get("created_intents", []))
    if follow_up.get("created"):
        created_intents.append(follow_up_id)
    created_messages = [message_id] if message_id else []
    created_messages.extend(routing_result.get("created_messages", []))
    agent_intents.complete_intent(
        run_dir,
        intent_id,
        outputs={
            "executor": "analyze_input",
            "applied": applied,
            "follow_up_intent_id": follow_up_id,
            "routing_requests": routing_result,
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
        created_messages=created_messages,
        artifacts=artifacts,
        detail={"applied": applied, "follow_up_intent_id": follow_up_id, "routing_requests": routing_result},
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
        payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
        repair_context = payload.get("repair_context") if isinstance(payload.get("repair_context"), dict) else None
        story_input = agent_outputs.build_story_input(run_dir)
        artifacts.append("artifacts/story.input.json")
        extra_context: dict[str, Any] = {"story_input": story_input}
        if repair_context is not None:
            extra_context["repair_context"] = repair_context
        story_output = _dispatch_agent_payload(
            "story",
            run_dir,
            root_dir,
            run_claude,
            extra_context,
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


def _execute_request_projection(
    run_dir: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str] | None,
) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
    actor_id = str(payload.get("actor_id") or "")
    source_message_id = str(payload.get("source_message_id") or intent.get("source_message_id") or "")
    source_call_id = str(payload.get("source_call_id") or "")
    fanout = _normalize_actor_fanout(payload.get("fanout"))
    accept_failure = _request_projection_transition_failure(
        run_dir,
        intent_id,
        "request_projection_accept_failed",
        _call_intent_transition(
            agent_intents.accept_intent,
            run_dir,
            intent_id,
            outputs={"executor": "request_projection"},
        ),
    )
    if accept_failure is not None:
        return accept_failure

    try:
        request = agent_actor_runtime.inspect_projection_request(
            run_dir,
            actor_id=actor_id,
            source_message_id=source_message_id,
            source_call_id=source_call_id,
        )
        existing_projected_message = request.get("existing_projected_message")
        projection_result: dict[str, Any] | None = None
        projection_artifact = ""
        if existing_projected_message is None:
            call = request.get("call") if isinstance(request.get("call"), dict) else {}
            source_payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
            objective_context = source_payload.get("objective_context")
            if not isinstance(objective_context, dict):
                objective_context = call.get("objective_context") if isinstance(call.get("objective_context"), dict) else {}
            review_packet = projection_agent.build_review_packet(
                actor_id=actor_id,
                source_call_id=str(request.get("source_call_id") or source_call_id),
                source_message_id=source_message_id,
                requested_actor_message=str(call.get("prompt") or ""),
                actor_packet=request.get("packet") if isinstance(request.get("packet"), dict) else {},
                objective_context=objective_context,
            )
            raw_projection = _dispatch_projection_agent(run_dir, root_dir, run_claude, review_packet)
            projection_result = projection_agent.validate_projection_output(
                raw_projection,
                actor_id=actor_id,
                source_call_id=str(request.get("source_call_id") or source_call_id),
            )
            projection_artifact = f"artifacts/projections/{intent_id}.json"
            write_artifact(
                run_dir,
                f"projections/{intent_id}.json",
                {
                    "review_packet": review_packet,
                    "projection_result": projection_result,
                },
            )
            if projection_result["decision"] == "blocked":
                raise agent_actor_runtime.AgentActorProjectionError(
                    "projection_agent_blocked",
                    {"feedback": projection_result.get("feedback", ""), "projection_result": projection_result},
                )
            if projection_result["decision"] == "needs_rewrite":
                follow_up = _ensure_follow_up_intent(
                    run_dir,
                    intent_id,
                    {
                        "requested_by": "projection",
                        "type": "run_gm_turn",
                        "source_message_id": source_message_id,
                        "payload": {
                            "reason": "projection_needs_rewrite",
                            "actor_id": actor_id,
                            "source_message_id": source_message_id,
                            "source_call_id": str(request.get("source_call_id") or source_call_id),
                            "projection_feedback": projection_result.get("feedback", ""),
                        },
                        "policy": {
                            "source_intent_id": intent_id,
                            **({"fanout_batch_id": fanout["batch_id"]} if fanout else {}),
                        },
                    },
                )
                follow_up_id = str(follow_up.get("id") or "")
                created_intents = [follow_up_id] if follow_up.get("created") else []
                outputs = {
                    "executor": "request_projection",
                    "intent_type": "request_projection",
                    "source_message_id": source_message_id,
                    "source_call_id": str(request.get("source_call_id") or source_call_id),
                    "projection_decision": projection_result["decision"],
                    "projection_feedback": projection_result.get("feedback", ""),
                    "follow_up_intent_id": follow_up_id,
                    "created_messages": [],
                    "created_intents": created_intents,
                    "artifacts": [projection_artifact],
                }
                complete_failure = _request_projection_transition_failure(
                    run_dir,
                    intent_id,
                    "request_projection_complete_failed",
                    _call_intent_transition(
                        agent_intents.complete_intent,
                        run_dir,
                        intent_id,
                        outputs=outputs,
                    ),
                    outputs=outputs,
                    created_intents=created_intents,
                    created_messages=[],
                )
                if complete_failure is not None:
                    return complete_failure
                return _result(
                    True,
                    "completed",
                    intent_id=intent_id,
                    intent_type="request_projection",
                    reason="",
                    created_intents=created_intents,
                    created_messages=[],
                    artifacts=[projection_artifact],
                    detail=outputs,
                )
        projection = agent_actor_runtime.project_actor_request(
            run_dir,
            actor_id=actor_id,
            source_message_id=source_message_id,
            source_call_id=source_call_id,
            projection_result=projection_result,
        )
        projected_message_id = str(projection.get("projected_message_id") or "")
        resolved_source_call_id = str(projection.get("source_call_id") or source_call_id)
        run_actor_payload = {
            "actor_id": actor_id,
            "projected_message_id": projected_message_id,
            "source_call_id": resolved_source_call_id,
        }
        if fanout:
            run_actor_payload["fanout"] = fanout
        follow_up = _ensure_follow_up_intent(
            run_dir,
            intent_id,
            {
                "requested_by": "projection",
                "type": "run_actor",
                "source_message_id": projected_message_id,
                "payload": run_actor_payload,
                "policy": {
                    "source_intent_id": intent_id,
                    **({"fanout_batch_id": fanout["batch_id"]} if fanout else {}),
                },
            },
        )
    except agent_actor_runtime.AgentActorProjectionError as exc:
        return _block_projection_failure(run_dir, intent_id, exc)
    except Exception as exc:
        wrapped = agent_actor_runtime.AgentActorProjectionError(
            "request_projection_failed",
            {"error": f"{type(exc).__name__}: {exc}", "exception_type": type(exc).__name__},
        )
        return _block_projection_failure(run_dir, intent_id, wrapped)

    follow_up_id = str(follow_up.get("id") or "")
    created_intents = [follow_up_id] if follow_up.get("created") else []
    projected_message_created = bool(projection.get("projected_message_created", True))
    created_messages = [projected_message_id] if projected_message_id and projected_message_created else []
    artifacts = [projection_artifact] if projection_artifact else []
    projection_decision = ""
    if isinstance(projection_result, dict):
        projection_decision = str(projection_result.get("decision") or "")
    outputs = {
        "executor": "request_projection",
        "intent_type": "request_projection",
        "projected_message_id": projected_message_id,
        "source_message_id": source_message_id,
        "source_call_id": resolved_source_call_id,
        "follow_up_intent_id": follow_up_id,
        "created_messages": created_messages,
        "created_intents": created_intents,
        "artifacts": artifacts,
    }
    if projection_decision:
        outputs["projection_decision"] = projection_decision
    complete_failure = _request_projection_transition_failure(
        run_dir,
        intent_id,
        "request_projection_complete_failed",
        _call_intent_transition(
            agent_intents.complete_intent,
            run_dir,
            intent_id,
            outputs=outputs,
        ),
        outputs=outputs,
        created_intents=created_intents,
        created_messages=created_messages,
    )
    if complete_failure is not None:
        return complete_failure
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="request_projection",
        reason="",
        created_intents=created_intents,
        created_messages=created_messages,
        artifacts=artifacts,
        detail=outputs,
    )


def _normalize_actor_fanout(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    batch_id = str(value.get("batch_id") or "")
    source_gm_intent_id = str(value.get("source_gm_intent_id") or "")
    raw_expected = value.get("expected_source_call_ids")
    if not batch_id or not source_gm_intent_id or not isinstance(raw_expected, list):
        return None
    expected_source_call_ids: list[str] = []
    for item in raw_expected:
        source_call_id = str(item or "")
        if source_call_id and source_call_id not in expected_source_call_ids:
            expected_source_call_ids.append(source_call_id)
    if not expected_source_call_ids:
        return None
    return {
        "batch_id": batch_id,
        "source_gm_intent_id": source_gm_intent_id,
        "expected_source_call_ids": expected_source_call_ids,
    }


def _dispatch_projection_agent(
    run_dir: Path,
    root_dir: Path,
    run_claude: Callable[[str, str, str | Path], str] | None,
    review_packet: dict[str, Any],
) -> dict[str, Any]:
    return _dispatch_agent_payload(
        "projection",
        run_dir,
        root_dir,
        run_claude,
        {"projection_packet": review_packet},
    )


def _gm_fanout_completed_source_call_ids(
    run_dir: Path,
    batch_id: str,
    current_source_call_id: str = "",
) -> list[str]:
    completed: list[str] = []
    if current_source_call_id:
        completed.append(current_source_call_id)
    for existing in agent_intents.list_intents(run_dir, "completed"):
        if existing.get("type") != "run_actor":
            continue
        payload = existing.get("payload")
        if not isinstance(payload, dict):
            continue
        fanout = _normalize_actor_fanout(payload.get("fanout"))
        if not fanout or fanout["batch_id"] != batch_id:
            continue
        result = existing.get("result")
        outputs = result.get("outputs") if isinstance(result, dict) else None
        if not isinstance(outputs, dict):
            continue
        source_call_id = str(outputs.get("source_call_id") or payload.get("source_call_id") or "")
        if source_call_id and source_call_id not in completed:
            completed.append(source_call_id)
    return completed


def _gm_fanout_ready_for_continuation(run_dir: Path, fanout: dict[str, Any], current_source_call_id: str) -> bool:
    completed = set(_gm_fanout_completed_source_call_ids(run_dir, fanout["batch_id"], current_source_call_id))
    return all(source_call_id in completed for source_call_id in fanout["expected_source_call_ids"])


def _gm_fanout_has_player_decision_required(run_dir: Path, batch_id: str) -> bool:
    for existing in agent_intents.list_intents(run_dir, "completed"):
        if existing.get("type") != "run_actor":
            continue
        payload = existing.get("payload")
        if not isinstance(payload, dict):
            continue
        fanout = _normalize_actor_fanout(payload.get("fanout"))
        if not fanout or fanout["batch_id"] != batch_id:
            continue
        result = existing.get("result")
        outputs = result.get("outputs") if isinstance(result, dict) else None
        if isinstance(outputs, dict) and outputs.get("player_decision_required") is True:
            return True
    return False


def _execute_run_actor(
    run_dir: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str] | None,
) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
    actor_id = str(payload.get("actor_id") or "")
    projected_message_id = str(payload.get("projected_message_id") or "")
    source_call_id = str(payload.get("source_call_id") or "")
    accept_failure = _run_actor_transition_failure(
        run_dir,
        intent_id,
        "run_actor_accept_failed",
        _call_intent_transition(
            agent_intents.accept_intent,
            run_dir,
            intent_id,
            outputs={"executor": "run_actor"},
        ),
    )
    if accept_failure is not None:
        return accept_failure

    artifacts: list[str] = []
    created_messages: list[str] = []
    created_intents: list[str] = []

    missing_payload_fields = [
        field
        for field, value in (
            ("actor_id", actor_id),
            ("projected_message_id", projected_message_id),
            ("source_call_id", source_call_id),
        )
        if not value
    ]
    if missing_payload_fields:
        return _block_run_actor_failure(
            run_dir,
            intent_id,
            "run_actor_payload_invalid",
            {"intent_id": intent_id, "missing_fields": missing_payload_fields},
            artifacts,
        )

    try:
        projection = agent_actor_runtime.read_projected_actor_packet(
            run_dir,
            actor_id=actor_id,
            projected_message_id=projected_message_id,
            source_call_id=source_call_id,
        )
        actor_packet = projection["packet"]
        resolved_source_call_id = str(projection.get("source_call_id") or source_call_id)
        raw_actor_payload = _dispatch_agent_payload(
            agent_turn_loop._dispatch_actor_key(actor_id),
            run_dir,
            root_dir,
            run_claude,
            {
                "actor_packet": actor_packet,
                "projected_message_id": projected_message_id,
                "source_call_id": resolved_source_call_id,
            },
        )
        actor_output = agent_turn_loop._validate_actor(actor_id, raw_actor_payload)
        call = projection.get("call") if isinstance(projection.get("call"), dict) else {}
        if resolved_source_call_id and not call.get("call_id"):
            call = {**call, "call_id": resolved_source_call_id}
        response_message_id = agent_actor_runtime.record_actor_response(run_dir, actor_id, call, actor_output)
        created_messages.append(response_message_id)
        agent_actor_runtime.append_actor_output(run_dir, actor_id, actor_output)
        artifacts.append("artifacts/actor.outputs.json")
        agent_actor_runtime.record_actor_events(run_dir, actor_id, actor_output, resolved_source_call_id)

        player_decision_required = _actor_output_requires_player_decision(actor_id, actor_output)
        requires_gm_resolution = _actor_output_requires_gm_resolution(actor_output)
        fanout = _normalize_actor_fanout(payload.get("fanout"))
        fanout_player_decision_required = bool(
            player_decision_required
            or (fanout and _gm_fanout_has_player_decision_required(run_dir, fanout["batch_id"]))
        )
        fanout_continuation_blocked_by_player_decision = False
        follow_up_id = ""
        if player_decision_required:
            agent_interactions.mark_decision_point(
                run_dir,
                "player decision required after actor response",
            )
            fanout_continuation_blocked_by_player_decision = bool(fanout)
        if not player_decision_required:
            follow_up_reason = (
                "actor_response_requires_resolution"
                if requires_gm_resolution
                else "actor_response_continue"
            )
            if fanout and fanout_player_decision_required:
                fanout_continuation_blocked_by_player_decision = True
                follow_up_id = ""
            elif fanout and not _gm_fanout_ready_for_continuation(run_dir, fanout, resolved_source_call_id):
                follow_up_id = ""
            elif fanout:
                completed_source_call_ids = _gm_fanout_completed_source_call_ids(
                    run_dir,
                    fanout["batch_id"],
                    resolved_source_call_id,
                )
                follow_up = _ensure_follow_up_intent_by_payload(
                    run_dir,
                    fanout["source_gm_intent_id"],
                    {
                        "requested_by": actor_id,
                        "type": "run_gm_turn",
                        "payload": {
                            "reason": "actor_fanout_complete",
                            "fanout_batch_id": fanout["batch_id"],
                            "source_gm_intent_id": fanout["source_gm_intent_id"],
                            "expected_source_call_ids": fanout["expected_source_call_ids"],
                            "completed_source_call_ids": completed_source_call_ids,
                            "actor_response_message_id": response_message_id,
                            "actor_outputs_path": "artifacts/actor.outputs.json",
                        },
                        "policy": {
                            "source_intent_id": fanout["source_gm_intent_id"],
                            "source_actor_intent_id": intent_id,
                            "fanout_batch_id": fanout["batch_id"],
                        },
                    },
                    payload_keys=("fanout_batch_id",),
                )
                follow_up_id = str(follow_up.get("id") or "")
                if follow_up.get("created"):
                    created_intents.append(follow_up_id)
            else:
                follow_up = _ensure_follow_up_intent(
                    run_dir,
                    intent_id,
                    {
                        "requested_by": actor_id,
                        "type": "run_gm_turn",
                        "payload": {
                            "reason": follow_up_reason,
                            "actor_id": actor_id,
                            "source_call_id": resolved_source_call_id,
                            "actor_response_message_id": response_message_id,
                            "actor_outputs_path": "artifacts/actor.outputs.json",
                        },
                        "policy": {"source_intent_id": intent_id},
                    },
                )
                follow_up_id = str(follow_up.get("id") or "")
                if follow_up.get("created"):
                    created_intents.append(follow_up_id)
    except agent_actor_runtime.AgentActorDispatchError as exc:
        if exc.reason in {"projected_message_missing", "projected_message_actor_mismatch", "projected_message_invalid"}:
            return _block_run_actor_failure(
                run_dir,
                intent_id,
                exc.reason,
                {"intent_id": intent_id, **exc.detail},
                artifacts,
                created_messages=created_messages,
            )
        return _block_run_actor_failure(
            run_dir,
            intent_id,
            "actor_dispatch_failed",
            {"intent_id": intent_id, "reason": exc.reason, **exc.detail},
            artifacts,
            created_messages=created_messages,
        )
    except Exception as exc:
        return _block_run_actor_failure(
            run_dir,
            intent_id,
            "actor_dispatch_failed",
            {
                "intent_id": intent_id,
                "exception_type": type(exc).__name__,
                "error": f"{type(exc).__name__}: {exc}",
            },
            artifacts,
            created_messages=created_messages,
        )

    outputs = {
        "executor": "run_actor",
        "intent_type": "run_actor",
        "actor_id": actor_id,
        "projected_message_id": projected_message_id,
        "source_call_id": resolved_source_call_id,
        "actor_response_message_id": response_message_id,
        "follow_up_intent_id": follow_up_id,
        "player_decision_required": player_decision_required,
        "fanout_player_decision_required": fanout_player_decision_required,
        "fanout_continuation_blocked_by_player_decision": fanout_continuation_blocked_by_player_decision,
        "requires_gm_resolution": requires_gm_resolution,
        "artifacts": artifacts,
        "created_messages": created_messages,
        "created_intents": created_intents,
    }
    complete_failure = _run_actor_transition_failure(
        run_dir,
        intent_id,
        "run_actor_complete_failed",
        _call_intent_transition(
            agent_intents.complete_intent,
            run_dir,
            intent_id,
            outputs=outputs,
        ),
        outputs=outputs,
        artifacts=artifacts,
        created_intents=created_intents,
        created_messages=created_messages,
    )
    if complete_failure is not None:
        return complete_failure
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="run_actor",
        reason="",
        created_intents=created_intents,
        created_messages=created_messages,
        artifacts=artifacts,
        detail=outputs,
    )


def _execute_run_subgm_thread(
    run_dir: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str] | None,
) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
    thread_id = str(payload.get("thread_id") or "")
    requested_reason = str(payload.get("reason") or "")

    accept_failure = _run_subgm_thread_transition_failure(
        run_dir,
        intent_id,
        "run_subgm_thread_accept_failed",
        _call_intent_transition(
            agent_intents.accept_intent,
            run_dir,
            intent_id,
            outputs={"executor": "run_subgm_thread"},
        ),
    )
    if accept_failure is not None:
        return accept_failure

    created_intents: list[str] = []

    def dispatch(agent_key: str, packet: dict[str, Any]) -> dict[str, Any]:
        return _dispatch_agent_payload(
            agent_key,
            run_dir,
            root_dir,
            run_claude,
            {
                "packet": packet,
                "subgm_thread_id": thread_id,
                "source_intent_id": intent_id,
                "intent_type": "run_subgm_thread",
            },
        )

    try:
        if not thread_id:
            raise subgm_threads.SubgmThreadError("thread_id must not be empty")
        side_result = subgm_turn_loop.run_side_thread(run_dir, thread_id, dispatch)
    except (subgm_threads.SubgmThreadError, subgm_turn_loop.SubgmTurnLoopError, AgentDispatcherError) as exc:
        return _block_run_subgm_thread_failure(
            run_dir,
            intent_id,
            "subgm_dispatch_failed",
            {
                "intent_id": intent_id,
                "thread_id": thread_id,
                "error": f"{type(exc).__name__}: {exc}",
                "exception_type": type(exc).__name__,
            },
        )
    except Exception as exc:
        return _block_run_subgm_thread_failure(
            run_dir,
            intent_id,
            "subgm_dispatch_failed",
            {
                "intent_id": intent_id,
                "thread_id": thread_id,
                "error": f"{type(exc).__name__}: {exc}",
                "exception_type": type(exc).__name__,
            },
        )

    if not isinstance(side_result, dict) or not side_result.get("ok"):
        return _block_run_subgm_thread_failure(
            run_dir,
            intent_id,
            "subgm_dispatch_failed",
            {
                "intent_id": intent_id,
                "thread_id": thread_id,
                "error": "subGM side-thread runner returned failure",
                "runner_result": side_result if isinstance(side_result, dict) else repr(side_result),
            },
        )

    safe_thread_id = str(side_result.get("thread_id") or thread_id)
    side_status = str(side_result.get("status") or "")
    called_actors = [
        str(actor_id)
        for actor_id in side_result.get("called_actors", [])
        if isinstance(actor_id, str) and actor_id
    ]
    try:
        steps = int(side_result.get("steps") or 0)
    except (TypeError, ValueError):
        steps = 0
    noop = steps == 0 and side_status in {"paused", "completed"}

    follow_up_id = ""
    if side_status not in {"completed", "paused"}:
        follow_up = _ensure_follow_up_intent(
            run_dir,
            intent_id,
            {
                "requested_by": f"subGM:{safe_thread_id}",
                "type": "run_gm_turn",
                "payload": {
                    "thread_id": safe_thread_id,
                    "status": side_status,
                    "called_actors": called_actors,
                    "reason": "subgm_thread_needs_gm_arbitration",
                },
                "policy": {"source_intent_id": intent_id},
            },
        )
        follow_up_id = str(follow_up.get("id") or "")
        if follow_up.get("created"):
            created_intents.append(follow_up_id)

    outputs = {
        "executor": "run_subgm_thread",
        "intent_type": "run_subgm_thread",
        "thread_id": safe_thread_id,
        "requested_reason": requested_reason,
        "side_thread_status": side_status,
        "steps": steps,
        "noop": noop,
        "called_actors": called_actors,
        "follow_up_intent_id": follow_up_id,
        "created_intents": created_intents,
        "side_thread_result": side_result,
    }
    complete_failure = _run_subgm_thread_transition_failure(
        run_dir,
        intent_id,
        "run_subgm_thread_complete_failed",
        _call_intent_transition(
            agent_intents.complete_intent,
            run_dir,
            intent_id,
            outputs=outputs,
        ),
        outputs=outputs,
        created_intents=created_intents,
    )
    if complete_failure is not None:
        return complete_failure
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="run_subgm_thread",
        reason="",
        created_intents=created_intents,
        created_messages=[],
        artifacts=[],
        detail=outputs,
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
        quality_metrics = agent_outputs.build_critic_quality_metrics(run_dir, story_output)
        critic_report = _dispatch_agent_payload(
            "critic",
            run_dir,
            root_dir,
            run_claude,
            {
                "story_input": story_input,
                "story_output": story_output,
                "quality_metrics": quality_metrics,
            },
        )
        critic_report = rp_generate_cli._normalize_critic_report_for_story(critic_report, story_output)
        write_artifact(run_dir, "critic.report.json", critic_report)
        artifacts.append("artifacts/critic.report.json")
        decision = str(critic_report.get("decision") or "")
        if decision == "pass":
            follow_up_payload = {
                "requested_by": "critic",
                "type": "run_postprocess",
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


def _execute_run_postprocess(
    run_dir: Path,
    card_folder: Path,
    root_dir: Path,
    intent: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str] | None,
) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "run_postprocess"})
    artifacts: list[str] = []

    try:
        story_input = read_artifact(run_dir, "story.input.json")
        story_output = read_artifact(run_dir, "story.output.json")
        critic_report = read_artifact(run_dir, "critic.report.json")
        critical_evidence = agent_outputs.extract_player_critical_action_evidence(story_input)
        context = {
            "story_input": story_input,
            "story_output": story_output,
            "critic_report": critic_report,
            "critical_action_evidence": critical_evidence,
            "pending_repairs": postprocess_outputs.read_pending_repairs(card_folder),
        }
        raw_output = _dispatch_agent_payload(
            "postprocess",
            run_dir,
            root_dir,
            run_claude,
            {"postprocess_context": context},
        )
        validation = postprocess_outputs.validate_postprocess_output(
            raw_output,
            critical_action_evidence=critical_evidence,
        )
        if not validation.get("ok"):
            blocked = agent_intents.block_intent(
                run_dir,
                intent_id,
                "postprocess_core_invalid",
                outputs={
                    "executor": "run_postprocess",
                    "validation": validation,
                    "artifacts": artifacts,
                },
            )
            _mark_blocked(run_dir, "postprocess_core_invalid", {"intent_id": intent_id, "validation": validation})
            return _result(
                False,
                "blocked",
                intent_id=intent_id,
                intent_type="run_postprocess",
                reason="postprocess_core_invalid",
                created_intents=[],
                created_messages=[],
                artifacts=artifacts,
                detail=blocked.get("result", {}),
            )

        postprocess = validation["output"]
        write_artifact(run_dir, "postprocess.output.json", postprocess)
        agent_run.write_json(run_dir / "postprocess.output.json", postprocess)
        artifacts.extend(["artifacts/postprocess.output.json", "postprocess.output.json"])

        repair_record = None
        if postprocess_outputs.ui_extensions_need_repair(postprocess):
            status = postprocess.get("ui_extension_status")
            reason = ""
            if isinstance(status, dict):
                reason = str(status.get("status") or "ui_extensions_need_repair")
            repair_record = postprocess_outputs.record_ui_extension_repair(
                run_dir,
                card_folder,
                reason=reason or "ui_extensions_need_repair",
                required_keys=postprocess_outputs.ui_extension_required_keys(postprocess),
                source_artifacts=["artifacts/postprocess.output.json"],
            )
            artifacts.append(f"artifacts/postprocess_repairs/{repair_record['id']}.json")

        follow_up_payload = {
            "requested_by": "postprocess",
            "type": "deliver_round",
            "payload": {
                "reason": "postprocess_core_valid",
                "postprocess_output_path": "artifacts/postprocess.output.json",
            },
            "policy": {"source_intent_id": intent_id},
        }
        follow_up = _ensure_follow_up_intent(run_dir, intent_id, follow_up_payload)
    except Exception as exc:
        return _block_executor_failure(run_dir, intent_id, "run_postprocess", "run_postprocess_failed", exc, artifacts)

    follow_up_id = str(follow_up.get("id") or "")
    created_intents = [follow_up_id] if follow_up.get("created") else []
    outputs = {
        "executor": "run_postprocess",
        "follow_up_intent_id": follow_up_id,
        "artifacts": artifacts,
    }
    if repair_record is not None:
        outputs["ui_extension_repair_id"] = repair_record.get("id")
    agent_intents.complete_intent(run_dir, intent_id, outputs=outputs)
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="run_postprocess",
        reason="",
        created_intents=created_intents,
        created_messages=[],
        artifacts=artifacts,
        detail=outputs,
    )


def _execute_repair_request(run_dir: Path, root_dir: Path, intent: dict[str, Any]) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "repair_request"})
    created_messages: list[str] = []

    try:
        payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
        report_path = str(payload.get("critic_report_path") or "artifacts/critic.report.json")
        critic_report = _read_critic_report(run_dir, report_path)
        routing_source = critic_report.get("repair_routing") if "repair_routing" in critic_report else payload.get("repair_routing")
        routing = self_repair.normalize_repair_routing(routing_source)
        repair_instruction = str(critic_report.get("repair_instruction") or payload.get("repair_instruction") or "")
        decision = str(critic_report.get("decision") or payload.get("decision") or "")
        repair_context = _repair_context(report_path, critic_report, payload, routing)

        if routing.get("stage") == "system_code":
            policy = self_repair.load_policy(root_dir / "skills" / "styles" / "settings.json")
            if not self_repair.policy_allows_route(policy, routing, decision):
                blocked = agent_intents.block_intent(
                    run_dir,
                    intent_id,
                    "source_code_self_repair_not_authorized",
                    outputs={
                        "executor": "repair_request",
                        "decision": decision,
                        "critic_report_path": report_path,
                        "repair_instruction": repair_instruction,
                        "repair_routing": routing,
                        "requires_source_repair_authorization": True,
                    },
                )
                _mark_blocked(
                    run_dir,
                    "source_code_self_repair_not_authorized",
                    {"intent_id": intent_id, "repair_routing": routing},
                )
                return _result(
                    False,
                    "blocked",
                    intent_id=intent_id,
                    intent_type="repair_request",
                    reason="source_code_self_repair_not_authorized",
                    created_intents=[],
                    created_messages=[],
                    artifacts=[],
                    detail=blocked.get("result", {}),
                )
            system_payload: dict[str, Any] = {
                "reason": "source_code_self_repair",
                "bounded": True,
                "requires": {
                    "selfRepairMode": "full",
                    "allowSourceCodeSelfRepair": True,
                },
                "critic_report_path": report_path,
                "repair_context": repair_context,
            }
            if repair_instruction:
                system_payload["repair_instruction"] = repair_instruction
            system_iteration_suggestion = str(critic_report.get("system_iteration_suggestion") or "")
            if system_iteration_suggestion:
                system_payload["system_iteration_suggestion"] = system_iteration_suggestion
            follow_up_payload = {
                "requested_by": "repair",
                "type": "system_request",
                "payload": system_payload,
                "policy": {"source_intent_id": intent_id},
            }
        elif routing.get("rollback") == "round_progression":
            follow_up_payload = {
                "requested_by": "repair",
                "type": "rollback_request",
                "payload": {
                    "mode": "round_progression",
                    "reason": "critic_repair",
                    "critic_report_path": report_path,
                    "repair_context": repair_context,
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
                "repair_context": repair_context,
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
        follow_up_id = str(follow_up.get("id") or "")
        if routing.get("stage") == "system_code" and follow_up.get("created"):
            message = agent_messages.append_message(
                run_dir,
                {
                    "from": "repair",
                    "to": ["system"],
                    "type": "system_request",
                    "visibility": "gm_only",
                    "payload": {"intent_id": follow_up_id, **follow_up_payload["payload"]},
                },
            )
            if not message.get("ok"):
                raise AgentDispatcherError(f"system_request message append failed: {message!r}")
            source_message_id = str((message.get("message") or {}).get("id") or "")
            if not source_message_id:
                raise AgentDispatcherError("system_request source message id is missing")
            attached = agent_intents.attach_source_message(run_dir, follow_up_id, source_message_id)
            if not attached.get("ok"):
                raise AgentDispatcherError(f"system_request source message attach failed: {attached!r}")
            created_messages.append(source_message_id)
    except Exception as exc:
        return _block_executor_failure(run_dir, intent_id, "repair_request", "repair_request_failed", exc, [])

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
        created_messages=created_messages,
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
        repair_context = payload.get("repair_context") if isinstance(payload.get("repair_context"), dict) else None
        if mode == "story_only":
            follow_up_run_dir = _active_run_dir(card_folder, run_dir)
            restore = _cleanup_story_only_artifacts(follow_up_run_dir)
        else:
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
            follow_up_run_dir = _restored_current_run_dir(card_folder)

        follow_up_type = "compose_story" if mode == "story_only" else "run_gm_turn"
        follow_up_payload: dict[str, Any] = {"rollback": restore}
        if repair_context is not None:
            follow_up_payload["repair_context"] = repair_context
        follow_up = _ensure_follow_up_intent(
            follow_up_run_dir,
            intent_id,
            {
                "requested_by": "rollback",
                "type": follow_up_type,
                "payload": follow_up_payload,
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


def _execute_system_request(run_dir: Path, intent: dict[str, Any]) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
    blocked = agent_intents.block_intent(
        run_dir,
        intent_id,
        "system_request_requires_main_agent",
        outputs={
            "executor": "system_request",
            "payload": payload,
            "note": "Source-code repair requires an explicit main-agent workflow; dispatcher will not edit source code automatically.",
        },
    )
    _mark_blocked(
        run_dir,
        "system_request_requires_main_agent",
        {"intent_id": intent_id, "payload": payload},
    )
    return _result(
        False,
        "blocked",
        intent_id=intent_id,
        intent_type="system_request",
        reason="system_request_requires_main_agent",
        created_intents=[],
        created_messages=[],
        artifacts=[],
        detail=blocked.get("result", {}),
    )


def _execute_assets_task(run_dir: Path, intent: dict[str, Any]) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
    agent_intents.accept_intent(run_dir, intent_id, outputs={"executor": "assets_task"})

    kind = str(payload.get("kind") or "scene").strip() or "scene"
    target = str(payload.get("target") or intent_id or "asset").strip() or "asset"
    prompt = str(payload.get("prompt") or "").strip()
    source = str(payload.get("source") or "").strip()
    artifact_relative = f"assets_tasks/{intent_id}.json"
    artifact_payload = {
        "schema_version": 1,
        "executor": "assets_task",
        "intent_id": intent_id,
        "requested_by": str(intent.get("requested_by") or ""),
        "kind": kind,
        "target": target,
        "prompt": prompt,
        "source": source,
        "status": "deferred",
        "nonblocking": True,
        "reason": "external_asset_generation_not_run_by_dispatcher",
    }
    write_artifact(run_dir, artifact_relative, artifact_payload)

    message_result = agent_messages.append_message(
        run_dir,
        {
            "from": "dispatcher",
            "to": ["assets"],
            "type": "assets_task",
            "visibility": "public",
            "payload": {
                "intent_id": intent_id,
                "artifact": f"artifacts/{artifact_relative}",
                "status": "deferred",
                "nonblocking": True,
                "kind": kind,
                "target": target,
                "reason": "external_asset_generation_not_run_by_dispatcher",
            },
        },
    )
    created_messages = []
    if message_result.get("ok") and isinstance(message_result.get("message"), dict):
        created_messages.append(str(message_result["message"].get("id") or ""))

    outputs = {
        "executor": "assets_task",
        "status": "deferred",
        "nonblocking": True,
        "artifact": f"artifacts/{artifact_relative}",
        "message_ids": created_messages,
    }
    agent_intents.complete_intent(run_dir, intent_id, outputs=outputs)
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="assets_task",
        reason="nonblocking_assets_task_deferred",
        created_intents=[],
        created_messages=created_messages,
        artifacts=[f"artifacts/{artifact_relative}"],
        detail=outputs,
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
        gate = _validate_deliver_postprocess_gate(run_dir, intent)
        if not gate.get("ok"):
            reason = str(gate.get("reason") or "postprocess_core_invalid")
            blocked = agent_intents.block_intent(
                run_dir,
                intent_id,
                reason,
                outputs={"executor": "deliver_round", **gate},
            )
            _mark_blocked(run_dir, reason, {"intent_id": intent_id, **gate})
            return _result(
                False,
                "blocked",
                intent_id=intent_id,
                intent_type="deliver_round",
                reason=reason,
                created_intents=[],
                created_messages=[],
                artifacts=[],
                detail=blocked.get("result", {}),
            )
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


def _validate_deliver_postprocess_gate(run_dir: Path, intent: dict[str, Any]) -> dict[str, Any]:
    payload = intent.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    postprocess_path = str(payload.get("postprocess_output_path") or "")
    if intent.get("requested_by") != "postprocess" or postprocess_path != "artifacts/postprocess.output.json":
        return {
            "ok": False,
            "reason": "postprocess_missing",
            "postprocess_output_path": postprocess_path,
        }

    try:
        postprocess = read_artifact(run_dir, "postprocess.output.json")
    except Exception as exc:
        return {
            "ok": False,
            "reason": "postprocess_missing",
            "postprocess_output_path": postprocess_path,
            "error": f"{type(exc).__name__}: {exc}",
        }

    critical_evidence = []
    try:
        story_input = read_artifact(run_dir, "story.input.json")
        critical_evidence = agent_outputs.extract_player_critical_action_evidence(story_input)
    except Exception:
        critical_evidence = []

    validation = postprocess_outputs.validate_postprocess_output(
        postprocess,
        critical_action_evidence=critical_evidence,
    )
    if not validation.get("ok"):
        return {
            "ok": False,
            "reason": "postprocess_core_invalid",
            "postprocess_output_path": postprocess_path,
            "validation": validation,
        }

    return {
        "ok": True,
        "reason": "",
        "postprocess_output_path": postprocess_path,
    }


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


def _repair_context(
    critic_report_path: str,
    critic_report: dict[str, Any],
    payload: dict[str, Any],
    routing: dict[str, Any],
) -> dict[str, Any]:
    context = {
        "critic_report_path": critic_report_path,
        "decision": str(critic_report.get("decision") or payload.get("decision") or ""),
        "repair_instruction": str(critic_report.get("repair_instruction") or payload.get("repair_instruction") or ""),
        "repair_routing": routing,
    }
    fingerprint = _first_text(payload.get("repair_fingerprint"), critic_report.get("repair_fingerprint"))
    if fingerprint:
        context["repair_fingerprint"] = fingerprint
    return context


def _active_run_dir(card_folder: Path, fallback_run_dir: Path) -> Path:
    current = agent_run.current_run_dir(card_folder)
    if current is None:
        return fallback_run_dir.resolve()
    return current.resolve()


def _restored_current_run_dir(card_folder: Path) -> Path:
    current = agent_run.current_run_dir(card_folder)
    if current is None:
        raise AgentDispatcherError(f"{Path(card_folder) / '.agent_runs' / 'current'} is missing after rollback restore")
    return current.resolve()


def _cleanup_story_only_artifacts(run_dir: Path) -> dict[str, Any]:
    removed: list[str] = []
    for relative in ("story.input.json", "story.output.json", "critic.report.json"):
        for path, label in ((run_dir / relative, relative), (run_dir / "artifacts" / relative, f"artifacts/{relative}")):
            if path.is_file() or path.is_symlink():
                path.unlink()
                removed.append(label)
    return {"ok": True, "mode": "story_only", "removed": removed, "strategy": "story_only_cleanup"}


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


def _find_intent_in_state(run_dir: Path, intent_id: str, state: str) -> dict[str, Any] | None:
    try:
        for item in agent_intents.list_intents(run_dir, state):
            if item.get("id") == intent_id:
                return item
    except Exception:
        return None
    return None


def _intent_exists(run_dir: Path, intent_id: str) -> bool:
    return any(
        _intent_in_state(run_dir, intent_id, state)
        for state in ("pending", "accepted", "rejected", "completed", "blocked")
    )


def _message_exists(run_dir: Path, message_id: str, message_type: str) -> bool:
    try:
        return any(
            message.get("id") == message_id and message.get("type") == message_type
            for message in agent_messages.read_messages(run_dir)
        )
    except Exception:
        return False


def _run_relative_file_exists(run_dir: Path, relative_path: str) -> bool:
    path = Path(relative_path)
    if path.is_absolute():
        return False
    try:
        root = run_dir.resolve()
        candidate = (run_dir / path).resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return False
    return candidate.is_file()


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


def _call_intent_transition(
    transition: Callable[..., dict[str, Any]],
    run_dir: Path,
    intent_id: str,
    *,
    outputs: dict[str, Any],
) -> dict[str, Any]:
    try:
        result = transition(run_dir, intent_id, outputs=outputs)
    except Exception as exc:
        return {
            "ok": False,
            "reason": "intent_transition_exception",
            "exception_type": type(exc).__name__,
        }
    if not isinstance(result, dict):
        return {
            "ok": False,
            "reason": "invalid_intent_transition_result",
            "result_type": type(result).__name__,
        }
    return result


def _request_projection_transition_failure(
    run_dir: Path,
    intent_id: str,
    reason: str,
    transition_result: dict[str, Any],
    *,
    outputs: dict[str, Any] | None = None,
    created_intents: list[str] | None = None,
    created_messages: list[str] | None = None,
) -> dict[str, Any] | None:
    if transition_result.get("ok"):
        return None

    detail = {
        "intent_id": intent_id,
        "reason": reason,
        "transition_reason": str(transition_result.get("reason") or "intent_transition_failed"),
    }
    exception_type = transition_result.get("exception_type")
    if isinstance(exception_type, str) and exception_type:
        detail["exception_type"] = exception_type
    result_type = transition_result.get("result_type")
    if isinstance(result_type, str) and result_type:
        detail["result_type"] = result_type
    if outputs is not None:
        detail["attempted_outputs"] = outputs

    blocked = agent_intents.block_intent(
        run_dir,
        intent_id,
        reason,
        outputs={"executor": "request_projection", **detail},
    )
    _mark_blocked(run_dir, reason, detail)
    return _result(
        False,
        "blocked",
        intent_id=intent_id,
        intent_type="request_projection",
        reason=reason,
        created_intents=created_intents,
        created_messages=created_messages,
        artifacts=[],
        detail=blocked.get("result", {}),
    )


def _run_actor_transition_failure(
    run_dir: Path,
    intent_id: str,
    reason: str,
    transition_result: dict[str, Any],
    *,
    outputs: dict[str, Any] | None = None,
    artifacts: list[str] | None = None,
    created_intents: list[str] | None = None,
    created_messages: list[str] | None = None,
) -> dict[str, Any] | None:
    if transition_result.get("ok"):
        return None

    if reason == "run_actor_complete_failed" and outputs is not None:
        recovered = _recover_completed_run_actor_transition(
            run_dir,
            intent_id,
            reason,
            outputs,
            artifacts=artifacts or [],
            created_intents=created_intents or [],
            created_messages=created_messages or [],
        )
        if recovered is not None:
            return recovered

    detail = {
        "intent_id": intent_id,
        "reason": reason,
        "transition_reason": str(transition_result.get("reason") or "intent_transition_failed"),
    }
    exception_type = transition_result.get("exception_type")
    if isinstance(exception_type, str) and exception_type:
        detail["exception_type"] = exception_type
    result_type = transition_result.get("result_type")
    if isinstance(result_type, str) and result_type:
        detail["result_type"] = result_type
    if outputs is not None:
        detail["attempted_outputs"] = outputs

    blocked = agent_intents.block_intent(
        run_dir,
        intent_id,
        reason,
        outputs={"executor": "run_actor", **detail, "artifacts": list(artifacts or [])},
    )
    _mark_blocked(run_dir, reason, detail)
    return _result(
        False,
        "blocked",
        intent_id=intent_id,
        intent_type="run_actor",
        reason=reason,
        created_intents=created_intents,
        created_messages=created_messages,
        artifacts=artifacts,
        detail=blocked.get("result", {}),
    )


def _recover_completed_run_actor_transition(
    run_dir: Path,
    intent_id: str,
    reason: str,
    outputs: dict[str, Any],
    *,
    artifacts: list[str],
    created_intents: list[str],
    created_messages: list[str],
) -> dict[str, Any] | None:
    completed_intent = _find_intent_in_state(run_dir, intent_id, "completed")
    if not completed_intent:
        return None

    actor_response_message_id = str(outputs.get("actor_response_message_id") or "")
    if not actor_response_message_id or not _message_exists(run_dir, actor_response_message_id, "actor_response"):
        return None

    attempted_artifacts = outputs.get("artifacts")
    if not isinstance(attempted_artifacts, list):
        attempted_artifacts = artifacts
    artifact_paths = [str(path) for path in attempted_artifacts if isinstance(path, str) and path]
    if "artifacts/actor.outputs.json" not in artifact_paths:
        return None
    if not all(_run_relative_file_exists(run_dir, path) for path in artifact_paths):
        return None

    follow_up_intent_id = str(outputs.get("follow_up_intent_id") or "")
    if follow_up_intent_id and not _intent_exists(run_dir, follow_up_intent_id):
        return None

    detail = dict(outputs)
    detail["transition_failure_recovered"] = reason
    detail["completed_intent_state"] = str(completed_intent.get("state") or "")
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="run_actor",
        reason="run_actor_complete_recovered",
        created_intents=created_intents,
        created_messages=created_messages,
        artifacts=artifact_paths,
        detail=detail,
    )


def _block_run_actor_failure(
    run_dir: Path,
    intent_id: str,
    reason: str,
    detail: dict[str, Any],
    artifacts: list[str],
    *,
    created_messages: list[str] | None = None,
) -> dict[str, Any]:
    blocked = agent_intents.block_intent(
        run_dir,
        intent_id,
        reason,
        outputs={"executor": "run_actor", "reason": reason, **detail, "artifacts": artifacts},
    )
    _mark_blocked(run_dir, reason, detail)
    return _result(
        False,
        "blocked",
        intent_id=intent_id,
        intent_type="run_actor",
        reason=reason,
        created_intents=[],
        created_messages=created_messages or [],
        artifacts=artifacts,
        detail=blocked.get("result", {}),
    )


def _run_gm_turn_transition_failure(
    run_dir: Path,
    intent_id: str,
    reason: str,
    transition_result: dict[str, Any],
    *,
    outputs: dict[str, Any] | None = None,
    artifacts: list[str] | None = None,
    created_intents: list[str] | None = None,
    created_messages: list[str] | None = None,
) -> dict[str, Any] | None:
    if transition_result.get("ok"):
        return None

    if reason == "run_gm_turn_complete_failed" and outputs is not None:
        recovered = _recover_completed_run_gm_turn_transition(
            run_dir,
            intent_id,
            reason,
            outputs,
            artifacts=artifacts or [],
            created_intents=created_intents or [],
            created_messages=created_messages or [],
        )
        if recovered is not None:
            return recovered

    detail = {
        "intent_id": intent_id,
        "reason": reason,
        "transition_reason": str(transition_result.get("reason") or "intent_transition_failed"),
    }
    exception_type = transition_result.get("exception_type")
    if isinstance(exception_type, str) and exception_type:
        detail["exception_type"] = exception_type
    result_type = transition_result.get("result_type")
    if isinstance(result_type, str) and result_type:
        detail["result_type"] = result_type
    if outputs is not None:
        detail["attempted_outputs"] = outputs

    blocked = agent_intents.block_intent(
        run_dir,
        intent_id,
        reason,
        outputs={"executor": "run_gm_turn", **detail, "artifacts": list(artifacts or [])},
    )
    _mark_blocked(run_dir, reason, detail)
    return _result(
        False,
        "blocked",
        intent_id=intent_id,
        intent_type="run_gm_turn",
        reason=reason,
        created_intents=created_intents or [],
        created_messages=created_messages or [],
        artifacts=artifacts or [],
        detail=blocked.get("result", {}),
    )


def _recover_completed_run_gm_turn_transition(
    run_dir: Path,
    intent_id: str,
    reason: str,
    outputs: dict[str, Any],
    *,
    artifacts: list[str],
    created_intents: list[str],
    created_messages: list[str],
) -> dict[str, Any] | None:
    completed_intent = _find_intent_in_state(run_dir, intent_id, "completed")
    if not completed_intent:
        return None

    attempted_artifacts = outputs.get("artifacts")
    if not isinstance(attempted_artifacts, list):
        attempted_artifacts = artifacts
    artifact_paths = [str(path) for path in attempted_artifacts if isinstance(path, str) and path]
    if "artifacts/gm.output.json" not in artifact_paths:
        return None
    if not all(_run_relative_file_exists(run_dir, path) for path in artifact_paths):
        return None

    attempted_intents: list[str] = []
    for key in ("created_projection_intents", "created_subgm_intents", "created_progression_intents"):
        value = outputs.get(key)
        if isinstance(value, list):
            attempted_intents.extend(str(item) for item in value if isinstance(item, str) and item)
    if not attempted_intents:
        attempted_intents = created_intents
    if not all(_intent_exists(run_dir, follow_up_id) for follow_up_id in attempted_intents):
        return None

    attempted_messages = outputs.get("created_messages")
    if not isinstance(attempted_messages, list):
        attempted_messages = created_messages
    message_ids = [str(item) for item in attempted_messages if isinstance(item, str) and item]
    if not all(_message_exists(run_dir, message_id, "request_actor") for message_id in message_ids):
        return None

    detail = dict(outputs)
    detail["transition_failure_recovered"] = reason
    detail["completed_intent_state"] = str(completed_intent.get("state") or "")
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="run_gm_turn",
        reason="run_gm_turn_complete_recovered",
        created_intents=created_intents,
        created_messages=created_messages,
        artifacts=artifact_paths,
        detail=detail,
    )


def _run_subgm_thread_transition_failure(
    run_dir: Path,
    intent_id: str,
    reason: str,
    transition_result: dict[str, Any],
    *,
    outputs: dict[str, Any] | None = None,
    created_intents: list[str] | None = None,
) -> dict[str, Any] | None:
    if transition_result.get("ok"):
        return None

    if reason == "run_subgm_thread_complete_failed" and outputs is not None:
        recovered = _recover_completed_run_subgm_thread_transition(
            run_dir,
            intent_id,
            reason,
            outputs,
            created_intents=created_intents or [],
        )
        if recovered is not None:
            return recovered

    detail = {
        "intent_id": intent_id,
        "reason": reason,
        "transition_reason": str(transition_result.get("reason") or "intent_transition_failed"),
    }
    exception_type = transition_result.get("exception_type")
    if isinstance(exception_type, str) and exception_type:
        detail["exception_type"] = exception_type
    result_type = transition_result.get("result_type")
    if isinstance(result_type, str) and result_type:
        detail["result_type"] = result_type
    if outputs is not None:
        detail["attempted_outputs"] = outputs

    blocked = agent_intents.block_intent(
        run_dir,
        intent_id,
        reason,
        outputs={"executor": "run_subgm_thread", **detail},
    )
    _mark_blocked(run_dir, reason, detail)
    return _result(
        False,
        "blocked",
        intent_id=intent_id,
        intent_type="run_subgm_thread",
        reason=reason,
        created_intents=created_intents,
        created_messages=[],
        artifacts=[],
        detail=blocked.get("result", {}),
    )


def _recover_completed_run_subgm_thread_transition(
    run_dir: Path,
    intent_id: str,
    reason: str,
    outputs: dict[str, Any],
    *,
    created_intents: list[str],
) -> dict[str, Any] | None:
    completed_intent = _find_intent_in_state(run_dir, intent_id, "completed")
    if not completed_intent:
        return None

    follow_up_intent_id = str(outputs.get("follow_up_intent_id") or "")
    if follow_up_intent_id and not _intent_exists(run_dir, follow_up_intent_id):
        return None

    detail = dict(outputs)
    detail["transition_failure_recovered"] = reason
    detail["completed_intent_state"] = str(completed_intent.get("state") or "")
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="run_subgm_thread",
        reason="run_subgm_thread_complete_recovered",
        created_intents=created_intents,
        created_messages=[],
        artifacts=[],
        detail=detail,
    )


def _block_run_subgm_thread_failure(
    run_dir: Path,
    intent_id: str,
    reason: str,
    detail: dict[str, Any],
) -> dict[str, Any]:
    blocked = agent_intents.block_intent(
        run_dir,
        intent_id,
        reason,
        outputs={"executor": "run_subgm_thread", "reason": reason, **detail},
    )
    _mark_blocked(run_dir, reason, detail)
    return _result(
        False,
        "blocked",
        intent_id=intent_id,
        intent_type="run_subgm_thread",
        reason=reason,
        created_intents=[],
        created_messages=[],
        artifacts=[],
        detail=blocked.get("result", {}),
    )


def _block_projection_failure(
    run_dir: Path,
    intent_id: str,
    exc: agent_actor_runtime.AgentActorProjectionError,
) -> dict[str, Any]:
    reason = exc.reason
    detail = {
        "intent_id": intent_id,
        "reason": reason,
        **exc.detail,
    }
    blocked = agent_intents.block_intent(
        run_dir,
        intent_id,
        reason,
        outputs={"executor": "request_projection", **detail},
    )
    _mark_blocked(run_dir, reason, detail)
    return _result(
        False,
        "blocked",
        intent_id=intent_id,
        intent_type="request_projection",
        reason=reason,
        created_intents=[],
        created_messages=[],
        artifacts=[],
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
    accept_failure = _run_gm_turn_transition_failure(
        run_dir,
        intent_id,
        "run_gm_turn_accept_failed",
        _call_intent_transition(
            agent_intents.accept_intent,
            run_dir,
            intent_id,
            outputs={"executor": "run_gm_turn"},
        ),
    )
    if accept_failure is not None:
        return accept_failure
    artifacts: list[str] = []

    try:
        if run_claude is None:
            raise AgentDispatcherError("run_claude is required for run_gm_turn")
        payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
        repair_context = payload.get("repair_context") if isinstance(payload.get("repair_context"), dict) else None

        def dispatch(agent_key: str, packet: dict[str, Any]) -> dict[str, Any]:
            extra_context: dict[str, Any] = {
                "packet": packet,
                "loop_packet": packet,
                "intent_type": "run_gm_turn",
                "source_intent_id": intent_id,
            }
            if repair_context is not None and agent_key == "gm":
                extra_context["repair_context"] = repair_context
            return _dispatch_agent_payload(agent_key, run_dir, root_dir, run_claude, extra_context)

        loop_result = agent_turn_loop.run_gm_only_step(run_dir, dispatch)
        artifacts = [_refresh_root_artifact_to_authority(run_dir, "gm.output.json")]

        created_messages: list[str] = []
        created_projection_intents: list[str] = []
        created_subgm_intents: list[str] = []
        created_progression_intents: list[str] = []
        follow_up_id = ""

        player_decision_required = str(loop_result.get("stop_reason") or "") == "player_decision"
        actor_calls = [call for call in loop_result.get("actor_calls", []) if isinstance(call, dict)]
        runnable_side_threads = [
            summary for summary in loop_result.get("runnable_side_threads", []) if isinstance(summary, dict)
        ]
        expected_actor_call_ids = [
            str(call.get("call_id") or "")
            for call in actor_calls
            if str(call.get("actor_id") or "") and str(call.get("call_id") or "")
        ]
        fanout = None
        if len(expected_actor_call_ids) > 1:
            fanout = {
                "batch_id": f"gm-fanout-{intent_id}",
                "source_gm_intent_id": intent_id,
                "expected_source_call_ids": expected_actor_call_ids,
            }

        if not player_decision_required:
            for call in actor_calls:
                actor_id = str(call.get("actor_id") or "")
                if not actor_id:
                    continue
                message_id, projection_intent_id = agent_actor_runtime.record_request_actor(
                    run_dir,
                    "gm",
                    actor_id,
                    call,
                    source_intent_id=intent_id,
                    fanout=fanout,
                )
                created_messages.append(message_id)
                created_projection_intents.append(projection_intent_id)

            for summary in runnable_side_threads:
                thread_id = str(summary.get("thread_id") or "")
                if not thread_id:
                    continue
                follow_up = _ensure_follow_up_intent_by_payload(
                    run_dir,
                    intent_id,
                    {
                        "requested_by": "gm",
                        "type": "run_subgm_thread",
                        "payload": {
                            "thread_id": thread_id,
                            "reason": "gm_requested_side_thread",
                        },
                        "policy": {"source_intent_id": intent_id},
                    },
                    payload_keys=("thread_id",),
                )
                subgm_intent_id = str(follow_up.get("id") or "")
                if follow_up.get("created"):
                    created_subgm_intents.append(subgm_intent_id)

            if not created_projection_intents and not created_subgm_intents:
                stop_reason = str(loop_result.get("stop_reason") or "")
                if stop_reason in {"complete", "word_target", "max_steps"}:
                    follow_up_payload: dict[str, Any] = {
                        "reason": "gm_step_complete",
                        "loop_result": loop_result,
                    }
                    if repair_context is not None:
                        follow_up_payload["repair_context"] = repair_context
                    follow_up = _ensure_follow_up_intent(
                        run_dir,
                        intent_id,
                        {
                            "requested_by": "gm",
                            "type": "compose_story",
                            "payload": follow_up_payload,
                            "policy": {"source_intent_id": intent_id},
                        },
                    )
                    follow_up_id = str(follow_up.get("id") or "")
                    if follow_up.get("created"):
                        created_progression_intents.append(follow_up_id)
                else:
                    follow_up = _ensure_follow_up_intent(
                        run_dir,
                        intent_id,
                        {
                            "requested_by": "gm",
                            "type": "run_gm_turn",
                            "payload": {
                                "reason": "gm_step_continue",
                                "loop_result": loop_result,
                            },
                            "policy": {"source_intent_id": intent_id},
                        },
                    )
                    follow_up_id = str(follow_up.get("id") or "")
                    if follow_up.get("created"):
                        created_progression_intents.append(follow_up_id)
    except Exception as exc:
        return _block_executor_failure(run_dir, intent_id, "run_gm_turn", "run_gm_turn_failed", exc, artifacts)

    created_intents = [*created_projection_intents, *created_subgm_intents, *created_progression_intents]
    outputs = {
        "executor": "run_gm_turn",
        "loop_result": loop_result,
        "follow_up_intent_id": follow_up_id,
        "player_decision_required": player_decision_required,
        "created_projection_intents": created_projection_intents,
        "created_subgm_intents": created_subgm_intents,
        "created_progression_intents": created_progression_intents,
        "created_messages": created_messages,
        "artifacts": artifacts,
    }
    complete_failure = _run_gm_turn_transition_failure(
        run_dir,
        intent_id,
        "run_gm_turn_complete_failed",
        _call_intent_transition(
            agent_intents.complete_intent,
            run_dir,
            intent_id,
            outputs=outputs,
        ),
        outputs=outputs,
        artifacts=artifacts,
        created_intents=created_intents,
        created_messages=created_messages,
    )
    if complete_failure is not None:
        return complete_failure
    return _result(
        True,
        "completed",
        intent_id=intent_id,
        intent_type="run_gm_turn",
        reason="",
        created_intents=created_intents,
        created_messages=created_messages,
        artifacts=artifacts,
        detail=outputs,
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


def _ensure_follow_up_intent_by_payload(
    run_dir: Path,
    source_intent_id: str,
    payload: dict[str, Any],
    *,
    payload_keys: tuple[str, ...],
) -> dict[str, Any]:
    expected_payload = payload.get("payload")
    if not isinstance(expected_payload, dict):
        expected_payload = {}
    for state in agent_intents.VALID_STATES:
        for existing in agent_intents.list_intents(run_dir, state):
            policy = existing.get("policy")
            if not isinstance(policy, dict):
                continue
            if policy.get("source_intent_id") != source_intent_id:
                continue
            if existing.get("type") != payload.get("type"):
                continue
            existing_payload = existing.get("payload")
            if not isinstance(existing_payload, dict):
                continue
            if all(existing_payload.get(key) == expected_payload.get(key) for key in payload_keys):
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


def _actor_output_requires_gm_resolution(actor_output: dict[str, Any]) -> bool:
    for event in actor_output.get("events", []):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        if event_type == "perceive_request":
            return True
        if event_type == "custom_action":
            metadata = event.get("metadata")
            if isinstance(metadata, dict) and metadata.get("requires_gm_resolution") is True:
                return True
    return False


def _actor_output_requires_player_decision(actor_id: str, actor_output: dict[str, Any]) -> bool:
    if str(actor_output.get("stop_reason") or "") == "stop_for_player_decision":
        return True
    for event in actor_output.get("events", []):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        if event_type == "stop_for_player_decision":
            return True
        if event_type == "custom_action" and actor_id == "player":
            metadata = event.get("metadata")
            if isinstance(metadata, dict) and str(metadata.get("risk_level") or "") in {"high", "critical"}:
                return True
    return False


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
