"""State and message persistence for subGM side threads."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

import agent_messages
import agent_interactions


CST = timezone(timedelta(hours=8))
ACTIVE_STATUSES = {"running", "merging", "needs_gm", "blocked"}
THREAD_STATUSES = ACTIVE_STATUSES | {"paused", "completed"}
COMMAND_ACTIONS = {"start", "message", "accelerate", "pause", "resume", "merge", "close"}
HIDDEN_ID_TOKENS = (
    "gmnotes",
    "hiddennote",
    "hiddentruth",
    "gmonly",
    "omniscient",
    "userinstructionchannel",
    "hiddenfacts",
    "worldtruth",
    "outofcharacter",
    "privateevents",
)
CHARACTER_HIDDEN_ID_TOKENS = HIDDEN_ID_TOKENS
THREAD_ID_RE = re.compile(r"^[a-z0-9_]+$")
CHARACTER_ACTOR_ID_RE = re.compile(r"^character:[A-Za-z][A-Za-z0-9_]*$")


class SubgmThreadError(RuntimeError):
    """Raised when side-thread state or command input is invalid."""


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SubgmThreadError(f"{path}: invalid JSON") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dumps(payload), encoding="utf-8")


def _read_jsonl(path: Path) -> list[Dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SubgmThreadError(f"{path}: cannot read JSONL") from exc
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SubgmThreadError(f"{path}:{index + 1}: invalid JSONL") from exc
        if isinstance(record, dict):
            record.setdefault("sequence", index)
            records.append(record)
    return records


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["sequence"] = len(_read_jsonl(path))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload


def _append_common_message(run_dir: str | Path, payload: Dict[str, Any]) -> None:
    try:
        result = agent_messages.append_message(run_dir, payload)
    except Exception as exc:
        raise SubgmThreadError(f"common message bus append failed: {exc}") from exc
    if not isinstance(result, dict) or not result.get("ok"):
        reason = result.get("reason") if isinstance(result, dict) else "invalid result"
        error = result.get("error") if isinstance(result, dict) else ""
        detail = f"{reason}: {error}" if error else str(reason)
        raise SubgmThreadError(f"common message bus append failed: {detail}")


def _sequence_number(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def side_threads_root(run_dir: str | Path) -> Path:
    """Return the side-thread root under a round run directory."""
    return Path(run_dir) / "side_threads"


def safe_thread_id(thread_id: Any) -> str:
    """Validate and return a filesystem-safe subGM thread id."""
    if not isinstance(thread_id, str):
        raise SubgmThreadError("thread_id must be a string")
    if not thread_id:
        raise SubgmThreadError("thread_id must not be empty")
    if thread_id != thread_id.strip():
        raise SubgmThreadError("thread_id must not contain spaces")
    if thread_id == "player":
        raise SubgmThreadError("thread_id must not be player")
    if not THREAD_ID_RE.fullmatch(thread_id):
        raise SubgmThreadError(
            "thread_id must use lowercase ASCII letters, digits, and underscores only"
        )
    compact = re.sub(r"[^a-z0-9]", "", thread_id.lower())
    for token in HIDDEN_ID_TOKENS:
        if token in compact:
            raise SubgmThreadError(f"thread_id contains forbidden hidden marker {token}")
    return thread_id


def thread_dir(run_dir: str | Path, thread_id: Any) -> Path:
    """Return the validated side-thread directory path."""
    return side_threads_root(run_dir) / safe_thread_id(thread_id)


def _state_path(run_dir: str | Path, thread_id: str) -> Path:
    return thread_dir(run_dir, thread_id) / "state.json"


def _messages_path(run_dir: str | Path, thread_id: str) -> Path:
    return thread_dir(run_dir, thread_id) / "messages.jsonl"


def _load_state(run_dir: str | Path, thread_id: str) -> Dict[str, Any]:
    state = _read_json(_state_path(run_dir, thread_id), None)
    if not isinstance(state, dict):
        raise SubgmThreadError(f"side thread {thread_id} does not exist")
    return state


def _save_state(run_dir: str | Path, thread_id: str, state: Dict[str, Any]) -> None:
    state["updated_at"] = _now()
    _write_json(_state_path(run_dir, thread_id), state)


def _validate_character_actor_id(actor_id: str, path: str) -> str:
    if not CHARACTER_ACTOR_ID_RE.fullmatch(actor_id):
        raise SubgmThreadError(f"{path} must match character:<ASCIIName>")
    compact = re.sub(r"[^a-z0-9]", "", actor_id.split(":", 1)[1].lower())
    for token in CHARACTER_HIDDEN_ID_TOKENS:
        if token in compact:
            raise SubgmThreadError(f"{path} contains forbidden hidden marker {token}")
    return actor_id


def _validate_allowed_characters(items: Any, path: str = "allowed_characters") -> list[str]:
    if items is None:
        return []
    if not isinstance(items, list):
        raise SubgmThreadError(f"{path} must be a list")
    normalized = []
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise SubgmThreadError(f"{path}[{index}] must be a string")
        actor_id = item.strip()
        if actor_id == "player":
            raise SubgmThreadError("player cannot be in allowed_characters")
        normalized.append(_validate_character_actor_id(actor_id, f"{path}[{index}]"))
    return normalized


def _validate_forbidden_characters(items: Any, path: str = "forbidden_characters") -> list[str]:
    if items is None:
        return []
    if not isinstance(items, list):
        raise SubgmThreadError(f"{path} must be a list")
    normalized = []
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise SubgmThreadError(f"{path}[{index}] must be a string")
        actor_id = item.strip()
        if actor_id == "player":
            normalized.append(actor_id)
            continue
        normalized.append(_validate_character_actor_id(actor_id, f"{path}[{index}]"))
    return normalized


def _validate_command(command: Any) -> Dict[str, Any]:
    if not isinstance(command, dict):
        raise SubgmThreadError("GM subGM command must be an object")
    action = command.get("action")
    if action not in COMMAND_ACTIONS:
        raise SubgmThreadError(f"unsupported subGM command action: {action!r}")
    thread_id = safe_thread_id(command.get("thread_id"))
    normalized = dict(command)
    normalized["action"] = action
    normalized["thread_id"] = thread_id
    normalized["allowed_characters"] = _validate_allowed_characters(
        normalized.get("allowed_characters", [])
    )
    normalized["forbidden_characters"] = _validate_forbidden_characters(
        normalized.get("forbidden_characters", [])
    )
    metadata = normalized.get("metadata", {})
    if not isinstance(metadata, dict):
        raise SubgmThreadError("metadata must be an object")
    normalized["metadata"] = dict(metadata)
    return normalized


def _thread_ids(run_dir: str | Path) -> list[str]:
    root = side_threads_root(run_dir)
    if not root.exists():
        return []
    return sorted(
        child.name
        for child in root.iterdir()
        if child.is_dir() and (child / "state.json").exists()
    )


def _is_active_status(status: Any) -> bool:
    return str(status or "") in ACTIVE_STATUSES


def _reservation_map(run_dir: str | Path, exclude_thread_id: str = "") -> Dict[str, str]:
    reservations: Dict[str, str] = {}
    for current_id in _thread_ids(run_dir):
        if current_id == exclude_thread_id:
            continue
        state = _load_state(run_dir, current_id)
        if not _is_active_status(state.get("status")):
            continue
        for actor_id in _validate_allowed_characters(state.get("allowed_characters", [])):
            owner = reservations.get(actor_id)
            if owner and owner != current_id:
                raise SubgmThreadError(
                    f"{actor_id} is already reserved by active side thread {owner}"
                )
            reservations[actor_id] = current_id
    return dict(sorted(reservations.items(), key=lambda item: (item[0], item[1])))


def active_character_reservations(run_dir: str | Path) -> Dict[str, str]:
    """Return active character reservations as actor_id -> thread_id."""
    return _reservation_map(run_dir)


def _assert_no_reservation_conflict(
    run_dir: str | Path,
    thread_id: str,
    allowed_characters: Iterable[str],
) -> None:
    reservations = _reservation_map(run_dir, exclude_thread_id=thread_id)
    for actor_id in allowed_characters:
        owner = reservations.get(actor_id)
        if owner:
            raise SubgmThreadError(
                f"{actor_id} is already reserved by active side thread {owner}"
            )


def _load_projected_states(run_dir: str | Path) -> Dict[str, Dict[str, Any]]:
    states: Dict[str, Dict[str, Any]] = {}
    for current_id in _thread_ids(run_dir):
        state = _load_state(run_dir, current_id)
        states[current_id] = {
            "status": str(state.get("status") or ""),
            "allowed_characters": _validate_allowed_characters(
                state.get("allowed_characters", []),
                f"side thread {current_id} allowed_characters",
            ),
        }
    return states


def _project_reservations(states: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    reservations: Dict[str, str] = {}
    for thread_id in sorted(states):
        state = states[thread_id]
        if not _is_active_status(state.get("status")):
            continue
        for actor_id in state.get("allowed_characters", []):
            owner = reservations.get(actor_id)
            if owner and owner != thread_id:
                raise SubgmThreadError(
                    f"{actor_id} is already reserved by active side thread {owner}"
                )
            reservations[actor_id] = thread_id
    return reservations


def _assert_projected_reservation_available(
    reservations: Dict[str, str],
    thread_id: str,
    allowed_characters: Iterable[str],
) -> None:
    for actor_id in allowed_characters:
        owner = reservations.get(actor_id)
        if owner and owner != thread_id:
            raise SubgmThreadError(
                f"{actor_id} is already reserved by active side thread {owner}"
            )


def _prevalidate_command_batch(
    run_dir: str | Path,
    commands: list[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    normalized_commands = [_validate_command(command) for command in commands]
    states = _load_projected_states(run_dir)

    for command in normalized_commands:
        action = command["action"]
        thread_id = command["thread_id"]

        if action == "start":
            if thread_id in states:
                raise SubgmThreadError(f"side thread {thread_id} already exists")
            reservations = _project_reservations(states)
            _assert_projected_reservation_available(
                reservations,
                thread_id,
                command["allowed_characters"],
            )
            states[thread_id] = {
                "status": "running",
                "allowed_characters": list(command["allowed_characters"]),
            }
            continue

        state = states.get(thread_id)
        if state is None:
            raise SubgmThreadError(f"side thread {thread_id} does not exist")

        if action == "pause":
            state["status"] = "paused"
        elif action == "close":
            state["status"] = "completed"
        elif action == "resume":
            reservations = _project_reservations(states)
            _assert_projected_reservation_available(
                reservations,
                thread_id,
                state.get("allowed_characters", []),
            )
            state["status"] = "running"
        elif action == "merge":
            reservations = _project_reservations(states)
            _assert_projected_reservation_available(
                reservations,
                thread_id,
                state.get("allowed_characters", []),
            )
            state["status"] = "merging"
        elif action in {"message", "accelerate"}:
            continue
        else:
            raise SubgmThreadError(f"unsupported subGM command action: {action!r}")

    return normalized_commands


def prevalidate_gm_commands(run_dir: str | Path, commands: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Validate a GM subGM command batch without writing side-thread state."""
    if commands is None:
        commands = []
    if not isinstance(commands, list):
        raise SubgmThreadError("commands must be a list")
    return _prevalidate_command_batch(run_dir, commands)


