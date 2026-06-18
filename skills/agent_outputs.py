"""Read, validate, and assemble multi-agent round outputs."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import agent_run
import agent_interactions
import agent_schemas


MAX_CRITIC_RETRIES = 2
ALLOWED_RAW_TRACE_STATUSES = {"interacting", "decision_point"}


class AgentOutputError(RuntimeError):
    """Raised when a required agent artifact is missing or invalid."""


def _canonical_tokens(text: str) -> list[str]:
    raw = str(text or "")
    acronym_separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", raw)
    camel_separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", acronym_separated)
    return re.findall(r"[a-z0-9]+", camel_separated.lower())


FORBIDDEN_ACTOR_KEY_TOKENS = {
    marker: tuple(_canonical_tokens(marker))
    for marker in agent_schemas.FORBIDDEN_ACTOR_KEYS
}


def _forbidden_actor_marker(text: str) -> str:
    tokens = _canonical_tokens(text)
    if not tokens:
        return ""
    for marker, marker_tokens in FORBIDDEN_ACTOR_KEY_TOKENS.items():
        if not marker_tokens or len(marker_tokens) > len(tokens):
            continue
        for index in range(0, len(tokens) - len(marker_tokens) + 1):
            if tuple(tokens[index:index + len(marker_tokens)]) == marker_tokens:
                return marker
    return ""


def _read_json_required(path: Path) -> Dict[str, Any]:
    data = agent_run.read_json(path)
    if not isinstance(data, dict):
        raise AgentOutputError(f"{path}: required JSON object is missing or invalid")
    return data


def _read_raw_trace(root: Path) -> Dict[str, Any]:
    path = root / "interaction.trace.json"
    if not path.exists():
        raise AgentOutputError(f"{path}: required trace v2 is missing")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise AgentOutputError(f"{path}: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise AgentOutputError(f"{path}: could not read trace: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentOutputError(f"{path}: trace must be a JSON object")
    return data


def _validate_raw_trace(root: Path) -> Dict[str, Any]:
    path = root / "interaction.trace.json"
    trace = _read_raw_trace(root)

    if "schema_version" not in trace:
        raise AgentOutputError(f"{path}.schema_version: required trace v2 schema_version is missing")
    schema_version = trace.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise AgentOutputError(f"{path}.schema_version: must be integer 2")
    if schema_version != 2:
        raise AgentOutputError(f"{path}.schema_version: required trace v2, got {schema_version!r}")

    if not isinstance(trace.get("events"), list):
        raise AgentOutputError(f"{path}.events: must be a list")
    if "parallel_groups" in trace and not isinstance(trace.get("parallel_groups"), list):
        raise AgentOutputError(f"{path}.parallel_groups: must be a list")

    status = trace.get("status")
    if not isinstance(status, str):
        raise AgentOutputError(f"{path}.status: must be a non-empty string")
    normalized_status = status.strip().lower()
    if not normalized_status or normalized_status in {"missing", "invalid"}:
        raise AgentOutputError(f"{path}.status: required valid trace status, got {status!r}")
    if normalized_status not in ALLOWED_RAW_TRACE_STATUSES:
        raise AgentOutputError(f"{path}.status: unsupported trace status {status!r}")

    return trace


def _load_required(path: Path, validator) -> Dict[str, Any]:
    if not path.exists():
        raise AgentOutputError(f"{path.as_posix()}: required artifact is missing")
    try:
        return agent_schemas.load_json_checked(path, validator)
    except agent_schemas.ValidationError as exc:
        raise AgentOutputError(str(exc)) from exc


def _load_manifest(run_dir: Path) -> Dict[str, Any] | None:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = agent_run.read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise AgentOutputError(f"{manifest_path}: manifest must be a JSON object")
    return manifest


def _write_manifest(run_dir: Path, manifest: Dict[str, Any]) -> None:
    agent_run.write_json(run_dir / "manifest.json", manifest)


def _expected_outputs(manifest: Dict[str, Any]) -> Dict[str, Any]:
    expected = manifest.get("expected_outputs")
    if not isinstance(expected, dict):
        raise AgentOutputError("manifest.expected_outputs is required")
    return expected


def _validate_actor_key(actor_id: Any, context: str) -> str:
    actor_key = str(actor_id or "").strip()
    if actor_key == "player":
        return actor_key
    if actor_key.startswith("character:"):
        suffix = actor_key.split(":", 1)[1].strip()
        if not suffix:
            raise AgentOutputError(f"{context}: unsupported actor id {actor_key or '<blank>'}")
        marker = _forbidden_actor_marker(suffix)
        if marker:
            raise AgentOutputError(f"{context}: forbidden actor marker {marker}")
        return actor_key
    marker = _forbidden_actor_marker(actor_key)
    if marker:
        raise AgentOutputError(f"{context}: forbidden actor marker {marker}")
    raise AgentOutputError(f"{context}: unsupported actor id {actor_key or '<blank>'}")


def _require_called_actor_outputs(
    gm_path: Path,
    gm_outputs: list[Dict[str, Any]],
    actor_outputs: Dict[str, list[Dict[str, Any]]],
) -> None:
    required_counts: Dict[str, int] = {}
    first_context: Dict[str, str] = {}
    for gm_index, gm_output in enumerate(gm_outputs):
        for call_index, call in enumerate(gm_output.get("actor_calls", [])):
            context = f"{gm_path}.outputs[{gm_index}].actor_calls[{call_index}].actor_id"
            actor_id = _validate_actor_key(call.get("actor_id"), context)
            required_counts[actor_id] = required_counts.get(actor_id, 0) + 1
            first_context.setdefault(actor_id, context)
    for actor_id, required_count in required_counts.items():
        actual_count = len(actor_outputs.get(actor_id) or [])
        if actual_count < required_count:
            raise AgentOutputError(
                f"{first_context[actor_id]}: missing actor outputs for {actor_id}; "
                f"required {required_count}, found {actual_count}"
            )


def _trace_source_call_ids_by_actor(raw_trace: Dict[str, Any]) -> Dict[str, set[str]]:
    source_call_ids: Dict[str, set[str]] = {}
    for event in raw_trace["events"]:
        if not isinstance(event, dict):
            continue
        actor = event.get("actor")
        source_call_id = event.get("source_call_id")
        if not isinstance(actor, str) or not isinstance(source_call_id, str):
            continue
        actor_key = actor.strip()
        source_key = source_call_id.strip()
        if actor_key and source_key:
            source_call_ids.setdefault(actor_key, set()).add(source_key)
    return source_call_ids


def _trace_event_sources(raw_trace: Dict[str, Any]) -> Counter[tuple[str, str, str]]:
    event_sources: Counter[tuple[str, str, str]] = Counter()
    for event in raw_trace["events"]:
        if not isinstance(event, dict):
            continue
        actor = event.get("actor")
        event_type = event.get("type")
        content = event.get("content")
        source_call_id = event.get("source_call_id")
        if (
            not isinstance(actor, str)
            or not isinstance(event_type, str)
            or not isinstance(content, str)
            or not isinstance(source_call_id, str)
        ):
            continue
        actor_key = actor.strip()
        type_key = event_type.strip()
        if actor_key and type_key and source_call_id.strip():
            event_sources[(actor_key, type_key, content)] += 1
    return event_sources


def _validate_actor_output_provenance(
    root: Path,
    raw_trace: Dict[str, Any],
    actor_outputs: Dict[str, list[Dict[str, Any]]],
) -> None:
    source_call_ids = _trace_source_call_ids_by_actor(raw_trace)
    event_sources = _trace_event_sources(raw_trace)
    actor_path = root / "actor.outputs.json"
    for actor_id, outputs in actor_outputs.items():
        context = f"{actor_path}.{actor_id}"
        if not outputs:
            raise AgentOutputError(f"{context}: actor output branch is empty or unproven")
        source_count = len(source_call_ids.get(actor_id, set()))
        if len(outputs) > source_count:
            raise AgentOutputError(
                f"{context}: actor outputs are not backed by raw trace source_call_id events; "
                f"outputs={len(outputs)}, source_call_ids={source_count}"
            )
        for output_index, output in enumerate(outputs):
            for event_index, event in enumerate(output["events"]):
                key = (actor_id, event["type"], event["content"])
                if event_sources[key] <= 0:
                    raise AgentOutputError(
                        f"{context}[{output_index}].events[{event_index}]: actor event is not backed by "
                        f"raw trace source_call_id event "
                        f"(actor={actor_id!r}, type={event['type']!r}, content={event['content']!r})"
                    )
                event_sources[key] -= 1


def _load_loop_outputs(root: Path) -> Dict[str, Any]:
    gm_path = root / "gm.output.json"
    actor_path = root / "actor.outputs.json"
    gm_loop = _read_json_required(gm_path)
    actor_outputs = _read_json_required(actor_path)

    if gm_loop.get("agent") != "gm_loop":
        raise AgentOutputError(f"{gm_path}: agent must be 'gm_loop'")
    gm_items = gm_loop.get("outputs")
    if not isinstance(gm_items, list):
        raise AgentOutputError(f"{gm_path}.outputs: must be a list")
    if not gm_items:
        raise AgentOutputError(f"{gm_path}.outputs: must not be empty")
    normalized_gm_outputs = []
    for index, item in enumerate(gm_items):
        try:
            normalized_gm_outputs.append(agent_schemas.validate_gm_output(item))
        except agent_schemas.ValidationError as exc:
            raise AgentOutputError(f"{gm_path}.outputs[{index}]: {exc}") from exc

    normalized_actor_outputs = {}
    for actor_id, outputs in actor_outputs.items():
        actor_key = _validate_actor_key(actor_id, f"{actor_path}.{actor_id}")
        actor_context = f"{actor_path}.{actor_key}"
        if not isinstance(outputs, list):
            raise AgentOutputError(f"{actor_context}: must be a list")
        normalized_outputs = []
        for index, item in enumerate(outputs):
            try:
                normalized = agent_schemas.validate_actor_output(item)
            except agent_schemas.ValidationError as exc:
                raise AgentOutputError(f"{actor_context}[{index}]: {exc}") from exc
            if normalized["agent_id"] != actor_key:
                raise AgentOutputError(
                    f"{actor_context}[{index}].agent_id mismatch: expected {actor_key}, got {normalized['agent_id']}"
                )
            normalized_outputs.append(normalized)
        normalized_actor_outputs[actor_key] = normalized_outputs

    _require_called_actor_outputs(gm_path, normalized_gm_outputs, normalized_actor_outputs)

    return {
        "gm": {"agent": "gm_loop", "outputs": normalized_gm_outputs},
        "actors": normalized_actor_outputs,
    }


def _memory_deltas_from_events(actor_outputs: Dict[str, Any], gm_loop: Dict[str, Any]) -> Dict[str, Any]:
    actor_memory: Dict[str, list[Any]] = {}
    for actor_id, outputs in actor_outputs.items():
        items = []
        for output in outputs:
            for event in output["events"]:
                if event["type"] in {"memory_delta", "goal_update"}:
                    items.append(event)
        actor_memory[str(actor_id)] = items

    world = []
    for output in gm_loop["outputs"]:
        world.extend(output["world_state_delta"])

    return {"actors": actor_memory, "world": world}


def _validate_trace_artifacts(root: Path) -> tuple[Dict[str, Any], Dict[str, Any]]:
    raw_trace = _validate_raw_trace(root)
    summary = agent_interactions.summarize_for_story_input(root)
    status = str(summary.get("status") or "").strip().lower()
    schema_version = summary.get("schema_version")
    if schema_version != 2 or not status or status in {"missing", "invalid"}:
        raise AgentOutputError(
            f"{root / 'interaction.trace.json'}: required trace v2 is missing or invalid "
            f"(schema_version={schema_version!r}, status={status or '<missing>'})"
        )
    return raw_trace, summary


def build_story_input(run_dir: str | Path) -> Dict[str, Any]:
    """Assemble story input from GM loop outputs and trace artifacts."""
    root = Path(run_dir)
    manifest = _load_manifest(root)
    if manifest is None:
        raise AgentOutputError(f"{root / 'manifest.json'}: manifest is missing")

    _expected_outputs(manifest)
    input_payload = _read_json_required(root / "input.json")
    raw_trace, trace_summary = _validate_trace_artifacts(root)
    loop_outputs = _load_loop_outputs(root)
    _validate_actor_output_provenance(root, raw_trace, loop_outputs["actors"])

    story_input = {
        "round_id": manifest.get("round_id", root.name),
        "player_inputs": {
            "raw_text": input_payload.get("raw_text", ""),
            "routed_input": input_payload.get("routed_input", {}),
            "input_analysis": input_payload.get("input_analysis", {}),
            "components": (input_payload.get("routed_input") or {}).get("components", []),
        },
        "loop_outputs": loop_outputs,
        "memory_deltas": _memory_deltas_from_events(loop_outputs["actors"], loop_outputs["gm"]),
        "interaction_trace": trace_summary,
        "delivery_constraints": {
            "preserve_raw_player_inputs": True,
            "preserve_character_dialogue_metadata": True,
        },
    }
    agent_run.write_json(root / "story.input.json", story_input)
    agent_run.update_manifest_stage(root, "story_ready", "Validated agent outputs and assembled story.input.json.")
    return story_input


def _retry_result(reason: str, message: str, detail: Any = None) -> Dict[str, Any]:
    result = {
        "ok": False,
        "action": "retry",
        "reason": reason,
        "message": message,
    }
    if detail is not None:
        result["detail"] = detail
    return result


def _blocked_result(reason: str, message: str, detail: Any = None) -> Dict[str, Any]:
    result = {
        "ok": False,
        "action": "blocked",
        "reason": reason,
        "message": message,
    }
    if detail is not None:
        result["detail"] = detail
    return result


def _increment_retry(run_dir: Path, manifest: Dict[str, Any], stage: str) -> None:
    manifest["retry_count"] = int(manifest.get("retry_count", 0) or 0) + 1
    agent_run.append_manifest_stage(manifest, stage, "Agent run is blocked pending revision.")
    _write_manifest(run_dir, manifest)


def _critic_retry_count(manifest: Dict[str, Any]) -> int:
    try:
        return int(manifest.get("critic_retry_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _increment_critic_retry(run_dir: Path, manifest: Dict[str, Any]) -> None:
    manifest["critic_retry_count"] = _critic_retry_count(manifest) + 1
    agent_run.append_manifest_stage(manifest, "blocked", "Critic blocked delivery pending revision.")
    _write_manifest(run_dir, manifest)


def _mark_blocked_without_retry(run_dir: Path, manifest: Dict[str, Any]) -> None:
    agent_run.append_manifest_stage(manifest, "blocked", "Agent run remains blocked pending revision.")
    _write_manifest(run_dir, manifest)


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[Dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _critic_fingerprint(critic_report: Dict[str, Any]) -> str:
    fields = {
        "decision": critic_report.get("decision", ""),
        "hard_failures": critic_report.get("hard_failures", []),
        "soft_issues": critic_report.get("soft_issues", []),
        "repair_instruction": critic_report.get("repair_instruction", ""),
        "system_iteration_suggestion": critic_report.get("system_iteration_suggestion", ""),
    }
    return json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_critic_repair(card_folder: str | Path, run_dir: Path, manifest: Dict[str, Any], critic_report: Dict[str, Any]) -> Dict[str, Any]:
    history_path = run_dir / "repair_history.jsonl"
    existing = _read_jsonl(history_path)
    fingerprint = _critic_fingerprint(critic_report)
    for entry in existing:
        if entry.get("fingerprint") == fingerprint:
            entry["recorded"] = False
            return entry

    attempt = len(existing) + 1
    entry = {
        "round_id": str(manifest.get("round_id") or run_dir.name),
        "attempt": attempt,
        "decision": critic_report.get("decision", ""),
        "hard_failures": critic_report.get("hard_failures", []),
        "soft_issues": critic_report.get("soft_issues", []),
        "repair_instruction": critic_report.get("repair_instruction", ""),
        "system_iteration_suggestion": critic_report.get("system_iteration_suggestion", ""),
        "fingerprint": fingerprint,
        "source": "critic.report.json",
        "timestamp": datetime.now(agent_run.CST).isoformat(timespec="seconds"),
    }
    _append_jsonl(history_path, entry)

    suggestion = str(critic_report.get("system_iteration_suggestion") or "").strip()
    if suggestion:
        _append_jsonl(
            agent_run.run_root(card_folder) / "improvement_queue.jsonl",
            {
                "round_id": entry["round_id"],
                "attempt": attempt,
                "decision": entry["decision"],
                "suggestion": suggestion,
                "hard_failures": entry["hard_failures"],
                "soft_issues": entry["soft_issues"],
                "source": str((run_dir / "critic.report.json").resolve()),
                "timestamp": entry["timestamp"],
            },
        )
    entry["recorded"] = True
    return entry


def prepare_delivery(card_folder: str | Path, styles_dir: str | Path) -> Dict[str, Any]:
    """Gate delivery for the current run and mirror story output to response.txt."""
    run_dir = agent_run.current_run_dir(card_folder)
    if run_dir is None:
        return {"ok": True, "mode": "legacy"}

    manifest = _load_manifest(run_dir)
    if manifest is None:
        return {"ok": True, "mode": "legacy"}

    try:
        build_story_input(run_dir)
    except AgentOutputError as exc:
        _increment_retry(run_dir, manifest, "blocked")
        return _retry_result("agent_outputs", "Required agent outputs are missing or invalid.", str(exc))
    manifest = _load_manifest(run_dir) or manifest

    expected = _expected_outputs(manifest)
    story_path = run_dir / expected.get("story", "story.output.json")
    critic_path = run_dir / expected.get("critic", "critic.report.json")

    try:
        story_output = _load_required(story_path, agent_schemas.validate_story_output)
        critic_report = _load_required(critic_path, agent_schemas.validate_critic_report)
    except AgentOutputError as exc:
        _increment_retry(run_dir, manifest, "blocked")
        return _retry_result("agent_outputs", "Required delivery artifacts are missing or invalid.", str(exc))

    decision = critic_report["decision"]
    if decision == "block":
        _record_critic_repair(card_folder, run_dir, manifest, critic_report)
        if _critic_retry_count(manifest) >= MAX_CRITIC_RETRIES:
            _mark_blocked_without_retry(run_dir, manifest)
            return _blocked_result("critic_retry_limit", "Critic retry limit reached.", critic_report)
        _increment_critic_retry(run_dir, manifest)
        return _retry_result("critic_block", "Critic blocked delivery.", critic_report)
    if decision == "revise":
        _record_critic_repair(card_folder, run_dir, manifest, critic_report)
        if _critic_retry_count(manifest) >= MAX_CRITIC_RETRIES:
            _mark_blocked_without_retry(run_dir, manifest)
            return _blocked_result("critic_retry_limit", "Critic retry limit reached.", critic_report)
        _increment_critic_retry(run_dir, manifest)
        return _retry_result("critic_revise", "Critic requested revision.", critic_report)

    response_path = Path(styles_dir) / "response.txt"
    agent_run.write_text(response_path, story_output["content"])
    agent_run.append_manifest_stage(manifest, "critic_passed", "Critic passed and story output was mirrored to response.txt.")
    _write_manifest(run_dir, manifest)
    return {
        "ok": True,
        "mode": "agent_run",
        "run_dir": str(run_dir),
        "story_output": story_output,
        "critic_report": critic_report,
    }


def mark_delivered(card_folder: str | Path) -> Dict[str, Any]:
    """Mark the current agent run delivered after frontend handoff succeeds."""
    run_dir = agent_run.current_run_dir(card_folder)
    if run_dir is None or not (run_dir / "manifest.json").exists():
        return {"ok": True, "mode": "legacy"}
    manifest = agent_run.update_manifest_stage(run_dir, "delivered", "Frontend delivery and memory update completed.")
    return {
        "ok": True,
        "mode": "agent_run",
        "run_dir": str(run_dir),
        "stage": manifest.get("stage"),
    }
