"""Bounded runner for one subGM side thread."""

from __future__ import annotations

import concurrent.futures
import json
import re
import threading
from pathlib import Path
from typing import Any, Callable

import agent_interactions
import agent_lifecycle
import agent_projection
import agent_run
import agent_schemas
import agent_visibility
import agent_visibility_guard
import subgm_threads

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


MAX_SUBGM_STEPS = 4
RUNNABLE_STATUSES = {"running", "merging", "needs_gm", "blocked"}
STOP_STATUSES = {"completed", "paused", "blocked", "needs_gm"}
WORLD_VISIBLE_ACTOR_EVENTS = {"dialogue", "action"}
NON_WORLD_VISIBLE_ACTOR_EVENTS = {"perception", "perceive_request", "memory_delta", "goal_update"}
TRACE_SAFE_CHARACTER_CALL_ID_RE = re.compile(r"^call-character-[A-Za-z][A-Za-z0-9_]*-[0-9]+$")
MAX_STEPS_NOTICE = "subGM side thread reached max_steps without terminal status"
SIDE_THREAD_IO_LOCK = threading.RLock()

DispatchFn = Callable[[str, dict], dict]


class SubgmTurnLoopError(RuntimeError):
    """Raised when a side-thread loop cannot validate or continue."""


def _card_folder_for_run(run_dir: Path) -> Path:
    return run_dir.parents[1] if run_dir.parent.name == ".agent_runs" else run_dir.parent


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SubgmTurnLoopError(f"{path}: invalid JSON") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_text_if_exists(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SubgmTurnLoopError(f"{path}: cannot snapshot file") from exc


def _restore_text_snapshot(path: Path, snapshot: str | None) -> None:
    if snapshot is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(snapshot, encoding="utf-8")


def _snapshot_paths(paths: list[Path]) -> dict[Path, str | None]:
    return {path: _read_text_if_exists(path) for path in paths}


def _restore_snapshots(snapshots: dict[Path, str | None]) -> None:
    for path, snapshot in snapshots.items():
        _restore_text_snapshot(path, snapshot)


def _step_snapshot_paths(run_dir: Path, side_dir: Path) -> list[Path]:
    return [
        side_dir / "subgm.output.json",
        side_dir / "interaction.trace.json",
        side_dir / "actor.outputs.json",
        side_dir / "state.json",
        side_dir / "messages.jsonl",
        run_dir / "messages.jsonl",
        run_dir / "inboxes" / "gm.jsonl",
    ]


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SubgmTurnLoopError(f"{path}: cannot read JSONL") from exc
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SubgmTurnLoopError(f"{path}:{index + 1}: invalid JSONL") from exc
        if isinstance(record, dict):
            record.setdefault("sequence", index)
            records.append(record)
    return records


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


def _actor_state(actor_id: str, input_payload: dict) -> dict:
    return _characters_by_actor_id(input_payload).get(
        actor_id,
        {"name": actor_id.split(":", 1)[-1]},
    )


def _load_state(side_dir: Path) -> dict:
    state = _read_json(side_dir / "state.json", None)
    if not isinstance(state, dict):
        raise SubgmTurnLoopError(f"{side_dir}: missing state.json")
    return state


def _validate_subgm_output(thread_id: str, payload: Any) -> dict:
    try:
        output = agent_schemas.validate_subgm_output(payload)
    except agent_schemas.ValidationError as exc:
        raise SubgmTurnLoopError(f"invalid subGM output: {exc}") from exc
    if output.get("thread_id") != thread_id:
        raise SubgmTurnLoopError(
            f"invalid subGM output: thread_id {output.get('thread_id')!r} does not match {thread_id!r}"
        )
    return output


def _validate_actor_output(actor_id: str, payload: Any) -> dict:
    try:
        output = agent_schemas.validate_actor_output(payload)
    except agent_schemas.ValidationError as exc:
        raise SubgmTurnLoopError(f"invalid actor output for {actor_id}: {exc}") from exc
    if output.get("agent") != "character":
        raise SubgmTurnLoopError(f"invalid actor output for {actor_id}: wrong agent {output.get('agent')!r}")
    if output.get("agent_id") != actor_id:
        raise SubgmTurnLoopError(f"invalid actor output for {actor_id}: wrong agent_id {output.get('agent_id')!r}")
    return output


def _validate_actor_call_id(call: dict) -> str:
    call_id = str(call.get("call_id") or "").strip()
    if not TRACE_SAFE_CHARACTER_CALL_ID_RE.fullmatch(call_id):
        raise SubgmTurnLoopError(
            f"invalid side-thread actor call_id {call_id!r}; expected call-character-Name-N"
        )
    return call_id


def _event_content(event: dict) -> str:
    return str(event.get("content") or "")


def _record_subgm_output(
    side_dir: Path,
    agent_key: str,
    output: dict,
    hidden_phrases: list[str],
) -> None:
    for beat in output.get("scene_beats", []):
        content = agent_visibility_guard.redact_text(_event_content(beat), hidden_phrases)
        if content:
            agent_interactions.append_event(
                side_dir,
                actor=agent_key,
                visibility="world_visible",
                event_type="scene_beat",
                content=content,
                visibility_metadata=agent_visibility.visibility_fields_from_event(beat),
            )

    for event in output.get("events", []):
        content = agent_visibility_guard.redact_text(_event_content(event), hidden_phrases)
        if not content:
            continue
        metadata = _dict(event.get("metadata"))
        visibility = str(metadata.get("visibility") or event.get("visibility") or "world_visible")
        if visibility not in {"world_visible", "gm_visible", "actor_visible"}:
            visibility = "gm_visible"
        agent_interactions.append_event(
            side_dir,
            actor=agent_key,
            visibility=visibility,
            event_type=str(event.get("type") or "subgm_event"),
            content=content,
            target=str(event.get("target") or ""),
            source_call_id=str(event.get("source_call_id") or ""),
            visibility_metadata=agent_visibility.visibility_fields_from_event(event),
        )


def _record_actor_event(side_dir: Path, actor_id: str, event: dict, source_call_id: str) -> None:
    event_type = str(event.get("type") or "")
    if event_type in WORLD_VISIBLE_ACTOR_EVENTS:
        visibility = "world_visible"
    elif event_type in NON_WORLD_VISIBLE_ACTOR_EVENTS:
        visibility = "actor_visible" if event_type != "perceive_request" else "gm_visible"
    else:
        visibility = "actor_visible"
    agent_interactions.append_event(
        side_dir,
        actor=actor_id,
        visibility=visibility,
        event_type=event_type,
        content=_event_content(event),
        target=str(event.get("target") or ""),
        source_call_id=source_call_id,
    )


def _subgm_packet(run_dir: Path, side_dir: Path, thread_id: str, state: dict, input_payload: dict) -> dict:
    with SIDE_THREAD_IO_LOCK:
        messages = _read_jsonl(side_dir / "messages.jsonl")
        side_thread_summaries = subgm_threads.load_thread_summaries(run_dir)
        subgm_messages = subgm_threads.load_messages_for_gm(run_dir)
    return {
        "thread_id": thread_id,
        "input": input_payload,
        "state": state,
        "messages": messages,
        "boundary": _dict(state.get("boundary")),
        "objective": str(state.get("objective") or ""),
        "allowed_characters": _list(state.get("allowed_characters")),
        "forbidden_characters": _list(state.get("forbidden_characters")),
        "side_trace_summary": agent_interactions.summarize_for_story_input(side_dir),
        "main_trace_summary": agent_interactions.summarize_for_story_input(run_dir),
        "side_thread_summaries": side_thread_summaries,
        "subgm_messages": subgm_messages,
    }


def _side_world_state(run_dir: Path, side_dir: Path, input_payload: dict) -> dict:
    routed = _dict(input_payload.get("routed_input"))
    return {
        "role_channel": routed.get("role_channel", input_payload.get("role_channel", "")),
        "visible_events": agent_interactions.summarize_for_story_input(side_dir)["visible_events"],
        "side_thread": {
            "trace_summary": agent_interactions.summarize_for_story_input(side_dir),
            "main_trace_summary": agent_interactions.summarize_for_story_input(run_dir),
        },
    }


def _ensure_actor_allowed(actor_id: str, state: dict, input_payload: dict) -> None:
    if actor_id == "player":
        raise SubgmTurnLoopError("subGM side-thread actor calls must not target player")
    allowed = set(str(item) for item in _list(state.get("allowed_characters")))
    forbidden = set(str(item) for item in _list(state.get("forbidden_characters")))
    if actor_id not in allowed:
        raise SubgmTurnLoopError(f"{actor_id} is not in side-thread allowed_characters")
    if actor_id in forbidden:
        raise SubgmTurnLoopError(f"{actor_id} is in side-thread forbidden_characters")
    if actor_id not in _characters_by_actor_id(input_payload):
        raise SubgmTurnLoopError(f"{actor_id} is not an existing important character in input.json")


def _persist_actor_output(side_dir: Path, actor_id: str, output: dict) -> dict[str, list[dict]]:
    path = side_dir / "actor.outputs.json"
    current = _read_json(path, {})
    actor_outputs = current if isinstance(current, dict) else {}
    items = actor_outputs.get(actor_id)
    if not isinstance(items, list):
        items = []
        actor_outputs[actor_id] = items
    items.append(output)
    _write_json(path, actor_outputs)
    return actor_outputs


def _append_subgm_messages(run_dir: Path, thread_id: str, state_before: dict, output: dict) -> None:
    base = {
        "status": output.get("status"),
        "last_scene_beats": output.get("scene_beats", []),
        "next_resume_point": output.get("next_resume_point", ""),
    }
    messages = output.get("messages_to_gm") or []
    if messages:
        with SIDE_THREAD_IO_LOCK:
            for message in messages:
                payload = dict(message)
                payload.update(base)
                subgm_threads.append_subgm_message(run_dir, thread_id, payload)
        return

    changed = (
        state_before.get("status") != output.get("status")
        or state_before.get("last_scene_beats") != output.get("scene_beats", [])
        or state_before.get("next_resume_point", "") != output.get("next_resume_point", "")
    )
    if changed:
        with SIDE_THREAD_IO_LOCK:
            subgm_threads.append_subgm_message(run_dir, thread_id, base)


def _route_actor_calls(
    run_dir: Path,
    side_dir: Path,
    state: dict,
    input_payload: dict,
    output: dict,
    dispatch: DispatchFn,
    called_actors: list[str],
    hidden_phrases: list[str],
) -> None:
    for call in output.get("actor_calls", []):
        actor_id = str(call.get("actor_id") or "")
        _ensure_actor_allowed(actor_id, state, input_payload)
        call_id = _validate_actor_call_id(call)
        actor_state = _actor_state(actor_id, input_payload)
        if not agent_visibility.actor_call_visible_to_actor(call, actor_id, actor_state):
            raise SubgmTurnLoopError(
                f"actor call visibility_basis does not prove visibility for {actor_id}"
            )
        prompt = agent_visibility_guard.redact_text(str(call.get("prompt") or ""), hidden_phrases)
        packet = agent_projection.project_actor_context(
            actor_id,
            _side_world_state(run_dir, side_dir, input_payload),
            actor_state,
            prompt,
            agent_visibility.actor_call_basis(call),
        )
        packet = agent_lifecycle.attach_actor_context_version(_card_folder_for_run(run_dir), actor_id, packet)
        _write_progress_safe(
            "gm_loop.actor_dispatch",
            "支线角色行动中",
            percent=48,
            detail={
                "agent": actor_id,
                "subgm_thread_id": str(state.get("thread_id") or ""),
                "actor_call_id": call_id,
            },
        )
        actor_output = _validate_actor_output(actor_id, dispatch(actor_id, packet))
        called_actors.append(actor_id)
        _persist_actor_output(side_dir, actor_id, actor_output)
        for event in actor_output.get("events", []):
            _record_actor_event(side_dir, actor_id, event, call_id)


def _actor_visible_subgm_output(output: dict, input_payload: dict) -> dict:
    return agent_visibility_guard.sanitize_gm_output(
        {
            "scene_beats": output.get("scene_beats", []),
            "events": output.get("events", []),
            "actor_calls": output.get("actor_calls", []),
            "character_promotions": [],
        },
        input_payload,
    )


def _persist_max_steps_notice(run_dir: Path, thread_id: str, last_output: dict | None) -> None:
    payload = {
        "content": MAX_STEPS_NOTICE,
        "status": "needs_gm",
        "last_scene_beats": _list(_dict(last_output).get("scene_beats")),
        "next_resume_point": str(_dict(last_output).get("next_resume_point") or ""),
    }
    with SIDE_THREAD_IO_LOCK:
        subgm_threads.append_subgm_message(run_dir, thread_id, payload)


def run_side_thread(
    run_dir: str | Path,
    thread_id: str,
    dispatch: DispatchFn,
    *,
    max_steps: int = MAX_SUBGM_STEPS,
) -> dict:
    """Run one already-created subGM side thread through a bounded loop."""
    root = Path(run_dir)
    safe_id = subgm_threads.safe_thread_id(thread_id)
    side_dir = subgm_threads.thread_dir(root, safe_id)
    with SIDE_THREAD_IO_LOCK:
        state = _load_state(side_dir)
    status = str(state.get("status") or "")
    if status not in RUNNABLE_STATUSES:
        return {
            "ok": True,
            "thread_id": safe_id,
            "status": status,
            "steps": 0,
            "called_actors": [],
        }

    input_payload = _read_json(root / "input.json", {})
    if not isinstance(input_payload, dict):
        input_payload = {}
    hidden_phrases = agent_visibility_guard.hidden_phrases(input_payload)
    if not (side_dir / "interaction.trace.json").exists():
        agent_interactions.init_trace(side_dir, participants=[f"subGM:{safe_id}"])

    try:
        step_limit = int(max_steps)
    except (TypeError, ValueError):
        step_limit = 0
    if step_limit <= 0:
        return {
            "ok": True,
            "thread_id": safe_id,
            "status": "max_steps",
            "steps": 0,
            "called_actors": [],
        }
    initially_blocked = status == "blocked"
    if initially_blocked:
        step_limit = min(step_limit, 1)
    steps = 0
    called_actors: list[str] = []
    final_status = status
    last_output: dict | None = None

    for step_index in range(step_limit):
        with SIDE_THREAD_IO_LOCK:
            state_before = _load_state(side_dir)
        _write_progress_safe(
            "gm_loop.subgm_dispatch",
            "支线 GM 正在推进",
            percent=47,
            detail={"subgm_thread_id": safe_id, "step": step_index + 1},
        )
        raw_output = dispatch(
            f"subGM:{safe_id}",
            _subgm_packet(root, side_dir, safe_id, state_before, input_payload) | {"step": step_index},
        )
        output = _validate_subgm_output(safe_id, raw_output)
        actor_visible_output = _actor_visible_subgm_output(output, input_payload)
        steps += 1
        final_status = str(output.get("status") or "")
        last_output = output
        step_snapshots = _snapshot_paths(_step_snapshot_paths(root, side_dir))
        try:
            _write_json(side_dir / "subgm.output.json", output)
            _record_subgm_output(side_dir, f"subGM:{safe_id}", actor_visible_output, hidden_phrases)
            _route_actor_calls(
                root,
                side_dir,
                state_before,
                input_payload,
                actor_visible_output,
                dispatch,
                called_actors,
                hidden_phrases,
            )
            _append_subgm_messages(root, safe_id, state_before, output)
        except Exception:
            _restore_snapshots(step_snapshots)
            called_actors[:] = [
                actor_id
                for actor_id in called_actors
                if actor_id not in {
                    str(call.get("actor_id") or "")
                    for call in output.get("actor_calls", [])
                    if isinstance(call, dict)
                }
            ]
            raise

        if final_status in STOP_STATUSES:
            break

    if final_status not in STOP_STATUSES and steps >= step_limit and not initially_blocked:
        _persist_max_steps_notice(root, safe_id, last_output)
        final_status = "max_steps"

    return {
        "ok": True,
        "thread_id": safe_id,
        "status": final_status,
        "steps": steps,
        "called_actors": called_actors,
    }


def _runnable_thread_ids(run_dir: Path) -> list[str]:
    selected: list[str] = []
    reserved: set[str] = set()
    with SIDE_THREAD_IO_LOCK:
        summaries = subgm_threads.load_thread_summaries(run_dir)
    for summary in summaries:
        thread_id = str(summary.get("thread_id") or "")
        status = str(summary.get("status") or "")
        if not thread_id or status not in RUNNABLE_STATUSES:
            continue
        allowed = {str(item) for item in _list(summary.get("allowed_characters")) if str(item)}
        if reserved.intersection(allowed):
            continue
        selected.append(thread_id)
        reserved.update(allowed)
    return sorted(selected)


def run_ready_side_threads(
    run_dir: str | Path,
    dispatch: DispatchFn,
    *,
    max_workers: int = 2,
) -> list[dict]:
    """Run currently runnable side threads in deterministic non-overlapping batches."""
    root = Path(run_dir)
    thread_ids = _runnable_thread_ids(root)
    if not thread_ids:
        return []

    try:
        worker_count = int(max_workers)
    except (TypeError, ValueError):
        worker_count = 1
    if worker_count <= 1:
        return [run_side_thread(root, thread_id, dispatch) for thread_id in thread_ids]

    worker_count = min(worker_count, len(thread_ids))
    results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(run_side_thread, root, thread_id, dispatch): thread_id
            for thread_id in thread_ids
        }
        for future in concurrent.futures.as_completed(futures):
            thread_id = futures[future]
            results[thread_id] = future.result()
    return [results[thread_id] for thread_id in sorted(results)]


__all__ = ["MAX_SUBGM_STEPS", "SubgmTurnLoopError", "run_side_thread", "run_ready_side_threads"]
