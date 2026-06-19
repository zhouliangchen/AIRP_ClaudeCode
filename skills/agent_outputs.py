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
import agent_visibility_guard
import self_repair


MAX_CRITIC_RETRIES = 2
ALLOWED_RAW_TRACE_STATUSES = {"interacting", "decision_point"}
TRACE_PRESERVED_TARGET_RE = re.compile(r"^(?:player|character:[A-Za-z][A-Za-z0-9_]*)$")


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


def _reject_actor_facing_gm_value(value: Any, path: str, hidden_phrases: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            marker = _forbidden_actor_marker(str(key))
            if marker:
                raise AgentOutputError(f"{child_path}: forbidden actor marker {marker}")
            _reject_actor_facing_gm_value(child, child_path, hidden_phrases)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_actor_facing_gm_value(child, f"{path}[{index}]", hidden_phrases)
        return
    if not isinstance(value, str):
        return

    marker = _forbidden_actor_marker(value)
    if marker:
        raise AgentOutputError(f"{path}: forbidden actor marker {marker}")
    if hidden_phrases and agent_visibility_guard.redact_text(value, hidden_phrases) != value:
        raise AgentOutputError(f"{path}: contains copied hidden phrase")


def _validate_gm_output_visibility(
    gm_path: Path,
    gm_outputs: list[Dict[str, Any]],
    input_payload: Dict[str, Any],
) -> None:
    hidden_phrases = agent_visibility_guard.hidden_phrases(input_payload)
    for gm_index, gm_output in enumerate(gm_outputs):
        output_context = f"{gm_path}.outputs[{gm_index}]"
        for beat_index, beat in enumerate(gm_output.get("scene_beats", [])):
            context = f"{output_context}.scene_beats[{beat_index}]"
            for field in ("content", "metadata"):
                if field in beat:
                    _reject_actor_facing_gm_value(beat[field], f"{context}.{field}", hidden_phrases)
        for event_index, event in enumerate(gm_output.get("events", [])):
            context = f"{output_context}.events[{event_index}]"
            for field in ("content", "metadata"):
                if field in event:
                    _reject_actor_facing_gm_value(event[field], f"{context}.{field}", hidden_phrases)
        for call_index, call in enumerate(gm_output.get("actor_calls", [])):
            context = f"{output_context}.actor_calls[{call_index}]"
            for field in ("prompt", "reason", "metadata"):
                if field in call:
                    _reject_actor_facing_gm_value(call[field], f"{context}.{field}", hidden_phrases)


def _sanitize_side_summary_value(value: Any, hidden_phrases: list[str]) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, child in value.items():
            if _forbidden_actor_marker(str(key)):
                continue
            sanitized[str(key)] = _sanitize_side_summary_value(child, hidden_phrases)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_side_summary_value(item, hidden_phrases) for item in value]
    if isinstance(value, str):
        redacted = agent_visibility_guard.redact_text(value, hidden_phrases)
        if _forbidden_actor_marker(redacted):
            return "[redacted]"
        return redacted
    return value


def _compact_side_state_summary(state: Dict[str, Any], hidden_phrases: list[str]) -> Dict[str, Any]:
    summary = {
        "thread_id": state.get("thread_id", ""),
        "status": state.get("status", ""),
        "title": state.get("title", ""),
        "boundary": state.get("boundary", {}) if isinstance(state.get("boundary"), dict) else {},
        "objective": state.get("objective", ""),
        "allowed_characters": state.get("allowed_characters", []) if isinstance(state.get("allowed_characters"), list) else [],
        "forbidden_characters": state.get("forbidden_characters", []) if isinstance(state.get("forbidden_characters"), list) else [],
        "last_scene_beats": state.get("last_scene_beats", []) if isinstance(state.get("last_scene_beats"), list) else [],
        "next_resume_point": state.get("next_resume_point", ""),
        "urgency": state.get("urgency", ""),
    }
    sanitized = _sanitize_side_summary_value(summary, hidden_phrases)
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_side_trace_summary(summary: Dict[str, Any], hidden_phrases: list[str], context: str) -> Dict[str, Any]:
    visible_events = summary.get("visible_events", [])
    if isinstance(visible_events, list):
        _reject_actor_facing_gm_value(visible_events, f"{context}.visible_events", hidden_phrases)
    sanitized = dict(summary)
    for field in ("decision_point", "stop_reason"):
        if field in sanitized:
            sanitized[field] = _sanitize_side_summary_value(sanitized[field], hidden_phrases)
    return sanitized


