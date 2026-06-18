"""Interactive GM-driven turn loop for Claude Code RP rounds."""

from __future__ import annotations

from collections import deque
import hashlib
import re
from pathlib import Path
from typing import Any, Callable, Deque, Iterable

import agent_interactions
import agent_projection
import agent_run
import agent_schemas


MAX_LOOP_STEPS = 8
GENERATED_TRANSFERS_PER_STEP = 4
STOP_REASONS = {"player_decision", "complete", "max_steps", "word_target"}

DispatchFn = Callable[[str, dict], dict]
HIDDEN_TEXT_KEYS = {
    "ai",
    "gm",
    "gm_only",
    "gm_only_text",
    "hidden",
    "hidden_text",
    "private",
    "private_notes",
    "world_truth",
}
HIDDEN_PHRASE_STRIP_CHARS = " \t\r\n.,:;!?。！？；，、："


class AgentTurnLoopError(RuntimeError):
    """Raised when the deterministic loop cannot validate or continue."""


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        texts = []
        for child in value.values():
            texts.extend(_string_leaves(child))
        return texts
    if isinstance(value, list):
        texts = []
        for child in value:
            texts.extend(_string_leaves(child))
        return texts
    return []


def _text_words(value: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", value.lower())


def _has_non_ascii_text(value: str) -> bool:
    return any(ord(char) > 127 and not char.isspace() for char in value)


def _clean_hidden_phrase(value: str) -> str:
    return str(value or "").strip(HIDDEN_PHRASE_STRIP_CHARS)


def _hidden_phrases_from_text(value: str) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    phrases = {text}
    for separator in (":", "："):
        if separator in text:
            phrases.add(text.split(separator, 1)[1].strip())

    words = _text_words(text)
    for size in range(4, min(8, len(words)) + 1):
        for index in range(0, len(words) - size + 1):
            phrases.add(" ".join(words[index:index + size]))

    kept = set()
    for phrase in phrases:
        clean = _clean_hidden_phrase(phrase)
        if len(clean) >= 12 or (_has_non_ascii_text(clean) and len(clean) >= 2):
            kept.add(clean)
    return kept


def _recent_chat_hidden_texts(input_payload: dict) -> list[str]:
    texts = []
    for item in _list(input_payload.get("recent_chat")):
        if isinstance(item, str):
            if "gm" in item.lower() or "hidden" in item.lower() or "private" in item.lower():
                texts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        hidden_visibility = str(item.get("visibility", "")).lower() in {"gm_only", "hidden", "private"}
        for key, value in item.items():
            if hidden_visibility or str(key).lower() in HIDDEN_TEXT_KEYS:
                texts.extend(_string_leaves(value))
    return texts


def _hidden_prompt_phrases(input_payload: dict) -> list[str]:
    routed = _dict(input_payload.get("routed_input"))
    hidden_sources = []
    hidden_sources.extend(_string_leaves(routed.get("user_instruction_channel")))
    hidden_sources.extend(_string_leaves(input_payload.get("user_instruction_channel")))
    hidden_sources.extend(_string_leaves(input_payload.get("gm_only_hidden_settings")))
    hidden_sources.extend(_string_leaves(input_payload.get("hidden_facts")))
    hidden_sources.extend(_string_leaves(input_payload.get("world_truth")))
    hidden_sources.extend(_string_leaves(input_payload.get("gm_only_recent_chat")))
    hidden_sources.extend(_string_leaves(input_payload.get("hidden_recent_chat")))
    hidden_sources.extend(_string_leaves(input_payload.get("private_recent_chat")))
    hidden_sources.extend(_recent_chat_hidden_texts(input_payload))

    phrases = set()
    for text in hidden_sources:
        phrases.update(_hidden_phrases_from_text(text))
    return sorted(phrases, key=lambda phrase: (-len(phrase), phrase))


def _redact_hidden_prompt_text(prompt: str, hidden_phrases: Iterable[str]) -> str:
    redacted = str(prompt or "")
    for phrase in hidden_phrases:
        pattern = re.compile(re.escape(str(phrase)), re.IGNORECASE)
        redacted = pattern.sub("[redacted]", redacted)
    return redacted


def _read_input(run_dir: Path) -> dict:
    data = agent_run.read_json(run_dir / "input.json", {})
    return data if isinstance(data, dict) else {}


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
        result[f"character:{name}"] = item
        result[f"character:{agent_run.safe_name(name)}"] = item

    for key, value in contexts.items():
        if key == "characters" or not isinstance(value, dict):
            continue
        actor_id = str(key)
        if not actor_id.startswith("character:"):
            actor_id = f"character:{actor_id}"
        result[actor_id] = value
    return result


def _registered_actor_targets(input_payload: dict) -> set[str]:
    return {"player", * _characters_by_actor_id(input_payload).keys()}


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
    world_state["user_instruction_channel"] = routed.get(
        "user_instruction_channel",
        input_payload.get("user_instruction_channel", ""),
    )
    world_state.setdefault("recent_chat", input_payload.get("recent_chat", []))
    world_state.setdefault("gm_only_hidden_settings", input_payload.get("gm_only_hidden_settings", []))
    world_state.setdefault("visible_events", [])
    world_state.setdefault("pending_perception_requests", [])
    if not isinstance(world_state.get("world_state_delta"), list):
        world_state["world_state_delta"] = []
    return world_state


def _ensure_trace(run_dir: Path, input_payload: dict) -> None:
    if not (run_dir / "interaction.trace.json").exists():
        agent_interactions.init_trace(run_dir, participants=_participants(input_payload), chapter_target_words=0)


def _validate_gm(payload: Any) -> dict:
    try:
        return agent_schemas.validate_gm_output(payload)
    except agent_schemas.ValidationError as exc:
        raise AgentTurnLoopError(f"invalid gm output: {exc}") from exc


def _validate_actor(actor_id: str, payload: Any) -> dict:
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


def _record_gm_output(run_dir: Path, gm_output: dict, step_index: int) -> None:
    for beat in gm_output.get("scene_beats", []):
        content = _event_content(beat)
        if content:
            agent_interactions.append_event(run_dir, "gm", "world_visible", "scene_beat", content)

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
    event_type = str(event.get("type") or "")
    if event_type in {"dialogue", "action"}:
        visibility = "world_visible"
    elif event_type == "perceive_request":
        visibility = "gm_visible"
    else:
        visibility = "actor_visible"
    agent_interactions.append_event(
        run_dir,
        actor=actor_id,
        visibility=visibility,
        event_type=event_type,
        content=_event_content(event),
        target=str(event.get("target") or ""),
        source_call_id=source_call_id,
    )


def _safe_actor_call_id(actor_id: str, counts: dict[str, int]) -> str:
    counts[actor_id] = counts.get(actor_id, 0) + 1
    if actor_id == "player":
        return f"call-player-{counts[actor_id]}"
    safe = _ascii_actor_slug(actor_id)
    return f"call-character-{safe}-{counts[actor_id]}"


def _ascii_actor_slug(actor_id: str) -> str:
    raw = str(actor_id).split(":", 1)[-1]
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    if not slug or not slug[0].isalpha():
        digest = hashlib.sha1(str(actor_id).encode("utf-8")).hexdigest()[:8]
        slug = f"Actor_{digest}"
    return slug


def _dialogue_transfer_call(
    actor_id: str,
    target: str,
    event: dict,
    call_id: str,
    generated_call_counts: dict[str, int],
) -> dict:
    content = _event_content(event)
    return {
        "call_id": _safe_actor_call_id(target, generated_call_counts),
        "actor_id": target,
        "prompt": f"{actor_id} says to you: {content}",
        "reason": "Visible dialogue transfer.",
        "source_call_id": call_id,
    }


def _record_dialogue_transfer(
    run_dir: Path,
    actor_id: str,
    target: str,
    content: str,
    source_call_id: str,
) -> None:
    agent_interactions.append_event(
        run_dir,
        actor="gm",
        visibility="world_visible",
        event_type="dialogue_transfer",
        content=content,
        target=target,
        source_call_id=source_call_id,
    )


def _record_perception_continuation(
    run_dir: Path,
    actor_id: str,
    event: dict,
    source_call_id: str,
    world_state: dict,
) -> None:
    request = {
        "actor_id": actor_id,
        "target": str(event.get("target") or ""),
        "content": _event_content(event),
        "source_call_id": source_call_id,
    }
    world_state.setdefault("pending_perception_requests", []).append(request)
    agent_interactions.append_event(
        run_dir,
        actor="gm",
        visibility="gm_visible",
        event_type="perception_continuation",
        content=f"GM should answer {actor_id}'s perception request: {request['content']}",
        target=actor_id,
        source_call_id=source_call_id,
    )


def _update_visible_events(run_dir: Path, world_state: dict) -> None:
    world_state["visible_events"] = agent_interactions.summarize_for_story_input(run_dir)["visible_events"]


def _gm_packet(run_dir: Path, world_state: dict, step_index: int) -> dict:
    pending_perception_requests = list(world_state.get("pending_perception_requests") or [])
    world_state["pending_perception_requests"] = []
    packet_world_state = dict(world_state)
    packet_world_state["pending_perception_requests"] = pending_perception_requests
    return {
        "step": step_index,
        "world_state": packet_world_state,
        "trace_summary": agent_interactions.summarize_for_story_input(run_dir),
        "pending_perception_requests": pending_perception_requests,
    }


def _actor_packet(
    input_payload: dict,
    world_state: dict,
    actor_id: str,
    prompt: str,
    hidden_phrases: Iterable[str],
) -> dict:
    safe_prompt = _redact_hidden_prompt_text(prompt, hidden_phrases)
    return agent_projection.project_actor_context(
        actor_id,
        world_state,
        _actor_state(actor_id, input_payload),
        safe_prompt,
    )


def _dispatch_actor_key(actor_id: str) -> str:
    return "player" if actor_id == "player" else actor_id


def _write_outputs(run_dir: Path, gm_outputs: list[dict], actor_outputs: dict[str, list[dict]]) -> None:
    agent_run.write_json(run_dir / "gm.output.json", {"agent": "gm_loop", "outputs": gm_outputs})
    agent_run.write_json(run_dir / "actor.outputs.json", actor_outputs)


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


def _apply_world_state_delta(world_state: dict, gm_output: dict) -> None:
    delta = gm_output.get("world_state_delta", [])
    if not isinstance(delta, list) or not delta:
        return
    accumulated = world_state.setdefault("world_state_delta", [])
    if not isinstance(accumulated, list):
        accumulated = []
        world_state["world_state_delta"] = accumulated
    accumulated.extend(delta)


def run_interactive_loop(
    run_dir: str | Path,
    dispatch: DispatchFn,
    *,
    max_steps: int = MAX_LOOP_STEPS,
) -> dict:
    """Run a bounded deterministic GM/actor control loop through `dispatch`."""
    root = Path(run_dir)
    input_payload = _read_input(root)
    _ensure_trace(root, input_payload)

    step_limit = max(1, int(max_steps or 0))
    generated_transfer_limit = step_limit * GENERATED_TRANSFERS_PER_STEP
    world_state = _initial_world_state(input_payload)
    hidden_phrases = _hidden_prompt_phrases(input_payload)
    registered_actor_targets = _registered_actor_targets(input_payload)
    gm_outputs: list[dict] = []
    actor_outputs: dict[str, list[dict]] = {}
    called_actors: list[str] = []
    generated_call_counts: dict[str, int] = {}
    seen_transfers: set[tuple[str, str, str]] = set()
    stop_reason = "continue"
    decision_point: Any = None
    generated_transfers_used = 0

    for step_index in range(step_limit):
        gm_output = _validate_gm(dispatch("gm", _gm_packet(root, world_state, step_index)))
        gm_outputs.append(gm_output)
        _apply_world_state_delta(world_state, gm_output)
        _record_gm_output(root, gm_output, step_index)
        _update_visible_events(root, world_state)

        gm_stop = str(gm_output.get("stop_reason") or "continue")
        gm_has_decision = gm_output.get("decision_point") is not None or gm_stop == "player_decision"
        gm_terminal_stop = gm_stop if gm_stop in STOP_REASONS else ""

        actor_queue: Deque[dict] = deque(gm_output.get("actor_calls") or [])
        while actor_queue:
            call = actor_queue.popleft()
            actor_id = str(call.get("actor_id") or "")
            if actor_id not in registered_actor_targets:
                continue
            call_id = str(call.get("call_id") or "") or _safe_actor_call_id(actor_id, generated_call_counts)
            packet = _actor_packet(
                input_payload,
                world_state,
                actor_id,
                str(call.get("prompt") or ""),
                hidden_phrases,
            )
            actor_output = _validate_actor(actor_id, dispatch(_dispatch_actor_key(actor_id), packet))
            called_actors.append(actor_id)
            actor_outputs.setdefault(actor_id, []).append(actor_output)

            transfer_calls = []
            actor_requested_decision = False
            for event in actor_output.get("events", []):
                _record_actor_event(root, actor_id, event, call_id)
                event_type = str(event.get("type") or "")
                target = str(event.get("target") or "")
                content = _event_content(event)

                if (
                    event_type == "dialogue"
                    and target in registered_actor_targets
                    and target != actor_id
                ):
                    _record_dialogue_transfer(root, actor_id, target, content, call_id)
                    transfer_key = (actor_id, target, content)
                    if transfer_key not in seen_transfers:
                        seen_transfers.add(transfer_key)
                        if generated_transfers_used < generated_transfer_limit:
                            generated_transfers_used += 1
                            transfer_calls.append(
                                _dialogue_transfer_call(
                                    actor_id,
                                    target,
                                    event,
                                    call_id,
                                    generated_call_counts,
                                )
                            )
                        else:
                            stop_reason = "max_steps"
                elif event_type == "perceive_request":
                    _record_perception_continuation(root, actor_id, event, call_id, world_state)
                elif event_type == "stop_for_player_decision":
                    actor_requested_decision = True

            _update_visible_events(root, world_state)
            for transfer_call in reversed(transfer_calls):
                actor_queue.appendleft(transfer_call)

            if actor_output.get("stop_reason") == "stop_for_player_decision" or actor_requested_decision:
                decision_point = _mark_decision(root, None, "Actor requested a real player decision.")
                stop_reason = "player_decision"
                actor_queue.clear()
                break

        if stop_reason in STOP_REASONS:
            break
        if gm_has_decision:
            decision_point = _mark_decision(
                root,
                gm_output.get("decision_point"),
                "The player must make the next decision.",
            )
            stop_reason = "player_decision"
            break
        if gm_terminal_stop:
            stop_reason = gm_terminal_stop
            break

    if stop_reason == "continue":
        stop_reason = "max_steps"

    _write_outputs(root, gm_outputs, actor_outputs)
    return {
        "ok": True,
        "gm_steps": len(gm_outputs),
        "called_actors": called_actors,
        "stop_reason": stop_reason,
        "decision_point": decision_point,
    }


__all__ = ["AgentTurnLoopError", "run_interactive_loop"]
