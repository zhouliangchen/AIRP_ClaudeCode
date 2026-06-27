"""Thin default runtime for one prepared RP round."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable

import agent_messages
import agent_memory
import agent_outputs
import agent_prompts
import agent_run
import agent_runtime_pump
import agent_snapshots
import agent_turn_loop
import input_analysis_apply
import input_routing_requests
import postprocess_outputs
import retcon_replay
import rp_generate_cli


class RoundRuntimeError(RuntimeError):
    """Raised when the thin runtime cannot continue."""


_INPUT_ANALYSIS_APPLY_ALLOWED_STAGES = {
    "",
    "prepared",
    "prompts_ready",
    "awaiting_agent_outputs",
    "analysis_applied",
}


def run_round(
    card_folder: str | Path,
    root_dir: str | Path,
    *,
    run_claude: Callable[[str, str, str | Path], str],
    run_command: Callable[..., Any],
) -> dict[str, Any]:
    card = Path(card_folder).resolve()
    root = Path(root_dir).resolve()
    run_dir = agent_run.current_run_dir(card)
    if run_dir is None:
        raise RoundRuntimeError(f"{card / '.agent_runs' / 'current'} is missing or invalid.")
    run_dir = Path(run_dir)
    manifest = _load_manifest(run_dir)
    stages: list[str] = []
    runtime_snapshot = agent_snapshots.create_snapshot(
        card,
        str(manifest.get("round_id") or run_dir.name),
        reason="before_round_runtime",
    )

    def restore_failed_runtime_state(reason: str) -> dict[str, Any]:
        snapshot_id = str(runtime_snapshot.get("snapshot_id") or "")
        if not snapshot_id:
            return {"ok": False, "reason": "snapshot_missing"}
        return agent_snapshots.restore_snapshot(
            card,
            snapshot_id,
            mode=f"round_runtime_{reason}",
        )

    try:
        input_analysis_result = _ensure_input_analysis(card, root, run_dir, manifest, run_claude)
        stages.append("input_analysis")
        if input_analysis_result.get("action") == "retcon_replay_prepared":
            return {
                "ok": False,
                "action": "retcon_replay_prepared",
                "run_dir": str(run_dir),
                "runtime": {"mode": "thin", "stages": stages},
                "input_analysis": input_analysis_result,
            }
        runtime_pump = {
            "after_input_analysis": input_analysis_result.get("runtime_pump", {}).get(
                "after_input_analysis",
                {"ok": True, "phase": "after_input_analysis", "processed": [], "blocked": [], "rejected": [], "deferred": []},
            )
        }

        loop_result = _run_gm_collaboration(card, root, run_dir, manifest, run_claude)
        stages.append("gm_collaboration")

        story_input = agent_outputs.build_relaxed_story_input(run_dir)
        story_output = _run_story(root, run_dir, manifest, run_claude, story_input)
        stages.append("story")

        critic = _run_critic(root, run_dir, manifest, run_claude, story_input, story_output)
        stages.append("critic")
        repair_attempts = 0
        while str(critic.get("decision") or "") != "pass" and _critic_allows_story_repair(critic):
            if repair_attempts >= 1:
                break
            repair_attempts += 1
            _record_story_repair_attempt(run_dir, critic, repair_attempts)
            story_output = _run_story(
                root,
                run_dir,
                manifest,
                run_claude,
                story_input,
                repair_context={
                    "critic_report": critic,
                    "repair_instruction": str(critic.get("repair_instruction") or ""),
                    "previous_rejected_story_output": story_output,
                },
            )
            stages.append("story_repair")
            critic = _run_critic(root, run_dir, manifest, run_claude, story_input, story_output)
            stages.append("critic_repair")

        if str(critic.get("decision") or "") != "pass":
            result = _blocked(
                run_dir,
                stages,
                "critic_requires_revision",
                {"critic": critic, "loop_result": loop_result},
            )
            result["rollback"] = restore_failed_runtime_state("blocked")
            _write_artifact(run_dir, "runtime.result.json", result)
            return result

        runtime_pump["after_critic"] = agent_runtime_pump.run_pending_intents(
            card,
            run_dir,
            phase="after_critic",
            runtime_settings=_runtime_settings_from_applied(input_analysis_result),
            run_command=run_command,
        )

        _run_postprocess(card, root, run_dir, run_claude, story_input, story_output)
        stages.append("postprocess")

        delivery = _run_delivery(card, root, run_dir, run_command)
        stages.append("delivery")
        ok = rp_generate_cli._delivery_complete(delivery)
        post_round_memory = {"ok": True, "status": "not_required", "scheduled": []}
        if ok:
            post_round_memory = _run_post_round_memory_jobs(card, root, run_dir, run_claude)
            if post_round_memory.get("status") != "not_required":
                stages.append("post_round_memory")
        replay_advance = {"ok": True, "action": "not_required"}
        if ok:
            replay_advance = retcon_replay.advance_after_delivery(card)
        result = {
            "ok": ok,
            "action": "generated" if ok else "blocked",
            "run_dir": str(run_dir),
            "runtime": {"mode": "thin", "stages": stages},
            "input_analysis": input_analysis_result,
            "delivery": delivery,
            "loop_result": loop_result,
            "runtime_pump": runtime_pump,
            "post_round_memory": post_round_memory,
        }
        if replay_advance.get("action") != "not_required":
            result["retcon_replay"] = replay_advance
        if not ok:
            result["rollback"] = restore_failed_runtime_state("blocked")
        _write_artifact(run_dir, "runtime.result.json", result)
        if ok:
            agent_run.update_manifest_stage(run_dir, "delivered", "Thin runtime delivery completed.")
        else:
            agent_run.update_manifest_stage(run_dir, "blocked", "Thin runtime delivery did not complete.")
        return result
    except Exception:
        restore_failed_runtime_state("error")
        raise


def _load_manifest(run_dir: Path) -> dict[str, Any]:
    manifest = agent_run.read_json(run_dir / "manifest.json", {}) or {}
    if not isinstance(manifest, dict):
        raise RoundRuntimeError(f"{run_dir / 'manifest.json'} is missing or invalid.")
    return manifest


def _artifact_path(run_dir: Path, relative_path: str) -> Path:
    relative = Path(str(relative_path))
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise RoundRuntimeError(f"artifact path must stay inside artifacts: {relative_path}")
    return run_dir / "artifacts" / relative


def _write_artifact(run_dir: Path, relative_path: str, payload: dict[str, Any]) -> Path:
    path = _artifact_path(run_dir, relative_path)
    agent_run.write_json(path, payload)
    return path


def _copy_to_artifact(run_dir: Path, source_name: str) -> None:
    source = run_dir / source_name
    if not source.exists():
        return
    destination = _artifact_path(run_dir, source_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _input_analysis_apply_allowed(stage: Any) -> bool:
    return str(stage or "") in _INPUT_ANALYSIS_APPLY_ALLOWED_STAGES


def _read_input_analysis_output(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RoundRuntimeError(f"{path}: input analysis output is invalid JSON.") from exc
    except OSError as exc:
        raise RoundRuntimeError(f"{path}: input analysis output cannot be read.") from exc
    if not isinstance(payload, dict):
        raise RoundRuntimeError(f"{path}: input analysis output must be a JSON object.")
    return payload


def _reuse_applied_input_analysis(
    output_path: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    source_path = output_path
    artifact_path = output_path.parent / "artifacts" / output_path.name
    if not source_path.exists() and artifact_path.exists():
        source_path = artifact_path
    payload = dict(_read_input_analysis_output(source_path))
    if source_path != output_path:
        agent_run.write_json(output_path, payload)
    payload.setdefault("ok", True)
    if not isinstance(payload.get("manifest"), dict):
        runtime_settings = manifest.get("runtime_settings")
        style_profile = manifest.get("style_profile")
        payload["manifest"] = {
            "runtime_settings": runtime_settings if isinstance(runtime_settings, dict) else {},
            "style_profile": style_profile if isinstance(style_profile, dict) else {},
        }
    payload.setdefault("capability_requests", [])
    payload.setdefault(
        "runtime_pump",
        {
            "after_input_analysis": {
                "ok": True,
                "phase": "after_input_analysis",
                "processed": [],
                "blocked": [],
                "rejected": [],
                "deferred": [],
                "skipped": [
                    {
                        "type": "input_analysis_apply",
                        "reason": "already_applied",
                        "stage": str(manifest.get("stage") or ""),
                    }
                ],
            }
        },
    )
    return payload


def _prompt_text(run_dir: Path, manifest: dict[str, Any], key: str, default: str) -> str:
    prompts = manifest.get("prompts") if isinstance(manifest.get("prompts"), dict) else {}
    relative = prompts.get(key) if isinstance(prompts.get(key), str) else default
    path = run_dir / str(relative)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoundRuntimeError(f"{path}: prompt is missing.") from exc


def _dispatch(
    run_dir: Path,
    root: Path,
    run_claude: Callable[[str, str, str | Path], str],
    agent_key: str,
    prompt: str,
    *,
    extra_context: dict[str, Any] | None = None,
    output_path: Path | None = None,
    attempts: int = 2,
    initial_error: rp_generate_cli.AgentExecutionError | None = None,
) -> dict[str, Any]:
    payload = rp_generate_cli._dispatch_agent_payload(
        agent_key,
        prompt,
        root,
        run_claude,
        extra_context=extra_context or {},
        attempts=attempts,
        initial_error=initial_error,
    )
    if output_path is not None:
        agent_run.write_json(output_path, payload)
    return payload


def _ensure_input_analysis(
    card: Path,
    root: Path,
    run_dir: Path,
    manifest: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str],
) -> dict[str, Any]:
    output_path = run_dir / "input_analysis.output.json"
    if not _input_analysis_apply_allowed(manifest.get("stage")):
        return _reuse_applied_input_analysis(output_path, manifest)

    prompt: str | None = None
    last_error: rp_generate_cli.AgentExecutionError | None = None
    for attempt in range(2):
        if not output_path.exists():
            if prompt is None:
                prompt = _prompt_text(run_dir, manifest, "input_analyst", "prompts/input_analyst.prompt.md")
            _dispatch(
                run_dir,
                root,
                run_claude,
                "input_analyst",
                prompt,
                output_path=output_path,
                attempts=1,
                initial_error=last_error,
            )
        replay = retcon_replay.prepare_replay_from_current_run(card, run_dir)
        if replay.get("action") == "retcon_replay_prepared":
            return {
                "ok": True,
                "action": "retcon_replay_prepared",
                "run_dir": str(run_dir),
                "retcon_replay": replay,
            }
        if replay.get("ok") is False:
            raise RoundRuntimeError(f"retcon replay preparation failed: {replay}")
        try:
            applied = input_analysis_apply.apply_current_run(card, root)
            break
        except Exception as exc:
            last_error = rp_generate_cli.AgentExecutionError(f"input analysis apply failed: {exc}")
            try:
                output_path.unlink()
            except OSError:
                pass
            if attempt == 1:
                raise RoundRuntimeError(str(last_error)) from exc
    else:
        raise RoundRuntimeError("input analysis apply failed without an error.")
    applied = applied if isinstance(applied, dict) else {}
    capability_requests = applied.get("capability_requests")
    if not isinstance(capability_requests, list):
        capability_requests = []
    applied_manifest = applied.get("manifest") if isinstance(applied.get("manifest"), dict) else {}
    runtime_settings = (
        applied_manifest.get("runtime_settings")
        if isinstance(applied_manifest.get("runtime_settings"), dict)
        else {}
    )
    applied["capability_requests_result"] = input_routing_requests.process_capability_requests(
        run_dir,
        capability_requests,
        runtime_settings=runtime_settings,
        source_intent_id="input_analysis",
    )
    applied["runtime_pump"] = {
        "after_input_analysis": agent_runtime_pump.run_pending_intents(
            card,
            run_dir,
            phase="after_input_analysis",
            runtime_settings=runtime_settings,
        )
    }
    _copy_to_artifact(run_dir, "input_analysis.output.json")
    _append_message_once(
        run_dir,
        "analysis_applied",
        {
            "from": "input_analyst",
            "to": ["gm", "main_agent"],
            "type": "analysis_applied",
            "visibility": "gm_only",
            "payload": {"applied": applied},
        },
    )
    return applied


def _runtime_settings_from_applied(applied: dict[str, Any]) -> dict[str, Any]:
    manifest = applied.get("manifest") if isinstance(applied.get("manifest"), dict) else {}
    settings = manifest.get("runtime_settings") if isinstance(manifest.get("runtime_settings"), dict) else {}
    return settings


def _append_message_once(run_dir: Path, message_type: str, payload: dict[str, Any]) -> None:
    for message in agent_messages.read_messages(run_dir):
        if isinstance(message, dict) and message.get("type") == message_type:
            return
    agent_messages.append_message(run_dir, payload)


def _run_gm_collaboration(
    card: Path,
    root: Path,
    run_dir: Path,
    manifest: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str],
) -> dict[str, Any]:
    def dispatch(agent_key: str, packet: dict[str, Any]) -> dict[str, Any]:
        prompt = rp_generate_cli._read_loop_prompt(run_dir, manifest, agent_key, packet)
        context_key = "projection_packet" if agent_key == "projection" else "loop_packet"
        return _dispatch(
            run_dir,
            root,
            run_claude,
            agent_key,
            prompt,
            extra_context={context_key: packet},
        )

    try:
        result = agent_turn_loop.run_interactive_loop(run_dir, dispatch, card_folder=card)
    except agent_turn_loop.AgentTurnLoopError as exc:
        raise RoundRuntimeError(str(exc)) from exc
    for artifact_name in ("gm.output.json", "actor.outputs.json", "interaction.trace.json"):
        _copy_to_artifact(run_dir, artifact_name)
    return result if isinstance(result, dict) else {}


def _run_story(
    root: Path,
    run_dir: Path,
    manifest: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str],
    story_input: dict[str, Any],
    repair_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    story_context = agent_outputs.story_prompt_context(story_input)
    runtime_input = {"story_input": story_context}
    if isinstance(repair_context, dict) and repair_context:
        runtime_input.update(repair_context)
    prompt = _prompt_text(run_dir, manifest, "story", "prompts/story.prompt.md")
    story_output = _dispatch(
        run_dir,
        root,
        run_claude,
        "story",
        prompt,
        extra_context=runtime_input,
    )
    story_output = rp_generate_cli._normalize_story_output(story_output, story_context)
    agent_run.write_json(run_dir / "story.output.json", story_output)
    _write_artifact(run_dir, "story.output.json", story_output)
    return story_output


def _run_critic(
    root: Path,
    run_dir: Path,
    manifest: dict[str, Any],
    run_claude: Callable[[str, str, str | Path], str],
    story_input: dict[str, Any],
    story_output: dict[str, Any],
) -> dict[str, Any]:
    story_context = agent_outputs.story_prompt_context(story_input)
    quality_metrics = agent_outputs.build_critic_quality_metrics(run_dir, story_output)
    prompt = _prompt_text(run_dir, manifest, "critic", "prompts/critic.prompt.md")
    critic = _dispatch(
        run_dir,
        root,
        run_claude,
        "critic",
        prompt,
        extra_context={
            "story_input": story_context,
            "story_output": story_output,
            "quality_metrics": quality_metrics,
        },
    )
    critic = rp_generate_cli._normalize_critic_report_for_story(critic, story_output, story_context)
    agent_run.write_json(run_dir / "critic.report.json", critic)
    _write_artifact(run_dir, "critic.report.json", critic)
    return critic


def _critic_allows_story_repair(critic: dict[str, Any]) -> bool:
    routing = critic.get("repair_routing")
    if not isinstance(routing, dict):
        return False
    if routing.get("can_auto_repair") is not True:
        return False
    if str(routing.get("rollback") or "") != "story_only":
        return False
    targets = routing.get("target_agents")
    if isinstance(targets, list) and targets:
        return all(str(item) == "story" for item in targets)
    return str(routing.get("stage") or "") == "story_composition"


def _record_story_repair_attempt(run_dir: Path, critic: dict[str, Any], attempt: int) -> None:
    manifest = agent_run.read_json(run_dir / "manifest.json", {}) or {}
    if not isinstance(manifest, dict):
        manifest = {}
    manifest["critic_retry_count"] = int(manifest.get("critic_retry_count", 0) or 0) + 1
    agent_run.write_json(run_dir / "manifest.json", manifest)
    record = {
        "attempt": attempt,
        "stage": "story_composition",
        "rollback": "story_only",
        "repair_instruction": critic.get("repair_instruction", ""),
        "hard_failures": critic.get("hard_failures", []),
        "repair_routing": critic.get("repair_routing", {}),
    }
    with (run_dir / "repair_history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _run_postprocess(
    card: Path,
    root: Path,
    run_dir: Path,
    run_claude: Callable[[str, str, str | Path], str],
    story_input: dict[str, Any],
    story_output: dict[str, Any],
) -> dict[str, Any]:
    context = {
        "story_input": agent_outputs.story_prompt_context(story_input),
        "story_output": story_output,
        "pending_repairs": postprocess_outputs.read_pending_repairs(card),
        "postprocess_contract": postprocess_outputs.load_postprocess_contract(card),
    }
    prompt = agent_prompts.build_postprocess_prompt({"postprocess_context": context})
    raw = _dispatch(
        run_dir,
        root,
        run_claude,
        "postprocess",
        prompt,
        extra_context={"postprocess_context": context},
    )
    validation = postprocess_outputs.validate_postprocess_output(
        raw,
        critical_action_evidence=agent_outputs.extract_player_critical_action_evidence(story_input),
    )
    if not validation.get("ok"):
        raise RoundRuntimeError(f"postprocess output rejected: {validation}")
    output = validation.get("output")
    if not isinstance(output, dict):
        raise RoundRuntimeError("postprocess output normalization failed.")
    _write_artifact(run_dir, "postprocess.output.json", output)
    return output


def _run_post_round_memory_jobs(
    card: Path,
    root: Path,
    run_dir: Path,
    run_claude: Callable[[str, str, str | Path], str],
) -> dict[str, Any]:
    scheduled_result = agent_memory.schedule_post_round_memory_jobs(card, run_dir)
    scheduled_agents = scheduled_result.get("scheduled")
    if not isinstance(scheduled_agents, list) or not scheduled_agents:
        return {
            "ok": True,
            "status": "not_required",
            "scheduled": [],
            "ingested": [],
            "missing": {},
            "failed": {},
        }

    manifest = agent_run.read_json(run_dir / "manifest.json", {}) or {}
    jobs = manifest.get("post_round_memory_jobs") if isinstance(manifest, dict) else {}
    scheduled = jobs.get("scheduled") if isinstance(jobs, dict) else {}
    if not isinstance(scheduled, dict):
        raise RoundRuntimeError("post_round_memory_jobs.scheduled is missing or invalid.")
    objective_jobs = manifest.get("post_round_objective_memory_jobs") if isinstance(manifest, dict) else {}
    objective_scheduled = objective_jobs.get("scheduled") if isinstance(objective_jobs, dict) else {}
    if objective_scheduled and not isinstance(objective_scheduled, dict):
        raise RoundRuntimeError("post_round_objective_memory_jobs.scheduled is missing or invalid.")

    failed: dict[str, str] = {}
    for agent_id in sorted(str(item) for item in scheduled_agents):
        entry = scheduled.get(agent_id)
        if not isinstance(entry, dict):
            failed[agent_id] = "post_round_memory job entry is missing"
            continue
        prompt_rel = str(entry.get("prompt") or "").strip()
        job_rel = str(entry.get("job") or "").strip()
        output_rel = str(entry.get("output") or "").strip()
        if not (prompt_rel and job_rel and output_rel):
            failed[agent_id] = "post_round_memory job paths are incomplete"
            continue
        try:
            prompt = (run_dir / prompt_rel).read_text(encoding="utf-8")
            job_payload = agent_run.read_json(run_dir / job_rel, {}) or {}
            if not isinstance(job_payload, dict):
                raise RoundRuntimeError(f"{job_rel}: post_round_memory job payload must be an object.")
            _dispatch(
                run_dir,
                root,
                run_claude,
                f"post_round_memory:{agent_run.safe_name(agent_id)}",
                prompt,
                extra_context={
                    "card_folder": str(card),
                    "post_round_memory_job": job_payload,
                    "post_round_output_path": output_rel,
                },
                output_path=run_dir / output_rel,
            )
        except Exception as exc:
            failed[agent_id] = str(exc)
        objective_entry = objective_scheduled.get(agent_id) if isinstance(objective_scheduled, dict) else None
        if not isinstance(objective_entry, dict):
            failed[f"objective:{agent_id}"] = "post_round_objective_memory job entry is missing"
            continue
        objective_prompt_rel = str(objective_entry.get("prompt") or "").strip()
        objective_job_rel = str(objective_entry.get("job") or "").strip()
        objective_output_rel = str(objective_entry.get("output") or "").strip()
        if not (objective_prompt_rel and objective_job_rel and objective_output_rel):
            failed[f"objective:{agent_id}"] = "post_round_objective_memory job paths are incomplete"
            continue
        try:
            prompt = (run_dir / objective_prompt_rel).read_text(encoding="utf-8")
            job_payload = agent_run.read_json(run_dir / objective_job_rel, {}) or {}
            if not isinstance(job_payload, dict):
                raise RoundRuntimeError(
                    f"{objective_job_rel}: post_round_objective_memory job payload must be an object."
                )
            _dispatch(
                run_dir,
                root,
                run_claude,
                f"post_round_objective_memory:{agent_run.safe_name(agent_id)}",
                prompt,
                extra_context={
                    "card_folder": str(card),
                    "post_round_objective_memory_job": job_payload,
                    "post_round_output_path": objective_output_rel,
                },
                output_path=run_dir / objective_output_rel,
            )
        except Exception as exc:
            failed[f"objective:{agent_id}"] = str(exc)

    if failed:
        agent_memory._update_post_round_job_status(run_dir, "degraded_memory_state", failed=failed)
        agent_memory._update_post_round_objective_job_status(run_dir, "degraded_memory_state", failed=failed)
        return {
            "ok": False,
            "status": "degraded_memory_state",
            "scheduled": scheduled_agents,
            "ingested": [],
            "missing": {},
            "failed": failed,
        }

    ingested = agent_memory.ingest_post_round_memory_jobs(card, run_dir)
    ingested["scheduled"] = scheduled_agents
    return ingested


def _run_delivery(
    card: Path,
    root: Path,
    run_dir: Path,
    run_command: Callable[..., Any],
) -> dict[str, Any]:
    delivery = rp_generate_cli._run_delivery(card, root, run_command)
    _write_artifact(run_dir, "delivery.result.json", delivery)
    return delivery


def _blocked(
    run_dir: Path,
    stages: list[str],
    reason: str,
    detail: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "ok": False,
        "action": "blocked",
        "run_dir": str(run_dir),
        "reason": reason,
        "runtime": {"mode": "thin", "stages": stages},
        "detail": detail,
    }
    _write_artifact(run_dir, "runtime.result.json", result)
    agent_run.update_manifest_stage(run_dir, "blocked", f"Thin runtime blocked: {reason}.")
    return result
