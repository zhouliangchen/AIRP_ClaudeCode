"""Visibility proof helpers for actor-facing RP projection."""

from __future__ import annotations

import re
from typing import Any, Iterable


PUBLIC_MARKERS = {"all", "everyone", "public", "world", "world_visible"}
SOURCE_BUCKET_PUBLIC_MARKERS = {"all", "everyone", "public"}
ALLOWED_BASIS_MODES = {
    "direct",
    "public",
    "location",
    "private_dialogue",
    "self",
    "witness",
    "inference",
}
VISIBILITY_FIELDS = (
    "scene_id",
    "location",
    "time_window",
    "visible_to",
    "sensory_channels",
    "source_actor",
    "target_actor",
    "visibility_basis",
)
DEFAULT_ACTOR_SENSORY_CHANNELS = {"visual", "auditory", "tactile", "olfactory", "taste"}


HIDDEN_MARKERS = {
    "gm_only",
    "world_truth",
    "hidden_fact",
    "hidden_facts",
    "hidden_note",
    "hidden_truth",
    "user_instruction_channel",
    "omniscient",
    "out_of_character",
    "private_memory",
    "internal_state",
    "internal_thoughts",
}

PRIVATE_EVENT_TYPES = {
    "gm_only",
    "hidden",
    "internal",
    "memory_delta",
    "out_of_character",
    "private",
    "thought",
}

PRIVATE_EVENT_VISIBILITIES = {
    "gm_only",
    "gm_visible",
    "hidden",
    "internal",
    "omniscient",
    "out_of_character",
    "private",
}

SCALAR_VISIBILITY_FIELDS = (
    "scene_id",
    "location",
    "time_window",
    "source_actor",
    "target_actor",
)
LIST_VISIBILITY_FIELDS = ("visible_to", "sensory_channels")


