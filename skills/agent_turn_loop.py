"""Interactive GM-driven turn loop for Claude Code RP rounds."""

from __future__ import annotations

import concurrent.futures
from collections import deque
import hashlib
import re
from pathlib import Path
from typing import Any, Callable, Deque, Iterable

import agent_actor_runtime
import agent_actor_batches
import agent_interactions
import agent_lifecycle
import agent_projection
import agent_run
import agent_schemas
import agent_visibility
import agent_visibility_guard
import actor_memory_store
import character_promotions
import player_decision_evidence
import projection_agent
import subgm_threads
import subgm_turn_loop

try:
    from handler import write_progress
except Exception:
    def write_progress(stage, label, percent=None, detail=None):
        return {"stage": stage, "label": label, "percent": percent, "detail": detail or {}}


def _write_progress_safe(stage, label, percent=None, detail=None):
    try:
        return write_progress(stage, label, percent=percent, detail=detail)
    except Exception:
        return None


STOP_REASONS = {"player_decision", "complete", "max_steps", "word_target"}
ACTIVE_SIDE_THREAD_STATUSES = {"running", "merging", "needs_gm", "blocked"}
RESERVATION_ACTIVATING_SUBGM_ACTIONS = {"start", "resume", "merge"}
RESERVATION_RELEASING_SUBGM_ACTIONS = {"pause", "close"}
TRACE_SAFE_PLAYER_CALL_ID_RE = re.compile(r"^call-player-([0-9]+)$")
TRACE_SAFE_CHARACTER_CALL_ID_RE = re.compile(r"^call-character-([A-Za-z][A-Za-z0-9_]*)-([0-9]+)$")

DispatchFn = Callable[[str, dict], dict]


class AgentTurnLoopError(RuntimeError):
    """Raised when the deterministic loop cannot validate or continue."""


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _read_input(run_dir: Path) -> dict:
    data = agent_run.read_json(run_dir / "input.json", {})
    return data if isinstance(data, dict) else {}


def _safe_character_actor_suffix(name: str) -> str:
    raw = str(name or "")
    canonical = actor_memory_store.canonical_actor_id(f"character:{raw}").split(":", 1)[1]
    safe = re.sub(r"[^A-Za-z0-9_]", "_", canonical)
    if not re.match(r"^[A-Za-z]", safe):
        digest = hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:8]
        safe = f"C_{digest}"
    return safe


def _characters_by_actor_id(input_payload: dict) -> dict[str, dict]:
    contexts = _dict(input_payload.get("character_contexts"))
    result: dict[str, dict] = {}

    characters = contexts.get("characters", [])
    if isinstance(characters, dict):
        characters = list(characters.values())
    for item in _list(characters):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("character_name") or "").strip()
        if not name:
            continue
        canonical_id = actor_memory_store.canonical_actor_id(f"character:{name}")
        result[f"character:{name}"] = item
        result[f"character:{_safe_character_actor_suffix(name)}"] = item
        result[canonical_id] = item

    for key, value in contexts.items():
        if key == "characters" or not isinstance(value, dict):
            continue
        actor_id = str(key)
        if not actor_id.startswith("character:"):
            actor_id = f"character:{actor_id}"
        result[actor_id] = value
        result[actor_memory_store.canonical_actor_id(actor_id)] = value
    return result


def _registered_actor_targets(input_payload: dict) -> set[str]:
    return {"player", * _characters_by_actor_id(input_payload).keys()}


def _card_folder_for_run(run_dir: Path) -> Path:
    return run_dir.parent.parent if run_dir.parent.name == ".agent_runs" else run_dir.parent


def _ensure_character_context(input_payload: dict, promotion: dict) -> None:
    name = str(promotion.get("name") or "").strip()
    if not name:
        return
    contexts = input_payload.get("character_contexts")
    if not isinstance(contexts, dict):
        contexts = {}
        input_payload["character_contexts"] = contexts
    characters = contexts.get("characters")
    if isinstance(characters, dict):
        characters = list(characters.values())
    elif not isinstance(characters, list):
        characters = []
    existing_names = {
        str(item.get("name") or item.get("character_name") or "").strip()
        for item in characters
        if isinstance(item, dict)
    }
    if name not in existing_names:
        context = dict(promotion)
        context["name"] = name
        if "memory" not in context:
            seed = str(context.get("profile_seed") or context.get("profile_summary") or "").strip()
            context["profile_summary"] = seed
            context["memory"] = {"long_term": [seed] if seed else [], "recent": [], "goals": []}
        characters.append(context)
    contexts["characters"] = characters


def _apply_character_promotions(root: Path, input_payload: dict, gm_output: dict) -> dict:
    records = gm_output.get("character_promotions", [])
    if not records:
        return {"promoted": [], "registered": [], "skipped": [], "records": []}
    try:
        result = character_promotions.apply_promotions(
            _card_folder_for_run(root),
            records,
            round_id=root.name,
        )
    except character_promotions.CharacterPromotionError as exc:
        raise AgentTurnLoopError(f"invalid character promotion: {exc}") from exc
    for context in result.get("contexts", []):
        if isinstance(context, dict):
            _ensure_character_context(input_payload, context)
    return result


def _participants(input_payload: dict) -> list[str]:
    participants = ["gm", "player"]
    for actor_id in sorted(_characters_by_actor_id(input_payload)):
        if actor_id not in participants:
            participants.append(actor_id)
    return participants


def _actor_state(actor_id: str, input_payload: dict) -> dict:
    if actor_id == "player":
        player_state = _dict(input_payload.get("player_state") or input_payload.get("player_context"))
        if player_state:
            return player_state
        return {"name": "player", "memory": [], "goals": []}
    return _characters_by_actor_id(input_payload).get(actor_id, {"name": actor_id.split(":", 1)[-1]})


def _initial_world_state(input_payload: dict) -> dict:
    routed = _dict(input_payload.get("routed_input"))
    world_state = dict(input_payload)
    world_state["role_channel"] = routed.get("role_channel", input_payload.get("role_channel", ""))
    world_state["role_action_channel"] = routed.get(
        "role_action_channel",
        input_payload.get("role_action_channel", ""),
    )
    world_state["narrative_guidance_channel"] = routed.get(
        "narrative_guidance_channel",
        input_payload.get("narrative_guidance_channel", ""),
    )
    world_state["user_instruction_channel"] = routed.get(
        "user_instruction_channel",
        input_payload.get("user_instruction_channel", ""),
    )
    world_state.setdefault("recent_chat", input_payload.get("recent_chat", []))
    world_state.setdefault("gm_only_hidden_settings", input_payload.get("gm_only_hidden_settings", []))
    world_state.setdefault("visible_events", [])
    if not isinstance(world_state.get("world_state_delta"), list):
        world_state["world_state_delta"] = []
    return world_state


