"""Deterministic mainline actor-call batch planning."""

from __future__ import annotations

from typing import Any, Iterable


DEFAULT_MAX_PARALLEL = 2


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_items(value: Any) -> list[str]:
    if isinstance(value, (str, bytes, dict)):
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        try:
            raw_items = list(value)
        except TypeError:
            return []
    return [_text(item) for item in raw_items if _text(item)]


def _max_parallel(value: Any, default: int = DEFAULT_MAX_PARALLEL) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


def max_parallel_from_input(input_payload: dict, default: int = DEFAULT_MAX_PARALLEL) -> int:
    card_data = _dict(input_payload.get("card_data"))
    orchestration = _dict(card_data.get("character_orchestration"))
    raw = orchestration.get("max_parallel_subagents", default)
    return _max_parallel(raw, default)


def _chunk(items: list[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _group_id(group: Any, fallback: str) -> str:
    if isinstance(group, dict):
        return _text(group.get("group_id")) or fallback
    return fallback


def _group_actor_ids(group: Any) -> list[str]:
    if isinstance(group, dict):
        raw = group.get("actors") or group.get("actor_ids") or []
    else:
        raw = group
    return _text_items(raw)


def _group_call_ids(group: Any) -> list[str]:
    if not isinstance(group, dict):
        return []
    raw = group.get("call_ids") or []
    return _text_items(raw)


def _warning(
    code: str,
    group_id: str,
    actors: list[str],
    call_ids: list[str],
    message: str,
) -> dict:
    return {
        "code": code,
        "group_id": group_id,
        "actors": actors,
        "call_ids": call_ids,
        "message": message,
    }


def _serial_batch(call: dict) -> dict:
    return {"kind": "serial", "group_id": "", "calls": [call]}


def _resolve_group_indices(
    group: Any,
    group_id: str,
    calls: list[dict],
    consumed: set[int],
) -> tuple[list[int], dict | None]:
    ids = _group_call_ids(group)
    actors = _group_actor_ids(group)
    call_ids = [_text(call.get("call_id")) for call in calls]
    actor_ids = [_text(call.get("actor_id")) for call in calls]

    if ids:
        indices = []
        missing = []
        for raw_id in ids:
            if raw_id in call_ids:
                index = call_ids.index(raw_id)
                if index not in consumed:
                    indices.append(index)
            else:
                missing.append(raw_id)
        if missing:
            return [], _warning(
                "unknown_parallel_group_member",
                group_id,
                actors,
                ids,
                "parallel group referenced call_ids that are not present in actor_calls",
            )
        return sorted(indices), None

    if actors:
        indices = []
        missing = []
        for actor_id in actors:
            matches = [
                index
                for index, candidate in enumerate(actor_ids)
                if candidate == actor_id and index not in consumed
            ]
            if len(matches) != 1:
                missing.append(actor_id)
            else:
                indices.append(matches[0])
        if missing:
            return [], _warning(
                "unknown_parallel_group_member",
                group_id,
                actors,
                ids,
                "parallel group referenced actors that do not map to exactly one pending actor_call",
            )
        return sorted(indices), None

    return [], _warning(
        "empty_parallel_group",
        group_id,
        actors,
        ids,
        "parallel group did not list actors, actor_ids, or call_ids",
    )


def _unsafe_group_warning(
    group_id: str,
    indices: list[int],
    calls: list[dict],
) -> dict | None:
    actors = [_text(calls[index].get("actor_id")) for index in indices]
    call_ids = [_text(calls[index].get("call_id")) for index in indices]
    if len(set(actors)) != len(actors):
        return _warning(
            "duplicate_actor_in_parallel_group",
            group_id,
            actors,
            call_ids,
            "same actor cannot be dispatched concurrently with itself",
        )
    if any(_text(calls[index].get("source_call_id")) for index in indices):
        return _warning(
            "dependent_call_in_parallel_group",
            group_id,
            actors,
            call_ids,
            "actor_calls with source_call_id are dependent continuations and must run serially",
        )
    if len(indices) < 2:
        return _warning(
            "parallel_group_too_small",
            group_id,
            actors,
            call_ids,
            "parallel group needs at least two safe calls to execute concurrently",
        )
    return None


def build_actor_batches(
    actor_calls: list[dict],
    parallel_groups: list[Any],
    *,
    max_parallel: int = DEFAULT_MAX_PARALLEL,
) -> dict:
    calls = [call for call in actor_calls if isinstance(call, dict)]
    limit = _max_parallel(max_parallel)
    consumed: set[int] = set()
    group_by_first_index: dict[int, tuple[str, list[int]]] = {}
    warnings: list[dict] = []

    for group_number, group in enumerate(_list(parallel_groups), start=1):
        group_id = _group_id(group, f"group-1-{group_number}")
        indices, warning = _resolve_group_indices(group, group_id, calls, consumed)
        if warning:
            warnings.append(warning)
            continue
        unsafe = _unsafe_group_warning(group_id, indices, calls)
        if unsafe:
            warnings.append(unsafe)
            continue
        for index in indices:
            consumed.add(index)
        group_by_first_index[min(indices)] = (group_id, indices)

    batches: list[dict] = []
    emitted: set[int] = set()
    for index, call in enumerate(calls):
        if index in emitted:
            continue
        group_info = group_by_first_index.get(index)
        if not group_info:
            batches.append(_serial_batch(call))
            emitted.add(index)
            continue
        group_id, indices = group_info
        for chunk in _chunk(indices, limit):
            chunk_calls = [calls[item] for item in chunk]
            batches.append({
                "kind": "parallel" if len(chunk_calls) > 1 else "serial",
                "group_id": group_id,
                "calls": chunk_calls,
            })
            emitted.update(chunk)

    return {"batches": batches, "warnings": warnings}