def _validate_subgm_output_visibility(
    output_path: Path,
    subgm_output: Dict[str, Any],
    hidden_phrases: list[str],
) -> None:
    story_facing_fields = (
        "scene_beats",
        "events",
        "actor_calls",
        "messages_to_gm",
        "world_state_delta",
        "promotion_requests",
        "boundary_requests",
        "notes_for_story",
        "next_resume_point",
    )
    for field in story_facing_fields:
        if field in subgm_output:
            _reject_actor_facing_gm_value(subgm_output[field], f"{output_path}.{field}", hidden_phrases)


def _validate_side_subgm_actor_call_ids(output_path: Path, subgm_output: Dict[str, Any]) -> None:
    for call_index, call in enumerate(subgm_output.get("actor_calls", [])):
        call_id = str(call.get("call_id") or "").strip() if isinstance(call, dict) else ""
        if not call_id:
            raise AgentOutputError(f"{output_path}.actor_calls[{call_index}].call_id: side subGM actor call id must be nonblank")


def _validate_actor_output_visibility(
    actor_path: Path,
    actor_outputs: Dict[str, list[Dict[str, Any]]],
    hidden_phrases: list[str],
) -> None:
    for actor_id, outputs in actor_outputs.items():
        for output_index, output in enumerate(outputs):
            _reject_actor_facing_gm_value(output, f"{actor_path}.{actor_id}[{output_index}]", hidden_phrases)


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
        raise AgentOutputError(f"{path}.status: must be exactly one of {sorted(ALLOWED_RAW_TRACE_STATUSES)!r}")
    if status not in ALLOWED_RAW_TRACE_STATUSES:
        raise AgentOutputError(f"{path}.status: unsupported trace status {status!r}")

    return trace


def _trace_preserved_target(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    target = value.strip()
    if TRACE_PRESERVED_TARGET_RE.fullmatch(target):
        return target
    return ""


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
    output_source_call_ids_by_actor: Dict[str, list[str]],
) -> None:
    required_call_counts: Dict[str, Counter[str]] = {}
    first_context: Dict[tuple[str, str], str] = {}
    for gm_index, gm_output in enumerate(gm_outputs):
        for call_index, call in enumerate(gm_output.get("actor_calls", [])):
            context = f"{gm_path}.outputs[{gm_index}].actor_calls[{call_index}]"
            actor_id = _validate_actor_key(call.get("actor_id"), f"{context}.actor_id")
            call_id = str(call.get("call_id") or "").strip()
            if not call_id:
                raise AgentOutputError(f"{context}.call_id: persisted actor call id must be nonblank")
            required_call_counts.setdefault(actor_id, Counter())[call_id] += 1
            first_context.setdefault((actor_id, call_id), context)

    for actor_id, required_counts in required_call_counts.items():
        actual_counts = Counter(output_source_call_ids_by_actor.get(actor_id, []))
        for call_id, required_count in required_counts.items():
            actual_count = actual_counts.get(call_id, 0)
            if actual_count != required_count:
                context = first_context[(actor_id, call_id)]
                raise AgentOutputError(
                    f"{context}.call_id: actor output count mismatch for {actor_id} call_id {call_id!r}; "
                    f"required {required_count}, found {actual_count}"
                )


def _required_actor_call_counts(gm_outputs: list[Dict[str, Any]]) -> Dict[str, Counter[str]]:
    required_call_counts: Dict[str, Counter[str]] = {}
    for gm_index, gm_output in enumerate(gm_outputs):
        for call_index, call in enumerate(gm_output.get("actor_calls", [])):
            context = f"gm.output.json.outputs[{gm_index}].actor_calls[{call_index}]"
            actor_id = _validate_actor_key(call.get("actor_id"), f"{context}.actor_id")
            call_id = str(call.get("call_id") or "").strip()
            if call_id:
                required_call_counts.setdefault(actor_id, Counter())[call_id] += 1
    return required_call_counts


def _trace_event_sources(raw_trace: Dict[str, Any]) -> Counter[tuple[str, str, str, str, str]]:
    event_sources: Counter[tuple[str, str, str, str, str]] = Counter()
    for event in raw_trace["events"]:
        if not isinstance(event, dict):
            continue
        actor = event.get("actor")
        event_type = event.get("type")
        content = event.get("content")
        source_call_id = event.get("source_call_id")
        target = event.get("target")
        if (
            not isinstance(actor, str)
            or not isinstance(event_type, str)
            or not isinstance(content, str)
            or not isinstance(source_call_id, str)
        ):
            continue
        actor_key = actor.strip()
        type_key = event_type.strip()
        source_key = source_call_id.strip()
        if actor_key and type_key and source_key:
            event_sources[(actor_key, type_key, content, _trace_preserved_target(target), source_key)] += 1
    return event_sources