def _ensure_trace(run_dir: Path, input_payload: dict) -> None:
    if not (run_dir / "interaction.trace.json").exists():
        agent_interactions.init_trace(
            run_dir,
            participants=_participants(input_payload),
            chapter_target_words=_runtime_word_target(input_payload),
        )


def _validate_gm(payload: Any) -> dict:
    try:
        return agent_schemas.validate_gm_output(payload)
    except agent_schemas.ValidationError as exc:
        raise AgentTurnLoopError(f"invalid gm output: {exc}") from exc


def _validate_actor(actor_id: str, payload: Any) -> dict:
    if isinstance(payload, str):
        try:
            return agent_schemas.natural_actor_output(
                actor_id,
                payload,
                actor_id.split(":", 1)[-1] if actor_id.startswith("character:") else "",
            )
        except agent_schemas.ValidationError as exc:
            raise AgentTurnLoopError(f"invalid actor output for {actor_id}: {exc}") from exc
    try:
        output = agent_schemas.validate_actor_output(payload)
    except agent_schemas.ValidationError as exc:
        raise AgentTurnLoopError(f"invalid actor output for {actor_id}: {exc}") from exc

    expected_agent = "player" if actor_id == "player" else "character"
    if output.get("agent") != expected_agent:
        raise AgentTurnLoopError(f"invalid actor output for {actor_id}: wrong agent {output.get('agent')!r}")
    if output.get("agent_id") != actor_id:
        raise AgentTurnLoopError(f"invalid actor output for {actor_id}: wrong agent_id {output.get('agent_id')!r}")
    return output


def _event_content(event: dict) -> str:
    return str(event.get("content") or "")


def _runtime_word_target(input_payload: dict) -> int:
    settings = _dict(input_payload.get("runtime_settings"))
    raw = settings.get("wordCount")
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return max(0, raw)
    if isinstance(raw, float) and raw.is_integer():
        return max(0, int(raw))
    text = str(raw or "").strip()
    if re.fullmatch(r"\d+", text):
        return max(0, int(text))
    return 0


