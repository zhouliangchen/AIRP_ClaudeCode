"""Lightweight pending-intent pump for the thin round runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import agent_intents
import agent_run
import capability_executors


class AgentRuntimePumpError(RuntimeError):
    """Raised when the runtime pump cannot persist its own state."""


def run_pending_intents(
    card_folder: str | Path,
    run_dir: str | Path,
    *,
    phase: str,
    runtime_settings: dict[str, Any] | None = None,
    run_command: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    run_path = Path(run_dir)
    phase_name = _safe_phase(phase)
    summary = {
        "ok": True,
        "phase": phase_name,
        "processed": [],
        "blocked": [],
        "rejected": [],
        "deferred": [],
        "skipped": [],
    }

    for intent in agent_intents.list_intents(run_path, "pending"):
        if not _should_run_intent_in_phase(intent, phase_name):
            summary["skipped"].append(
                {
                    "intent_id": str(intent.get("id") or ""),
                    "type": str(intent.get("type") or ""),
                    "status": "pending",
                    "reason": "phase_deferred",
                }
            )
            continue
        item = _process_intent(
            card_folder,
            run_path,
            intent,
            phase=phase_name,
            runtime_settings=runtime_settings,
            run_command=run_command,
        )
        status = item.get("status")
        if status == "blocked":
            summary["blocked"].append(item)
        elif status == "rejected":
            summary["rejected"].append(item)
        else:
            summary["processed"].append(item)
        outputs = item.get("outputs")
        if isinstance(outputs, dict) and outputs.get("status") == "deferred":
            summary["deferred"].append(item)

    _write_phase_artifact(run_path, phase_name, summary)
    return summary


def execute_intent(
    card_folder: str | Path,
    run_dir: str | Path,
    intent: dict[str, Any],
    *,
    phase: str,
    runtime_settings: dict[str, Any] | None = None,
    run_command: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    return capability_executors.execute_intent(
        card_folder,
        run_dir,
        intent,
        phase=phase,
        runtime_settings=runtime_settings,
        run_command=run_command,
    )


def _process_intent(
    card_folder: str | Path,
    run_dir: Path,
    intent: dict[str, Any],
    *,
    phase: str,
    runtime_settings: dict[str, Any] | None,
    run_command: Callable[..., Any] | None,
) -> dict[str, Any]:
    intent_id = str(intent.get("id") or "")
    intent_type = str(intent.get("type") or "")
    accepted = agent_intents.accept_intent(run_dir, intent_id, outputs={"phase": phase})
    if not accepted.get("ok"):
        return {
            "intent_id": intent_id,
            "type": intent_type,
            "status": "rejected",
            "reason": str(accepted.get("reason") or "accept_failed"),
            "outputs": accepted.get("result", {}),
        }

    try:
        result = execute_intent(
            card_folder,
            run_dir,
            intent,
            phase=phase,
            runtime_settings=runtime_settings,
            run_command=run_command,
        )
    except Exception as exc:
        result = {
            "status": "rejected",
            "reason": "executor_error",
            "outputs": {"error": str(exc), "intent_type": intent_type},
        }

    status = str(result.get("status") or "blocked")
    outputs = result.get("outputs")
    if not isinstance(outputs, dict):
        outputs = {}
    reason = str(result.get("reason") or "")

    if status == "completed":
        transitioned = agent_intents.complete_intent(run_dir, intent_id, outputs=outputs)
    elif status == "rejected":
        transitioned = agent_intents.reject_intent(
            run_dir,
            intent_id,
            reason or "executor_rejected",
            outputs=outputs,
        )
    else:
        status = "blocked"
        transitioned = agent_intents.block_intent(
            run_dir,
            intent_id,
            reason or "executor_blocked",
            outputs=outputs,
        )

    return {
        "intent_id": intent_id,
        "type": intent_type,
        "status": status,
        "reason": reason,
        "outputs": outputs,
        "transition_ok": bool(transitioned.get("ok")),
    }


def _safe_phase(phase: str) -> str:
    value = agent_run.safe_name(str(phase or "runtime_pump"))
    return value.replace(" ", "_")


def _should_run_intent_in_phase(intent: dict[str, Any], phase: str) -> bool:
    intent_type = str(intent.get("type") or "")
    if intent_type == "assets_task":
        return phase == "after_critic"
    return True


def _write_phase_artifact(run_dir: Path, phase: str, summary: dict[str, Any]) -> None:
    try:
        agent_run.write_json(
            run_dir / "artifacts" / "runtime_pump" / f"{phase}.json",
            summary,
        )
    except Exception as exc:
        raise AgentRuntimePumpError(f"write runtime pump artifact failed: {exc}") from exc