def _canonical_marker(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _actor_identity_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _location_identity_key(value: Any) -> str:
    return _actor_identity_key(value)


def _sensory_identity_key(value: Any) -> str:
    return _actor_identity_key(value)


HIDDEN_MARKER_CANONICALS = {_canonical_marker(marker) for marker in HIDDEN_MARKERS}
PUBLIC_MARKER_CANONICALS = {_canonical_marker(marker) for marker in PUBLIC_MARKERS}
SOURCE_BUCKET_PUBLIC_MARKER_CANONICALS = {
    _canonical_marker(marker) for marker in SOURCE_BUCKET_PUBLIC_MARKERS
}


def _contains_hidden_marker_text(value: Any) -> bool:
    canonical = _canonical_marker(value)
    return any(marker and marker in canonical for marker in HIDDEN_MARKER_CANONICALS)


def _has_hidden_marker(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if _contains_hidden_marker_text(key) or _has_hidden_marker(child):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_has_hidden_marker(item) for item in value)
    if isinstance(value, str):
        return _contains_hidden_marker_text(value)
    return False


def _has_private_classification(event: dict[str, Any]) -> bool:
    return (
        _canonical_marker(event.get("type")) in PRIVATE_EVENT_TYPES
        or _canonical_marker(event.get("visibility")) in PRIVATE_EVENT_VISIBILITIES
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, tuple):
        raw_items = list(value)
    elif isinstance(value, set):
        raw_items = sorted(value, key=lambda item: str(item))
    else:
        raw_items = [value]
    return [_string(item) for item in raw_items if item is not None]


def _canonical_set(values: Iterable[Any]) -> set[str]:
    return {_canonical_marker(value) for value in values if _canonical_marker(value)}


def _location_identity_set(values: Iterable[Any]) -> set[str]:
    return {_location_identity_key(value) for value in values if _location_identity_key(value)}


def _sensory_identity_set(values: Iterable[Any]) -> set[str]:
    return {_sensory_identity_key(value) for value in values if _sensory_identity_key(value)}


def _actor_matches(value: Any, actor_id: str) -> bool:
    value_key = _actor_identity_key(value)
    return bool(value_key) and value_key == _actor_identity_key(actor_id)


def _contains_actor(values: Iterable[Any], actor_id: str) -> bool:
    actor_key = _actor_identity_key(actor_id)
    return bool(actor_key) and any(_actor_identity_key(value) == actor_key for value in values)


def _contains_public_marker(values: Iterable[Any]) -> bool:
    return bool(_canonical_set(values).intersection(PUBLIC_MARKER_CANONICALS))


def _source_bucket_grants_visibility(source_bucket_actor_id: Any, actor_id: str) -> bool:
    if source_bucket_actor_id is None:
        return False
    marker = _canonical_marker(source_bucket_actor_id)
    identity_key = _actor_identity_key(source_bucket_actor_id)
    if not marker and not identity_key:
        return False
    return marker in SOURCE_BUCKET_PUBLIC_MARKER_CANONICALS or identity_key == _actor_identity_key(actor_id)


def normalize_visibility_basis(value: Any, *, require_summary: bool = False) -> dict[str, Any]:
    """Return a compact, JSON-safe visibility-basis dict."""
    if not isinstance(value, dict):
        return {}
    if _has_hidden_marker(value):
        return {}

    normalized: dict[str, Any] = {}

    mode = _canonical_marker(value.get("mode"))
    if mode in ALLOWED_BASIS_MODES:
        normalized["mode"] = mode

    if "summary" in value:
        summary = _string(value.get("summary")).strip()
        if summary and not _contains_hidden_marker_text(summary):
            normalized["summary"] = summary

    if require_summary and not normalized.get("summary"):
        return {}

    for field in SCALAR_VISIBILITY_FIELDS:
        if field in value and value.get(field) is not None:
            normalized[field] = _string(value.get(field))

    for field in LIST_VISIBILITY_FIELDS:
        if field in value:
            normalized[field] = _string_list(value.get(field))

    return normalized


def visibility_fields_from_event(event: Any) -> dict[str, Any]:
    """Extract normalized P2 visibility fields from an event or actor call."""
    payload = _as_dict(event)
    if not payload:
        return {}

    fields: dict[str, Any] = {}
    for nested_key in ("visibility_metadata", "metadata"):
        nested = _as_dict(payload.get(nested_key))
        for field in SCALAR_VISIBILITY_FIELDS:
            if field not in fields and field in nested and nested.get(field) is not None:
                fields[field] = _string(nested.get(field))
        for field in LIST_VISIBILITY_FIELDS:
            if field not in fields and field in nested:
                fields[field] = _string_list(nested.get(field))
        if "visibility_basis" not in fields and "visibility_basis" in nested:
            basis = normalize_visibility_basis(nested.get("visibility_basis"))
            if basis:
                fields["visibility_basis"] = basis

    for field in SCALAR_VISIBILITY_FIELDS:
        if field in payload and payload.get(field) is not None:
            fields[field] = _string(payload.get(field))

    for field in LIST_VISIBILITY_FIELDS:
        if field in payload:
            fields[field] = _string_list(payload.get(field))

    if "source_actor" not in fields and "actor" in payload and payload.get("actor") is not None:
        fields["source_actor"] = _string(payload.get("actor"))
    if "target_actor" not in fields:
        target = _first_present(payload, "target", "actor_id")
        if target is not None:
            fields["target_actor"] = _string(target)
    if "visible_to" not in fields:
        visible_to = _first_present(payload, "recipients", "witnesses")
        if visible_to is not None:
            fields["visible_to"] = _string_list(visible_to)

    if "visibility_basis" in payload:
        basis = normalize_visibility_basis(payload.get("visibility_basis"))
        if basis:
            fields["visibility_basis"] = basis

    return fields


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload.get(key)
    return None


def _actor_locations(actor_state: Any) -> set[str]:
    actor = _as_dict(actor_state)
    values: list[Any] = []
    for key in ("location", "current_location", "scene_id"):
        if key in actor:
            values.extend(_string_list(actor.get(key)))
    for nested_key in ("self_knowledge", "metadata"):
        nested = _as_dict(actor.get(nested_key))
        if "location" in nested:
            values.extend(_string_list(nested.get("location")))
    return _location_identity_set(values)


def _actor_sensory_channels(actor_state: Any) -> set[str]:
    actor = _as_dict(actor_state)
    values: list[Any] = []
    found_explicit = False

    for key in ("sensory_channels", "available_sensory_channels"):
        if key in actor:
            found_explicit = True
            values.extend(_string_list(actor.get(key)))

    nested = _as_dict(actor.get("self_knowledge"))
    if "sensory_channels" in nested:
        found_explicit = True
        values.extend(_string_list(nested.get("sensory_channels")))

    if not found_explicit:
        values.extend(DEFAULT_ACTOR_SENSORY_CHANNELS)
    return _sensory_identity_set(values)


def _visibility_basis_for_event(event: dict[str, Any]) -> dict[str, Any]:
    fields = visibility_fields_from_event(event)
    raw_basis = event.get("visibility_basis")
    if raw_basis is None:
        raw_basis = fields.get("visibility_basis")
    basis = normalize_visibility_basis(raw_basis, require_summary=True)
    if not basis:
        return {}

    for field in SCALAR_VISIBILITY_FIELDS:
        if field not in basis and field in fields:
            basis[field] = fields[field]
    for field in LIST_VISIBILITY_FIELDS:
        if field not in basis and field in fields:
            basis[field] = fields[field]
    return basis


def _visible_to_values(event: dict[str, Any], basis: dict[str, Any]) -> list[str]:
    if "visible_to" in basis:
        return _string_list(basis.get("visible_to"))
    return _string_list(event.get("visible_to") or event.get("recipients") or event.get("witnesses"))


def _event_location(event: dict[str, Any], basis: dict[str, Any]) -> str:
    location = basis.get("location") if "location" in basis else event.get("location")
    if location is None:
        location = basis.get("scene_id") if "scene_id" in basis else event.get("scene_id")
    return _string(location)


def _event_sensory_channels(event: dict[str, Any], basis: dict[str, Any]) -> set[str]:
    if "sensory_channels" in basis:
        return _sensory_identity_set(_string_list(basis.get("sensory_channels")))
    return _sensory_identity_set(_string_list(event.get("sensory_channels")))


def _event_has_explicit_sensory_channels(event: dict[str, Any], basis: dict[str, Any]) -> bool:
    return "sensory_channels" in basis or "sensory_channels" in event


def _public_visible(event: dict[str, Any], actor_id: str, basis: dict[str, Any]) -> bool:
    visible_to = _visible_to_values(event, basis)
    return _contains_public_marker(visible_to) or _contains_actor(visible_to, actor_id)


def _location_visible(event: dict[str, Any], actor_id: str, actor_state: Any, basis: dict[str, Any]) -> bool:
    del actor_id
    location = _location_identity_key(_event_location(event, basis))
    if not location:
        return False
    if location not in _actor_locations(actor_state):
        return False

    event_channels = _event_sensory_channels(event, basis)
    if not event_channels:
        return not _event_has_explicit_sensory_channels(event, basis)
    return bool(event_channels.intersection(_actor_sensory_channels(actor_state)))


def _private_dialogue_visible(event: dict[str, Any], actor_id: str, basis: dict[str, Any]) -> bool:
    source_actor = basis.get("source_actor") or event.get("source_actor") or event.get("actor")
    target_actor = basis.get("target_actor") or event.get("target_actor") or event.get("target")
    witnesses = _visible_to_values(event, basis)
    return (
        _actor_matches(source_actor, actor_id)
        or _actor_matches(target_actor, actor_id)
        or _contains_actor(witnesses, actor_id)
    )


def _direct_visible(event: dict[str, Any], actor_id: str, basis: dict[str, Any]) -> bool:
    target_actor = basis.get("target_actor") or event.get("target_actor") or event.get("target")
    visible_to = _visible_to_values(event, basis)
    return (
        _actor_matches(target_actor, actor_id)
        or _contains_actor(visible_to, actor_id)
        or _contains_public_marker(visible_to)
    )


def _self_visible(event: dict[str, Any], actor_id: str, basis: dict[str, Any]) -> bool:
    source_actor = basis.get("source_actor") or event.get("source_actor") or event.get("actor")
    target_actor = basis.get("target_actor") or event.get("target_actor") or event.get("target")
    visible_to = _visible_to_values(event, basis)
    return (
        _actor_matches(source_actor, actor_id)
        or _actor_matches(target_actor, actor_id)
        or _contains_actor(visible_to, actor_id)
        or _contains_public_marker(visible_to)
    )


def _witness_visible(event: dict[str, Any], actor_id: str, basis: dict[str, Any]) -> bool:
    visible_to = _visible_to_values(event, basis)
    return _contains_actor(visible_to, actor_id) or _contains_public_marker(visible_to)


def _inference_visible(event: dict[str, Any], actor_id: str, actor_state: Any, basis: dict[str, Any]) -> bool:
    if _witness_visible(event, actor_id, basis):
        return True
    return _location_visible(event, actor_id, actor_state, basis)


def event_visible_to_actor(
    event: Any,
    actor_id: str,
    actor_state: Any,
    *,
    source_bucket_actor_id: str = "",
) -> bool:
    """Return whether an event has fail-closed proof for this actor."""
    if not isinstance(event, dict) or not _string(actor_id).strip():
        return False
    if _has_hidden_marker(event):
        return False
    if _has_private_classification(event):
        return False

    if _source_bucket_grants_visibility(source_bucket_actor_id, actor_id):
        return True

    basis = _visibility_basis_for_event(event)
    mode = basis.get("mode")
    if mode not in ALLOWED_BASIS_MODES:
        return False

    if mode == "public":
        return _public_visible(event, actor_id, basis)
    if mode == "location":
        return _location_visible(event, actor_id, actor_state, basis)
    if mode == "private_dialogue":
        return _private_dialogue_visible(event, actor_id, basis)
    if mode == "direct":
        return _direct_visible(event, actor_id, basis)
    if mode == "self":
        return _self_visible(event, actor_id, basis)
    if mode == "witness":
        return _witness_visible(event, actor_id, basis)
    if mode == "inference":
        return _inference_visible(event, actor_id, actor_state, basis)
    return False


def _iter_events(events: Any) -> list[Any]:
    if events is None:
        return []
    if isinstance(events, list):
        return events
    if isinstance(events, tuple):
        return list(events)
    if isinstance(events, set):
        return sorted(events, key=lambda item: str(item))
    return [events]


def filter_visible_events(
    events: Any,
    actor_id: str,
    actor_state: Any,
    *,
    source_bucket_actor_id: str = "",
) -> list[Any]:
    """Filter a scalar or iterable of events through actor visibility proof."""
    return [
        event
        for event in _iter_events(events)
        if event_visible_to_actor(
            event,
            actor_id,
            actor_state,
            source_bucket_actor_id=source_bucket_actor_id,
        )
    ]


def actor_call_basis(actor_call: Any) -> dict[str, Any]:
    """Return the normalized visibility basis for an actor call."""
    call = _as_dict(actor_call)
    if not call:
        return {}

    fields = visibility_fields_from_event(call)
    raw_basis = call.get("visibility_basis")
    if raw_basis is None:
        raw_basis = fields.get("visibility_basis")
    basis = normalize_visibility_basis(raw_basis, require_summary=True)
    if not basis:
        return {}

    for field in SCALAR_VISIBILITY_FIELDS:
        if field not in basis and field in fields:
            basis[field] = fields[field]
    for field in LIST_VISIBILITY_FIELDS:
        if field not in basis and field in fields:
            basis[field] = fields[field]
    if "target_actor" not in basis and call.get("actor_id") is not None:
        basis["target_actor"] = _string(call.get("actor_id"))
    return basis


def actor_call_visible_to_actor(actor_call: Any, actor_id: str, actor_state: Any) -> bool:
    """Return whether an actor call's basis proves visibility to this actor."""
    call = _as_dict(actor_call)
    if not call:
        return False
    if _has_hidden_marker(call):
        return False

    basis = actor_call_basis(call)
    if not basis:
        return False
    event = dict(visibility_fields_from_event(call))
    event["visibility_basis"] = basis
    return event_visible_to_actor(event, actor_id, actor_state)


__all__ = [
    "ALLOWED_BASIS_MODES",
    "PUBLIC_MARKERS",
    "VISIBILITY_FIELDS",
    "actor_call_basis",
    "actor_call_visible_to_actor",
    "event_visible_to_actor",
    "filter_visible_events",
    "normalize_visibility_basis",
    "visibility_fields_from_event",
]
