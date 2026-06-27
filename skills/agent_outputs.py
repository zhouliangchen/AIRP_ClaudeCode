"""Read, validate, and assemble multi-agent round outputs."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import agent_run
import agent_intents
import agent_interactions
import agent_messages
import agent_schemas
import agent_visibility
import agent_visibility_guard
import hidden_settings
import player_decision_evidence
import postprocess_outputs
import runtime_settings
import self_repair


MAX_CRITIC_RETRIES = 2
ALLOWED_RAW_TRACE_STATUSES = {"interacting", "decision_point"}
TRACE_PRESERVED_TARGET_RE = re.compile(r"^(?:player|character:[A-Za-z][A-Za-z0-9_]*)$")
FORBIDDEN_ACTOR_MARKERS = set(agent_schemas.FORBIDDEN_ACTOR_KEYS) | set(agent_visibility.HIDDEN_MARKERS)
STORY_PROMPT_ACTOR_EVENT_TYPES = {"reply"}
STORY_GUARD_CJK_TERM_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
STORY_GUARD_YEAR_TERM_RE = re.compile(r"([0-9零〇一二两三四五六七八九十百千万]+年)(?:前|后)?")
STORY_GUARD_CJK_MIN_CHARS = 3
STORY_GUARD_CJK_MAX_CHARS = 6
STORY_PRIVATE_ARTIFACT_MARKERS = (
    "内部评估",
    "内心确认",
    "内心",
    "私密",
    "绝不暴露",
    "不暴露",
    "不能让他知道",
    "不能让她知道",
    "gm-only",
    "gm only",
    "gm_only",
)


class AgentOutputError(RuntimeError):
    """Raised when a required agent artifact is missing or invalid."""


def _canonical_tokens(text: str) -> list[str]:
    raw = str(text or "")
    acronym_separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", raw)
    camel_separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", acronym_separated)
    return re.findall(r"[a-z0-9]+", camel_separated.lower())


FORBIDDEN_ACTOR_KEY_TOKENS = {
    marker: tuple(_canonical_tokens(marker))
    for marker in sorted(FORBIDDEN_ACTOR_MARKERS)
}


def _forbidden_actor_marker(text: str) -> str:
    tokens = _canonical_tokens(text)
    if tokens:
        for marker, marker_tokens in FORBIDDEN_ACTOR_KEY_TOKENS.items():
            if not marker_tokens or len(marker_tokens) > len(tokens):
                continue
            for index in range(0, len(tokens) - len(marker_tokens) + 1):
                if tuple(tokens[index:index + len(marker_tokens)]) == marker_tokens:
                    return marker
    return agent_visibility.hidden_marker_name(text, FORBIDDEN_ACTOR_MARKERS)


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
    scene_beat_fields = ("content", "metadata", *agent_visibility.VISIBILITY_FIELDS)
    event_fields = ("content", "target", "source_call_id", "metadata", *agent_visibility.VISIBILITY_FIELDS)
    actor_call_fields = ("source_call_id", "prompt", "reason", "metadata", *agent_visibility.VISIBILITY_FIELDS)
    for gm_index, gm_output in enumerate(gm_outputs):
        output_context = f"{gm_path}.outputs[{gm_index}]"
        for beat_index, beat in enumerate(gm_output.get("scene_beats", [])):
            context = f"{output_context}.scene_beats[{beat_index}]"
            for field in scene_beat_fields:
                if field in beat:
                    _reject_actor_facing_gm_value(beat[field], f"{context}.{field}", hidden_phrases)
        for event_index, event in enumerate(gm_output.get("events", [])):
            context = f"{output_context}.events[{event_index}]"
            for field in event_fields:
                if field in event:
                    _reject_actor_facing_gm_value(event[field], f"{context}.{field}", hidden_phrases)
        for call_index, call in enumerate(gm_output.get("actor_calls", [])):
            context = f"{output_context}.actor_calls[{call_index}]"
            for field in actor_call_fields:
                if field in call:
                    _reject_actor_facing_gm_value(call[field], f"{context}.{field}", hidden_phrases)
        if gm_output.get("decision_point") is not None:
            _reject_actor_facing_gm_value(
                gm_output["decision_point"],
                f"{output_context}.decision_point",
                hidden_phrases,
            )


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


def _sanitize_trace_summary_for_story_input(
    summary: Dict[str, Any],
    hidden_phrases: list[str],
    context: str,
) -> Dict[str, Any]:
    visible_events = summary.get("visible_events", [])
    if isinstance(visible_events, list):
        _reject_actor_facing_gm_value(visible_events, f"{context}.visible_events", hidden_phrases)
    sanitized = dict(summary)
    for field in ("decision_point", "stop_reason"):
        if field in sanitized:
            sanitized[field] = _sanitize_side_summary_value(sanitized[field], hidden_phrases)
    return sanitized


def _string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        texts: list[str] = []
        for child in value.values():
            texts.extend(_string_leaves(child))
        return texts
    if isinstance(value, list):
        texts: list[str] = []
        for child in value:
            texts.extend(_string_leaves(child))
        return texts
    return []


def _card_folder_from_run_dir(root: Path) -> Path:
    if root.parent.name == ".agent_runs":
        return root.parent.parent
    return root.parent.parent


def _active_hidden_settings_for_story_guard(root: Path) -> list[Dict[str, Any]]:
    records = hidden_settings.load_hidden_settings(_card_folder_from_run_dir(root), limit=None)
    return [
        record
        for record in records
        if isinstance(record, dict) and str(record.get("status") or "active") == "active"
    ]


def _story_hidden_guard_payload(root: Path, input_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(input_payload)
    current = payload.get("gm_only_hidden_settings")
    settings: list[Any] = []
    if isinstance(current, list):
        settings.extend(current)
    elif current:
        settings.append(current)
    settings.extend(_active_hidden_settings_for_story_guard(root))
    if settings:
        payload["gm_only_hidden_settings"] = settings
    return payload


def _visible_recent_chat_texts(input_payload: Dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for item in input_payload.get("recent_chat", []):
        if isinstance(item, str):
            texts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        visibility = str(item.get("visibility") or "").lower()
        if visibility in {"gm_only", "hidden", "private"}:
            continue
        for key in ("role", "user", "player", "raw_text", "display_text", "ai", "assistant", "content"):
            if key in item:
                texts.extend(_string_leaves(item[key]))
    return texts


def _story_public_text_key(input_payload: Dict[str, Any]) -> str:
    routed = input_payload.get("routed_input") if isinstance(input_payload.get("routed_input"), dict) else {}
    texts: list[str] = []
    for value in (
        input_payload.get("raw_text"),
        input_payload.get("role_channel"),
        routed.get("role_channel"),
    ):
        texts.extend(_string_leaves(value))
    texts.extend(_visible_recent_chat_texts(input_payload))
    return _story_guard_text_key("\n".join(texts))


def _story_hidden_source_texts(input_payload: Dict[str, Any]) -> list[str]:
    routed = input_payload.get("routed_input") if isinstance(input_payload.get("routed_input"), dict) else {}
    sources: list[str] = []
    for value in (
        routed.get("user_instruction_channel"),
        input_payload.get("user_instruction_channel"),
        input_payload.get("gm_only_hidden_settings"),
        input_payload.get("hidden_facts"),
        input_payload.get("world_truth"),
        input_payload.get("gm_only_recent_chat"),
        input_payload.get("hidden_recent_chat"),
        input_payload.get("private_recent_chat"),
    ):
        sources.extend(_string_leaves(value))
    return sources


def _story_guard_text_key(text: str) -> str:
    return "".join(
        char
        for char in str(text or "")
        if char.isalnum() or ("\u3400" <= char <= "\u4dbf") or ("\u4e00" <= char <= "\u9fff")
    ).casefold()


def _story_guard_cjk_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for run in STORY_GUARD_CJK_TERM_RE.findall(str(text or "")):
        max_size = min(STORY_GUARD_CJK_MAX_CHARS, len(run))
        if max_size < STORY_GUARD_CJK_MIN_CHARS:
            continue
        for size in range(STORY_GUARD_CJK_MIN_CHARS, max_size + 1):
            for index in range(0, len(run) - size + 1):
                term = _story_guard_text_key(run[index:index + size])
                if len(term) >= STORY_GUARD_CJK_MIN_CHARS:
                    terms.add(term)
    for match in STORY_GUARD_YEAR_TERM_RE.finditer(str(text or "")):
        term = _story_guard_text_key(match.group(1))
        if term:
            terms.add(term)
    return terms


def _story_guard_protected_terms(input_payload: Dict[str, Any], public_text_key: str) -> set[str]:
    terms: set[str] = set()
    for text in _story_hidden_source_texts(input_payload):
        terms.update(_story_guard_cjk_terms(text))
    return {
        term
        for term in terms
        if term and term not in public_text_key
    }


def _story_text_is_safe(text: Any, hidden_phrases: list[str], protected_terms: set[str]) -> bool:
    raw = str(text or "")
    if not raw.strip():
        return False
    lower = raw.casefold()
    if any(marker in lower for marker in STORY_PRIVATE_ARTIFACT_MARKERS):
        return False
    redacted = agent_visibility_guard.redact_text(raw, hidden_phrases)
    if redacted != raw:
        return False
    if _forbidden_actor_marker(redacted):
        return False
    key = _story_guard_text_key(redacted)
    return not any(term in key for term in protected_terms)


def _story_safe_text(text: Any, hidden_phrases: list[str], protected_terms: set[str]) -> str:
    raw = str(text or "").strip()
    return raw if _story_text_is_safe(raw, hidden_phrases, protected_terms) else ""


def _story_safe_dict(value: Any, hidden_phrases: list[str], protected_terms: set[str]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if not _story_text_is_safe(serialized, hidden_phrases, protected_terms):
        return {}
    sanitized = _sanitize_side_summary_value(value, hidden_phrases)
    return sanitized if isinstance(sanitized, dict) else {}


def _compact_story_prompt_actor_outputs(
    actor_outputs: Any,
    hidden_phrases: list[str],
    protected_terms: set[str],
) -> Dict[str, list[Dict[str, Any]]]:
    if not isinstance(actor_outputs, dict):
        return {}
    compact_actors: Dict[str, list[Dict[str, Any]]] = {}
    for actor_id, outputs in actor_outputs.items():
        if not isinstance(outputs, list):
            continue
        actor_items: list[Dict[str, Any]] = []
        for output in outputs:
            if not isinstance(output, dict):
                continue
            events = []
            for event in output.get("events", []):
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type") or "")
                if event_type not in STORY_PROMPT_ACTOR_EVENT_TYPES:
                    continue
                content = _story_safe_text(event.get("content"), hidden_phrases, protected_terms)
                if not content:
                    continue
                compact_event = {
                    "type": event_type,
                    "target": str(event.get("target") or ""),
                    "content": content,
                }
                metadata = _story_safe_dict(event.get("metadata"), hidden_phrases, protected_terms)
                if metadata:
                    compact_event["metadata"] = metadata
                events.append(compact_event)
            if not events:
                continue
            compact_output = {
                "agent": output.get("agent"),
                "agent_id": output.get("agent_id"),
                "events": events,
                "stop_reason": output.get("stop_reason", "continue"),
            }
            character_name = str(output.get("character_name") or "").strip()
            if character_name:
                compact_output["character_name"] = character_name
            actor_items.append(compact_output)
        if actor_items:
            compact_actors[str(actor_id)] = actor_items
    return compact_actors


def _compact_story_prompt_items(
    items: Any,
    hidden_phrases: list[str],
    protected_terms: set[str],
    *,
    allowed_fields: tuple[str, ...],
) -> list[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    compact_items: list[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = _story_safe_text(item.get("content"), hidden_phrases, protected_terms)
        if not content:
            continue
        compact = {"content": content}
        for field in allowed_fields:
            if field == "content" or field not in item:
                continue
            value = item.get(field)
            if isinstance(value, str):
                safe = _story_safe_text(value, hidden_phrases, protected_terms)
                if safe:
                    compact[field] = safe
            elif isinstance(value, dict):
                safe_dict = _story_safe_dict(value, hidden_phrases, protected_terms)
                if safe_dict:
                    compact[field] = safe_dict
            elif isinstance(value, list):
                safe_list = _sanitize_side_summary_value(value, hidden_phrases)
                safe_serialized = json.dumps(safe_list, ensure_ascii=False, sort_keys=True)
                if _story_text_is_safe(safe_serialized, hidden_phrases, protected_terms):
                    compact[field] = safe_list
            else:
                compact[field] = value
        compact_items.append(compact)
    return compact_items


def _compact_story_prompt_gm_output(
    output: Dict[str, Any],
    hidden_phrases: list[str],
    protected_terms: set[str],
) -> Dict[str, Any]:
    compact = {
        "agent": output.get("agent", "gm"),
        "scene_beats": _compact_story_prompt_items(
            output.get("scene_beats"),
            hidden_phrases,
            protected_terms,
            allowed_fields=("content", "metadata", *agent_visibility.VISIBILITY_FIELDS),
        ),
        "events": _compact_story_prompt_items(
            output.get("events"),
            hidden_phrases,
            protected_terms,
            allowed_fields=("type", "target", "source_call_id", "content", "metadata", *agent_visibility.VISIBILITY_FIELDS),
        ),
        "actor_calls": [],
        "world_state_delta": _compact_story_prompt_items(
            output.get("world_state_delta"),
            hidden_phrases,
            protected_terms,
            allowed_fields=("scope", "fact", "status", "content"),
        ),
        "stop_reason": output.get("stop_reason", "continue"),
    }
    for call in output.get("actor_calls", []):
        if not isinstance(call, dict):
            continue
        compact_call = {
            "call_id": str(call.get("call_id") or ""),
            "actor_id": str(call.get("actor_id") or ""),
        }
        basis = _story_safe_dict(call.get("visibility_basis"), hidden_phrases, protected_terms)
        if basis:
            compact_call["visibility_basis"] = basis
        compact["actor_calls"].append(compact_call)
    decision_point = output.get("decision_point")
    if decision_point is not None:
        compact_decision = _sanitize_side_summary_value(decision_point, hidden_phrases)
        serialized = json.dumps(compact_decision, ensure_ascii=False, sort_keys=True)
        if _story_text_is_safe(serialized, hidden_phrases, protected_terms):
            compact["decision_point"] = compact_decision
    return compact


def _compact_story_prompt_loop_outputs(
    loop_outputs: Any,
    hidden_phrases: list[str],
    protected_terms: set[str],
) -> Dict[str, Any]:
    if not isinstance(loop_outputs, dict):
        return {"gm": {"agent": "gm_loop", "outputs": []}, "actors": {}}
    gm = loop_outputs.get("gm")
    gm_outputs = gm.get("outputs") if isinstance(gm, dict) else []
    return {
        "gm": {
            "agent": "gm_loop",
            "outputs": [
                _compact_story_prompt_gm_output(output, hidden_phrases, protected_terms)
                for output in gm_outputs
                if isinstance(output, dict)
            ],
        },
        "actors": _compact_story_prompt_actor_outputs(
            loop_outputs.get("actors"),
            hidden_phrases,
            protected_terms,
        ),
    }


def _compact_story_prompt_trace(
    trace_summary: Any,
    hidden_phrases: list[str],
    protected_terms: set[str],
) -> Dict[str, Any]:
    if not isinstance(trace_summary, dict):
        return {}
    compact = {
        key: trace_summary[key]
        for key in ("schema_version", "status", "chapter_target_words", "private_event_count")
        if key in trace_summary
    }
    visible_events = trace_summary.get("visible_events")
    if isinstance(visible_events, list):
        visible_events = [
            event
            for event in visible_events
            if not (
                isinstance(event, dict)
                and str(event.get("type") or "") == "action"
                and str(event.get("actor") or "").startswith("character:")
            )
        ]
    visible = _compact_story_prompt_items(
        visible_events,
        hidden_phrases,
        protected_terms,
        allowed_fields=("id", "index", "actor", "type", "target", "content", "source_call_id"),
    )
    compact["visible_events"] = visible
    for field in ("decision_point", "stop_reason"):
        if field not in trace_summary:
            continue
        value = _sanitize_side_summary_value(trace_summary[field], hidden_phrases)
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if _story_text_is_safe(serialized, hidden_phrases, protected_terms):
            compact[field] = value
    return compact


def _compact_story_prompt_side_threads(
    side_threads: Any,
    hidden_phrases: list[str],
    protected_terms: set[str],
) -> Dict[str, Any]:
    if not isinstance(side_threads, dict):
        return {"threads": []}
    threads = []
    for thread in side_threads.get("threads", []):
        if not isinstance(thread, dict):
            continue
        compact_thread = {
            "thread_id": str(thread.get("thread_id") or ""),
            "status": str(thread.get("status") or ""),
            "state": _story_safe_dict(thread.get("state"), hidden_phrases, protected_terms),
            "actor_outputs": _compact_story_prompt_actor_outputs(
                thread.get("actor_outputs"),
                hidden_phrases,
                protected_terms,
            ),
            "interaction_trace": _compact_story_prompt_trace(
                thread.get("interaction_trace"),
                hidden_phrases,
                protected_terms,
            ),
        }
        subgm_output = thread.get("subgm_output")
        if isinstance(subgm_output, dict):
            compact_thread["subgm_output"] = _compact_story_prompt_gm_output(
                subgm_output,
                hidden_phrases,
                protected_terms,
            )
        threads.append(compact_thread)
    return {"threads": threads}


def _build_story_prompt_context(
    story_input: Dict[str, Any],
    hidden_phrases: list[str],
    protected_terms: set[str],
) -> Dict[str, Any]:
    context: Dict[str, Any] = {
        "round_id": story_input.get("round_id", ""),
        "player_inputs": story_input.get("player_inputs", {}),
        "loop_outputs": _compact_story_prompt_loop_outputs(
            story_input.get("loop_outputs"),
            hidden_phrases,
            protected_terms,
        ),
        "side_threads": _compact_story_prompt_side_threads(
            story_input.get("side_threads"),
            hidden_phrases,
            protected_terms,
        ),
        "interaction_trace": _compact_story_prompt_trace(
            story_input.get("interaction_trace"),
            hidden_phrases,
            protected_terms,
        ),
    }
    for field in (
        "delivery_constraints",
        "runtime_settings",
        "style_guidance",
        "story_output_guidance",
        "critic_style_guidance",
    ):
        if field in story_input:
            context[field] = story_input[field]
    return context


def story_prompt_context(story_input: Dict[str, Any]) -> Dict[str, Any]:
    """Return the story-facing prompt context, never the raw audit artifact view."""
    if not isinstance(story_input, dict):
        return {}
    context = story_input.get("story_prompt_context")
    if isinstance(context, dict):
        return context
    return story_input


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


def _artifact_path(root: Path, relative_path: str) -> Path:
    raw = Path(str(relative_path))
    if raw.is_absolute() or any(part == ".." for part in raw.parts):
        raise AgentOutputError(f"{relative_path}: artifact path must be run-relative and stay under artifacts")
    if not str(relative_path).strip() or str(relative_path).strip() in {".", ""}:
        raise AgentOutputError("artifact path must not be empty")
    artifacts_dir = (root / "artifacts").resolve()
    candidate = (artifacts_dir / raw).resolve()
    try:
        candidate.relative_to(artifacts_dir)
    except ValueError as exc:
        raise AgentOutputError(f"{relative_path}: artifact path escapes artifacts directory") from exc
    return candidate


def _write_artifact(root: Path, relative_path: str, payload: Dict[str, Any]) -> None:
    agent_run.write_json(_artifact_path(root, relative_path), payload)


def export_delivery_artifact(root: str | Path, relative_path: str) -> Path:
    """Export an authoritative artifact to the run root for delivery-boundary consumers."""
    run_dir = Path(root)
    source = _artifact_path(run_dir, relative_path)
    if not source.exists():
        raise AgentOutputError(f"{source.as_posix()}: required artifact is missing")
    data = _read_json_required(source)
    destination = run_dir / relative_path
    agent_run.write_json(destination, data)
    return destination


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
            if not _gm_actor_call_requires_output(gm_output, actor_id):
                continue
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


def _gm_output_declares_player_decision(gm_output: Dict[str, Any]) -> bool:
    return str(gm_output.get("stop_reason") or "").strip() == "player_decision"


def _role_action_channel_text(input_payload: Any) -> str:
    return player_decision_evidence.role_action_reply({"player_inputs": input_payload})


def _validate_gm_player_decisions(
    input_payload: Any,
    gm_outputs: list[Dict[str, Any]],
    player_output_source_call_ids: list[str],
    actor_path: Path,
    gm_path: Path,
) -> None:
    player_participated = bool(_role_action_channel_text(input_payload))
    player_output_source_ids = set(player_output_source_call_ids)
    for index, output in enumerate(gm_outputs):
        if not _gm_output_declares_player_decision(output):
            for call in output.get("actor_calls", []):
                if str(call.get("actor_id") or "").strip() != "player":
                    continue
                call_id = str(call.get("call_id") or "").strip()
                if call_id and call_id in player_output_source_ids:
                    player_participated = True
            continue
        validation = player_decision_evidence.valid_gm_player_decision(
            output,
            player_participated_before_gm=player_participated,
        )
        if not validation.get("valid"):
            reason = str(validation.get("reason") or "invalid_player_decision")
            if reason == "missing_prior_player_reply":
                raise AgentOutputError(
                    f"{actor_path.as_posix()}.player: player_decision requires prior player actor output"
                )
            raise AgentOutputError(f"{gm_path}.outputs[{index}].player_decision: {reason}")
        for call in output.get("actor_calls", []):
            if str(call.get("actor_id") or "").strip() != "player":
                continue
            call_id = str(call.get("call_id") or "").strip()
            if call_id and call_id in player_output_source_ids:
                player_participated = True


def _player_output_source_call_ids_for_decision_chronology(
    root: Path,
    raw_trace: Dict[str, Any] | None,
    normalized_actor_outputs: Dict[str, list[Dict[str, Any]]],
    required_actor_counts: Dict[str, Counter[str]],
    actor_path: Path,
) -> list[str]:
    if raw_trace is None or not normalized_actor_outputs.get("player"):
        return []
    output_source_call_ids_by_actor = _validate_actor_output_provenance(
        root,
        raw_trace,
        {"player": normalized_actor_outputs["player"]},
        {"player": Counter(required_actor_counts.get("player", Counter()))},
        actor_path,
    )
    return output_source_call_ids_by_actor.get("player", [])


def _gm_actor_call_requires_output(gm_output: Dict[str, Any], actor_id: str) -> bool:
    return True


def _required_actor_call_counts(gm_outputs: list[Dict[str, Any]]) -> Dict[str, Counter[str]]:
    required_call_counts: Dict[str, Counter[str]] = {}
    for gm_index, gm_output in enumerate(gm_outputs):
        for call_index, call in enumerate(gm_output.get("actor_calls", [])):
            context = f"gm.output.json.outputs[{gm_index}].actor_calls[{call_index}]"
            actor_id = _validate_actor_key(call.get("actor_id"), f"{context}.actor_id")
            if not _gm_actor_call_requires_output(gm_output, actor_id):
                continue
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
    actor_path: Path | None = None,
) -> Dict[str, list[str]]:
    event_sources = _trace_event_sources(raw_trace)
    actor_path = actor_path or root / "actor.outputs.json"
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



def _load_loop_outputs(root: Path, input_payload: Any = None, raw_trace: Dict[str, Any] | None = None) -> Dict[str, Any]:
    gm_path = _artifact_path(root, "gm.output.json")
    actor_path = _artifact_path(root, "actor.outputs.json")
    gm_loop = _read_json_required(gm_path)

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

    required_actor_counts = _required_actor_call_counts(normalized_gm_outputs)
    if actor_path.exists():
        actor_outputs = _read_json_required(actor_path)
    elif any(counts for counts in required_actor_counts.values()):
        raise AgentOutputError(f"{actor_path.as_posix()}: required artifact is missing")
    else:
        actor_outputs = {}

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

    player_output_source_call_ids = []
    if any(_gm_output_declares_player_decision(output) for output in normalized_gm_outputs):
        player_output_source_call_ids = _player_output_source_call_ids_for_decision_chronology(
            root,
            raw_trace,
            normalized_actor_outputs,
            required_actor_counts,
            actor_path,
        )
    _validate_gm_player_decisions(
        input_payload,
        normalized_gm_outputs,
        player_output_source_call_ids,
        actor_path,
        gm_path,
    )

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
        trace_summary = _sanitize_trace_summary_for_story_input(
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


_STORY_INPUT_ANALYSIS_KEEP_KEYS = (
    "schema_version",
    "round_id",
    "analysis_mode",
    "semantic_units",
    "world_updates",
    "narrative_directives",
    "routing_requests",
    "capability_requests",
    "risks",
)
_STORY_SEMANTIC_UNIT_KEEP_KEYS = (
    "id",
    "type",
    "visibility",
    "derived_summary",
    "source_channel",
    "confidence",
    "persist",
)
_STORY_ALLOWED_GM_ONLY_UNIT_TYPES = {"edit_request", "synopsis"}


def _compact_story_semantic_units_for_prompt(units: Any, hidden_phrases: list[str]) -> list[Dict[str, Any]]:
    if not isinstance(units, list):
        return []
    compact_units: list[Dict[str, Any]] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        unit_type = str(unit.get("type") or "")
        visibility = str(unit.get("visibility") or "")
        if visibility == "gm_only" and unit_type not in _STORY_ALLOWED_GM_ONLY_UNIT_TYPES:
            continue
        compact = {
            key: unit[key]
            for key in _STORY_SEMANTIC_UNIT_KEEP_KEYS
            if key in unit
        }
        sanitized = _sanitize_side_summary_value(compact, hidden_phrases)
        if isinstance(sanitized, dict):
            compact_units.append(sanitized)
    return compact_units


def _compact_story_world_updates_for_prompt(updates: Any, hidden_phrases: list[str]) -> Dict[str, Any]:
    if not isinstance(updates, dict):
        return {}
    compact: Dict[str, Any] = {}
    for key in ("public_facts", "retcon_requests"):
        value = updates.get(key)
        if isinstance(value, list):
            compact[key] = _sanitize_side_summary_value(value, hidden_phrases)
    important = []
    important_source = updates.get("important_characters")
    if not isinstance(important_source, list):
        important_source = []
    for item in important_source:
        if isinstance(item, dict) and str(item.get("visibility") or "") == "public_world":
            important.append(item)
    if important:
        compact["important_characters"] = _sanitize_side_summary_value(important, hidden_phrases)
    return compact


def _compact_story_input_analysis_for_prompt(analysis: Any, hidden_phrases: list[str]) -> Dict[str, Any]:
    if not isinstance(analysis, dict):
        return {}
    compact = {
        key: analysis[key]
        for key in _STORY_INPUT_ANALYSIS_KEEP_KEYS
        if key in analysis
    }
    compact["semantic_units"] = _compact_story_semantic_units_for_prompt(
        analysis.get("semantic_units"),
        hidden_phrases,
    )
    if "world_updates" in compact:
        compact["world_updates"] = _compact_story_world_updates_for_prompt(
            analysis.get("world_updates"),
            hidden_phrases,
        )
    for key in ("risks", "routing_requests", "capability_requests"):
        if key in compact:
            compact[key] = _sanitize_side_summary_value(compact.get(key), hidden_phrases)
    return compact


def _story_player_inputs_for_prompt(input_payload: Dict[str, Any], hidden_phrases: list[str]) -> Dict[str, Any]:
    routed = input_payload.get("routed_input") if isinstance(input_payload.get("routed_input"), dict) else {}
    role_channel = str(routed.get("role_channel") or input_payload.get("role_channel") or input_payload.get("raw_text") or "")
    role_action = str(routed.get("role_action_channel") or input_payload.get("role_action_channel") or "").strip()
    narrative_guidance = str(
        routed.get("narrative_guidance_channel")
        or input_payload.get("narrative_guidance_channel")
        or ""
    ).strip()
    if not role_action and not narrative_guidance:
        role_action = role_channel
    safe_routed = {
        key: routed[key]
        for key in (
            "input_schema",
            "analysis_mode",
            "role_channel",
            "role_action_channel",
            "narrative_guidance_channel",
            "gm",
            "player",
            "characters",
        )
        if key in routed
    }
    if "role_channel" not in safe_routed:
        safe_routed["role_channel"] = role_channel
    if "role_action_channel" not in safe_routed:
        safe_routed["role_action_channel"] = role_action
    if narrative_guidance and "narrative_guidance_channel" not in safe_routed:
        safe_routed["narrative_guidance_channel"] = narrative_guidance
    safe_inputs = {
        "raw_text": role_action,
        "routed_input": safe_routed,
        "input_analysis": _compact_story_input_analysis_for_prompt(
            input_payload.get("input_analysis"),
            hidden_phrases,
        ),
    }
    sanitized = _sanitize_side_summary_value(safe_inputs, hidden_phrases)
    return sanitized if isinstance(sanitized, dict) else safe_inputs


def _relaxed_side_thread_summaries(root: Path) -> list[Dict[str, Any]]:
    side_root = root / "side_threads"
    if not side_root.exists():
        return []
    threads = []
    for side_dir in sorted(path for path in side_root.iterdir() if path.is_dir()):
        state = agent_run.read_json(side_dir / "state.json", {}) or {}
        subgm_output = agent_run.read_json(side_dir / "subgm.output.json", {}) or {}
        actor_outputs = agent_run.read_json(side_dir / "actor.outputs.json", {}) or {}
        threads.append(
            {
                "thread_id": side_dir.name,
                "status": str(
                    (subgm_output.get("status") if isinstance(subgm_output, dict) else "")
                    or (state.get("status") if isinstance(state, dict) else "")
                    or ""
                ),
                "state": state if isinstance(state, dict) else {},
                "subgm_output": subgm_output if isinstance(subgm_output, dict) else {},
                "actor_outputs": actor_outputs if isinstance(actor_outputs, dict) else {},
            }
        )
    return threads


def build_relaxed_story_input(run_dir: str | Path) -> Dict[str, Any]:
    """Assemble story input without strict trace/provenance gates."""
    root = Path(run_dir)
    manifest = _load_manifest(root)
    if manifest is None:
        raise AgentOutputError(f"{root / 'manifest.json'}: manifest is missing")

    input_payload = _read_json_required(root / "input.json")
    artifacts_dir = root / "artifacts"
    gm_loop = _read_json_required(artifacts_dir / "gm.output.json")
    if gm_loop.get("agent") != "gm_loop":
        raise AgentOutputError(f"{artifacts_dir / 'gm.output.json'}: agent must be 'gm_loop'")
    if not isinstance(gm_loop.get("outputs"), list) or not gm_loop["outputs"]:
        raise AgentOutputError(f"{artifacts_dir / 'gm.output.json'}.outputs: must not be empty")

    actor_outputs = {}
    actor_path = artifacts_dir / "actor.outputs.json"
    if actor_path.exists():
        actor_outputs = _read_json_required(actor_path)

    analysis = {}
    analysis_path = artifacts_dir / "input_analysis.output.json"
    if analysis_path.exists():
        analysis = _read_json_required(analysis_path)

    runtime_payload = runtime_settings.normalize_prompt_payload(
        {
            "settings": manifest.get("runtime_settings", {}),
            "style_profile": manifest.get("style_profile", {}),
        }
    )
    settings = runtime_payload["settings"]
    style_profile = runtime_payload["style_profile"]
    routed = input_payload.get("routed_input") if isinstance(input_payload.get("routed_input"), dict) else {}
    role_text = str(
        routed.get("role_action_channel")
        or input_payload.get("role_action_channel")
        or routed.get("role_channel")
        or input_payload.get("role_channel")
        or input_payload.get("raw_text")
        or ""
    )
    side_threads = {"threads": _relaxed_side_thread_summaries(root)}

    story_input = {
        "round_id": manifest.get("round_id", root.name),
        "player_inputs": {
            "raw_text": role_text,
            "routed_input": routed,
            "input_analysis": analysis,
        },
        "loop_outputs": {
            "gm": gm_loop,
            "actors": actor_outputs,
        },
        "side_threads": side_threads,
        "memory_deltas": _memory_deltas_from_events(actor_outputs, gm_loop, side_threads),
        "interaction_trace": {"status": "relaxed", "visible_events": []},
        "delivery_constraints": {
            "preserve_raw_player_inputs": True,
            "preserve_character_dialogue_metadata": False,
        },
        "runtime_settings": settings,
        "style_guidance": {
            "style": settings["style"],
            "name": style_profile.get("name", ""),
            "title": style_profile.get("title", ""),
            "content": style_profile.get("content", ""),
            "warning": style_profile.get("warning", ""),
        },
        "story_output_guidance": {
            "word_count_target": settings["wordCount"],
            "word_count_is_soft": True,
            "nsfw": settings["nsfw"],
        },
        "critic_style_guidance": {
            "style": settings["style"],
            "name": style_profile.get("name", ""),
            "title": style_profile.get("title", ""),
            "content": style_profile.get("content", ""),
            "warning": style_profile.get("warning", ""),
        },
    }
    story_input["story_prompt_context"] = {
        key: value
        for key, value in story_input.items()
        if key != "story_prompt_context"
    }
    _write_artifact(root, "story.input.json", story_input)
    agent_run.update_manifest_stage(root, "story_ready", "Assembled relaxed story.input.json.")
    return story_input


def build_story_input(run_dir: str | Path) -> Dict[str, Any]:
    """Assemble story input from GM loop outputs and trace artifacts."""
    root = Path(run_dir)
    manifest = _load_manifest(root)
    if manifest is None:
        raise AgentOutputError(f"{root / 'manifest.json'}: manifest is missing")

    input_payload = _read_json_required(root / "input.json")
    hidden_phrases = agent_visibility_guard.hidden_phrases(input_payload)
    story_guard_payload = _story_hidden_guard_payload(root, input_payload)
    story_hidden_phrases = agent_visibility_guard.hidden_phrases(story_guard_payload)
    story_protected_terms = _story_guard_protected_terms(
        story_guard_payload,
        _story_public_text_key(input_payload),
    )
    raw_trace, trace_summary = _validate_trace_artifacts(root)
    trace_summary = _sanitize_trace_summary_for_story_input(
        trace_summary,
        hidden_phrases,
        f"{root / 'interaction.trace.json'}",
    )
    loop_outputs = _load_loop_outputs(root, input_payload, raw_trace)
    gm_path = _artifact_path(root, "gm.output.json")
    actor_path = _artifact_path(root, "actor.outputs.json")
    _validate_gm_output_visibility(gm_path, loop_outputs["gm"]["outputs"], input_payload)
    output_source_call_ids_by_actor = _validate_actor_output_provenance(
        root,
        raw_trace,
        loop_outputs["actors"],
        _required_actor_call_counts(loop_outputs["gm"]["outputs"]),
        actor_path,
    )
    _require_called_actor_outputs(
        gm_path,
        loop_outputs["gm"]["outputs"],
        output_source_call_ids_by_actor,
    )
    side_threads = _load_side_thread_outputs(root, input_payload)
    runtime_payload = runtime_settings.normalize_prompt_payload({
        "settings": manifest.get("runtime_settings", {}),
        "style_profile": manifest.get("style_profile", {}),
    })
    settings = runtime_payload["settings"]
    style_profile = runtime_payload["style_profile"]

    story_input = {
        "round_id": manifest.get("round_id", root.name),
        "player_inputs": _story_player_inputs_for_prompt(input_payload, hidden_phrases),
        "loop_outputs": loop_outputs,
        "side_threads": side_threads,
        "memory_deltas": _memory_deltas_from_events(loop_outputs["actors"], loop_outputs["gm"], side_threads),
        "interaction_trace": trace_summary,
        "delivery_constraints": {
            "preserve_raw_player_inputs": True,
            "preserve_character_dialogue_metadata": True,
        },
        "runtime_settings": settings,
        "style_guidance": {
            "style": settings["style"],
            "name": style_profile.get("name", ""),
            "title": style_profile.get("title", ""),
            "content": style_profile.get("content", ""),
            "warning": style_profile.get("warning", ""),
        },
        "story_output_guidance": {
            "word_count_target": settings["wordCount"],
            "word_count_is_soft": True,
            "nsfw": settings["nsfw"],
        },
        "critic_style_guidance": {
            "style": settings["style"],
            "name": style_profile.get("name", ""),
            "title": style_profile.get("title", ""),
            "content": style_profile.get("content", ""),
            "warning": style_profile.get("warning", ""),
        },
    }
    story_input["story_prompt_context"] = _build_story_prompt_context(
        story_input,
        story_hidden_phrases,
        story_protected_terms,
    )
    _write_artifact(root, "story.input.json", story_input)
    agent_run.update_manifest_stage(root, "story_ready", "Validated agent outputs and assembled story.input.json.")
    return story_input


def extract_player_critical_action_evidence(story_input) -> list[Dict[str, Any]]:
    """Extract GM-declared player decision evidence from story input."""
    return player_decision_evidence.extract_player_critical_action_evidence(story_input)


def build_critic_quality_metrics(run_dir: str | Path, story_output: Dict[str, Any]) -> Dict[str, Any]:
    """Build deterministic critic-facing style and length metrics for `story.output.json`."""
    root = Path(run_dir)
    manifest = _load_manifest(root)
    if manifest is None:
        raise AgentOutputError(f"{root / 'manifest.json'}: manifest is missing")

    runtime_payload = runtime_settings.normalize_prompt_payload({
        "settings": manifest.get("runtime_settings", {}),
        "style_profile": manifest.get("style_profile", {}),
    })
    settings = runtime_payload["settings"]
    style_profile = runtime_payload["style_profile"]
    metrics = runtime_settings.build_quality_metrics(root, settings, style_profile, story_output)

    word_count = dict(metrics.get("word_count", {}))
    word_count["exemption_reason"] = "player_decision" if word_count.get("exempted") else ""
    profile_metrics = dict(metrics.get("style_profile", {}))
    profile_metrics["title"] = style_profile.get("title", "")
    profile_metrics["content"] = style_profile.get("content", "")

    return {
        "style": settings["style"],
        "style_profile": profile_metrics,
        "word_count": word_count,
        "chinese_char_count": dict(metrics.get("chinese_char_count", {})),
        "visible_content": dict(metrics.get("visible_content", {})),
    }


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


def _load_valid_postprocess(run_dir: Path, story_input: Dict[str, Any]) -> Dict[str, Any]:
    postprocess_path = _artifact_path(run_dir, "postprocess.output.json")
    if not postprocess_path.exists():
        raise AgentOutputError("postprocess_missing")
    payload = _read_json_required(postprocess_path)
    critical_evidence = extract_player_critical_action_evidence(story_input)
    result = postprocess_outputs.validate_postprocess_output(
        payload,
        critical_action_evidence=critical_evidence,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        errors = result.get("errors") if isinstance(result, dict) else []
        detail = {
            "reason": (result.get("reason") if isinstance(result, dict) else "postprocess_core_invalid")
            or "postprocess_core_invalid",
            "errors": errors if isinstance(errors, list) else [str(errors)],
        }
        raise AgentOutputError(json.dumps(detail, ensure_ascii=False))
    output = result.get("output")
    if not isinstance(output, dict):
        raise AgentOutputError(json.dumps(
            {
                "reason": "postprocess_core_invalid",
                "errors": ["normalized postprocess output must be an object"],
            },
            ensure_ascii=False,
        ))
    agent_run.write_json(postprocess_path, output)
    return output


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


def _normalized_critic_fingerprint(critic_report: Dict[str, Any]) -> str:
    routing = self_repair.normalize_repair_routing(critic_report.get("repair_routing"))
    return _critic_fingerprint({**critic_report, "repair_routing": routing})


def _record_critic_repair(card_folder: str | Path, run_dir: Path, manifest: Dict[str, Any], critic_report: Dict[str, Any]) -> Dict[str, Any]:
    history_path = run_dir / "repair_history.jsonl"
    existing = _read_jsonl(history_path)
    fingerprint = _normalized_critic_fingerprint(critic_report)
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
        "source": "artifacts/critic.report.json",
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
                "source": str(_artifact_path(run_dir, "critic.report.json").resolve()),
                "timestamp": entry["timestamp"],
            },
        )
    entry["recorded"] = True
    return entry


def _existing_repair_request_intent(
    run_dir: Path,
    fingerprint: str,
    states: tuple[str, ...] = ("pending",),
) -> Dict[str, Any] | None:
    for state in states:
        for intent in agent_intents.list_intents(run_dir, state):
            if intent.get("requested_by") != "critic" or intent.get("type") != "repair_request":
                continue
            payload = intent.get("payload")
            if isinstance(payload, dict) and payload.get("repair_fingerprint") == fingerprint:
                return {"ok": True, "intent": intent, "deduped": True}
    return None


def _record_repair_request_intent(run_dir: Path, critic_report: Dict[str, Any]) -> Dict[str, Any]:
    routing = self_repair.normalize_repair_routing(critic_report.get("repair_routing"))
    fingerprint = _normalized_critic_fingerprint(critic_report)
    existing = _existing_repair_request_intent(run_dir, fingerprint)
    if existing is not None:
        return existing

    intent_result = agent_intents.create_intent(
        run_dir,
        {
            "requested_by": "critic",
            "type": "repair_request",
            "payload": {
                "critic_report_path": "artifacts/critic.report.json",
                "decision": critic_report.get("decision", ""),
                "repair_instruction": critic_report.get("repair_instruction", ""),
                "repair_routing": routing,
                "repair_fingerprint": fingerprint,
            },
        },
    )
    intent = intent_result.get("intent") if isinstance(intent_result, dict) else None
    intent_id = intent.get("id", "") if isinstance(intent, dict) else ""
    if not intent_id:
        raise AgentOutputError(f"repair_request intent creation failed: {intent_result!r}")

    try:
        message = agent_messages.append_message(
            run_dir,
            {
                "from": "critic",
                "to": ["story", "gm", "main_agent"],
                "type": "repair_request",
                "visibility": "gm_only",
                "payload": {
                    "decision": critic_report.get("decision", ""),
                    "repair_instruction": critic_report.get("repair_instruction", ""),
                    "repair_routing": routing,
                    "repair_fingerprint": fingerprint,
                    "intent_id": intent_id,
                    "critic_report_path": "artifacts/critic.report.json",
                },
            },
        )
    except Exception as exc:
        agent_intents.block_intent(
            run_dir,
            intent_id,
            "repair_request_message_failed",
            outputs={"error": str(exc), "error_type": type(exc).__name__},
        )
        raise AgentOutputError(f"repair_request message append failed: {exc}") from exc
    if not isinstance(message, dict) or not message.get("ok"):
        agent_intents.block_intent(run_dir, intent_id, "repair_request_message_failed", outputs={"message_result": message})
        raise AgentOutputError(f"repair_request message append failed: {message!r}")
    source_message_id = (message.get("message") or {}).get("id", "")
    if not source_message_id:
        agent_intents.block_intent(run_dir, intent_id, "repair_request_message_failed", outputs={"message_result": message})
        raise AgentOutputError("repair_request source message id is missing")
    try:
        attached = agent_intents.attach_source_message(run_dir, intent_id, source_message_id)
    except Exception as exc:
        agent_intents.block_intent(
            run_dir,
            intent_id,
            "repair_request_link_failed",
            outputs={"source_message_id": source_message_id, "error": str(exc)},
        )
        raise AgentOutputError(f"repair_request source message attach failed: {exc}") from exc
    if not isinstance(attached, dict) or not attached.get("ok"):
        agent_intents.block_intent(
            run_dir,
            intent_id,
            "repair_request_link_failed",
            outputs={"source_message_id": source_message_id, "attach_result": attached},
        )
        raise AgentOutputError(f"repair_request source message attach failed: {attached!r}")
    return attached


def _record_terminal_repair_request_intent(run_dir: Path, critic_report: Dict[str, Any]) -> Dict[str, Any]:
    fingerprint = _normalized_critic_fingerprint(critic_report)
    existing = _existing_repair_request_intent(run_dir, fingerprint, states=("pending", "blocked"))
    if existing is not None:
        return existing
    return _record_repair_request_intent(run_dir, critic_report)


def record_critic_repair_request(card_folder: str | Path, run_dir: str | Path, critic_report: Dict[str, Any]) -> Dict[str, Any]:
    """Record critic repair metadata and create the standard repair_request intent."""

    root = Path(run_dir)
    manifest = _load_manifest(root)
    if manifest is None:
        raise AgentOutputError(f"{root / 'manifest.json'}: manifest is missing")
    history = _record_critic_repair(card_folder, root, manifest, critic_report)
    intent_result = _record_repair_request_intent(root, critic_report)
    intent = intent_result.get("intent") if isinstance(intent_result, dict) else None
    intent_id = intent.get("id", "") if isinstance(intent, dict) else ""
    return {
        "ok": True,
        "id": intent_id,
        "created": not bool(intent_result.get("deduped")),
        "history": history,
        "intent": intent,
        "deduped": bool(intent_result.get("deduped")),
    }


def _block_repair_request_intent(
    run_dir: Path,
    intent_result: Dict[str, Any],
    reason: str,
    critic_report: Dict[str, Any],
) -> Dict[str, Any]:
    intent = intent_result.get("intent") if isinstance(intent_result, dict) else None
    intent_id = intent.get("id", "") if isinstance(intent, dict) else ""
    if not intent_id:
        raise AgentOutputError("repair_request intent id is missing")
    routing = self_repair.normalize_repair_routing(critic_report.get("repair_routing"))
    requires_source_authorization = (
        routing.get("stage") == "system_code"
        and reason == "source_code_self_repair_not_authorized"
    )
    return agent_intents.block_intent(
        run_dir,
        intent_id,
        reason,
        outputs={
            "delivery_reason": reason,
            "critic_decision": critic_report.get("decision", ""),
            "critic_report_path": "artifacts/critic.report.json",
            "repair_routing": routing,
            "requires_source_repair_authorization": requires_source_authorization,
        },
    )


def _blocked_route_reason(routing: Dict[str, Any]) -> str:
    if routing.get("stage") == "system_code":
        return "source_code_self_repair_not_authorized"
    return "self_repair_mode_blocks_route"


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
        story_input = build_story_input(run_dir)
    except AgentOutputError as exc:
        _increment_retry(run_dir, manifest, "blocked")
        return _retry_result("agent_outputs", "Required agent outputs are missing or invalid.", str(exc))
    manifest = _load_manifest(run_dir) or manifest

    story_path = _artifact_path(run_dir, "story.output.json")
    critic_path = _artifact_path(run_dir, "critic.report.json")

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
            block_reason = _blocked_route_reason(routing)
            intent_result = _record_terminal_repair_request_intent(run_dir, critic_report)
            _block_repair_request_intent(run_dir, intent_result, block_reason, critic_report)
            _mark_blocked_without_retry(run_dir, manifest)
            return _blocked_result(block_reason, "Self-repair mode does not allow this critic repair route.", critic_report)
        if _critic_retry_count(manifest) >= policy.critic_retry_limit:
            intent_result = _record_terminal_repair_request_intent(run_dir, critic_report)
            _block_repair_request_intent(run_dir, intent_result, "critic_retry_limit", critic_report)
            _mark_blocked_without_retry(run_dir, manifest)
            return _blocked_result("critic_retry_limit", "Critic retry limit reached.", critic_report)
        _record_repair_request_intent(run_dir, critic_report)
        _increment_critic_retry(run_dir, manifest)
        return _retry_result("critic_block", "Critic blocked delivery.", critic_report)
    if decision == "revise":
        _record_critic_repair(card_folder, run_dir, manifest, critic_report)
        routing = self_repair.normalize_repair_routing(critic_report.get("repair_routing"))
        if not self_repair.policy_allows_route(policy, routing, decision):
            block_reason = _blocked_route_reason(routing)
            intent_result = _record_terminal_repair_request_intent(run_dir, critic_report)
            _block_repair_request_intent(run_dir, intent_result, block_reason, critic_report)
            _mark_blocked_without_retry(run_dir, manifest)
            return _blocked_result(block_reason, "Self-repair mode does not allow this critic repair route.", critic_report)
        if _critic_retry_count(manifest) >= policy.critic_retry_limit:
            intent_result = _record_terminal_repair_request_intent(run_dir, critic_report)
            _block_repair_request_intent(run_dir, intent_result, "critic_retry_limit", critic_report)
            _mark_blocked_without_retry(run_dir, manifest)
            return _blocked_result("critic_retry_limit", "Critic retry limit reached.", critic_report)
        _record_repair_request_intent(run_dir, critic_report)
        _increment_critic_retry(run_dir, manifest)
        return _retry_result("critic_revise", "Critic requested revision.", critic_report)

    try:
        postprocess = _load_valid_postprocess(run_dir, story_input)
    except AgentOutputError as exc:
        _increment_retry(run_dir, manifest, "blocked")
        detail_text = str(exc)
        if detail_text == "postprocess_missing":
            return _retry_result(
                "postprocess_missing",
                "Required postprocess artifact is missing.",
                f"{_artifact_path(run_dir, 'postprocess.output.json').as_posix()}: required artifact is missing",
            )
        try:
            detail_payload = json.loads(detail_text)
        except json.JSONDecodeError:
            detail_payload = {"reason": "postprocess_core_invalid", "errors": [detail_text]}
        reason = str(detail_payload.get("reason") or "postprocess_core_invalid")
        return _retry_result(reason, "Postprocess core is missing or invalid.", detail_payload)

    response_path = Path(styles_dir) / "response.txt"
    export_delivery_artifact(run_dir, "story.input.json")
    export_delivery_artifact(run_dir, "story.output.json")
    export_delivery_artifact(run_dir, "critic.report.json")
    export_delivery_artifact(run_dir, "postprocess.output.json")
    agent_run.write_text(response_path, story_output["content"])
    agent_run.append_manifest_stage(manifest, "critic_passed", "Critic passed and story output was mirrored to response.txt.")
    _write_manifest(run_dir, manifest)
    return {
        "ok": True,
        "mode": "agent_run",
        "run_dir": str(run_dir),
        "story_output": story_output,
        "critic_report": critic_report,
        "postprocess": postprocess,
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