def _state_history_entry(action: str, message: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "action": action,
        "message": str(message or ""),
        "created_at": _now(),
        "metadata": dict(metadata),
    }


def _append_state_history(
    state: Dict[str, Any],
    action: str,
    message: str,
    metadata: Dict[str, Any],
) -> None:
    history = state.setdefault("history", [])
    if not isinstance(history, list):
        history = []
        state["history"] = history
    history.append(_state_history_entry(action, message, metadata))


def _append_gm_message(
    run_dir: str | Path,
    thread_id: str,
    action: str,
    content: str,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    text = str(content or "")
    meta = dict(metadata or {})
    record = _append_jsonl(
        _messages_path(run_dir, thread_id),
        {
            "from": "gm",
            "to": f"subGM:{thread_id}",
            "thread_id": thread_id,
            "action": action,
            "content": text,
            "created_at": _now(),
            "metadata": meta,
        },
    )
    _append_common_message(
        run_dir,
        {
            "from": "gm",
            "to": [f"subGM:{thread_id}"],
            "type": "message",
            "visibility": "gm_only",
            "thread_id": thread_id,
            "payload": {
                "thread_id": thread_id,
                "action": action,
                "content": text,
                "metadata": meta,
            },
        },
    )
    return record


def _message_text(command: Dict[str, Any]) -> str:
    return str(command.get("message", "") or "")


def _create_thread(run_dir: str | Path, command: Dict[str, Any]) -> str:
    thread_id = command["thread_id"]
    side_dir = thread_dir(run_dir, thread_id)
    if (side_dir / "state.json").exists():
        raise SubgmThreadError(f"side thread {thread_id} already exists")

    allowed = command["allowed_characters"]
    _assert_no_reservation_conflict(run_dir, thread_id, allowed)

    now = _now()
    state = {
        "thread_id": thread_id,
        "status": "running",
        "title": str(command.get("title", "") or ""),
        "boundary": {
            "outline": str(command.get("outline", "") or ""),
            "time_window": str(command.get("time_window", "") or ""),
            "location": str(command.get("location", "") or ""),
            "priority": str(command.get("priority", "") or ""),
        },
        "objective": str(command.get("objective", "") or ""),
        "allowed_characters": allowed,
        "forbidden_characters": command["forbidden_characters"],
        "urgency": str(command.get("priority", "") or "normal"),
        "last_scene_beats": [],
        "next_resume_point": "",
        "metadata": dict(command.get("metadata", {})),
        "history": [_state_history_entry("start", _message_text(command), command["metadata"])],
        "created_at": now,
        "updated_at": now,
    }

    side_dir.mkdir(parents=True, exist_ok=True)
    _write_json(side_dir / "state.json", state)
    (side_dir / "messages.jsonl").write_text("", encoding="utf-8")
    agent_interactions.init_trace(side_dir, participants=[f"subGM:{thread_id}"])
    if _message_text(command):
        _append_gm_message(run_dir, thread_id, "start", _message_text(command), command["metadata"])
    return thread_id


def _apply_existing_thread_command(run_dir: str | Path, command: Dict[str, Any]) -> str:
    action = command["action"]
    thread_id = command["thread_id"]
    state = _load_state(run_dir, thread_id)
    message = _message_text(command)
    metadata = command["metadata"]

    if action == "message":
        _append_state_history(state, action, message, metadata)
        _append_gm_message(run_dir, thread_id, action, message, metadata)
    elif action == "accelerate":
        state["urgency"] = "accelerate"
        _append_state_history(state, action, message, metadata)
        _append_gm_message(run_dir, thread_id, action, message, metadata)
    elif action == "pause":
        state["status"] = "paused"
        _append_state_history(state, action, message, metadata)
        _append_gm_message(run_dir, thread_id, action, message, metadata)
    elif action == "resume":
        allowed = _validate_allowed_characters(state.get("allowed_characters", []))
        _assert_no_reservation_conflict(run_dir, thread_id, allowed)
        state["status"] = "running"
        _append_state_history(state, action, message, metadata)
        _append_gm_message(run_dir, thread_id, action, message, metadata)
    elif action == "merge":
        if not _is_active_status(state.get("status")):
            allowed = _validate_allowed_characters(state.get("allowed_characters", []))
            _assert_no_reservation_conflict(run_dir, thread_id, allowed)
        state["status"] = "merging"
        _append_state_history(state, action, message, metadata)
        _append_gm_message(run_dir, thread_id, action, message, metadata)
    elif action == "close":
        state["status"] = "completed"
        _append_state_history(state, action, message, metadata)
        _append_gm_message(run_dir, thread_id, action, message, metadata)
    else:
        raise SubgmThreadError(f"unsupported subGM command action: {action!r}")

    _save_state(run_dir, thread_id, state)
    return thread_id


def apply_gm_commands(run_dir: str | Path, commands: list[Dict[str, Any]]) -> Dict[str, list[str]]:
    """Apply normalized main-GM side-thread commands to thread state."""
    if commands is None:
        commands = []
    if not isinstance(commands, list):
        raise SubgmThreadError("commands must be a list")

    result = {
        "started": [],
        "messaged": [],
        "accelerated": [],
        "paused": [],
        "resumed": [],
        "merged": [],
        "closed": [],
    }
    result_key = {
        "start": "started",
        "message": "messaged",
        "accelerate": "accelerated",
        "pause": "paused",
        "resume": "resumed",
        "merge": "merged",
        "close": "closed",
    }

    for command in prevalidate_gm_commands(run_dir, commands):
        action = command["action"]
        if action == "start":
            thread_id = _create_thread(run_dir, command)
        else:
            thread_id = _apply_existing_thread_command(run_dir, command)
        result[result_key[action]].append(thread_id)
    return result


def append_subgm_message(run_dir: str | Path, thread_id: Any, message: Dict[str, Any]) -> Dict[str, Any]:
    """Append a subGM-to-GM message record to a side-thread log."""
    safe_id = safe_thread_id(thread_id)
    _load_state(run_dir, safe_id)
    if not isinstance(message, dict):
        raise SubgmThreadError("message must be an object")
    metadata = message.get("metadata", {})
    if not isinstance(metadata, dict):
        raise SubgmThreadError("message.metadata must be an object")
    content = str(message.get("content", "") or "")
    action = str(message.get("action", "message") or "message")
    record = {
        "from": f"subGM:{safe_id}",
        "to": "gm",
        "thread_id": safe_id,
        "action": action,
        "content": content,
        "created_at": str(message.get("created_at") or _now()),
        "metadata": dict(metadata),
    }
    for key, value in message.items():
        if key not in record and key != "metadata":
            record[key] = value

    state = _load_state(run_dir, safe_id)
    status = message.get("status")
    if status is not None:
        if status not in THREAD_STATUSES:
            raise SubgmThreadError(f"status is not an allowed side-thread status: {status!r}")
        was_active = _is_active_status(state.get("status"))
        will_be_active = _is_active_status(status)
        if will_be_active and not was_active:
            raise SubgmThreadError(
                "subGM messages cannot reactivate inactive side threads; use GM resume"
            )
        state["status"] = status
    if isinstance(message.get("last_scene_beats"), list):
        state["last_scene_beats"] = message["last_scene_beats"]
        state["last_scene_beats_updated_at"] = _now()
    elif isinstance(message.get("scene_beats"), list):
        state["last_scene_beats"] = message["scene_beats"]
        state["last_scene_beats_updated_at"] = _now()
    if isinstance(message.get("next_resume_point"), str):
        state["next_resume_point"] = message["next_resume_point"]
        state["next_resume_point_updated_at"] = _now()
    _save_state(run_dir, safe_id, state)

    written = _append_jsonl(_messages_path(run_dir, safe_id), record)
    _append_common_message(
        run_dir,
        {
            "from": f"subGM:{safe_id}",
            "to": ["gm"],
            "type": "message",
            "visibility": "gm_only",
            "thread_id": safe_id,
            "payload": {
                "thread_id": safe_id,
                "action": action,
                "content": content,
                "status": written.get("status"),
                "metadata": dict(metadata),
            },
        },
    )
    return written


def _last_message(run_dir: str | Path, thread_id: str) -> Dict[str, Any] | None:
    messages = _read_jsonl(_messages_path(run_dir, thread_id))
    return messages[-1] if messages else None


def _last_scene_beats(side_dir: Path, state: Dict[str, Any]) -> list[Any]:
    beats = state.get("last_scene_beats", [])
    if isinstance(beats, list) and (beats or state.get("last_scene_beats_updated_at")):
        return beats
    output = _read_json(side_dir / "subgm.output.json", None)
    if isinstance(output, dict) and isinstance(output.get("scene_beats"), list):
        return output["scene_beats"]
    return beats if isinstance(beats, list) else []


def _next_resume_point(side_dir: Path, state: Dict[str, Any]) -> str:
    resume_point = state.get("next_resume_point", "")
    if isinstance(resume_point, str) and (resume_point or state.get("next_resume_point_updated_at")):
        return resume_point
    output = _read_json(side_dir / "subgm.output.json", None)
    if isinstance(output, dict) and isinstance(output.get("next_resume_point"), str):
        return output["next_resume_point"]
    return str(resume_point or "")


def load_thread_summaries(run_dir: str | Path) -> list[Dict[str, Any]]:
    """Load sorted side-thread summaries for later GM/story packets."""
    summaries = []
    for current_id in _thread_ids(run_dir):
        state = _load_state(run_dir, current_id)
        side_dir = thread_dir(run_dir, current_id)
        summaries.append({
            "thread_id": current_id,
            "status": str(state.get("status", "") or ""),
            "title": str(state.get("title", "") or ""),
            "boundary": state.get("boundary", {}) if isinstance(state.get("boundary"), dict) else {},
            "objective": str(state.get("objective", "") or ""),
            "allowed_characters": _validate_allowed_characters(state.get("allowed_characters", [])),
            "forbidden_characters": _validate_forbidden_characters(state.get("forbidden_characters", [])),
            "last_message": _last_message(run_dir, current_id),
            "last_scene_beats": _last_scene_beats(side_dir, state),
            "next_resume_point": _next_resume_point(side_dir, state),
            "urgency": str(state.get("urgency", "") or ""),
        })
    return summaries


def load_messages_for_gm(run_dir: str | Path) -> list[Dict[str, Any]]:
    """Return all side-thread messages sorted by timestamp, thread id, and log order."""
    messages = []
    for current_id in _thread_ids(run_dir):
        for record in _read_jsonl(_messages_path(run_dir, current_id)):
            item = dict(record)
            item.setdefault("thread_id", current_id)
            messages.append(item)
    return sorted(
        messages,
        key=lambda item: (
            str(item.get("created_at", "")),
            str(item.get("thread_id", "")),
            _sequence_number(item.get("sequence", 0)),
        ),
    )


def assert_main_actor_calls_do_not_conflict(
    run_dir: str | Path,
    actor_calls: Iterable[Dict[str, Any]],
) -> None:
    """Reject main-GM actor calls that target characters reserved by active side threads."""
    reservations = active_character_reservations(run_dir)
    for call in actor_calls or []:
        if not isinstance(call, dict):
            continue
        actor_id = call.get("actor_id")
        owner = reservations.get(actor_id)
        if owner:
            call_id = str(call.get("call_id", "") or "")
            detail = f" for call {call_id}" if call_id else ""
            raise SubgmThreadError(
                f"{actor_id} is reserved by active side thread {owner}{detail}"
            )