def _candidate_source_call_ids(
    event_sources: Counter[tuple[str, str, str, str, str]],
    event_key: tuple[str, str, str, str],
) -> set[str]:
    candidates = set()
    for source_key, count in event_sources.items():
        if count > 0 and source_key[:4] == event_key:
            candidates.add(source_key[4])
    return candidates


def _choose_source_call_id(
    candidates: list[str],
    preferred_counts: Counter[str],
) -> str:
    for source_call_id in sorted(candidates):
        if preferred_counts.get(source_call_id, 0) > 0:
            return source_call_id
    return sorted(candidates)[0]


def _validate_actor_output_provenance(
    root: Path,
    raw_trace: Dict[str, Any],
    actor_outputs: Dict[str, list[Dict[str, Any]]],
    preferred_call_counts: Dict[str, Counter[str]] | None = None,
) -> Dict[str, list[str]]:
    event_sources = _trace_event_sources(raw_trace)
    actor_path = root / "actor.outputs.json"
    output_source_call_ids_by_actor: Dict[str, list[str]] = {}
    remaining_preferred = {
        actor_id: Counter(counts)
        for actor_id, counts in (preferred_call_counts or {}).items()
    }
    for actor_id, outputs in actor_outputs.items():
        context = f"{actor_path}.{actor_id}"
        if not outputs:
            raise AgentOutputError(f"{context}: actor output branch is empty or unproven")
        for output_index, output in enumerate(outputs):
            event_counts: Counter[tuple[str, str, str, str]] = Counter()
            common_sources: set[str] | None = None
            for event_index, event in enumerate(output["events"]):
                preserved_target = _trace_preserved_target(event.get("target"))
                event_key = (actor_id, event["type"], event["content"], preserved_target)
                candidates = _candidate_source_call_ids(event_sources, event_key)
                if not candidates:
                    raise AgentOutputError(
                        f"{context}[{output_index}].events[{event_index}]: actor event is not backed by "
                        f"raw trace source_call_id event "
                        f"(actor={actor_id!r}, type={event['type']!r}, target={preserved_target!r}, "
                        f"content={event['content']!r})"
                    )
                common_sources = candidates if common_sources is None else common_sources & candidates
                event_counts[event_key] += 1

            eligible_sources = []
            for source_call_id in common_sources or set():
                if all(
                    event_sources[(*event_key, source_call_id)] >= event_count
                    for event_key, event_count in event_counts.items()
                ):
                    eligible_sources.append(source_call_id)
            if not eligible_sources:
                raise AgentOutputError(
                    f"{context}[{output_index}]: actor output events must share one nonblank "
                    f"raw trace source_call_id"
                )

            actor_preferred = remaining_preferred.setdefault(actor_id, Counter())
            output_source_call_id = _choose_source_call_id(eligible_sources, actor_preferred)
            if actor_preferred.get(output_source_call_id, 0) > 0:
                actor_preferred[output_source_call_id] -= 1
            for event_key, event_count in event_counts.items():
                event_sources[(*event_key, output_source_call_id)] -= event_count
            output_source_call_ids_by_actor.setdefault(actor_id, []).append(output_source_call_id)
    return output_source_call_ids_by_actor



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

    return {
        "gm": {"agent": "gm_loop", "outputs": normalized_gm_outputs},
        "actors": normalized_actor_outputs,
    }


def _load_side_actor_outputs(side_dir: Path) -> Dict[str, list[Dict[str, Any]]]:
    actor_path = side_dir / "actor.outputs.json"
    if not actor_path.exists():
        return {}
    raw_outputs = _read_json_required(actor_path)
    normalized_actor_outputs = {}
    for actor_id, outputs in raw_outputs.items():
        actor_key = _validate_actor_key(actor_id, f"{actor_path}.{actor_id}")
        if actor_key == "player":
            raise AgentOutputError(f"{actor_path}.{actor_id}: side-thread actor outputs must target character:*")
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
            if not normalized["agent_id"].startswith("character:"):
                raise AgentOutputError(f"{actor_context}[{index}].agent_id: side-thread actor must be character:*")
            normalized_actor_outputs[actor_key] = normalized_actor_outputs.get(actor_key, []) + [normalized]
    return normalized_actor_outputs


