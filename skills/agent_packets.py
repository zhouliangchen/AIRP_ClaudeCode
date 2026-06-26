"""Utilities for routing player input and building per-agent context packets."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable

import agent_projection
import agent_run
import agent_prompts
import agent_memory
import agent_lifecycle
import agent_messages
import actor_memory_store
import objective_world
import input_analysis
import postprocess_outputs
import runtime_settings


def _to_text(value: Any) -> str:
    return "" if value is None else str(value)


RUNTIME_CONTRACT_TAGS = (
    "character_dialogues",
    "derived_content_edits",
    "edit_only",
    "summary",
    "options",
    "tokens",
    "polished_input",
)


def _strip_runtime_contract_tags(value: Any) -> str:
    text = _to_text(value)
    for tag in RUNTIME_CONTRACT_TAGS:
        text = re.sub(rf"<{tag}>.*?</{tag}>", "", text, flags=re.DOTALL)
    return text.strip()


def _sanitize_recent_chat_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, child in value.items():
            if str(key) == "tokens":
                continue
            sanitized[str(key)] = _sanitize_recent_chat_value(child)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_recent_chat_value(item) for item in value]
    if isinstance(value, str):
        return _strip_runtime_contract_tags(value)
    return value


def _sanitize_recent_chat_for_packets(chat_log: Any) -> list[Any]:
    if not isinstance(chat_log, list):
        return []
    return [_sanitize_recent_chat_value(item) for item in chat_log]


def _clean_text_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            result.append(text)
    return result


def _append_required_message(run_dir: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    for message in agent_messages.read_messages(run_dir):
        if _is_equivalent_delivered_message(message, payload):
            return message

    result = agent_messages.append_message(run_dir, payload)
    if isinstance(result, dict) and result.get("ok") is True:
        message = result.get("message")
        return message if isinstance(message, dict) else {}

    message_type = _to_text(payload.get("type")).strip() or "<unknown>"
    reason = "invalid_result"
    error = ""
    if isinstance(result, dict):
        reason = _to_text(result.get("reason")).strip() or reason
        error = _to_text(result.get("error")).strip()
    detail = f": {error}" if error else ""
    raise RuntimeError(f"required message append failed for {message_type}: {reason}{detail}")


def _is_equivalent_delivered_message(message: Any, payload: Dict[str, Any]) -> bool:
    if not isinstance(message, dict):
        return False
    if message.get("status") != "delivered":
        return False
    for key in ("from", "to", "type", "visibility", "payload"):
        if message.get(key) != payload.get(key):
            return False
    return True


def _clip_text(value: Any, limit: int) -> str:
    text = _to_text(value).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _first_text(*values: Any) -> str:
    for value in values:
        text = _to_text(value).strip()
        if text:
            return text
    return ""


def compact_card_data(card_data: Any) -> Dict[str, Any]:
    """Project large SillyTavern card data into a bounded per-round context."""
    if not isinstance(card_data, dict):
        return {}

    nested = card_data.get("data")
    if not isinstance(nested, dict):
        nested = {}

    extensions = {}
    for source in (nested.get("extensions"), card_data.get("extensions")):
        if isinstance(source, dict):
            extensions.update(source)

    tags = card_data.get("tags") or nested.get("tags") or []
    if not isinstance(tags, list):
        tags = []

    return {
        "projection": "compacted_card_data_v1",
        "name": _first_text(card_data.get("name"), nested.get("name"), card_data.get("title")),
        "world": _first_text(extensions.get("world"), card_data.get("world"), nested.get("world")),
        "description": _clip_text(_first_text(card_data.get("description"), nested.get("description")), 4000),
        "personality": _clip_text(_first_text(card_data.get("personality"), nested.get("personality")), 2000),
        "scenario": _clip_text(_first_text(card_data.get("scenario"), nested.get("scenario")), 3000),
        "first_mes": _clip_text(_first_text(card_data.get("first_mes"), nested.get("first_mes")), 5000),
        "creator_notes": _clip_text(
            _first_text(card_data.get("creatorcomment"), nested.get("creator_notes"), card_data.get("creator_notes")),
            1500,
        ),
        "tags": tags[:20],
    }


def route_player_input(text: str) -> Dict[str, Any]:
    """Preserve a single-channel raw player input without semantic parsing."""
    raw = _to_text(text).strip()
    if not raw:
        return {
            "role_channel": "",
            "user_instruction_channel": "",
            "components": [],
        }

    return {
        "role_channel": raw,
        "user_instruction_channel": "",
        "components": [{"channel": "role", "text": raw}],
    }


def route_input_payload(user_text: str, input_payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Route only explicit dual-channel payloads; otherwise preserve raw input."""
    payload = input_payload if isinstance(input_payload, dict) else {}
    if payload.get("input_schema") == "dual_channel_v1":
        role = _to_text(payload.get("role_text"))
        instruction = _to_text(payload.get("user_instruction_text"))
        components = []
        if role:
            components.append({"channel": "role", "text": role})
        if instruction:
            components.append({"channel": "user_instruction", "text": instruction})
        return {
            "role_channel": role,
            "user_instruction_channel": instruction,
            "components": components,
            "input_schema": "dual_channel_v1",
        }

    routed = route_player_input(user_text)
    routed["input_schema"] = "raw_single_channel_v1"
    return routed