def _raw_story_word_limit(input_payload: dict) -> int:
    target = _runtime_word_target(input_payload)
    if target <= 0:
        return 0
    return int(target * 12 // 10)


def _count_raw_story_units(text: str) -> int:
    clean = re.sub(r"<[^>]+>", "", str(text or ""))
    cjk_count = sum(1 for ch in clean if "\u3400" <= ch <= "\u9fff")
    latin_words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", clean)
    return cjk_count + len(latin_words)


def _gm_raw_story_units(gm_output: dict) -> int:
    total = 0
    for beat in gm_output.get("scene_beats", []) or []:
        if isinstance(beat, dict):
            total += _count_raw_story_units(beat.get("content", ""))
    for event in gm_output.get("events", []) or []:
        if isinstance(event, dict):
            total += _count_raw_story_units(event.get("content", ""))
    return total


def _actor_raw_story_units(actor_output: dict) -> int:
    natural_reply = str(actor_output.get("natural_reply") or "").strip()
    if natural_reply:
        return _count_raw_story_units(natural_reply)
    total = 0
    for event in actor_output.get("events", []) or []:
        if isinstance(event, dict):
            total += _count_raw_story_units(event.get("content", ""))
    return total


def _record_gm_output(run_dir: Path, gm_output: dict, step_index: int) -> None:
    for beat in gm_output.get("scene_beats", []):
        content = _event_content(beat)
        if content:
            agent_interactions.append_event(
                run_dir,
                "gm",
                "world_visible",
                "scene_beat",
                content,
                visibility_metadata=agent_visibility.visibility_fields_from_event(beat),
            )

    for event in gm_output.get("events", []):
        content = _event_content(event)
        if not content:
            continue
        agent_interactions.append_event(
            run_dir,
            actor="gm",
            visibility="world_visible",
            event_type=str(event.get("type") or "gm_event"),
            content=content,
            target=str(event.get("target") or ""),
            source_call_id=str(event.get("source_call_id") or ""),
            visibility_metadata=agent_visibility.visibility_fields_from_event(event),
        )

    for group_index, group in enumerate(gm_output.get("parallel_groups", []), start=1):
        group_id = f"group-{step_index + 1}-{group_index}"
        actors: Iterable[str]
        if isinstance(group, dict):
            group_id = str(group.get("group_id") or group_id)
            actors = group.get("actors") or group.get("actor_ids") or []
        else:
            actors = group
        agent_interactions.record_parallel_group(run_dir, group_id, actors)


def _record_actor_event(
    run_dir: Path,
    actor_id: str,
    event: dict,
    source_call_id: str,
) -> None:
    agent_actor_runtime.record_actor_event(run_dir, actor_id, event, source_call_id)


def _contains_dynamic_hidden_phrase(text: str, hidden_phrases: Iterable[str]) -> bool:
    original = str(text or "")
    if not original:
        return False
    return agent_visibility_guard.redact_text(original, hidden_phrases) != original


def _safe_actor_call_id(actor_id: str, counts: dict[str, int]) -> str:
    counts[actor_id] = counts.get(actor_id, 0) + 1
    if actor_id == "player":
        return f"call-player-{counts[actor_id]}"
    safe = _ascii_actor_slug(actor_id)
    return f"call-character-{safe}-{counts[actor_id]}"


def _actor_call_id_index(actor_id: str, call_id: str) -> int | None:
    if actor_id == "player":
        match = TRACE_SAFE_PLAYER_CALL_ID_RE.fullmatch(call_id)
        return int(match.group(1)) if match else None
    match = TRACE_SAFE_CHARACTER_CALL_ID_RE.fullmatch(call_id)
    if not match or match.group(1) != _ascii_actor_slug(actor_id):
        return None
    return int(match.group(2))


def _next_unique_actor_call_id(actor_id: str, counts: dict[str, int], used: set[str]) -> str:
    while True:
        call_id = _safe_actor_call_id(actor_id, counts)
        if call_id not in used:
            used.add(call_id)
            return call_id


def _normalize_main_actor_call_ids(gm_output: dict, counts: dict[str, int], used: set[str]) -> None:
    for call in gm_output.get("actor_calls", []) or []:
        actor_id = str(call.get("actor_id") or "")
        call_id = str(call.get("call_id") or "").strip()
        call_index = _actor_call_id_index(actor_id, call_id) if call_id else None
        if call_index is not None and call_id not in used:
            used.add(call_id)
            counts[actor_id] = max(counts.get(actor_id, 0), call_index)
            call["call_id"] = call_id
            continue
        call["call_id"] = _next_unique_actor_call_id(actor_id, counts, used)


def _ascii_actor_slug(actor_id: str) -> str:
    raw = str(actor_id).split(":", 1)[-1]
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    if not slug or not slug[0].isalpha():
        digest = hashlib.sha1(str(actor_id).encode("utf-8")).hexdigest()[:8]
        slug = f"Actor_{digest}"
    return slug


def _update_visible_events(run_dir: Path, world_state: dict) -> None:
    world_state["visible_events"] = agent_interactions.summarize_for_story_input(run_dir)["visible_events"]


def _gm_packet(run_dir: Path, world_state: dict, step_index: int) -> dict:
    return {
        "step": step_index,
        "role_channel": str(world_state.get("role_channel") or ""),
        "role_action_channel": str(world_state.get("role_action_channel") or ""),
        "narrative_guidance_channel": str(world_state.get("narrative_guidance_channel") or ""),
        "user_instruction_channel": str(world_state.get("user_instruction_channel") or ""),
        "world_state": dict(world_state),
        "trace_summary": agent_interactions.summarize_for_story_input(run_dir),
    }


def _actor_packet(
    input_payload: dict,
    world_state: dict,
    actor_id: str,
    prompt: str,
    hidden_phrases: Iterable[str],
    actor_call: dict | None = None,
) -> dict:
    safe_prompt = agent_visibility_guard.redact_text(prompt, hidden_phrases)
    return agent_projection.project_actor_context(
        actor_id,
        world_state,
        _actor_state(actor_id, input_payload),
        safe_prompt,
        agent_visibility.actor_call_basis(actor_call or {}),
    )


def _dispatch_actor_key(actor_id: str) -> str:
    return "player" if actor_id == "player" else actor_id


def _restore_actor_call_source_call_ids(gm_output: dict, raw_payload: Any) -> None:
    if not isinstance(raw_payload, dict):
        return
    raw_calls = raw_payload.get("actor_calls")
    calls = gm_output.get("actor_calls")
    if not isinstance(raw_calls, list) or not isinstance(calls, list):
        return

    raw_by_call_id = {
        str(raw_call.get("call_id") or ""): raw_call
        for raw_call in raw_calls
        if isinstance(raw_call, dict)
    }
    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            continue
        raw_call = raw_by_call_id.get(str(call.get("call_id") or ""))
        if raw_call is None and index < len(raw_calls) and isinstance(raw_calls[index], dict):
            raw_call = raw_calls[index]
        if not isinstance(raw_call, dict):
            continue
        source_call_id = str(raw_call.get("source_call_id") or "").strip()
        if source_call_id:
            call["source_call_id"] = source_call_id


def _batch_actors(batch: dict) -> list[str]:
    return [str(call.get("actor_id") or "") for call in batch.get("calls", []) if isinstance(call, dict)]


def _batch_call_ids(batch: dict) -> list[str]:
    return [str(call.get("call_id") or "") for call in batch.get("calls", []) if isinstance(call, dict)]


def _record_actor_batch_plan(run_dir: Path, step_index: int, batch_index: int, batch: dict) -> None:
    agent_interactions.record_actor_batch(
        run_dir,
        batch_id=f"batch-{step_index + 1}-{batch_index + 1}",
        kind=str(batch.get("kind") or "serial"),
        actors=_batch_actors(batch),
        call_ids=_batch_call_ids(batch),
        group_id=str(batch.get("group_id") or ""),
    )


def _record_routing_warnings(run_dir: Path, warnings: list[dict]) -> None:
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        agent_interactions.record_routing_warning(
            run_dir,
            code=str(warning.get("code") or ""),
            message=str(warning.get("message") or ""),
            group_id=str(warning.get("group_id") or ""),
            actors=[str(item) for item in warning.get("actors") or []],
            call_ids=[str(item) for item in warning.get("call_ids") or []],
        )


def _runtime_write_error(action: str, exc: Exception) -> AgentTurnLoopError:
    return AgentTurnLoopError(f"{action} failed: {exc}")


def _actor_runtime_loop_error(exc: agent_actor_runtime.AgentActorRuntimeError) -> AgentTurnLoopError:
    error = AgentTurnLoopError(str(exc))
    cause = exc.__cause__ if exc.__cause__ is not None else exc
    raise error from cause


def _record_request_actor_intent(run_dir: Path, sender: str, actor_id: str, call: dict) -> tuple[str, str]:
    try:
        return agent_actor_runtime.record_request_actor(run_dir, sender, actor_id, call)
    except agent_actor_runtime.AgentActorRuntimeError as exc:
        _actor_runtime_loop_error(exc)
    except Exception as exc:
        raise _runtime_write_error("record request_actor intent", exc) from exc


def _complete_projection_intent(root: Path, intent_id: str, projected_message_id: str) -> None:
    try:
        agent_actor_runtime._require_intent_result(
            "complete request_projection intent",
            agent_actor_runtime.agent_intents.complete_intent(
                root,
                intent_id,
                outputs={"projected_message_id": projected_message_id},
            ),
        )
    except agent_actor_runtime.AgentActorRuntimeError as exc:
        _actor_runtime_loop_error(exc)
    except Exception as exc:
        raise _runtime_write_error("complete request_projection intent", exc) from exc


def _validate_projection_result(actor_id: str, source_call_id: str, payload: Any) -> dict:
    try:
        result = projection_agent.validate_projection_output(
            payload,
            actor_id=actor_id,
            source_call_id=source_call_id,
        )
    except projection_agent.ProjectionValidationError as exc:
        raise AgentTurnLoopError(f"invalid projection output: {exc}") from exc
    decision = str(result.get("decision") or "")
    if decision not in {"pass", "edited"}:
        feedback = str(result.get("feedback") or "").strip()
        detail = f": {feedback}" if feedback else ""
        raise AgentTurnLoopError(f"projection rejected actor message with {decision}{detail}")
    return result


def _project_actor_message(
    run_dir: Path,
    card_folder: Path,
    actor_id: str,
    call: dict,
    packet: dict,
    source_message_id: str,
    intent_id: str,
    dispatch: DispatchFn,
) -> dict:
    call_id = str(call.get("call_id") or "")
    try:
        projection_packet = projection_agent.build_review_packet(
            actor_id=actor_id,
            source_call_id=call_id,
            source_message_id=source_message_id,
            requested_actor_message=str(call.get("prompt") or ""),
            actor_packet=packet,
            objective_context={},
            card_folder=str(card_folder),
        )
        projection_output = dispatch("projection", projection_packet)
        projection_result = _validate_projection_result(actor_id, call_id, projection_output)
        projected = agent_actor_runtime.project_actor_request(
            run_dir,
            actor_id=actor_id,
            source_message_id=source_message_id,
            source_call_id=call_id,
            projection_result=projection_result,
        )
        projected_message_id = str(projected.get("projected_message_id") or "")
        _complete_projection_intent(Path(run_dir), intent_id, projected_message_id)
        return projection_result
    except agent_actor_runtime.AgentActorRuntimeError as exc:
        _actor_runtime_loop_error(exc)
    except AgentTurnLoopError:
        raise
    except Exception as exc:
        raise _runtime_write_error("project actor message", exc) from exc


def _record_actor_response_message(run_dir: Path, actor_id: str, call: dict, actor_output: dict) -> str:
    try:
        return agent_actor_runtime.record_actor_response(run_dir, actor_id, call, actor_output)
    except agent_actor_runtime.AgentActorRuntimeError as exc:
        _actor_runtime_loop_error(exc)
    except Exception as exc:
        raise _runtime_write_error("record actor_response message", exc) from exc


def _append_short_term_dialogue(
    card_folder: Path,
    actor_id: str,
    speaker: str,
    content: str,
    source_id: str,
) -> None:
    try:
        actor_memory_store.append_short_term_dialogue(
            card_folder,
            actor_id,
            speaker,
            content,
            source_id=source_id,
        )
    except Exception as exc:
        raise AgentTurnLoopError(f"append short-term memory failed: {exc}") from exc


def _initial_player_action_text(input_payload: dict) -> str:
    routed = _dict(input_payload.get("routed_input"))
    return str(routed.get("role_action_channel") or input_payload.get("role_action_channel") or "").strip()


def _record_initial_player_action(
    card_folder: Path | None,
    run_dir: Path,
    input_payload: dict,
) -> bool:
    action = _initial_player_action_text(input_payload)
    if not action or card_folder is None:
        return False
    _append_short_term_dialogue(
        card_folder,
        "player",
        "player",
        action,
        f"{run_dir.name}:player_input:actor",
    )
    return True


def _dispatch_actor_call(
    *,
    run_dir: Path,
    card_folder: Path,
    input_payload: dict,
    world_state: dict,
    actor_id: str,
    call: dict,
    hidden_phrases: Iterable[str],
    dispatch: DispatchFn,
) -> tuple[dict, dict | None]:
    actor_state = _actor_state(actor_id, input_payload)
    if not agent_visibility.actor_call_visible_to_actor(call, actor_id, actor_state):
        raise AgentTurnLoopError(
            f"actor call visibility_basis does not prove visibility for {actor_id}"
        )
    packet = _actor_packet(
        input_payload,
        world_state,
        actor_id,
        str(call.get("prompt") or ""),
        hidden_phrases,
        call,
    )
    packet = agent_lifecycle.attach_actor_context_version(card_folder, actor_id, packet)
    root = Path(run_dir)
    request_message_id, intent_id = _record_request_actor_intent(root, "gm", actor_id, call)
    projection_result = _project_actor_message(
        root,
        card_folder,
        actor_id,
        call,
        packet,
        request_message_id,
        intent_id,
        dispatch,
    )
    final_actor_message = str(projection_result.get("final_actor_message") or "").strip()
    if final_actor_message:
        packet = dict(packet)
        packet["gm_prompt"] = final_actor_message
        call = dict(call)
        call["prompt"] = final_actor_message
    packet = dict(packet)
    packet["card_folder"] = str(card_folder)
    call_id = str(call.get("call_id") or "")
    _write_progress_safe(
        "gm_loop.actor_dispatch",
        "actor dispatch",
        percent=48,
        detail={
            "actor": actor_id,
            "actor_call_id": call_id,
        },
    )
    raw_actor_payload = dispatch(_dispatch_actor_key(actor_id), packet)
    actor_output = _validate_actor(actor_id, raw_actor_payload)
    _append_short_term_dialogue(
        card_folder,
        actor_id,
        "gm",
        final_actor_message,
        f"{root.name}:{call_id}:gm",
    )
    _append_short_term_dialogue(
        card_folder,
        actor_id,
        actor_id,
        str(actor_output.get("natural_reply") or ""),
        f"{root.name}:{call_id}:actor",
    )
    _record_actor_response_message(root, actor_id, call, actor_output)
    returned_version = _dict(raw_actor_payload.get("context_version")) if isinstance(raw_actor_payload, dict) else {}
    returned_hash = str(returned_version.get("hash") or "").strip()
    current_hash = str(_dict(packet.get("context_version")).get("hash") or "").strip()
    warning = None
    if returned_hash and current_hash and returned_hash != current_hash:
        warning = {
            "actor_id": actor_id,
            "returned_hash": returned_hash,
            "current_hash": current_hash,
        }
    return actor_output, warning


def _process_actor_output(
    *,
    run_dir: Path,
    actor_id: str,
    actor_output: dict,
    call_id: str,
) -> None:
    for event in actor_output.get("events", []):
        _record_actor_event(run_dir, actor_id, event, call_id)


def _write_outputs(run_dir: Path, gm_outputs: list[dict], actor_outputs: dict[str, list[dict]]) -> None:
    agent_run.write_json(run_dir / "gm.output.json", {"agent": "gm_loop", "outputs": gm_outputs})
    agent_run.write_json(run_dir / "actor.outputs.json", actor_outputs)


def _read_gm_outputs(run_dir: Path) -> list[dict]:
    source = run_dir / "artifacts" / "gm.output.json"
    if not source.exists():
        source = run_dir / "gm.output.json"
    payload = agent_run.read_json(source, {})
    if not isinstance(payload, dict):
        return []
    outputs = payload.get("outputs")
    if not isinstance(outputs, list):
        return []
    return [item for item in outputs if isinstance(item, dict)]


def _seed_actor_call_ids_from_outputs(gm_outputs: list[dict]) -> tuple[dict[str, int], set[str]]:
    counts: dict[str, int] = {}
    used: set[str] = set()
    for output in gm_outputs:
        for call in output.get("actor_calls", []) or []:
            if not isinstance(call, dict):
                continue
            actor_id = str(call.get("actor_id") or "")
            call_id = str(call.get("call_id") or "")
            if not actor_id or not call_id:
                continue
            used.add(call_id)
            index = _actor_call_id_index(actor_id, call_id)
            if index is not None:
                counts[actor_id] = max(counts.get(actor_id, 0), index)
    return counts, used


def _append_gm_output(run_dir: Path, gm_output: dict) -> list[dict]:
    outputs = _read_gm_outputs(run_dir)
    outputs.append(gm_output)
    payload = {"agent": "gm_loop", "outputs": outputs}
    agent_run.write_json(run_dir / "gm.output.json", payload)
    agent_run.write_json(run_dir / "artifacts" / "gm.output.json", payload)
    return outputs


def _runnable_side_thread_summaries(run_dir: Path) -> list[dict]:
    runnable_statuses = getattr(subgm_turn_loop, "RUNNABLE_STATUSES", {"running", "merging", "needs_gm", "blocked"})
    try:
        summaries = subgm_threads.load_thread_summaries(run_dir)
    except subgm_threads.SubgmThreadError as exc:
        raise AgentTurnLoopError(f"invalid subGM side-thread state: {exc}") from exc
    return [
        summary
        for summary in summaries
        if str(summary.get("thread_id") or "") and str(summary.get("status") or "") in runnable_statuses
    ]


def run_gm_only_step(
    run_dir: str | Path,
    dispatch: DispatchFn,
) -> dict:
    """Run one GM dispatch/validation/persist step without actor, subGM, or story dispatch."""

    root = Path(run_dir)
    input_payload = _read_input(root)
    _ensure_trace(root, input_payload)

    existing_gm_outputs = _read_gm_outputs(root)
    step_index = len(existing_gm_outputs)
    world_state = _initial_world_state(input_payload)
    for prior_output in existing_gm_outputs:
        _apply_world_state_delta(world_state, prior_output)
    _refresh_side_thread_state(root, world_state)
    _update_visible_events(root, world_state)

    _write_progress_safe(
        "gm_loop.gm_dispatch",
        "GM 正在推进剧情",
        percent=47,
        detail={"run_id": root.name, "step": step_index + 1},
    )
    raw_gm_payload = dispatch("gm", _gm_packet(root, world_state, step_index))
    raw_gm_output = _validate_gm(raw_gm_payload)
    _restore_actor_call_source_call_ids(raw_gm_output, raw_gm_payload)

    gm_output = agent_visibility_guard.sanitize_gm_output(raw_gm_output, input_payload)
    gm_output = _normalize_subgm_command_actor_ids(gm_output, input_payload)
    _prevalidate_subgm_commands(root, gm_output, input_payload)
    _preflight_subgm_actor_conflicts(root, gm_output, input_payload)
    _apply_character_promotions(root, input_payload, gm_output)
    registered_actor_targets = _registered_actor_targets(input_payload)
    gm_output = _filter_gm_actor_calls(gm_output, registered_actor_targets)

    generated_call_counts, used_actor_call_ids = _seed_actor_call_ids_from_outputs(existing_gm_outputs)
    _normalize_main_actor_call_ids(gm_output, generated_call_counts, used_actor_call_ids)
    _preflight_subgm_actor_conflicts(root, gm_output, input_payload)
    _apply_subgm_commands(root, gm_output, input_payload)
    _assert_main_actor_calls_do_not_conflict(root, gm_output.get("actor_calls", []))
    _assert_complete_handles_existing_side_threads(root, gm_output)

    _record_gm_output(root, gm_output, step_index)
    _apply_world_state_delta(world_state, gm_output)
    _refresh_side_thread_state(root, world_state)
    _update_visible_events(root, world_state)

    hidden_phrases = agent_visibility_guard.hidden_phrases(input_payload)
    all_gm_outputs = _append_gm_output(root, gm_output)

    gm_stop = str(gm_output.get("stop_reason") or "continue")
    decision_point: Any = None

    actor_calls = []
    actor_calls.extend([call for call in gm_output.get("actor_calls", []) or [] if isinstance(call, dict)])
    actor_work = []
    for call in actor_calls:
        actor_id = str(call.get("actor_id") or "")
        if not actor_id:
            continue
        call_for_projection = dict(call)
        call_for_projection["packet"] = _actor_packet(
            input_payload,
            world_state,
            actor_id,
            str(call.get("prompt") or ""),
            hidden_phrases,
            actor_call=call,
        )
        actor_work.append(call_for_projection)
    runnable_side_threads = _runnable_side_thread_summaries(root)
    stop_reason = gm_stop if gm_stop in STOP_REASONS and gm_stop != "player_decision" else "continue"

    return {
        "ok": True,
        "gm_steps": len(all_gm_outputs),
        "step_index": step_index,
        "gm_output": gm_output,
        "actor_calls": actor_work,
        "runnable_side_threads": runnable_side_threads,
        "called_actors": [],
        "stop_reason": stop_reason,
        "decision_point": decision_point,
        "side_thread_results": [],
    }


def _filter_gm_actor_calls(gm_output: dict, registered_actor_targets: set[str]) -> dict:
    filtered = dict(gm_output)
    filtered["actor_calls"] = [
        call
        for call in gm_output.get("actor_calls", [])
        if str(call.get("actor_id") or "") in registered_actor_targets
    ]
    return filtered


def _input_requests_player_actor(input_payload: dict) -> bool:
    routed = _dict(input_payload.get("routed_input"))
    analysis = _dict(input_payload.get("input_analysis"))
    directives = _dict(analysis.get("narrative_directives"))
    card_data = _dict(input_payload.get("card_data"))
    compact_name = str(card_data.get("name") or "").strip()
    blank_card = bool(card_data) and (
        str(card_data.get("mode") or "").strip() == "blank_bootstrap"
        or str(card_data.get("source_type") or "").strip() == "blank"
        or compact_name in {"", "未命名角色", "player"}
    )
    return routed.get("player") is True and (
        directives.get("expand_synopsis_before_continue") is True or blank_card
    )


def _visible_player_prompt_from_gm_output(gm_output: dict) -> str:
    parts: list[str] = []
    for beat in gm_output.get("scene_beats", []) or []:
        if not isinstance(beat, dict):
            continue
        content = str(beat.get("content") or "").strip()
        if content:
            parts.append(content)
    for event in gm_output.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        target = str(event.get("target") or "").strip().casefold()
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        visibility = str(metadata.get("visibility") or "").strip().casefold()
        if "hidden" in target or visibility in {"gm_only", "hidden", "[redacted]"}:
            continue
        content = str(event.get("content") or "").strip()
        if content:
            parts.append(content)
    text = "\n\n".join(parts[:6]).strip()
    if not text:
        text = "你能感到当前场景正在继续展开。"
    return f"{text}\n\n你现在想做什么？"


def _ensure_initial_player_actor_call(
    gm_output: dict,
    input_payload: dict,
    player_already_participated: bool,
) -> dict:
    if player_already_participated or not _input_requests_player_actor(input_payload):
        return gm_output
    calls = [call for call in gm_output.get("actor_calls", []) if isinstance(call, dict)]
    if calls:
        return gm_output
    repaired = dict(gm_output)
    repaired["actor_calls"] = calls + [
        {
            "call_id": "call-player-1",
            "actor_id": "player",
            "prompt": _visible_player_prompt_from_gm_output(gm_output),
            "reason": "Initial GM output did not call the player actor; control plane requires player agency before story delivery.",
            "metadata": {"control_plane_injected": True},
            "visibility_basis": {
                "mode": "direct",
                "summary": "The prompt is built only from GM-visible scene beats and actor-visible events from this GM step.",
                "target_actor": "player",
            },
        }
    ]
    return repaired


def _decision_reason(decision_point: Any) -> str:
    if isinstance(decision_point, dict):
        return str(decision_point.get("reason") or "")
    return str(decision_point or "")


def _decision_options(decision_point: Any) -> list[str]:
    if isinstance(decision_point, dict):
        return [str(item) for item in decision_point.get("options") or []]
    return []


def _mark_decision(run_dir: Path, decision_point: Any, fallback_reason: str) -> Any:
    reason = _decision_reason(decision_point) or fallback_reason
    options = _decision_options(decision_point)
    agent_interactions.mark_decision_point(run_dir, reason=reason, options=options)
    if isinstance(decision_point, dict):
        return decision_point
    return {"reason": reason, "options": options}


def _player_actor_participated(called_actors: Iterable[str]) -> bool:
    return any(str(actor_id or "") == "player" for actor_id in called_actors)


def _apply_world_state_delta(world_state: dict, gm_output: dict) -> None:
    delta = gm_output.get("world_state_delta", [])
    if not isinstance(delta, list) or not delta:
        return
    accumulated = world_state.setdefault("world_state_delta", [])
    if not isinstance(accumulated, list):
        accumulated = []
        world_state["world_state_delta"] = accumulated
    accumulated.extend(delta)


def _refresh_side_thread_state(root: Path, world_state: dict) -> None:
    try:
        world_state["side_thread_summaries"] = subgm_threads.load_thread_summaries(root)
        world_state["subgm_messages"] = subgm_threads.load_messages_for_gm(root)
    except subgm_threads.SubgmThreadError as exc:
        raise AgentTurnLoopError(f"invalid subGM side-thread state: {exc}") from exc


def _normalize_character_actor_id(actor_id: Any, input_payload: dict) -> str:
    text = str(actor_id or "").strip()
    if text == "player" or not text.startswith("character:"):
        return text
    registered = _characters_by_actor_id(input_payload)
    if text in registered:
        name = text.split(":", 1)[1]
        return f"character:{_safe_character_actor_suffix(name)}"
    name = text.split(":", 1)[1]
    for key in registered:
        if key.split(":", 1)[1] == name:
            return f"character:{_safe_character_actor_suffix(name)}"
    if not re.match(r"^character:[A-Za-z][A-Za-z0-9_]*$", text):
        return f"character:{_safe_character_actor_suffix(name)}"
    return text


def _normalize_subgm_command_actor_ids(gm_output: dict, input_payload: dict) -> dict:
    commands = gm_output.get("subgm_commands")
    if not isinstance(commands, list):
        return gm_output
    normalized_commands = []
    for command in commands:
        if not isinstance(command, dict):
            normalized_commands.append(command)
            continue
        item = dict(command)
        for field in ("allowed_characters", "forbidden_characters"):
            values = item.get(field)
            if isinstance(values, list):
                item[field] = [_normalize_character_actor_id(value, input_payload) for value in values]
        normalized_commands.append(item)
    gm_output["subgm_commands"] = normalized_commands
    return gm_output


def _apply_subgm_commands(root: Path, gm_output: dict, input_payload: dict | None = None) -> dict:
    if input_payload is not None:
        gm_output = _normalize_subgm_command_actor_ids(gm_output, input_payload)
    try:
        return subgm_threads.apply_gm_commands(root, gm_output.get("subgm_commands", []))
    except subgm_threads.SubgmThreadError as exc:
        raise AgentTurnLoopError(f"invalid subGM command: {exc}") from exc


def _prevalidate_subgm_commands(root: Path, gm_output: dict, input_payload: dict | None = None) -> None:
    if input_payload is not None:
        gm_output = _normalize_subgm_command_actor_ids(gm_output, input_payload)
    try:
        subgm_threads.prevalidate_gm_commands(root, gm_output.get("subgm_commands", []))
    except subgm_threads.SubgmThreadError as exc:
        raise AgentTurnLoopError(f"invalid subGM command: {exc}") from exc


def _preflight_subgm_actor_conflicts(root: Path, gm_output: dict, input_payload: dict | None = None) -> None:
    if input_payload is not None:
        gm_output = _normalize_subgm_command_actor_ids(gm_output, input_payload)
    try:
        summaries = subgm_threads.load_thread_summaries(root)
    except subgm_threads.SubgmThreadError as exc:
        raise AgentTurnLoopError(f"invalid subGM side-thread state: {exc}") from exc

    allowed_by_thread: dict[str, set[str]] = {}
    reservations: dict[str, str] = {}

    def reserve(actor_id: str, thread_id: str) -> None:
        owner = reservations.get(actor_id)
        if owner and owner != thread_id:
            raise AgentTurnLoopError(
                "subGM command reservation conflict: "
                f"{actor_id} is already reserved by side thread {owner}; cannot reserve for {thread_id}"
            )
        reservations[actor_id] = thread_id

    for summary in summaries:
        thread_id = str(summary.get("thread_id") or "")
        if not thread_id:
            continue
        allowed = {str(item) for item in _list(summary.get("allowed_characters")) if str(item)}
        allowed_by_thread[thread_id] = allowed
        if str(summary.get("status") or "") in ACTIVE_SIDE_THREAD_STATUSES:
            for actor_id in allowed:
                reserve(actor_id, thread_id)

    for command in gm_output.get("subgm_commands", []):
        if not isinstance(command, dict):
            continue
        action = str(command.get("action") or "")
        thread_id = str(command.get("thread_id") or "")
        if not thread_id:
            continue
        if action == "start":
            if thread_id in allowed_by_thread:
                raise AgentTurnLoopError(
                    f"invalid subGM command preflight: side thread {thread_id} already exists"
                )
            allowed_by_thread[thread_id] = {
                str(item) for item in _list(command.get("allowed_characters")) if str(item)
            }
        elif thread_id not in allowed_by_thread:
            raise AgentTurnLoopError(
                f"invalid subGM command preflight: side thread {thread_id} is missing"
            )
        allowed = allowed_by_thread[thread_id]
        if action in RESERVATION_RELEASING_SUBGM_ACTIONS:
            for actor_id, owner in list(reservations.items()):
                if owner == thread_id:
                    reservations.pop(actor_id, None)
        elif action in RESERVATION_ACTIVATING_SUBGM_ACTIONS:
            for actor_id in allowed:
                reserve(actor_id, thread_id)

    for call in gm_output.get("actor_calls", []):
        if not isinstance(call, dict):
            continue
        actor_id = str(call.get("actor_id") or "")
        owner = reservations.get(actor_id)
        if owner:
            raise AgentTurnLoopError(
                f"main actor call conflicts with subGM side thread: {actor_id} is reserved by active side thread {owner}"
            )


def _run_ready_side_threads(root: Path, dispatch: DispatchFn) -> list[dict]:
    try:
        return subgm_turn_loop.run_ready_side_threads(root, dispatch, max_workers=2)
    except (subgm_threads.SubgmThreadError, subgm_turn_loop.SubgmTurnLoopError) as exc:
        raise AgentTurnLoopError(f"subGM side-thread failed: {exc}") from exc


def _active_side_thread_summaries(root: Path) -> list[dict]:
    try:
        summaries = subgm_threads.load_thread_summaries(root)
    except subgm_threads.SubgmThreadError as exc:
        raise AgentTurnLoopError(f"invalid subGM side-thread state: {exc}") from exc
    return [
        summary
        for summary in summaries
        if str(summary.get("status") or "") in ACTIVE_SIDE_THREAD_STATUSES
    ]


def _handled_subgm_thread_ids(gm_output: dict) -> set[str]:
    handled: set[str] = set()
    for command in _list(gm_output.get("subgm_commands")):
        if not isinstance(command, dict):
            continue
        action = str(command.get("action") or "")
        thread_id = str(command.get("thread_id") or "")
        if action in {"message", "accelerate", "pause", "resume", "merge", "close"} and thread_id:
            handled.add(thread_id)
    return handled


def _format_side_threads(summaries: list[dict]) -> str:
    return ", ".join(
        f"{summary.get('thread_id', '')}({summary.get('status', '')})"
        for summary in summaries
    )


def _assert_complete_handles_existing_side_threads(root: Path, gm_output: dict) -> None:
    if str(gm_output.get("stop_reason") or "") != "complete":
        return
    active = _active_side_thread_summaries(root)
    if not active:
        return
    handled = _handled_subgm_thread_ids(gm_output)
    unhandled = [
        summary
        for summary in active
        if str(summary.get("thread_id") or "") not in handled
    ]
    if unhandled:
        raise AgentTurnLoopError(
            "unresolved subGM side thread before complete: "
            f"{_format_side_threads(unhandled)}; GM must message, accelerate, pause, merge, "
            "or close each active side thread, or keep stop_reason as continue while waiting."
        )


def _assert_complete_leaves_no_active_side_threads(root: Path, gm_output: dict) -> None:
    if str(gm_output.get("stop_reason") or "") != "complete":
        return
    active = _active_side_thread_summaries(root)
    if active:
        raise AgentTurnLoopError(
            "unresolved subGM side thread remains before complete: "
            f"{_format_side_threads(active)}; GM must continue the loop, stop for player decision, "
            "or explicitly pause/close the side thread."
        )


def _assert_main_actor_calls_do_not_conflict(root: Path, actor_calls: list[dict]) -> None:
    try:
        subgm_threads.assert_main_actor_calls_do_not_conflict(root, actor_calls)
    except subgm_threads.SubgmThreadError as exc:
        raise AgentTurnLoopError(f"main actor call conflicts with subGM side thread: {exc}") from exc


def run_interactive_loop(
    run_dir: str | Path,
    dispatch: DispatchFn,
    *,
    max_steps: int | None = None,
    card_folder: str | Path | None = None,
) -> dict:
    """Run the GM/actor control loop through `dispatch` until a hard stop condition."""
    root = Path(run_dir)
    card_for_versions = Path(card_folder) if card_folder is not None else _card_folder_for_run(root)
    input_payload = _read_input(root)
    _ensure_trace(root, input_payload)

    step_limit = max(1, int(max_steps)) if max_steps is not None else None
    raw_story_limit = _raw_story_word_limit(input_payload)
    raw_story_units = 0
    world_state = _initial_world_state(input_payload)
    hidden_phrases = agent_visibility_guard.hidden_phrases(input_payload)
    registered_actor_targets = _registered_actor_targets(input_payload)
    gm_outputs: list[dict] = []
    actor_outputs: dict[str, list[dict]] = {}
    called_actors: list[str] = []
    generated_call_counts: dict[str, int] = {}
    used_actor_call_ids: set[str] = set()
    stop_reason = "continue"
    decision_point: Any = None
    side_thread_results: list[dict] = []
    initial_player_action_seen = _record_initial_player_action(
        card_for_versions,
        root,
        input_payload,
    )

    step_index = 0
    while step_limit is None or step_index < step_limit:
        player_participated_before_gm = initial_player_action_seen or _player_actor_participated(called_actors)
        world_state["raw_story_progress"] = {
            "current": raw_story_units,
            "target": _runtime_word_target(input_payload),
            "hard_stop_threshold": raw_story_limit,
            "unit": "words_or_chinese_characters",
            "hard_stop_rule": "stop when current exceeds hard_stop_threshold unless player_decision applies first",
        }
        _refresh_side_thread_state(root, world_state)
        _write_progress_safe(
            "gm_loop.gm_dispatch",
            "GM 正在推进剧情",
            percent=47,
            detail={"run_id": root.name, "step": step_index + 1},
        )
        raw_gm_payload = dispatch("gm", _gm_packet(root, world_state, step_index))
        raw_gm_output = _validate_gm(raw_gm_payload)
        _restore_actor_call_source_call_ids(raw_gm_output, raw_gm_payload)
        gm_output = agent_visibility_guard.sanitize_gm_output(raw_gm_output, input_payload)
        gm_output = _normalize_subgm_command_actor_ids(gm_output, input_payload)
        _prevalidate_subgm_commands(root, gm_output, input_payload)
        _preflight_subgm_actor_conflicts(root, gm_output, input_payload)
        _apply_character_promotions(root, input_payload, gm_output)
        registered_actor_targets = _registered_actor_targets(input_payload)
        gm_output = _filter_gm_actor_calls(gm_output, registered_actor_targets)
        gm_output = _ensure_initial_player_actor_call(
            gm_output,
            input_payload,
            player_participated_before_gm,
        )
        _normalize_main_actor_call_ids(gm_output, generated_call_counts, used_actor_call_ids)
        _preflight_subgm_actor_conflicts(root, gm_output, input_payload)
        _apply_subgm_commands(root, gm_output, input_payload)
        _assert_main_actor_calls_do_not_conflict(root, gm_output.get("actor_calls", []))
        _assert_complete_handles_existing_side_threads(root, gm_output)
        side_thread_results.extend(_run_ready_side_threads(root, dispatch))
        _refresh_side_thread_state(root, world_state)
        _assert_complete_leaves_no_active_side_threads(root, gm_output)
        gm_outputs.append(gm_output)
        raw_story_units += _gm_raw_story_units(gm_output)
        _apply_world_state_delta(world_state, gm_output)
        _record_gm_output(root, gm_output, step_index)
        _update_visible_events(root, world_state)

        gm_stop = str(gm_output.get("stop_reason") or "continue")
        gm_player_decision = player_decision_evidence.valid_gm_player_decision(
            gm_output,
            player_participated_before_gm=player_participated_before_gm,
        )
        gm_terminal_stop = gm_stop if gm_stop in STOP_REASONS and gm_stop != "player_decision" else ""

        max_parallel = agent_actor_batches.max_parallel_from_input(input_payload)
        pending_parallel_groups = gm_output.get("parallel_groups") or []
        batch_trace_index = 0
        actor_queue: Deque[dict] = deque(gm_output.get("actor_calls") or [])
        while actor_queue:
            queued_calls: list[dict] = []
            while actor_queue:
                call = actor_queue.popleft()
                actor_id = str(call.get("actor_id") or "")
                if actor_id in registered_actor_targets:
                    queued_calls.append(call)
            if not queued_calls:
                break

            active_parallel_groups = _list(pending_parallel_groups)
            batch_plan = agent_actor_batches.build_actor_batches(
                queued_calls,
                active_parallel_groups,
                max_parallel=max_parallel,
            )
            routing_warnings = batch_plan.get("warnings", [])
            pending_parallel_groups = []
            _record_routing_warnings(root, routing_warnings)
            batches = [batch for batch in batch_plan.get("batches", []) if isinstance(batch, dict)]

            for batch_index, batch in enumerate(batches):
                calls = [call for call in batch.get("calls", []) if isinstance(call, dict)]
                if not calls:
                    continue
                _record_actor_batch_plan(root, step_index, batch_trace_index, batch)
                batch_trace_index += 1
                _write_progress_safe(
                    "gm_loop.actor_batch",
                    "actor batch",
                    percent=48,
                    detail={
                        "run_id": root.name,
                        "batch_id": str(batch.get("batch_id") or f"step-{step_index + 1}-batch-{batch_index + 1}"),
                        "kind": str(batch.get("kind") or "serial"),
                        "actors": [str(call.get("actor_id") or "") for call in calls],
                    },
                )

                results: list[tuple[dict, str, dict, dict | None]] = []
                if str(batch.get("kind") or "") == "parallel" and len(calls) > 1:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as executor:
                        future_results = [
                            executor.submit(
                                _dispatch_actor_call,
                                run_dir=root,
                                card_folder=card_for_versions,
                                input_payload=input_payload,
                                world_state=world_state,
                                actor_id=str(call.get("actor_id") or ""),
                                call=call,
                                hidden_phrases=hidden_phrases,
                                dispatch=dispatch,
                            )
                            for call in calls
                        ]
                        for call, future in zip(calls, future_results):
                            actor_id = str(call.get("actor_id") or "")
                            actor_output, warning = future.result()
                            results.append((call, actor_id, actor_output, warning))
                else:
                    for call in calls:
                        actor_id = str(call.get("actor_id") or "")
                        actor_output, warning = _dispatch_actor_call(
                            run_dir=root,
                            card_folder=card_for_versions,
                            input_payload=input_payload,
                            world_state=world_state,
                            actor_id=actor_id,
                            call=call,
                            hidden_phrases=hidden_phrases,
                            dispatch=dispatch,
                        )
                        results.append((
                            call,
                            actor_id,
                            actor_output,
                            warning,
                        ))

                for _call, _actor_id, _actor_output, warning in results:
                    if warning:
                        agent_lifecycle.record_stale_actor_context_warning(
                            root,
                            str(warning.get("actor_id") or ""),
                            str(warning.get("returned_hash") or ""),
                            str(warning.get("current_hash") or ""),
                        )
                for call, actor_id, actor_output, _warning in results:
                    call_id = str(call.get("call_id") or "")
                    called_actors.append(actor_id)
                    actor_outputs.setdefault(actor_id, []).append(actor_output)
                    raw_story_units += _actor_raw_story_units(actor_output)
                    _process_actor_output(
                        run_dir=root,
                        actor_id=actor_id,
                        actor_output=actor_output,
                        call_id=call_id,
                    )

                _update_visible_events(root, world_state)
                if stop_reason in STOP_REASONS:
                    actor_queue.clear()
                    break

        if stop_reason in STOP_REASONS:
            break
        if gm_player_decision.get("valid"):
            decision_point = _mark_decision(
                root,
                gm_player_decision.get("decision_point"),
                str(gm_player_decision.get("label") or "The player must make the next decision."),
            )
            _write_progress_safe(
                "gm_loop.waiting_player_decision",
                "等待玩家决策",
                percent=60,
                detail={"reason": "gm_decision_point"},
            )
            stop_reason = "player_decision"
            break
        if raw_story_limit > 0 and raw_story_units > raw_story_limit:
            stop_reason = "word_target"
            break
        if gm_terminal_stop:
            stop_reason = gm_terminal_stop
            break
        step_index += 1

    if stop_reason == "continue" and step_limit is not None:
        stop_reason = "max_steps"

    _write_outputs(root, gm_outputs, actor_outputs)
    _write_progress_safe("gm_loop.completed", "剧情推演完成", percent=62, detail={"stop_reason": stop_reason})
    return {
        "ok": True,
        "gm_steps": len(gm_outputs),
        "called_actors": called_actors,
        "stop_reason": stop_reason,
        "decision_point": decision_point,
        "side_thread_results": side_thread_results,
    }


__all__ = ["AgentTurnLoopError", "run_gm_only_step", "run_interactive_loop"]