def _load_optional_side_subgm_output(side_dir: Path, thread_id: str, hidden_phrases: list[str]) -> Dict[str, Any] | None:
    output_path = side_dir / "subgm.output.json"
    if not output_path.exists():
        return None
    try:
        subgm_output = agent_schemas.load_json_checked(output_path, agent_schemas.validate_subgm_output)
    except agent_schemas.ValidationError as exc:
        raise AgentOutputError(str(exc)) from exc
    if subgm_output.get("thread_id") != thread_id:
        raise AgentOutputError(f"{output_path}.thread_id mismatch: expected {thread_id}, got {subgm_output.get('thread_id')}")
    _validate_subgm_output_visibility(output_path, subgm_output, hidden_phrases)
    _validate_side_subgm_actor_call_ids(output_path, subgm_output)
    return subgm_output


def _side_thread_dirs(root: Path) -> list[Path]:
    side_root = root / "side_threads"
    if not side_root.exists():
        return []
    return sorted(
        (child for child in side_root.iterdir() if child.is_dir()),
        key=lambda path: path.name,
    )


def _validate_side_actor_outputs_are_allowed(
    side_dir: Path,
    state: Dict[str, Any],
    actor_outputs: Dict[str, list[Dict[str, Any]]],
    preferred_counts: Dict[str, Counter[str]],
) -> None:
    allowed = {
        str(item).strip()
        for item in state.get("allowed_characters", [])
        if isinstance(item, str) and str(item).strip()
    }
    for actor_id in actor_outputs:
        if actor_id not in preferred_counts:
            raise AgentOutputError(
                f"{side_dir / 'actor.outputs.json'}.{actor_id}: side actor output has no matching subGM actor_calls"
            )
        if actor_id not in allowed:
            raise AgentOutputError(
                f"{side_dir / 'actor.outputs.json'}.{actor_id}: actor is outside side thread allowed_characters"
            )


def _require_exact_side_actor_output_calls(
    side_dir: Path,
    output_source_call_ids_by_actor: Dict[str, list[str]],
    preferred_counts: Dict[str, Counter[str]],
) -> None:
    for actor_id, source_call_ids in output_source_call_ids_by_actor.items():
        actual_counts = Counter(source_call_ids)
        required_counts = preferred_counts.get(actor_id, Counter())
        for call_id, actual_count in actual_counts.items():
            required_count = required_counts.get(call_id, 0)
            if actual_count > required_count:
                raise AgentOutputError(
                    f"{side_dir / 'actor.outputs.json'}.{actor_id}: extra side actor output for source_call_id {call_id!r}"
                )


def _load_side_thread_outputs(root: Path, input_payload: dict) -> dict:
    hidden_phrases = agent_visibility_guard.hidden_phrases(input_payload if isinstance(input_payload, dict) else {})
    threads = []
    for side_dir in _side_thread_dirs(root):
        thread_id = side_dir.name
        state_path = side_dir / "state.json"
        state = agent_run.read_json(state_path, {})
        if state is None:
            state = {}
        if not isinstance(state, dict):
            raise AgentOutputError(f"{state_path}: state must be a JSON object when present")
        state_summary = _compact_side_state_summary(state, hidden_phrases) if state else {}

        subgm_output = _load_optional_side_subgm_output(side_dir, thread_id, hidden_phrases)
        actor_outputs = _load_side_actor_outputs(side_dir)
        _validate_actor_output_visibility(side_dir / "actor.outputs.json", actor_outputs, hidden_phrases)

        raw_trace, trace_summary = _validate_trace_artifacts(side_dir)
        trace_summary = _sanitize_side_trace_summary(
            trace_summary,
            hidden_phrases,
            f"{side_dir / 'interaction.trace.json'}",
        )

        actor_output_source_call_ids = {}
        preferred_counts = _required_actor_call_counts([subgm_output]) if subgm_output else {}
        if preferred_counts and not actor_outputs:
            raise AgentOutputError(
                f"{side_dir / 'actor.outputs.json'}: side subGM actor_calls require side actor outputs"
            )
        if actor_outputs:
            _validate_side_actor_outputs_are_allowed(side_dir, state, actor_outputs, preferred_counts)
            actor_output_source_call_ids = _validate_actor_output_provenance(
                side_dir,
                raw_trace,
                actor_outputs,
                preferred_counts,
            )
            if subgm_output:
                _require_called_actor_outputs(
                    side_dir / "subgm.output.json",
                    [subgm_output],
                    actor_output_source_call_ids,
                )
            _require_exact_side_actor_output_calls(side_dir, actor_output_source_call_ids, preferred_counts)

        status = ""
        if isinstance(state_summary.get("status"), str):
            status = state_summary.get("status", "")
        if not status and subgm_output:
            status = subgm_output.get("status", "")
        threads.append({
            "thread_id": thread_id,
            "status": status,
            "state": state_summary,
            "subgm_output": subgm_output,
            "actor_outputs": actor_outputs,
            "actor_output_source_call_ids": actor_output_source_call_ids,
            "interaction_trace": trace_summary,
        })
    return {"threads": threads}