def _input_analysis_explicit_payload(input_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return only explicit channel metadata needed by the input analyst.

    The top-level request already carries raw_text, role_text, and
    user_instruction_text with hashes.  Repeating raw/display text inside the
    explicit payload bloats the model prompt and expands the injection surface.
    """
    if input_payload.get("input_schema") != "dual_channel_v1":
        return {}
    payload = {
        "input_schema": "dual_channel_v1",
        "role_text": _to_text(input_payload.get("role_text")),
        "user_instruction_text": _to_text(input_payload.get("user_instruction_text")),
    }
    for key in ("id", "created_at", "source"):
        if key in input_payload:
            payload[key] = _to_text(input_payload.get(key))
    return payload


def _actor_visible_role_channel(routed_input: Dict[str, Any]) -> str:
    """Return actor-visible role text only after explicit or analysis-applied routing."""
    schema = _to_text(routed_input.get("input_schema")).strip()
    if schema in {"dual_channel_v1", "analysis_v1"}:
        return _to_text(routed_input.get("role_channel")).strip()
    return ""


def _actor_visible_world_state(world_state: Dict[str, Any], routed_input: Dict[str, Any]) -> Dict[str, Any]:
    if _actor_visible_role_channel(routed_input):
        return world_state
    world = dict(world_state)
    world["role_channel"] = ""
    return world


def _filter_role_components(routed_input: Dict[str, Any]) -> list[Dict[str, str]]:
    return [item for item in routed_input.get("components", []) if item.get("channel") == "role"]


def _build_world_state(
    routed_input: Dict[str, Any],
    recent_chat,
    *,
    hidden_setting_records=None,
    visible_events=None,
    card_data=None,
    character_contexts=None,
) -> Dict[str, Any]:
    events = []
    if isinstance(visible_events, list):
        events.extend(visible_events)
    return {
        "role_channel": _to_text(routed_input.get("role_channel")),
        "user_instruction_channel": _to_text(routed_input.get("user_instruction_channel")),
        "recent_chat": _sanitize_recent_chat_for_packets(recent_chat),
        "gm_only_hidden_settings": hidden_setting_records or [],
        "visible_events": events,
        "card_data": compact_card_data(card_data),
        "character_contexts": character_contexts or {},
        "components": routed_input.get("components", []),
    }


STRUCTURED_MEMORY_KEYS = ("long_term", "key_memories", "short_term", "goals")


def _empty_structured_memory() -> Dict[str, list[Any]]:
    return {key: [] for key in STRUCTURED_MEMORY_KEYS}


def _player_actor_state(card_folder=None) -> Dict[str, Any]:
    return {
        "name": "player",
        "card_folder": str(card_folder) if card_folder is not None else "",
        "memory": _load_actor_memory(card_folder, "player"),
    }


def _character_actor_id(character: Dict[str, Any]) -> str:
    name = _to_text(character.get("name") or character.get("character_name")).strip()
    if not name:
        return "character:unknown"
    return actor_memory_store.canonical_actor_id(f"character:{name}")


def _safe_context_name_from_actor_id(actor_id: Any) -> str:
    text = _to_text(actor_id)
    if text.startswith("character:"):
        return agent_run.safe_name(text.split(":", 1)[1] or "_unknown")
    return agent_run.safe_name(text or "_unknown")


def _append_unique(items: list[Any], value: Any) -> None:
    if value not in items:
        items.append(value)


def _append_structured_value(memory: Dict[str, list[Any]], key: str, value: Any) -> None:
    if key not in memory:
        return
    for item in _as_memory_items(value):
        _append_unique(memory[key], item)


def _append_goals_json(memory: Dict[str, list[Any]], value: Any) -> None:
    data = value if isinstance(value, dict) else {}
    goals = data.get("goals", data)
    if isinstance(goals, dict):
        for status in ("active", "paused", "resolved"):
            for item in _as_memory_items(goals.get(status)):
                if isinstance(item, dict):
                    entry = dict(item)
                    entry.setdefault("status", status)
                else:
                    entry = {"status": status, "content": item}
                _append_unique(memory["goals"], entry)
        return
    _append_structured_value(memory, "goals", goals)


def _load_actor_memory(card_folder: Any, actor_id: str) -> Dict[str, list[Any]]:
    memory = _empty_structured_memory()
    if card_folder is None:
        return memory

    stored = actor_memory_store.read_actor_memory(card_folder, actor_id)
    _append_structured_value(memory, "long_term", stored.get("long_term"))
    _append_structured_value(
        memory,
        "key_memories",
        [
            {"tag": item.get("tag", ""), "summary": item.get("summary", "")}
            for item in _as_memory_items(stored.get("key_memories"))
            if isinstance(item, dict)
        ],
    )
    _append_structured_value(memory, "short_term", stored.get("short_term"))
    return memory


def _merge_memory(target: Dict[str, list[Any]], source: Any) -> None:
    if not isinstance(source, dict):
        return
    _append_structured_value(target, "long_term", source.get("long_term"))
    _append_structured_value(target, "key_memories", source.get("key_memories"))
    _append_structured_value(target, "short_term", source.get("short_term"))
    _append_goals_json(target, {"goals": source.get("goals")})


def _as_memory_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    text = _to_text(value).strip()
    return [text] if text else []


def _append_memory_value(memory: Dict[str, Any], key: str, value: Any) -> None:
    items = _as_memory_items(memory.get(key))
    for item in _as_memory_items(value):
        if item not in items:
            items.append(item)
    if items:
        memory[key] = items


def _projectable_character_state(character: Dict[str, Any], card_folder=None) -> Dict[str, Any]:
    state = dict(character or {})
    state["card_folder"] = str(card_folder) if card_folder is not None else ""
    memory = _load_actor_memory(card_folder, _character_actor_id(state))
    _merge_memory(memory, state.get("memory"))

    state["memory"] = memory
    return state


def _projectable_player_state(card_folder, actor_state: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if isinstance(actor_state, dict):
        state = dict(actor_state)
        state["card_folder"] = str(card_folder) if card_folder is not None else ""
        memory = _load_actor_memory(card_folder, "player")
        _merge_memory(memory, state.get("memory"))
        _append_goals_json(memory, {"goals": state.get("goals")})
        state["memory"] = memory
        state.setdefault("name", "player")
        return state
    return _player_actor_state(card_folder)


def _actor_prompt_from_role(routed_input: Dict[str, Any], *, for_character: bool = False) -> str:
    role_channel = _actor_visible_role_channel(routed_input)
    if not role_channel:
        return "Use only your projected first-person context for this turn."
    if for_character:
        return "React only to visible events and your own memory for this turn."
    return f"Current first-person role-channel anchor: {role_channel}"


def build_gm_packet(
    card_folder,
    routed_input: Dict[str, Any],
    recent_chat,
    card_data=None,
    character_contexts=None,
    hidden_setting_records=None,
    runtime_settings_payload=None,
    objective_world_payload=None,
):
    """Build GM packet with both role and instruction channels."""
    runtime_payload = runtime_settings.normalize_prompt_payload(runtime_settings_payload)
    objective_payload = (
        objective_world_payload
        if isinstance(objective_world_payload, dict)
        else objective_world.read_objective_world(card_folder)
    )
    return {
        "agent": "gm",
        "card_folder": str(card_folder),
        "role_channel": _to_text(routed_input.get("role_channel")),
        "user_instruction_channel": _to_text(routed_input.get("user_instruction_channel")),
        "gm_only_hidden_settings": hidden_setting_records or [],
        "objective_world": objective_payload,
        "recent_chat": _sanitize_recent_chat_for_packets(recent_chat),
        "card_data": compact_card_data(card_data),
        "character_contexts": character_contexts or [],
        "components": routed_input.get("components", []),
        "runtime_settings": runtime_payload["settings"],
        "style_profile": runtime_payload["style_profile"],
    }


def build_player_packet(
    card_folder,
    routed_input: Dict[str, Any],
    recent_chat,
    world_state: Dict[str, Any] | None = None,
    actor_state: Dict[str, Any] | None = None,
    gm_prompt: str | None = None,
):
    """Build first-person packet for player agent."""
    world = world_state or _build_world_state(routed_input, recent_chat)
    world = _actor_visible_world_state(world, routed_input)
    actor = _projectable_player_state(card_folder, actor_state)
    prompt = gm_prompt if gm_prompt is not None else _actor_prompt_from_role(routed_input)
    packet = agent_projection.project_actor_context("player", world, actor, prompt)
    return agent_lifecycle.attach_actor_context_version(card_folder, "player", packet)


def build_character_packet(
    card_folder,
    character: Dict[str, Any],
    routed_input: Dict[str, Any],
    recent_chat,
    world_state: Dict[str, Any] | None = None,
    gm_prompt: str | None = None,
):
    """Build first-person packet for a character subagent."""
    character_data = character or {}
    world = world_state or _build_world_state(routed_input, recent_chat)
    world = _actor_visible_world_state(world, routed_input)
    actor_state = _projectable_character_state(character_data, card_folder)
    prompt = gm_prompt if gm_prompt is not None else _actor_prompt_from_role(routed_input, for_character=True)
    actor_id = _character_actor_id(actor_state)
    packet = agent_projection.project_actor_context(
        actor_id,
        world,
        actor_state,
        prompt,
    )
    return agent_lifecycle.attach_actor_context_version(card_folder, actor_id, packet)


def _iter_characters(character_contexts: Any) -> Iterable[Dict[str, Any]]:
    if not character_contexts:
        return []
    if isinstance(character_contexts, dict):
        if "characters" in character_contexts and isinstance(character_contexts["characters"], (list, tuple)):
            return character_contexts["characters"]
    if isinstance(character_contexts, (list, tuple)):
        result = []
        for item in character_contexts:
            if isinstance(item, str):
                result.append({"name": item})
            elif isinstance(item, dict):
                result.append(item)
        return result
    return []


def build_character_contexts_from_card(card_folder, card_data, chat_log, user_text):
    """Build character contexts through round_prepare, falling back conservatively."""
    try:
        import round_prepare

        card_structure = agent_run.read_json(
            Path(card_folder) / "memory" / ".card_structure.json",
            {},
        )
        contexts = round_prepare.build_character_contexts(
            card_folder,
            card_data if isinstance(card_data, dict) else {},
            card_structure or {},
            chat_log or [],
            _to_text(user_text),
        )
        if isinstance(contexts, dict):
            return contexts
    except Exception:
        pass
    return {"characters": []}


DEFAULT_CRITIC_REPORT = {
    "passed": True,
    "hard_failures": [],
    "soft_issues": [],
    "repair_instruction": "",
    "system_iteration_suggestion": "",
    "source": "default-pre-critic",
}


def build_input_analysis_request(run_dir, user_text, input_payload, chat_log, card_data):
    """Build the immutable raw-input request for the input analyst subagent."""
    source_payload = dict(input_payload) if isinstance(input_payload, dict) else {}
    safe_chat_log = _sanitize_recent_chat_for_packets(chat_log)
    if source_payload.get("input_schema") == "dual_channel_v1":
        raw_text = _to_text(source_payload.get("raw_text"))
        role_text = _to_text(source_payload.get("role_text"))
        user_instruction_text = _to_text(source_payload.get("user_instruction_text"))
        explicit_payload = _input_analysis_explicit_payload(source_payload)
    else:
        routed = route_input_payload(user_text, None)
        raw_text = _to_text(user_text)
        role_text = _to_text(routed.get("role_channel"))
        user_instruction_text = _to_text(routed.get("user_instruction_channel"))
        explicit_payload = {}

    return {
        "round_id": Path(run_dir).name,
        "raw_text": raw_text,
        "explicit_payload": explicit_payload,
        "role_text": role_text,
        "user_instruction_text": user_instruction_text,
        "source_integrity": {
            "raw_text_sha256": input_analysis.sha256_text(raw_text),
            "role_text_sha256": input_analysis.sha256_text(role_text),
            "user_instruction_text_sha256": input_analysis.sha256_text(user_instruction_text),
            "raw_preserved": True,
        },
        "recent_chat": safe_chat_log,
        "card_projection": compact_card_data(card_data),
    }


def _input_raw_record(input_request: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "round_id": input_request.get("round_id", ""),
        "raw_text": input_request.get("raw_text", ""),
        "explicit_payload": input_request.get("explicit_payload", {}),
        "role_text": input_request.get("role_text", ""),
        "user_instruction_text": input_request.get("user_instruction_text", ""),
        "source_integrity": input_request.get("source_integrity", {}),
    }


def _input_analysis_model_request(input_request: Dict[str, Any]) -> Dict[str, Any]:
    """Return the model-facing input request without duplicating raw channels.

    The complete player payload is preserved in input.raw.json. The input
    analyst prompt only needs the separated role/instruction channels plus
    integrity hashes, which avoids re-injecting the same user text through
    raw_text and explicit_payload copies.
    """
    explicit_payload = input_request.get("explicit_payload")
    if not isinstance(explicit_payload, dict):
        explicit_payload = {}
    request = {
        "round_id": input_request.get("round_id", ""),
        "input_schema": explicit_payload.get("input_schema", ""),
        "role_text": input_request.get("role_text", ""),
        "user_instruction_text": input_request.get("user_instruction_text", ""),
        "source_integrity": input_request.get("source_integrity", {}),
        "recent_chat": input_request.get("recent_chat", []),
        "card_projection": input_request.get("card_projection", {}),
    }
    return {key: value for key, value in request.items() if value not in ("", None)}


def _input_analysis_request_markdown(input_request: Dict[str, Any]) -> str:
    return (
        "# Input Analysis Request\n\n"
        "Classify the preserved player input into `input_analysis.output.json`.\n\n"
        "```json\n"
        f"{json.dumps(input_request, ensure_ascii=False, indent=2)}\n"
        "```\n"
    )


def _input_analysis_request_reference(input_request: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "round_id": input_request.get("round_id", ""),
        "request_path": "input_analysis.request.md",
        "raw_path": "input.raw.json",
        "output_path": "input_analysis.output.json",
        "source_integrity": input_request.get("source_integrity", {}),
    }


def prepare_agent_run(
    card_folder,
    user_text,
    chat_log,
    card_data,
    character_contexts,
    turn_index=None,
    input_payload=None,
    hidden_setting_records=None,
    runtime_settings_payload=None,
):
    """Create one round run directory and persist agent packets."""
    routed_input = route_input_payload(user_text, input_payload)
    run_dir = agent_run.create_run_dir(card_folder, turn_index=turn_index)
    hidden_setting_records = hidden_setting_records or []
    runtime_payload = runtime_settings.normalize_prompt_payload(runtime_settings_payload)
    safe_chat_log = _sanitize_recent_chat_for_packets(chat_log)
    input_request = build_input_analysis_request(run_dir, user_text, input_payload, safe_chat_log, card_data)
    world_state = _build_world_state(
        routed_input,
        safe_chat_log,
        hidden_setting_records=hidden_setting_records,
        card_data=card_data,
        character_contexts=character_contexts,
    )
    model_input_request = _input_analysis_model_request(input_request)
    objective_payload = objective_world.read_objective_world(card_folder)
    agent_run.write_json(run_dir / "input.raw.json", _input_raw_record(input_request))
    agent_run.write_text(run_dir / "input_analysis.request.md", _input_analysis_request_markdown(model_input_request))

    input_json = input_payload if isinstance(input_payload, dict) else {"raw_text": _to_text(user_text)}
    input_json = dict(input_json)
    input_json["raw_text"] = _to_text(input_json.get("raw_text", user_text))
    input_json["routed_input"] = routed_input
    input_json["gm_only_hidden_settings"] = hidden_setting_records
    input_json["objective_world"] = objective_payload
    input_json["recent_chat"] = safe_chat_log
    input_json["card_data"] = compact_card_data(card_data)
    input_json["character_contexts"] = character_contexts or {}
    input_json["visible_events"] = world_state["visible_events"]
    input_json["runtime_settings"] = runtime_payload["settings"]
    input_json["style_profile"] = runtime_payload["style_profile"]
    input_json["postprocess_repairs"] = postprocess_outputs.read_pending_repairs(card_folder)
    degraded_memory_state = agent_memory.previous_post_round_memory_state(card_folder)
    if degraded_memory_state:
        input_json["degraded_memory_state"] = degraded_memory_state
    agent_run.write_json(run_dir / "input.json", input_json)
    source_integrity = input_request.get("source_integrity") if isinstance(input_request, dict) else {}
    raw_text_hash = source_integrity.get("raw_text_sha256") if isinstance(source_integrity, dict) else None
    input_received_payload = {
        "input_path": "input.json",
        "raw_path": "input.raw.json",
    }
    if raw_text_hash:
        input_received_payload["raw_text_hash"] = raw_text_hash
    _append_required_message(
        run_dir,
        {
            "from": "main_agent",
            "to": ["gm", "input_analyst"],
            "type": "input_received",
            "visibility": "gm_only",
            "payload": input_received_payload,
        },
    )
    _append_required_message(
        run_dir,
        {
            "from": "main_agent",
            "to": ["input_analyst"],
            "type": "analysis_requested",
            "visibility": "gm_only",
            "payload": {
                "request_path": "input_analysis.request.md",
                "output_path": "input_analysis.output.json",
            },
        },
    )

    gm_packet = build_gm_packet(
        card_folder,
        routed_input,
        safe_chat_log,
        card_data,
        character_contexts,
        hidden_setting_records=hidden_setting_records,
        runtime_settings_payload=runtime_payload,
        objective_world_payload=objective_payload,
    )
    gm_packet["input_analysis_request"] = _input_analysis_request_reference(input_request)
    player_packet = build_player_packet(card_folder, routed_input, safe_chat_log, world_state=world_state)
    agent_run.write_json(run_dir / "gm.context.json", gm_packet)
    agent_run.write_json(run_dir / "player.context.json", player_packet)

    character_packets = {}
    for character in _iter_characters(character_contexts):
        packet = build_character_packet(card_folder, character, routed_input, safe_chat_log, world_state=world_state)
        safe = _safe_context_name_from_actor_id(packet.get("actor_id"))
        agent_run.write_json(run_dir / "characters" / f"{safe}.context.json", packet)
        character_packets[safe] = packet

    agent_run.write_json(run_dir / "critic.report.json", DEFAULT_CRITIC_REPORT)
    manifest = agent_prompts.write_round_prompts(
        run_dir,
        gm_packet,
        player_packet,
        character_packets,
        card_folder=card_folder,
        input_analysis_request=model_input_request,
        runtime_settings_payload=runtime_payload,
    )
    return {
        "run_dir": str(run_dir.resolve()),
        "routed_input": routed_input,
        "gm_packet": gm_packet,
        "player_packet": player_packet,
        "manifest": manifest,
    }


def _clear_generated_character_files(run_dir: Path) -> None:
    for pattern in (
        "characters/*.context.json",
        "prompts/characters/*.prompt.md",
    ):
        for path in run_dir.glob(pattern):
            try:
                path.unlink()
            except OSError:
                pass


def rebuild_agent_run_from_analysis(
    card_folder,
    run_dir,
    analysis: Dict[str, Any],
    routed_input: Dict[str, Any],
    raw_request: Dict[str, Any],
    *,
    chat_log=None,
    card_data=None,
    character_contexts=None,
    hidden_setting_records=None,
    runtime_settings_payload=None,
):
    """Rewrite final agent packets/prompts after input analysis has been applied."""
    root = Path(run_dir)
    chat_log = _sanitize_recent_chat_for_packets(chat_log)
    hidden_setting_records = hidden_setting_records or []
    card_data = card_data if isinstance(card_data, dict) else {}
    character_contexts = character_contexts or {"characters": []}
    if runtime_settings_payload is None:
        previous_manifest = agent_run.read_json(root / "manifest.json", {}) or {}
        runtime_settings_payload = {
            "settings": previous_manifest.get("runtime_settings", {}),
            "style_profile": previous_manifest.get("style_profile", {}),
        }
    runtime_payload = runtime_settings.normalize_prompt_payload(runtime_settings_payload)
    world_state = _build_world_state(
        routed_input,
        chat_log,
        hidden_setting_records=hidden_setting_records,
        card_data=card_data,
        character_contexts=character_contexts,
    )
    objective_payload = objective_world.read_objective_world(card_folder)
    _clear_generated_character_files(root)

    input_json = {
        "input_analysis": analysis,
        "routed_input": routed_input,
        "raw_text": _to_text(raw_request.get("raw_text")),
        "explicit_payload": raw_request.get("explicit_payload", {}),
        "source_integrity": raw_request.get("source_integrity", {}),
        "recent_chat": chat_log,
        "gm_only_hidden_settings": hidden_setting_records,
        "objective_world": objective_payload,
        "card_data": compact_card_data(card_data),
        "character_contexts": character_contexts,
        "visible_events": world_state["visible_events"],
        "runtime_settings": runtime_payload["settings"],
        "style_profile": runtime_payload["style_profile"],
        "postprocess_repairs": postprocess_outputs.read_pending_repairs(card_folder),
    }
    agent_run.write_json(root / "input.json", input_json)
    _append_required_message(
        root,
        {
            "from": "input_analyst",
            "to": ["gm"],
            "type": "analysis_applied",
            "visibility": "gm_only",
            "payload": {
                "input_path": "input.json",
                "analysis_path": "input_analysis.output.json",
                "routed_characters": _clean_text_list(routed_input.get("characters", [])),
            },
        },
    )

    gm_packet = build_gm_packet(
        card_folder,
        routed_input,
        chat_log,
        card_data,
        character_contexts,
        hidden_setting_records=hidden_setting_records,
        runtime_settings_payload=runtime_payload,
        objective_world_payload=objective_payload,
    )
    gm_packet["input_analysis_request"] = _input_analysis_request_reference(raw_request)
    player_packet = build_player_packet(card_folder, routed_input, chat_log, world_state=world_state)
    agent_run.write_json(root / "gm.context.json", gm_packet)
    agent_run.write_json(root / "player.context.json", player_packet)

    character_packets = {}
    for character in _iter_characters(character_contexts):
        packet = build_character_packet(card_folder, character, routed_input, chat_log, world_state=world_state)
        safe = _safe_context_name_from_actor_id(packet.get("actor_id"))
        agent_run.write_json(root / "characters" / f"{safe}.context.json", packet)
        character_packets[safe] = packet

    manifest = agent_prompts.write_round_prompts(
        root,
        gm_packet,
        player_packet,
        character_packets,
        card_folder=card_folder,
        input_analysis_request=_input_analysis_model_request(raw_request),
        runtime_settings_payload=runtime_payload,
    )
    agent_run.append_manifest_stage(
        manifest,
        "analysis_applied",
        "Input analysis has been validated and applied to agent packets.",
    )
    agent_run.write_json(root / "manifest.json", manifest)
    return {
        "run_dir": str(root.resolve()),
        "routed_input": routed_input,
        "gm_packet": gm_packet,
        "player_packet": player_packet,
        "manifest": manifest,
        "character_packets": character_packets,
    }