def _memory_deltas_from_events(
    actor_outputs: Dict[str, Any],
    gm_loop: Dict[str, Any],
    side_threads: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
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
    if isinstance(side_threads, dict):
        for thread in side_threads.get("threads", []):
            if not isinstance(thread, dict):
                continue
            thread_id = str(thread.get("thread_id") or "")
            for actor_id, outputs in (thread.get("actor_outputs") or {}).items():
                items = actor_memory.setdefault(str(actor_id), [])
                for output in outputs:
                    for event in output["events"]:
                        if event["type"] in {"memory_delta", "goal_update"}:
                            items.append(event)
            subgm_output = thread.get("subgm_output")
            if isinstance(subgm_output, dict):
                for item in subgm_output.get("world_state_delta", []):
                    world_item = dict(item) if isinstance(item, dict) else {"fact": str(item)}
                    world_item["source_thread_id"] = thread_id
                    world.append(world_item)

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
    _validate_gm_output_visibility(root / "gm.output.json", loop_outputs["gm"]["outputs"], input_payload)
    output_source_call_ids_by_actor = _validate_actor_output_provenance(
        root,
        raw_trace,
        loop_outputs["actors"],
        _required_actor_call_counts(loop_outputs["gm"]["outputs"]),
    )
    _require_called_actor_outputs(
        root / "gm.output.json",
        loop_outputs["gm"]["outputs"],
        output_source_call_ids_by_actor,
    )
    side_threads = _load_side_thread_outputs(root, input_payload)

    story_input = {
        "round_id": manifest.get("round_id", root.name),
        "player_inputs": {
            "raw_text": input_payload.get("raw_text", ""),
            "routed_input": input_payload.get("routed_input", {}),
            "input_analysis": input_payload.get("input_analysis", {}),
            "components": (input_payload.get("routed_input") or {}).get("components", []),
        },
        "loop_outputs": loop_outputs,
        "side_threads": side_threads,
        "memory_deltas": _memory_deltas_from_events(loop_outputs["actors"], loop_outputs["gm"], side_threads),
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
        "repair_routing": critic_report.get("repair_routing", {}),
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
        "repair_routing": self_repair.normalize_repair_routing(critic_report.get("repair_routing")),
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
                "repair_routing": entry["repair_routing"],
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
    policy = self_repair.load_policy(Path(styles_dir) / "settings.json")

    manifest = _load_manifest(run_dir)
    if manifest is None:
        return {"ok": True, "mode": "legacy"}
    if manifest.get("stage") == "delivered":
        return {
            "ok": True,
            "mode": "already_delivered",
            "run_dir": str(run_dir),
            "stage": "delivered",
        }

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
        routing = self_repair.normalize_repair_routing(critic_report.get("repair_routing"))
        if not self_repair.policy_allows_route(policy, routing, decision):
            _mark_blocked_without_retry(run_dir, manifest)
            return _blocked_result("self_repair_mode_blocks_route", "Self-repair mode does not allow this critic repair route.", critic_report)
        if _critic_retry_count(manifest) >= policy.critic_retry_limit:
            _mark_blocked_without_retry(run_dir, manifest)
            return _blocked_result("critic_retry_limit", "Critic retry limit reached.", critic_report)
        _increment_critic_retry(run_dir, manifest)
        return _retry_result("critic_block", "Critic blocked delivery.", critic_report)
    if decision == "revise":
        _record_critic_repair(card_folder, run_dir, manifest, critic_report)
        routing = self_repair.normalize_repair_routing(critic_report.get("repair_routing"))
        if not self_repair.policy_allows_route(policy, routing, decision):
            _mark_blocked_without_retry(run_dir, manifest)
            return _blocked_result("self_repair_mode_blocks_route", "Self-repair mode does not allow this critic repair route.", critic_report)
        if _critic_retry_count(manifest) >= policy.critic_retry_limit:
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
